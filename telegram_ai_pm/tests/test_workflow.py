"""Integration tests use a disposable PostgreSQL database and never call remote APIs.

TEST_DATABASE_ADMIN_URL=postgresql://... python -m unittest discover -s tests -v
The admin role must be able to CREATE DATABASE. Existing databases are not modified.
"""
import asyncio
import json
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import psycopg
from psycopg import sql

ADMIN = os.getenv('TEST_DATABASE_ADMIN_URL')
if not ADMIN:
    raise unittest.SkipTest('Set TEST_DATABASE_ADMIN_URL to run PostgreSQL integration tests')

DB_NAME = 'pm_test_' + uuid.uuid4().hex
admin = psycopg.connect(ADMIN, autocommit=True)
admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(DB_NAME)))
from sqlalchemy.engine import make_url
url = make_url(ADMIN).set(drivername='postgresql+psycopg', database=DB_NAME)
os.environ.update(DATABASE_URL=url.render_as_string(hide_password=False),
                  BOT_TOKEN='123456789:LOCAL_ONLY_NOT_A_REAL_TELEGRAM_TOKEN',
                  OPENAI_API_KEY='local-only-unused', OWNER_TELEGRAM_ID='1',
                  GOOGLE_TOKEN_FILE='/nonexistent-calendar-token')

from app import main as m
from app.ai import find_semantic_duplicates, client
from app.db import Base, Session, engine, Task, Project, Setting, EventLog, TaskDependency, Evidence
from app.dependencies import add_dependency
from app.migrate import upgrade
from app.workflow import future_due, require_transition, save_change, PRIORITIES
upgrade()


def tearDownModule():
    asyncio.run(m.bot.session.close())
    asyncio.run(client.close())
    engine.dispose()
    admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(DB_NAME)))
    admin.close()


def due(days=2):
    return (datetime.now(m.TZ) + timedelta(days=days)).strftime('%Y-%m-%d %H:%M')


def message(text, owner=1):
    return SimpleNamespace(text=text, from_user=SimpleNamespace(id=owner), answer=AsyncMock())


