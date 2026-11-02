import asyncio
import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock
import psycopg
from sqlalchemy import text, func
from sqlalchemy.exc import IntegrityError
import test_workflow as fixture
from app.db import (Session, engine, Task, Project, Setting, TaskDependency,
                    TaskTimeEntry, TaskEstimateRevision, EventLog)
from app import main as m
from app.schemas import task_data, classify_plan, deadline, TZ
from app.services.tasks import create_task, update_task, record_time
from app.services.projects import update_project
from app.services.proposals import prepare, apply
from app.services.planning import task_summary, project_summary, forecast, calibration
from app.dependencies import add_dependency
from app.natural import handle


def item(title='New work', minutes=10, children=None, **extra):
    result = dict(title=title, description='', criteria='Result accepted', priority='P2', due=None,
                complexity='simple', task_type='list', project_id=None, workstream='general',
                effort_breakdown=[dict(name='Prepare result', minutes=minutes)], children=children or [])
    result.update(extra)
    return result


class V2Tests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.WorkflowTests.setUp

    def proposal(self, op, target, data):
        with Session.begin() as s:
            return prepare(s, op, target, data, 1)

    async def test_hierarchy_progress_and_no_double_counting(self):
        with Session.begin() as s:
            root = create_task(s, item(minutes=300, children=[item('A', 10), item('B', 20)]), project_id=self.pid)
            rid = root.id
        with Session() as s:
            root = s.get(Task, rid)
            info = task_summary(s, root)
            self.assertEqual((info['estimate'], info['count']), (30, 2))
            self.assertEqual(s.query(Task).filter_by(parent_task_id=rid).count(), 2)
        request = fixture.message('/hierarchy')
        await m.hierarchy(request)
        self.assertIn('↳', '\n'.join(call.args[0] for call in request.answer.call_args_list))

    async def test_depth_closed_parent_project_and_database_constraints(self):
        with Session.begin() as s:
            root = create_task(s, item(children=[item('Child')]))
            child = s.query(Task).filter_by(parent_task_id=root.id).one()
            root_id, child_id = root.id, child.id
        with self.assertRaises(ValueError), Session.begin() as s:
            create_task(s, item('Third level'), parent_id=child_id)
        with self.assertRaises(IntegrityError), engine.begin() as c:
            c.execute(text('INSERT INTO tasks(title,parent_task_id) VALUES (:title,:parent)'),
                      dict(title='Raw third level', parent=child_id))
        with self.assertRaises(ValueError), Session.begin() as s:
            create_task(s, item('Wrong project'), parent_id=root_id, project_id=self.pid)
        with self.assertRaises(ValueError), Session.begin() as s:
            update_task(s, root_id, {'status': 'cancelled'})

    async def test_project_creation_and_tasks_are_atomic_and_preview_is_readonly(self):
        data = dict(project=dict(title='Strategy', goal='Results', success_criteria='Accepted'),
                    items=[item('Research'), item('Delivery')])
        token, payload = self.proposal('create_plan', None, data)
        with Session() as s:
            self.assertEqual(s.query(Project).count(), 1)
            self.assertEqual(s.query(Task).count(), 1)
            self.assertEqual(s.query(TaskEstimateRevision).count(), 0)
        await m.confirm_change(fixture.callback(token))
        await m.confirm_change(fixture.callback(token))
        with Session() as s:
            project = s.query(Project).filter_by(title='Strategy').one()
            self.assertEqual(project.status, 'planned')
            self.assertEqual(s.query(Task).filter_by(project_id=project.id).count(), 2)
        with self.assertRaises(ValueError), Session.begin() as s:
            apply(s, dict(payload, data=dict(data, items=[item('First'), item('Invalid', due='2000-01-01 12:00')])), 1, uuid.uuid4().hex)
        with Session() as s:
            self.assertEqual(s.query(Project).count(), 2)

    async def test_plan_scale_and_estimates(self):
        simple = task_data(item())
        self.assertEqual(classify_plan([simple]), 'task')
        complex_work = task_data(item(children=[item('Outline'), item('Content')]))
        self.assertEqual(classify_plan([complex_work]), 'task_with_subtasks')
        plan = [task_data(dict(item(str(i)), workstream=str(i % 2))) for i in range(3)]
        self.assertEqual(classify_plan(plan), 'project')
        self.assertEqual(classify_plan(plan, ambiguous=True), 'clarify')
        self.assertEqual(classify_plan(plan[:2]), 'clarify')
        with self.assertRaises(ValueError):
            task_data(dict(item(), effort_breakdown=[dict(name='Work', minutes=True)]))

    async def test_manual_deadline_recalculation_and_completion(self):
        old_due = fixture.due(2)
        with Session.begin() as s:
            update_project(s, self.pid, {'manual_due': fixture.due(3)})
            update_task(s, self.tid, {'due': fixture.due(5)})
        with Session() as s:
            project = s.get(Project, self.pid)
            self.assertEqual(project.planned_due_at, project.manual_due_at)
            self.assertGreater(project.auto_due_at, project.manual_due_at)
            self.assertTrue(any('позже ручного' in risk for risk in forecast(s)))
        with Session.begin() as s:
            update_task(s, self.tid, {'status': 'cancelled'})
        with Session() as s:
            self.assertIsNone(s.get(Project, self.pid).auto_due_at)
            self.assertIsNotNone(s.get(Project, self.pid).manual_due_at)

    async def test_project_status_guards_and_paused_execution(self):
        token, _ = self.proposal('project_update', self.pid, {'status': 'paused'})
        await m.confirm_change(fixture.callback(token))
        await m.begin(fixture.message(f'/begin {self.tid}'))
        with Session() as s:
            self.assertEqual(s.get(Task, self.tid).status, 'planned')
            self.assertEqual(s.get(Project, self.pid).status, 'paused')
        with self.assertRaises(ValueError), Session.begin() as s:
            update_project(s, self.pid, {'status': 'completed'})
        with Session.begin() as s:
            update_project(s, self.pid, {'status': 'active'})
            update_task(s, self.tid, {'status': 'cancelled'})
            update_project(s, self.pid, {'status': 'completed'})
        await m.approve(fixture.message(f'/approve {self.pid}'))
        with Session() as s:
            self.assertEqual(s.get(Project, self.pid).status, 'completed')

    async def test_date_relative_preserves_moscow_time(self):
        now = datetime(2030, 1, 2, 23, 50, tzinfo=TZ)
        previous = datetime(2030, 1, 4, 13, 20, tzinfo=TZ)
        result = deadline('завтра', previous=previous, now=now).astimezone(TZ)
        self.assertEqual((result.day, result.hour, result.minute), (3, 13, 20))
        self.assertEqual(deadline('завтра', now=now).astimezone(TZ).hour, 18)

    async def test_revision_aba_and_wrong_owner_rejected(self):
        token, _ = self.proposal('task_update', self.tid, {'title': 'Approved title'})
        with Session.begin() as s:
            update_task(s, self.tid, {'title': 'Temporary'})
            update_task(s, self.tid, {'title': 'Task'})
        await m.confirm_change(fixture.callback(token))
        with Session() as s:
            self.assertEqual(s.get(Task, self.tid).title, 'Task')
        token, payload = self.proposal('task_update', self.tid, {'title': 'New'})
        with self.assertRaises(ValueError), Session.begin() as s:
            apply(s, payload, 2, token)
        await m.confirm_change(fixture.callback(token, 'cancel'))
        await m.confirm_change(fixture.callback(token))
        with Session() as s:
            self.assertEqual(s.get(Task, self.tid).title, 'Task')

    async def test_actual_time_idempotency_and_insufficient_calibration(self):
        token, _ = self.proposal('time', self.tid, {'minutes': 15})
        await m.confirm_change(fixture.callback(token))
        await m.confirm_change(fixture.callback(token))
        with Session() as s:
            self.assertEqual(task_summary(s, s.get(Task, self.tid))['actual'], 15)
            self.assertEqual(s.query(TaskTimeEntry).count(), 1)
            self.assertEqual(calibration(s, 'list', 'simple'), dict(samples=0, sufficient=False, factor=None))

    async def test_dependency_conflicts_not_deadline_coincidence(self):
        with Session.begin() as s:
            other = create_task(s, dict(item('Other'), due=fixture.due(2)), project_id=self.pid)
            oid = other.id
            add_dependency(s, self.tid, oid)
        with Session() as s:
            self.assertFalse(any('Пересекаются' in risk for risk in forecast(s)))
        with Session.begin() as s:
            update_task(s, self.tid, {'due': fixture.due(4)})
        with Session() as s:
            self.assertTrue(any('Конфликт зависимости' in risk for risk in forecast(s)))

    async def test_dependency_cycles_include_aggregate_nodes(self):
        with Session.begin() as s:
            first = create_task(s, item('First', children=[item('Child A')]), project_id=self.pid)
            second = create_task(s, item('Second', children=[item('Child B')]), project_id=self.pid)
            a = s.query(Task).filter_by(parent_task_id=first.id).one()
            b = s.query(Task).filter_by(parent_task_id=second.id).one()
            ids = first.id, second.id, a.id, b.id
            add_dependency(s, first.id, second.id)
        for before, after in ((ids[3], ids[2]), (ids[0], ids[2])):
            with self.assertRaises(ValueError), Session.begin() as s:
                add_dependency(s, before, after)

    async def test_natural_project_status_and_name_ambiguity(self):
        request = fixture.message('Приостанови проект Test')
        with patch('app.natural.propose_request', AsyncMock(return_value=dict(operation='project_update', target='Test', changes={'status': 'paused'}))):
            await m.chat(request)
        with Session() as s:
            self.assertEqual(s.get(Project, self.pid).status, 'active')
            token = s.get(Setting, f'latest_change:domain:project_update:{self.pid}').value
        await m.confirm_change(fixture.callback(token))
        with Session() as s:
            self.assertEqual(s.get(Project, self.pid).status, 'paused')
        with Session.begin() as s:
            s.add(Task(title='Task', status='planned'))
        request = fixture.message('Переименуй задачу Task в Renamed')
        intent = dict(operation='task_update', target='Task', changes={'title': 'Renamed'})
        with patch('app.natural.propose_request', AsyncMock(return_value=intent)):
            await m.chat(request)
        self.assertIn('несколько вариантов', request.answer.call_args.args[0])

    async def test_natural_subtasks_analysis_then_confirm(self):
        intent = dict(operation='subtasks', target='Task', items=[item('Outline'), item('Content'), item('Review')], analysis_only=True)
        request = fixture.message('Предложи структуру задачи Task')
        with patch('app.natural.propose_request', AsyncMock(return_value=intent)):
            await m.chat(request)
        with Session() as s:
            self.assertEqual(s.query(Setting).filter(Setting.key.like('pending_change:%')).count(), 0)
        request = fixture.message('Разбей задачу Task на три подзадачи')
        with patch('app.natural.propose_request', AsyncMock(return_value=dict(intent, analysis_only=False))):
            await m.chat(request)
        with Session() as s:
            token = s.get(Setting, f'latest_change:domain:subtasks:{self.tid}').value
            self.assertEqual(s.query(Task).count(), 1)
        await m.confirm_change(fixture.callback(token))
        with Session() as s:
            self.assertEqual(s.query(Task).filter_by(parent_task_id=self.tid).count(), 3)

    async def test_natural_creation_no_extra_stages_and_relative_move(self):
        request = fixture.message('Составь список пяти способов хранения фильтров V60')
        intent = dict(operation='create_plan', items=[item('Five storage ideas')], ambiguous=False)
        with patch('app.natural.propose_request', AsyncMock(return_value=intent)), patch('app.ai.find_semantic_duplicates', AsyncMock(return_value=[])):
            await m.chat(request)
        with Session() as s:
            token = s.get(Setting, 'latest_change:domain:create_plan:new').value
        await m.confirm_change(fixture.callback(token))
        with Session() as s:
            task = s.query(Task).filter_by(title='Five storage ideas').one()
            self.assertEqual(task.estimate_minutes, 10)
            self.assertIsNone(task.parent_task_id)
            self.assertIsNone(task.due_at)
        request = fixture.message(f'Перенеси задачу №{self.tid} на завтра')
        await m.chat(request)
        with Session() as s:
            self.assertIsNotNone(s.get(Setting, f'latest_change:domain:task_update:{self.tid}'))

    async def test_natural_priority_without_id_and_field_edits(self):
        request = fixture.message('Повысь приоритет задачи Task')
        with patch('app.natural.propose_request', AsyncMock(return_value=dict(operation='task_update', target='Task', changes={'priority': 'raise'}))):
            await m.chat(request)
        with Session() as s:
            token = s.get(Setting, f'latest_change:domain:task_update:{self.tid}').value
        await m.confirm_change(fixture.callback(token))
        token, _ = self.proposal('task_update', self.tid, dict(title='Updated', description='New description',
            criteria='New criterion', effort_breakdown=[dict(name='Single stage', minutes=25)]))
        await m.confirm_change(fixture.callback(token))
        with Session() as s:
            task = s.get(Task, self.tid)
            self.assertEqual((task.priority, task.title, task.estimate_minutes), ('P1', 'Updated', 25))
            self.assertEqual(task.criteria, 'New criterion')
            self.assertEqual(s.query(TaskEstimateRevision).filter_by(task_id=self.tid).count(), 1)

    async def test_large_plan_routes_to_project_and_bad_simple_estimate_clarifies(self):
        items = [dict(item('Outcome ' + str(i), 300, complexity='complex'), workstream='direction' + str(i % 2)) for i in range(3)]
        request = fixture.message('Разработай комплексную стратегию продвижения на три месяца')
        intent = dict(operation='create_plan', items=items,
                      project=dict(title='Promotion', goal='Launch', success_criteria='Strategy accepted'))
        with patch('app.natural.propose_request', AsyncMock(return_value=intent)):
            await m.chat(request)
        with Session() as s:
            token = s.get(Setting, 'latest_change:domain:create_plan:new').value
        await m.confirm_change(fixture.callback(token))
        with Session() as s:
            self.assertEqual(s.query(Project).filter_by(title='Promotion').count(), 1)
        request = fixture.message('Составь краткий список')
        with patch('app.natural.propose_request', AsyncMock(return_value=dict(operation='create_plan', items=[item('Short list', 120)]))):
            await m.chat(request)
        self.assertIn('Нужно исследование', request.answer.call_args.args[0])

    async def test_concurrent_new_drafts_and_hierarchy_inserts(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.proposal, 'task_update', self.tid, {'title': title}) for title in ('One', 'Two')]
            results = [await asyncio.to_thread(f.result, 15) for f in futures]
        with Session() as s:
            self.assertEqual(s.query(Setting).filter(Setting.key.like('pending_change:%')).count(), 1)
        with Session.begin() as s:
            root = create_task(s, item('Parent'))
            rid = root.id
        def child():
            with Session.begin() as s:
                return create_task(s, item('Concurrent child'), parent_id=rid).id
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(child) for _ in range(2)]
            outcomes = []
            for future in futures:
                try:
                    outcomes.append(await asyncio.to_thread(future.result, 15))
                except ValueError:
                    outcomes.append(None)
        self.assertEqual(sum(value is not None for value in outcomes), 1)

    async def test_schedule_overlap_requires_explicit_intervals(self):
        with Session.begin() as s:
            other = create_task(s, item('Scheduled'), project_id=self.pid)
            date = fixture.due(3).split()[0]
            update_task(s, self.tid, {'scheduled_start': date + ' 10:00', 'scheduled_end': date + ' 12:00'})
            update_task(s, other.id, {'scheduled_start': date + ' 11:00', 'scheduled_end': date + ' 13:00'})
        with Session() as s:
            self.assertTrue(any('Пересекаются явные' in r for r in forecast(s)))

    async def test_subtask_cancellation_and_calibration_history(self):
        with Session.begin() as s:
            parent = create_task(s, item('Root', children=[item('First'), item('Second')]))
            child = s.query(Task).filter_by(parent_task_id=parent.id).order_by(Task.id).first()
            update_task(s, child.id, {'status': 'cancelled'})
            self.assertEqual(task_summary(s, parent)['count'], 1)
            for i in range(10):
                task = create_task(s, item('Calibration ' + str(i)))
                update_task(s, task.id, {'status': 'in_progress'})
                record_time(s, task.id, 20, uuid.uuid4().hex)
                # Historical completed fixtures; Telegram acceptance is covered separately.
                task.status = 'done'
            s.flush()
            stat = calibration(s, 'list', 'simple')
            self.assertEqual(stat, dict(samples=10, sufficient=True, factor=2.0))
            self.assertEqual(task_summary(s, task)['deviation_minutes'], 10)

    async def test_relative_draft_is_frozen_and_survives_new_session(self):
        token, payload = self.proposal('task_update', self.tid, {'due': 'завтра'})
        self.assertRegex(payload['data']['due'], r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$')
        with Session() as s:
            saved = json.loads(s.get(Setting, f'pending_change:{token}').value)
            self.assertEqual(saved['payload']['data']['due'], payload['data']['due'])
        await m.confirm_change(fixture.callback(token))
        with Session() as s:
            self.assertEqual(s.get(Task, self.tid).due_at.astimezone(TZ).strftime('%Y-%m-%d %H:%M'), payload['data']['due'])


if __name__ == '__main__':
    unittest.main()