def callback(token, action='approve', owner=1):
    return SimpleNamespace(data=f'change:{action}:{token}', from_user=SimpleNamespace(id=owner),
                           answer=AsyncMock(), message=SimpleNamespace(
                               answer=AsyncMock(), edit_reply_markup=AsyncMock()))


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with engine.begin() as connection:
            names = ', '.join('"' + table + '"' for table in Base.metadata.tables)
            connection.exec_driver_sql(f'TRUNCATE {names} RESTART IDENTITY CASCADE')
        with Session.begin() as session:
            project = Project(title='Test', status='active')
            session.add(project)
            session.flush()
            task = Task(title='Task', status='planned', project_id=project.id,
                        due_at=future_due(due()), priority='P2', criteria='Result')
            session.add(task)
            session.flush()
            self.tid, self.pid = task.id, project.id

    def proposal(self, kind='priority', **overrides):
        with Session.begin() as session:
            task = session.get(Task, self.tid)
            payload = dict(task_id=self.tid, status=task.status, old=task.priority, new='P4')
            payload.update(overrides)
            return save_change(session, kind, self.tid, payload)

    async def test_old_button_cannot_confirm_or_cancel_replacement(self):
        old = self.proposal(new='P1')
        new = self.proposal(new='P4')
        for action in ('approve', 'cancel'):
            await m.confirm_change(callback(old, action))
        with Session() as s:
            self.assertEqual(s.get(Task, self.tid).priority, 'P2')
            self.assertIsNotNone(s.get(Setting, f'pending_change:{new}'))
        await m.confirm_change(callback(new))
        await m.confirm_change(callback(new))
        with Session() as s:
            self.assertEqual(s.get(Task, self.tid).priority, 'P4')
            self.assertEqual(s.query(EventLog).filter_by(kind='task_priority_changed').count(), 1)

    async def test_concurrent_confirmation_is_single_use(self):
        token = self.proposal()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(asyncio.run, m.confirm_change(callback(token))) for _ in range(2)]
            for result in results:
                await asyncio.to_thread(result.result, 10)
        with Session() as s:
            self.assertEqual(s.query(EventLog).filter_by(kind='task_priority_changed').count(), 1)

    async def test_concurrent_proposals_leave_only_latest_token_valid(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.proposal, new=value) for value in ('P1', 'P4')]
            tokens = [await asyncio.to_thread(future.result, 10) for future in futures]
        with Session() as s:
            latest = s.get(Setting, f'latest_change:priority:{self.tid}').value
            self.assertIn(latest, tokens)
            self.assertEqual(s.query(Setting).filter(Setting.key.like('pending_change:%')).count(), 1)
        for token in tokens:
            if token != latest:
                await m.confirm_change(callback(token))
        with Session() as s:
            self.assertEqual(s.get(Task, self.tid).priority, 'P2')

    async def test_model_risk_cycles_and_false_confirmation_are_rejected(self):
        from app.risk_validation import validate_analysis
        tasks = [{'id': 1, 'project_id': 1}, {'id': 2, 'project_id': 1}]
        for edges in ([dict(before=1, after=2), dict(before=2, after=1)],
                      [dict(before=1, after=2, confirmed=True)],
                      [dict(before=1, after=999)]):
            with self.assertRaises(ValueError):
                validate_analysis({'dependencies': edges}, tasks)
        with self.assertRaises(ValueError):
            validate_analysis({'risks': [dict(level='UNKNOWN', task_ids=[1])]}, tasks)

    async def test_owner_expiration_and_changed_status(self):
        token = self.proposal()
        await m.confirm_change(callback(token, owner=2))
        with Session.begin() as s:
            setting = s.get(Setting, f'pending_change:{token}')
            data = json.loads(setting.value)
            data['created_at'] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            setting.value = json.dumps(data)
        await m.confirm_change(callback(token))
        token = self.proposal()
        with Session.begin() as s:
            s.get(Task, self.tid).status = 'cancelled'
        await m.confirm_change(callback(token))
        with Session() as s:
            self.assertEqual(s.get(Task, self.tid).priority, 'P2')

    async def test_p4_text_and_status_confirmation(self):
        request = message(f'Понизь приоритет задачи №{self.tid} до P4')
        await m.chat(request)
        keyboard = request.answer.call_args.kwargs['reply_markup']
        data = keyboard.inline_keyboard[0][0].callback_data
        self.assertLessEqual(len(data.encode()), 64)
        await m.confirm_change(callback(data.split(':')[-1]))
        await m.chat(message(f'Начал задачу №{self.tid}'))
        with Session() as s:
            token = s.get(Setting, f'latest_change:status:{self.tid}').value
        await m.confirm_change(callback(token))
        with Session() as s:
            self.assertEqual(s.get(Task, self.tid).status, 'in_progress')
            self.assertEqual(s.get(Task, self.tid).priority, 'P4')

    async def test_submission_and_terminal_states(self):
        with patch.object(m, 'assess_evidence', AsyncMock(return_value='Advisory only')):
            await m.done(message(f'/done {self.tid} | Evidence result'))
            with Session() as s:
                self.assertEqual(s.query(Evidence).count(), 0)
            await m.begin(message(f'/begin {self.tid}'))
            await m.done(message(f'/done {self.tid} | Evidence result'))
            await m.reject(message(f'/reject {self.tid} | More detail'))
            await m.done(message(f'/done {self.tid} | Improved evidence'))
            await m.verify(message(f'/verify {self.tid}'))
            await m.done(message(f'/done {self.tid} | Cannot reopen'))
            await m.move(message(f'/move {self.tid} | {due(3)}'))
        with Session() as s:
            self.assertEqual(s.get(Task, self.tid).status, 'done')
            self.assertEqual(s.query(Evidence).count(), 2)
            self.assertTrue(s.query(Evidence).order_by(Evidence.id.desc()).first().verified)
        with Session.begin() as s:
            s.get(Task, self.tid).status = 'cancelled'
        await m.move(message(f'/move {self.tid} | {due(4)}'))
        await m.done(message(f'/done {self.tid} | No reopening'))
        with Session() as s:
            self.assertEqual(s.get(Task, self.tid).status, 'cancelled')
            self.assertEqual(s.get(Task, self.tid).reschedules, 0)

    async def test_deadline_validation_in_commands_and_confirmation(self):
        for text in (f'/move {self.tid} | {due(-1)}', f'/task Test | {due(-1)} | P1 | Result'):
            handler = m.move if text.startswith('/move') else m.task
            await handler(message(text))
        with Session() as s:
            old = s.get(Task, self.tid).due_at.isoformat()
        token = self.proposal('move', old=old, new=due(-1))
        await m.confirm_change(callback(token))
        with Session() as s:
            self.assertEqual(s.get(Task, self.tid).due_at.isoformat(), old)
            self.assertEqual(s.query(Task).count(), 1)

    async def test_batch_is_atomic_and_superseded(self):
        with Session.begin() as s:
            second = Task(title='Second', status='planned', project_id=self.pid)
            s.add(second); s.flush(); second_id = second.id
            first = s.get(Task, self.tid)
            payload = {'changes': [dict(id=self.tid, old=first.due_at.isoformat(), new=due(3), status='planned'),
                                   dict(id=second_id, old=None, new=due(-1), status='planned')]}
            old = save_change(s, 'batch_move', 'all', payload)
            token = save_change(s, 'batch_move', 'all', payload)
        await m.confirm_change(callback(old))
        await m.confirm_change(callback(token))
        with Session() as s:
            self.assertEqual(s.get(Task, self.tid).reschedules, 0)
            self.assertIsNone(s.get(Task, second_id).due_at)

    async def test_next_step_single_use_and_supersession(self):
        old = self.proposal('next_step', title='Old', criteria='Result', due=due(), project_id=self.pid)
        new = self.proposal('next_step', title='New', criteria='Result', due=due(), project_id=self.pid)
        await m.confirm_change(callback(old))
        await m.confirm_change(callback(new))
        await m.confirm_change(callback(new))
        with Session() as s:
            self.assertEqual(s.query(Task).count(), 2)
            self.assertEqual(s.query(Task).filter_by(title='New').count(), 1)

    async def test_calendar_does_not_block_event_loop(self):
        import time
        def slow_calendar(*args):
            time.sleep(0.1)
            return [], None
        with patch.object(m, 'events_for_day', slow_calendar):
            report = asyncio.create_task(m.daily_text())
            await asyncio.sleep(0.02)
            self.assertFalse(report.done())
            self.assertIn('Task', await report)

    async def test_duplicate_comparisons_are_project_scoped(self):
        mock = AsyncMock(side_effect=[[1], [2]])
        proposed = [{'project_id': 1}, {'project_id': None}]
        existing = [{'id': 1, 'project_id': 1}, {'id': 2, 'project_id': None}, {'id': 3, 'project_id': 2}]
        with patch('app.ai._find_project_duplicates', mock):
            self.assertEqual(await find_semantic_duplicates(proposed, existing), [1, 2])
        for call in mock.call_args_list:
            new, old = call.args
            self.assertEqual({t['project_id'] for t in new + old}, {new[0]['project_id']})

    async def test_dependency_cycle_and_cross_project_rejected(self):
        with Session.begin() as s:
            other = Project(title='Other', status='active'); s.add(other); s.flush()
            tasks = [Task(title='B', project_id=self.pid), Task(title='C', project_id=other.id)]
            s.add_all(tasks); s.flush(); b, c = [t.id for t in tasks]
            add_dependency(s, self.tid, b)
            add_dependency(s, self.tid, b)
        for before, after in ((b, self.tid), (self.tid, c), (self.tid, self.tid)):
            with self.assertRaises(ValueError), Session.begin() as s:
                add_dependency(s, before, after)
        with Session() as s:
            self.assertEqual(s.query(TaskDependency).count(), 1)

    async def test_all_terminal_transitions_rejected(self):
        for old in ('done', 'cancelled'):
            for new in ('planned', 'in_progress', 'review', 'done', 'cancelled'):
                with self.assertRaises(ValueError):
                    require_transition(old, new)

    async def test_task_creation_is_atomic_single_use_and_validated(self):
        item = dict(title='Draft task', criteria='Result', priority='P4', due=due(),
                    project_id=self.pid, estimate_minutes=30,
                    effort_breakdown=[dict(name='Work', minutes=30)])
        async def confirm(items):
            token = uuid.uuid4().hex
            with Session.begin() as s:
                s.add(Setting(key=f'pending_task_draft:{token}', value=json.dumps(items)))
            c = callback(token)
            c.data = f'draft_tasks:approve:{token}'
            await m.confirm_draft(c)
            return c
        invalid = dict(item, project_id=999999)
        await confirm([item, invalid])
        with Session() as s:
            self.assertEqual(s.query(Task).count(), 1)
        await confirm([dict(item, due=due(-1))])
        await confirm([dict(item, estimate_minutes=31)])
        c = await confirm([item])
        await m.confirm_draft(c)
        with Session() as s:
            self.assertEqual(s.query(Task).filter_by(title='Draft task').count(), 1)

    async def test_legacy_buttons_and_malformed_buttons_never_change_data(self):
        for data in ('priority_task:approve:1', 'batch_move:approve', 'next_step:approve:1'):
            c = callback('unused'); c.data = data
            await m.reject_legacy_confirmation(c)
            c.answer.assert_awaited_once()
        for data in ('change:approve:1', 'change:unknown:' + uuid.uuid4().hex, 'change:approve'):
            c = callback('unused'); c.data = data
            await m.confirm_change(c)
        with Session() as s:
            self.assertEqual(s.get(Task, self.tid).priority, 'P2')

    async def test_empty_ai_risks_are_valid(self):
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(dict(dependencies=[], risks=[], next_actions=[]))))])
        api = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=response))))
        request = message('/risks')
        with patch.object(m, 'AsyncOpenAI', return_value=api):
            await m.risks(request)
        self.assertIn('Календарных конфликтов', request.answer.call_args.args[0])

    async def test_saved_dependencies_detect_conflicts_without_model_edges(self):
        with Session.begin() as s:
            successor = Task(title='Successor', project_id=self.pid, status='planned', due_at=future_due(due(1)))
            s.add(successor); s.flush()
            add_dependency(s, self.tid, successor.id)
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(dict(dependencies=[], risks=[], next_actions=[]))))])
        api = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=response))))
        request = message('/risks')
        with patch.object(m, 'AsyncOpenAI', return_value=api):
            await m.risks(request)
        self.assertIn('Обнаружены конфликты', request.answer.call_args.args[0])
        self.assertIn('утверждены владельцем', request.answer.call_args.args[0])

    async def test_notifications_deduplicate_and_retry_failed_delivery(self):
        send = AsyncMock(side_effect=[RuntimeError('Test failure'), None])
        with patch.object(m, 'bot', SimpleNamespace(send_message=send)):
            self.assertFalse(await m.send_once('test-notification', 'Test'))
            self.assertTrue(await m.send_once('test-notification', 'Test'))
            self.assertFalse(await m.send_once('test-notification', 'Test'))
        self.assertEqual(send.await_count, 2)

    def legacy_case(self, incompatible=False):
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import create_engine
        from pathlib import Path
        name = 'pm_test_legacy_' + uuid.uuid4().hex
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        legacy_url = make_url(ADMIN).set(drivername='postgresql+psycopg', database=name)
        legacy = create_engine(legacy_url)
        try:
            with patch.dict(os.environ, DATABASE_URL=legacy_url.render_as_string(hide_password=False)):
                cfg = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
                command.upgrade(cfg, '0002')
                with legacy.begin() as c:
                    c.exec_driver_sql("INSERT INTO projects(title,status) VALUES ('Legacy','active')")
                    c.exec_driver_sql("INSERT INTO tasks(title,project_id) VALUES ('Preserved',1)")
                    if incompatible:
                        c.exec_driver_sql('ALTER TABLE tasks ADD COLUMN unknown_legacy_field TEXT')
                if incompatible:
                    with self.assertRaisesRegex(RuntimeError, 'incompatible'):
                        upgrade()
                    with legacy.connect() as c:
                        self.assertEqual(c.exec_driver_sql('SELECT version_num FROM alembic_version').scalar(), '0002')
                else:
                    for revision in ('0003', '0004', '0005'):
                        command.upgrade(cfg, revision)
                        with legacy.connect() as c:
                            self.assertEqual(c.exec_driver_sql('SELECT version_num FROM alembic_version').scalar(), revision)
                            self.assertEqual(c.exec_driver_sql('SELECT title FROM tasks WHERE id=1').scalar(), 'Preserved')
                    upgrade()
                with legacy.connect() as c:
                    self.assertEqual(c.exec_driver_sql('SELECT title FROM tasks WHERE id=1').scalar(), 'Preserved')
                    if not incompatible:
                        self.assertIsNone(c.exec_driver_sql('SELECT parent_task_id FROM tasks WHERE id=1').scalar())
        finally:
            legacy.dispose()
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))

    async def test_migrations_adopt_legacy_and_preserve_rows(self):
        self.legacy_case()

    async def test_migrations_refuse_incompatible_legacy_schema(self):
        self.legacy_case(incompatible=True)


if __name__ == '__main__':
    unittest.main()
