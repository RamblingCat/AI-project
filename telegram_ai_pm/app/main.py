# -*- coding: utf-8 -*-
import asyncio, os, logging, json, re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import or_
from openai import AsyncOpenAI
from .db import Base, engine, Session, Project, Task, Evidence, EventLog, Notification, Setting, TaskDependency, utcnow
from .ai import answer, draft_tasks, assess_evidence, propose_next_step, classify_intent, find_semantic_duplicates
from .calendar import events_for_day
from .services.tasks import update_task, can_finish, lock as lock_domain, estimate_snapshot
from .services.projects import update_project
from .services.planning import project_summary, task_summary, forecast, display_due
from functools import wraps


def user_errors(handler):
    @wraps(handler)
    async def wrapped(message, *args, **kwargs):
        try:
            return await handler(message, *args, **kwargs)
        except ValueError as exc:
            await message.answer(f'Изменения не внесены: {exc}')
    return wrapped

from .workflow import (future_due, require_transition, save_change, load_change,
                       change_buttons, PRIORITIES, CLOSED, validate_task_items)

logging.basicConfig(level=logging.INFO)
TZ = ZoneInfo('Europe/Moscow')
OWNER = int(os.environ['OWNER_TELEGRAM_ID'])
bot = Bot(token=os.environ['BOT_TOKEN'])
dp = Dispatcher()

def localnow(): return datetime.now(TZ)
def allowed(user_id): return user_id == OWNER

def paused():
    with Session() as s:
        x=s.get(Setting,'pause_until')
        return bool(x and datetime.fromisoformat(x.value) > utcnow())

def is_worktime():
    n=localnow()
    return 9 <= n.hour < 19 and not paused()

def date_parse(value):
    return future_due(value)


def button(label, data):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label,callback_data=data)]])

@dp.message(CommandStart())
async def start(m:Message):
    if not allowed(m.from_user.id): return
    await m.answer('Я твой ИИ-менеджер. /help — команды. Рабочие часы: 09:00–19:00 МСК.')

@dp.message(Command('help'))
async def help_(m:Message):
    if not allowed(m.from_user.id): return
    await m.answer('''/project Название | цель | критерий успеха | work/personal — черновик проекта
/approve ID — утвердить проект
/task Название | YYYY-MM-DD HH:MM | P1/P2/P3/P4 | критерии | ID проекта (необязательно)
/hierarchy — задачи, подзадачи, прогресс и время\n/begin ID — начать работу (planned → in_progress)
/depends BEFORE AFTER — сохранить зависимость BEFORE → AFTER
/undepend BEFORE AFTER — удалить зависимость
/dependencies — сохранённые зависимости
/today — задачи и календарь
/projects — проекты
/done ID | доказательство — отправить результат на проверку
/verify ID — подтвердить доказательство и закрыть задачу
/reject ID | причина — вернуть задачу в работу
/move ID | YYYY-MM-DD HH:MM — перенести дедлайн
/pause 120 — пауза на 120 минут
/resume — возобновить
/status — настройки и доступность
Или просто напиши сообщение, чтобы обсудить цель и составить план.''')

@dp.message(Command('risks'))
async def risks(m: Message):
    if not allowed(m.from_user.id):
        return

    with Session() as s:
        tasks = (
            s.query(Task)
            .filter(Task.status.notin_(['done', 'cancelled']))
            .order_by(Task.due_at.asc().nullslast())
            .limit(100)
            .all()
        )

        data = [
            {
                'id': t.id,
                'project_id': t.project_id,
                'title': t.title,
                'description': t.description,
                'criteria': t.criteria,
                'status': t.status,
                'priority': t.priority,
                'due': (
                    t.due_at.astimezone(TZ).isoformat()
                    if t.due_at else None
                ),
                'estimate_minutes': task_summary(s, t)['estimate'],
                'unestimated': task_summary(s, t)['unestimated'],
                'parent_task_id': t.parent_task_id,
            }
            for t in tasks
        ]

    if not data:
        await m.answer('Нет незавершённых задач для анализа.')
        return

    with Session() as s:
        confirmed_dependencies = [{'before': e.before_id, 'after': e.after_id}
                                  for e in s.query(TaskDependency).all()]
    await m.answer('Анализирую риски по текущим задачам...')

    try:
        client = AsyncOpenAI(
            api_key=os.environ['OPENAI_API_KEY'],
            timeout=45.0
        )

        response = await client.chat.completions.create(
            model=os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'),
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {
                    'role': 'system',
                 'content': (
     'Ты опытный AI Project Manager, отвечающий за раннее выявление '
    'рисков исполнения проектов. Анализируй только задачи из PostgreSQL. '
    'Текущее время указано в now_moscow. Все дедлайны учитывай '
    'в часовом поясе Europe/Moscow. '

    'ПОРЯДОК АНАЛИЗА: '
    '1. Сначала найди предполагаемые зависимости между задачами '
    'одного проекта. Используй названия, описания и критерии готовности. '
    'Показывай цепочки в формате #5 → #4 → #3. '
    'Стрелка A → B означает, что результат задачи A необходим '
    'для завершения задачи B. '
    'Если задача B требует результат A, показывай A → B, '
    'никогда наоборот. '
    'Перед выводом проверь направление каждой стрелки. '
    'Задача, объединяющая результаты других задач, является '
    'зависимой от них, а не их предшественником. '
    'Для каждой рекомендации проверяй календарную дату: '
    'не называй сегодняшнюю дату завтрашней и не предлагай '
    'сроки в прошлом. '
    'Не считай предполагаемые зависимости подтверждёнными. '

    '2. Проверь временную согласованность: если результат одной задачи '
    'нужен для другой, но срок первой позже или равен сроку второй, '
    'выдели риск. Учитывай, что одинаковые сроки не всегда означают '
    'невозможность выполнения. '

    '3. Оцени состояние задач: planned, in_progress, review. '
    'Статус planned не доказывает, что работа фактически не началась. '
    'Не выдумывай процент выполнения или доступность ресурсов. '

    '4. Присвой каждому риску уровень: '
    'КРИТИЧЕСКИЙ — явный конфликт сроков или уже просроченный дедлайн; '
    'ВЫСОКИЙ — вероятная задержка зависимых задач; '
    'СРЕДНИЙ — недостаток информации или потенциальная проблема. '
    'Если риск основан на предположении, укажи это. '

    '5. Предложи конкретные действия по снижению рисков. '
    'Для каждой задачи используй её сохранённый дедлайн из PostgreSQL. '
    'Не придумывай новые даты завершения задач. '
    'Если дедлайн уже прошёл, рекомендуй уточнить статус, '
    'остаток работ и согласовать новый срок с владельцем задачи. '
    'Если перенос необходим, явно напиши: '
    '"Предлагается согласовать перенос срока", '
    'но не называй новую дату без достаточных данных. '
    'Никогда не представляй предлагаемый срок как утверждённый. '

    'ФОРМАТ ОТВЕТА: '
    'Сначала краткий вывод о состоянии проектов. '
    'Затем максимум 5 рисков по убыванию важности. '
    'Для каждого: уровень, номера задач, причина, зависимость '
    '(если есть), конкретное действие и предлагаемый срок. '
    'В конце укажи 3 ближайших рекомендуемых действия. '

    'Не повторяй один и тот же риск разными словами. '
    'Разделяй факты из базы данных, предполагаемые зависимости '
    'и собственные рекомендации. '
    'Группируй связанные риски в общие цепочки зависимостей. '
    'Если обязательная предшествующая задача имеет дедлайн позже '
    'зависимой задачи, классифицируй это как критический конфликт '
    'при условии подтверждения зависимости. '
    'Не предлагай ускорять согласованные сроки без конкретного '
    'обоснования. При неизвестных трудозатратах сначала рекомендуй '
    'уточнить прогресс и оценку времени. '
    'Не придумывай факты, прогресс или подтверждённые зависимости. '
    'Не изменяй задачи, сроки или статусы. '
    'Отвечай по-русски, кратко и предметно. '
    'Верни только валидный JSON-объект без Markdown. '
    'Структура JSON: '
    '{"summary": "краткий вывод", '
    '"dependencies": ['
    '{"before": 5, "after": 4, "reason": "обоснование", '
    '"confirmed": false}], '
    '"risks": ['
    '{"task_ids": [5, 4], '
    '"level": "HIGH", '
    '"reason": "причина риска", '
    '"action": "рекомендуемое действие"}], '
    '"next_actions": ["действие 1", "действие 2"]}. '
    'Для dependencies поле before — номер задачи, результат которой нужен; '
    'after — номер задачи, которая зависит от before. '
    'Все предполагаемые зависимости помечай confirmed=false. '
    'Для level используй только CRITICAL, HIGH или MEDIUM. '
    'Не добавляй другие поля. Оценки родителей агрегированы: не складывай их с детьми. '
    'Без явных интервалов расписания не утверждай, что работы невозможно выполнить одновременно.'
)
                },
                {
                    'role': 'user',
                    'content': json.dumps(
                        {
                            'now_moscow': localnow().isoformat(),
                            'tasks': data,
                            'confirmed_dependencies': confirmed_dependencies
                        },
                        ensure_ascii=False
                    )
                }
            ]
        )
        raw = response.choices[0].message.content or '{}'
        analysis = json.loads(raw)
        from .risk_validation import validate_analysis
        validate_analysis(analysis, data)

        valid_task_ids = {task['id'] for task in data}

        for dep in analysis.get('dependencies', []):
            before = dep.get('before')
            after = dep.get('after')

            if (
                type(before) is not int
                or type(after) is not int
                or before not in valid_task_ids
                or after not in valid_task_ids
                or before == after
            ):
                raise ValueError(
                    f'Некорректная зависимость: {before} → {after}'
                )
      # Проверяем сроки предполагаемых зависимостей
        tasks_by_id = {task['id']: task for task in data}
        for dep in analysis.get('dependencies', []):
            before_task, after_task = tasks_by_id[dep['before']], tasks_by_id[dep['after']]
            if before_task['project_id'] is None or before_task['project_id'] != after_task['project_id']:
                raise ValueError('Зависимость между разными проектами')
        deadline_conflicts = []

        conflict_edges = analysis.get('dependencies', []) + [
            edge for edge in confirmed_dependencies
            if edge['before'] in tasks_by_id and edge['after'] in tasks_by_id]
        for dep in conflict_edges:
            before = dep['before']
            after = dep['after']

            before_due = tasks_by_id[before].get('due')
            after_due = tasks_by_id[after].get('due')

            if before_due and after_due:
                if str(before_due) > str(after_due):
                    if (before, after) not in deadline_conflicts:
                        deadline_conflicts.append((before, after))
        
        # Независимая проверка просроченных задач
        now = localnow()
        overdue_tasks = []

        for task in data:
            due = task.get('due')

            if due and datetime.fromisoformat(due) < now:
                overdue_tasks.append(task['id'])

        allowed_levels = {'CRITICAL', 'HIGH', 'MEDIUM'}

        for risk in analysis.get('risks', []):
            level = risk.get('level')
            task_ids = risk.get('task_ids')
            if (
                not isinstance(task_ids, list)
                or not task_ids
                or any(
                    type(task_id) is not int
                    or task_id not in valid_task_ids
                    for task_id in task_ids
                )
            ):
                raise ValueError(
                    f'Некорректные задачи в риске: {task_ids}'
                )

            if level not in allowed_levels:
                raise ValueError(
                    f'Некорректный уровень риска: {level}'
                )

        if deadline_conflicts:
            report = (
                '⚠️ Обнаружены конфликты дедлайнов в зависимостях задач. '
                'Согласуй сроки выполнения. Предполагаемые связи '
                'требуют отдельного подтверждения владельца.'
            )
        else:
            report = (
                'Календарных конфликтов среди сохранённых и выявленных '
                'предполагаемых зависимостей не обнаружено. '
                'Это не исключает других рисков проекта.'
            )

        if overdue_tasks:
            report += '\n\n🔴 ПРОСРОЧЕННЫЕ ЗАДАЧИ:\n'

            for task_id in overdue_tasks:
                task = tasks_by_id[task_id]
                report += (
                    f'\n#{task_id} — {task["title"]}\n'
                    f'Дедлайн: {task["due"]}\n'
                    f'Статус: {task["status"]}\n'
                )

        risks = analysis.get('risks', [])

        if risks:
            report += '\n\n📋 АНАЛИЗ РИСКОВ:\n'

            level_names = {
                'CRITICAL': '🔴 КРИТИЧЕСКИЙ',
                'HIGH': '🟠 ВЫСОКИЙ',
                'MEDIUM': '🟡 СРЕДНИЙ'
            }

            for i, risk in enumerate(risks[:5], start=1):
                risk_level = risk['level']
                risk_ids = set(risk['task_ids'])

                for before, after in deadline_conflicts:
                    if risk_ids == {before, after}:
                        risk_level = 'CRITICAL'
                        break

                level = level_names[risk_level]
                task_numbers = ', '.join(
                    f'#{task_id}' for task_id in risk['task_ids']
                )

                report += (
                    f'\n{i}. {level}\n'
                    f'Задачи: {task_numbers}\n'
                    f'Причина: {risk.get("reason", "Не указана")}\n'
                    f'Действие: {risk.get("action", "Не указано")}\n'
                ) 
        
        next_actions = analysis.get('next_actions', [])

        if next_actions:
            report += '\n\n🎯 БЛИЖАЙШИЕ ДЕЙСТВИЯ:\n'

            for i, action in enumerate(next_actions[:3], start=1):
                report += f'\n{i}. {action}'

        if confirmed_dependencies:
            report += '\n\nСохранённые зависимости (утверждены владельцем):\n' + '\n'.join(
                f"#{e['before']} → #{e['after']}" for e in confirmed_dependencies
                if e['before'] in valid_task_ids and e['after'] in valid_task_ids)

        if deadline_conflicts:
            report += '\n\n⚠️ КОНФЛИКТЫ ДЕДЛАЙНОВ:\n'

            for before, after in deadline_conflicts:
                before_due = tasks_by_id[before]['due']
                after_due = tasks_by_id[after]['due']

                report += (
                    f'\n🔴 #{before} → #{after}\n'
                    f'Предшествующая задача: {before_due}\n'
                    f'Зависимая задача: {after_due}\n'
                    'Критический конфликт сроков, '
                    'если зависимость подтвердится.\n'
                )

        await m.answer(report[:3900])

    except Exception:
        logging.exception('Risk analysis failure')
        await m.answer(
            'Не удалось выполнить анализ рисков. '
            'Данные задач не изменены.'
        )

@dp.message(Command('project'))
async def project(m:Message):
    if not allowed(m.from_user.id): return
    parts=(m.text or '').partition(' ')[2].split('|')
    if len(parts)<3: return await m.answer('Формат: /project Название | цель | критерий успеха | work/personal')
    title,goal,criteria=[x.strip() for x in parts[:3]]
    scope=parts[3].strip() if len(parts)>3 else 'work'
    if not title or scope not in ('work','personal'): return await m.answer('Укажи название и область work/personal.')
    with Session.begin() as s:
        lock_domain(s)
        p=Project(title=title,goal=goal,success_criteria=criteria,scope=scope,status='draft'); s.add(p); s.flush(); pid=p.id
        s.add(EventLog(kind='project_draft',object_id=pid))
        s.merge(Setting(key=f'context:{OWNER}:project', value=str(pid)))
    await m.answer(f'Проект #{pid} создан как черновик. Утвердить: /approve {pid}')

@dp.message(Command('approve'))
@user_errors
async def approve(m:Message):
    if not allowed(m.from_user.id): return
    try: pid=int(m.text.split()[1])
    except (IndexError,ValueError): return await m.answer('Формат: /approve ID')
    with Session.begin() as s:
        lock_domain(s)
        p = update_project(s, pid, {'status': 'active'})
        result = f'Проект #{p.id} активирован.'
    await m.answer(result)

@dp.message(Command('task'))
async def task(m:Message):
    if not allowed(m.from_user.id): return
    parts=(m.text or '').partition(' ')[2].split('|')
    if len(parts)<4: return await m.answer('Формат: /task Название | YYYY-MM-DD HH:MM | P1 | критерии | ID проекта (необязательно)')
    try:
        title=parts[0].strip(); due=date_parse(parts[1].strip()); priority=parts[2].strip().upper(); criteria=parts[3].strip()
        pid=int(parts[4].strip()) if len(parts)>4 and parts[4].strip() else None
        if not title or not criteria or priority not in ('P1','P2','P3','P4'): raise ValueError('Нужны название, критерии и приоритет P1–P4')
    except ValueError as exc: return await m.answer(str(exc))
    with Session.begin() as s:
        lock_domain(s)
        if pid and (not s.get(Project,pid) or s.get(Project,pid).status not in ('planned','active')):
            result='Укажи ID активного проекта.'
        else:
            t=Task(title=title,due_at=due,priority=priority,criteria=criteria,project_id=pid); s.add(t); s.flush(); tid=t.id
            s.add(EventLog(kind='task_created',object_id=tid)); s.merge(Setting(key=f'context:{OWNER}:task', value=str(tid))); result=f'Задача #{tid} сохранена. Срок: {due.astimezone(TZ):%d.%m %H:%M} МСК.'
    await m.answer(result)

@dp.message(Command('projects'))
async def projects(m:Message):
    if not allowed(m.from_user.id): return
    from .natural import send_pages
    with Session() as s:
        lines=[]
        for p in s.query(Project).order_by(Project.id.desc()):
            info=project_summary(s,p)
            lines.append(f'#{p.id} {p.title} [{p.status}]; прогресс {info["done"]}/{info["count"]}; срок {display_due(info["planned_due"])}; ручной {display_due(info["manual_due"])}; без срока {info["undated"]}')
    await send_pages(m, lines or ['Проектов пока нет.'])

@dp.message(Command('hierarchy'))
async def hierarchy(m: Message):
    if not allowed(m.from_user.id): return
    from .natural import send_pages
    with Session() as s:
        tasks=s.query(Task).order_by(Task.id).all()
        lines=[]
        for root in (t for t in tasks if t.parent_task_id is None):
            info=task_summary(s,root)
            lines.append(f'#{root.id} {root.title} [{root.status}]; проект #{root.project_id or "—"}; {info["done"]}/{info["count"]}; оценка {info["estimate"]} мин + неоценено {info["unestimated"]}; факт {info["actual"]} мин + без записей {info["unreported"]}; отклонение {info["deviation_minutes"] if info["deviation_minutes"] is not None else "ещё неизвестно"}')
            lines.extend(f'  ↳ #{t.id} {t.title} [{t.status}]' for t in tasks if t.parent_task_id==root.id)
    await send_pages(m, lines or ['Задач нет.'])


@dp.message(Command('today'))
async def today(m:Message):
    if not allowed(m.from_user.id): return
    await m.answer(await daily_text())

async def daily_text(proactive=False):
    now=localnow(); start=now.replace(hour=0,minute=0,second=0,microsecond=0); end=start+timedelta(days=1)
    with Session() as s:
        tasks=s.query(Task).filter(Task.status.notin_(['done','cancelled'])).order_by(Task.due_at.asc().nullslast()).limit(30).all()
        if proactive:
            active_projects={p.id for p in s.query(Project).filter_by(status='active')}
            tasks=[t for t in tasks if t.project_id is None or t.project_id in active_projects]
        lines=[(f'  ↳ #{t.id} (родитель #{t.parent_task_id})' if t.parent_task_id else f'#{t.id}') + f' [{t.priority}/{t.status}] {t.title} — '+(t.due_at.astimezone(TZ).strftime('%d.%m %H:%M') if t.due_at else 'без срока') for t in tasks]
    events,err=await asyncio.to_thread(events_for_day, start, end)
    cal='\n'.join(f"• {e['start']} — {e['title']}" for e in events[:12]) or 'Нет событий'
    if err: cal=err
    return ('Задачи:\n'+'\n'.join(lines or ['Нет открытых задач'])+'\n\nКалендарь:\n'+cal)[:3900]

@dp.message(Command('depends', 'undepend'))
async def dependency(m: Message):
    if not allowed(m.from_user.id): return
    from .dependencies import add_dependency
    try:
        command, raw_before, raw_after = m.text.split()
        before, after = int(raw_before), int(raw_after)
        with Session.begin() as s:
            lock_domain(s)
            if command.split('@')[0] == '/depends':
                add_dependency(s, before, after)
                kind = 'dependency_added'
            else:
                task = s.get(Task, before)
                if task and task.project_id:
                    s.get(Project, task.project_id, with_for_update=True)
                edge = s.get(TaskDependency, (before, after))
                if not edge:
                    raise ValueError('Зависимость не найдена.')
                s.delete(edge)
                kind = 'dependency_removed'
            s.add(EventLog(kind=kind, object_id=after, detail={'before': before, 'after': after}))
        result = f'Зависимость #{before} → #{after}: ' + ('сохранена.' if kind == 'dependency_added' else 'удалена.')
    except ValueError as exc:
        result = f'{exc} Формат: /depends BEFORE AFTER или /undepend BEFORE AFTER'
    await m.answer(result)


@dp.message(Command('dependencies'))
async def dependencies(m: Message):
    if not allowed(m.from_user.id): return
    with Session() as s:
        edges = s.query(TaskDependency).order_by(TaskDependency.before_id, TaskDependency.after_id).limit(100).all()
        result = '\n'.join(f'#{e.before_id} → #{e.after_id}' for e in edges)
    await m.answer(result or 'Сохранённых зависимостей нет.')


@dp.message(Command('done'))
@user_errors
async def done(m:Message):
    if not allowed(m.from_user.id): return
    try:
        raw=m.text.partition(' ')[2]; idpart,sep,proof=raw.partition('|'); tid=int(idpart.strip()); proof=proof.strip()
        if not sep or len(proof)<8: raise ValueError()
    except ValueError: return await m.answer('Формат: /done ID | ссылка или описание результата (минимум 8 символов)')
    with Session.begin() as s:
        lock_domain(s)
        t=s.get(Task,tid,with_for_update=True)
        if not t or t.status != 'in_progress': msg='Сдать результат можно только для задачи в работе. Сначала: /begin ID.'
        else:
            can_finish(s, t); require_transition(t.status, 'review'); t.status='review'; s.add(Evidence(task_id=tid,content=proof)); s.add(EventLog(kind='evidence_submitted',object_id=tid)); msg=f'Результат задачи #{tid} сохранён. Проверь соответствие критериям: {t.criteria}\nПодтверди: /verify {tid}\nИли отклони: /reject {tid} | причина'
    await m.answer(msg)
    if msg.startswith('Результат задачи #'):
        try:
            with Session() as s:
                current=s.get(Task,tid,with_for_update=True)
                title, criteria=current.title, current.criteria
            assessment=await assess_evidence(title,criteria,proof)
            await m.answer(('Предварительная AI-проверка (не является подтверждением выполнения):\n'+assessment+'\n\nОкончательное решение остаётся за владельцем: /verify '+str(tid)+' или /reject '+str(tid)+' | причина')[:3900])
        except Exception:
            logging.exception('Evidence assessment failed')
            await m.answer('Предварительная AI-проверка недоступна. Результат сохранён на ручную проверку; задача не закрыта.')

@dp.message(Command('begin'))
@user_errors
async def begin(m: Message):
    if not allowed(m.from_user.id): return
    try:
        tid = int(m.text.split()[1])
        with Session.begin() as s:
            lock_domain(s)
            task = s.get(Task, tid, with_for_update=True)
            if not task:
                raise ValueError('Задача не найдена.')
            if task.status != 'planned':
                raise ValueError('Начать работу можно только для запланированной задачи; для возврата с проверки используй /reject.')
            update_task(s, tid, {'status': 'in_progress'})
        result = f'Задача #{tid} переведена в работу.'
    except (ValueError, IndexError) as exc:
        result = str(exc) if isinstance(exc, ValueError) else 'Формат: /begin ID'
    await m.answer(result)

@dp.message(Command('verify'))
@user_errors
async def verify(m:Message):
    if not allowed(m.from_user.id): return
    try: tid=int(m.text.split()[1])
    except (IndexError,ValueError): return await m.answer('Формат: /verify ID')
    with Session.begin() as s:
        lock_domain(s)
        t=s.get(Task,tid,with_for_update=True); ev=s.query(Evidence).filter_by(task_id=tid).order_by(Evidence.id.desc()).first()
        if not t or t.status!='review' or not ev: msg='Нет задачи с результатом на проверке.'
        else:
            can_finish(s, t); require_transition(t.status, 'done'); ev.verified=True; t.status='done'; s.add(EventLog(kind='task_verified',object_id=tid)); msg=f'Задача #{tid} подтверждена и закрыта.'
    await m.answer(msg)

@dp.message(Command('reject'))
@user_errors
async def reject(m:Message):
    if not allowed(m.from_user.id): return
    try:
        idpart,sep,reason=m.text.partition(' ')[2].partition('|'); tid=int(idpart.strip())
        if not sep or not reason.strip(): raise ValueError()
    except ValueError: return await m.answer('Формат: /reject ID | причина')
    with Session.begin() as s:
        lock_domain(s)
        t=s.get(Task,tid,with_for_update=True)
        if not t or t.status!='review': msg='Задача не ожидает проверки.'
        else:
            require_transition(t.status, 'in_progress'); t.status='in_progress'; s.add(EventLog(kind='evidence_rejected',object_id=tid,detail={'reason':reason.strip()})); msg=f'Задача #{tid} возвращена в работу.'
    await m.answer(msg)

@dp.message(Command('move'))
@user_errors
async def move(m:Message):
    if not allowed(m.from_user.id): return
    try:
        idpart,sep,date=m.text.partition(' ')[2].partition('|'); tid=int(idpart.strip()); due=date_parse(date.strip())
        if not sep: raise ValueError()
    except ValueError: return await m.answer('Формат: /move ID | YYYY-MM-DD HH:MM')
    with Session.begin() as s:
        lock_domain(s)
        t=s.get(Task,tid,with_for_update=True)
        if not t or t.status in CLOSED: msg='Задача не найдена или уже закрыта.'
        else:
            update_task(s, tid, {'due': date})
            msg = f'Срок задачи #{tid} изменён. Переносов: {t.reschedules}.'
            warnings = forecast(s)
            if warnings: msg += '\n' + '\n'.join(warnings)
    await m.answer(msg)

@dp.message(Command('pause'))
async def pause(m:Message):
    if not allowed(m.from_user.id): return
    try: mins=int(m.text.split()[1]); assert 1<=mins<=10080
    except (IndexError,ValueError,AssertionError): return await m.answer('Формат: /pause 120 (от 1 до 10080 минут)')
    until=utcnow()+timedelta(minutes=mins)
    with Session.begin() as s: s.merge(Setting(key='pause_until',value=until.isoformat()))
    await m.answer(f'Инициативные сообщения приостановлены до {until.astimezone(TZ):%d.%m %H:%M} МСК.')

@dp.message(Command('resume'))
async def resume(m:Message):
    if not allowed(m.from_user.id): return
    with Session.begin() as s: s.merge(Setting(key='pause_until',value=utcnow().isoformat()))
    await m.answer('Инициативные сообщения возобновлены.')

@dp.message(Command('status'))
async def status(m:Message):
    if not allowed(m.from_user.id): return
    await m.answer('Часовой пояс: Europe/Moscow; контроль 09:00–19:00; календарь: только чтение; режим: '+('пауза' if paused() else 'активен'))

@dp.callback_query(F.data.startswith('draft_tasks:'))
async def confirm_draft(c:CallbackQuery):
    if not allowed(c.from_user.id):
        await c.answer('Нет доступа.', show_alert=True)
        return

    parts = (c.data or '').split(':', 2)
    if len(parts) != 3:
        await c.answer('Некорректная кнопка подтверждения.', show_alert=True)
        return

    _, action, draft_id = parts
    if action not in ('approve', 'cancel') or not re.fullmatch(r'[0-9a-f]{32}', draft_id):
        await c.answer('Некорректная кнопка подтверждения.', show_alert=True)
        return

    draft_key = f'pending_task_draft:{draft_id}'

    try:
        with Session.begin() as s:
            lock_domain(s)
            setting = (
                s.query(Setting)
                .filter_by(key=draft_key)
                .with_for_update()
                .one_or_none()
            )

            if not setting:
                msg = 'Черновик уже обработан или отсутствует. Задачи не созданы.'
            elif action == 'cancel':
                s.delete(setting)
                msg = 'Черновик отменён. Задачи не созданы.'
            else:
                tasks = json.loads(setting.value)
                try:
                    validate_task_items(tasks)
                except (ValueError, KeyError, TypeError):
                    s.delete(setting)
                    await c.answer('Срок черновика истёк или некорректен. Создай новый.', show_alert=True)
                    return
                created = []

                for item in tasks:
                    pid = item['project_id']
                    if pid is not None:
                        project = s.get(Project, pid)
                        if not project or project.status not in ('planned', 'active'):
                            raise ValueError(
                                'Проект больше не активен. Задачи не сохранены.'
                            )

                    due = date_parse(item['due'])
                    breakdown = item.get('effort_breakdown', [])
                    estimate = sum(stage['minutes'] for stage in breakdown)

                    if estimate != item['estimate_minutes']:
                        raise ValueError(
                            'Оценка не совпадает с суммой этапов. Задачи не сохранены.'
                        )

                    task = Task(
                        title=item['title'],
                        criteria=item['criteria'],
                        priority=item['priority'],
                        due_at=due,
                        project_id=pid,
                        estimate_minutes=estimate,
                        effort_breakdown=json.dumps(
                            breakdown, ensure_ascii=False
                        )
                    )
                    s.add(task)
                    s.flush()
                    created.append(task.id)

                    s.add(EventLog(
                        kind='task_created',
                        object_id=task.id,
                        detail={'source': 'approved_chat_draft'}
                    ))

                s.delete(setting)
                msg = 'Задачи сохранены: ' + ', '.join(
                    '#' + str(i) for i in created
                ) + '\nПроверить: /today'

    except (ValueError, KeyError, TypeError) as exc:
        msg = f'Черновик отклонён: {exc}. Задачи не созданы.'

    await c.answer()
    await c.message.edit_reply_markup(reply_markup=None)
    await c.message.answer(msg)


@dp.callback_query(F.data.startswith('change:'))
async def confirm_change(c: CallbackQuery):
    if not allowed(c.from_user.id):
        await c.answer('Нет доступа.', show_alert=True)
        return
    parts = (c.data or '').split(':')
    if (len(parts) != 3 or parts[1] not in ('approve', 'cancel')
            or not re.fullmatch(r'[0-9a-f]{32}', parts[2])):
        await c.answer('Некорректная кнопка.', show_alert=True)
        return
    _, action, token = parts
    try:
        with Session.begin() as s:
            lock_domain(s)
            setting, envelope = load_change(s, token)
            kind, draft = envelope['kind'], envelope['payload']
            if action == 'cancel':
                msg = 'Предложение отменено. Ничего не изменено.'
            elif kind == 'domain':
                from .services.proposals import apply
                msg = apply(s, draft, c.from_user.id, operation_id=token)
            elif kind == 'batch_move':
                updates = []
                for item in sorted(draft['changes'], key=lambda item: item['id']):
                    task = s.get(Task, item['id'], with_for_update=True)
                    if not task or task.status in CLOSED:
                        raise ValueError(f"Задача #{item['id']} закрыта или отсутствует")
                    current = task.due_at.isoformat() if task.due_at else None
                    if current != item['old'] or task.status != item['status']:
                        raise ValueError(f"Задача #{item['id']} изменилась после формирования плана")
                    updates.append((task, date_parse(item['new']), current))
                if not updates:
                    raise ValueError('Пустой план переноса')
                for task, due, old in updates:
                    update_task(s, task.id, {'due': due.astimezone(TZ).strftime('%Y-%m-%d %H:%M')})
                    s.add(EventLog(kind='deadline_moved', object_id=task.id,
                                   detail={'old': old, 'new': due.isoformat(), 'source': 'approved_batch_move'}))
                msg = 'Сроки изменены одной транзакцией: ' + ', '.join(f'#{t.id}' for t, _, _ in updates)
            else:
                tid = draft['task_id']
                task = s.get(Task, tid, with_for_update=True)
                if not task or task.status in CLOSED:
                    raise ValueError('Задача отсутствует или закрыта')
                if task.status != draft['status']:
                    raise ValueError('Статус задачи изменился. Запроси предложение заново')
                if kind == 'priority':
                    if task.priority != draft['old'] or draft['new'] not in PRIORITIES:
                        raise ValueError('Приоритет изменился или некорректен')
                    update_task(s, tid, {'priority': draft['new']})
                    s.add(EventLog(kind='task_priority_changed', object_id=tid, detail=draft))
                    msg = f'Приоритет задачи #{tid} изменён на {task.priority}.'
                elif kind == 'status':
                    if task.status != draft['old'] or draft['new'] != 'in_progress':
                        raise ValueError('Недопустимый переход статуса')
                    require_transition(task.status, draft['new'])
                    update_task(s, tid, {'status': draft['new']})
                    s.add(EventLog(kind='task_status_changed', object_id=tid, detail=draft))
                    msg = f'Задача #{tid} переведена в работу.'
                elif kind == 'move':
                    current = task.due_at.isoformat() if task.due_at else None
                    if current != draft['old']:
                        raise ValueError('Срок задачи уже изменился')
                    update_task(s, tid, {'due': draft['new']})
                    s.add(EventLog(kind='deadline_moved', object_id=tid, detail=draft))
                    msg = f'Срок задачи #{tid} изменён.'
                elif kind == 'next_step':
                    if task.parent_task_id is not None:
                        raise ValueError('Разрешено только два уровня задач')
                    if task.project_id != draft['project_id']:
                        raise ValueError('Проект исходной задачи изменился')
                    project = s.get(Project, task.project_id, with_for_update=True) if task.project_id else None
                    if task.project_id and (not project or project.status not in ('planned', 'active')):
                        raise ValueError('Проект больше не активен')
                    child = Task(title=draft['title'], criteria=draft['criteria'],
                                 project_id=task.project_id, parent_task_id=task.id, priority=task.priority,
                                 due_at=date_parse(draft['due']))
                    s.add(child)
                    s.flush()
                    s.add(EventLog(kind='task_created', object_id=child.id,
                                   detail={'source': 'approved_next_step', 'parent_task_id': tid}))
                    msg = f'Создана задача #{child.id} «{child.title}» как следующий шаг по задаче #{tid}.'
                else:
                    raise ValueError('Неизвестная операция')
            if action == 'approve' and kind != 'domain':
                s.flush()
                warnings = forecast(s)
                if warnings: msg += '\n' + '\n'.join(warnings)
            s.delete(setting)
    except (ValueError, KeyError, TypeError) as exc:
        msg = f'Операция отклонена: {exc}. Ничего не изменено.'
    await c.answer()
    if c.message:
        await c.message.edit_reply_markup(reply_markup=None)
        await c.message.answer(msg)


@dp.callback_query(F.data.startswith('priority_task:'))
@dp.callback_query(F.data.startswith('status_task:'))
@dp.callback_query(F.data.startswith('move_task:'))
@dp.callback_query(F.data.startswith('batch_move:'))
@dp.callback_query(F.data.startswith('next_step:'))
async def reject_legacy_confirmation(c: CallbackQuery):
    await c.answer('Кнопка устарела. Запроси новое предложение.', show_alert=True)


@dp.message(F.text)
async def chat(m:Message):
    if not allowed(m.from_user.id): return
    from .natural import handle
    try:
        if await handle(m, OWNER): return
    except Exception:
        logging.exception('Natural-language request failed')
        await m.answer('Не удалось подготовить корректное предложение. Изменения не внесены.')
        return
    text=(m.text or '').lower()
    # Multi-task analysis must be handled before the single-task branch.
    # Capture numbers only in an explicit task-ID enumeration, not dates or times.
    task_ids = []
    for match in re.finditer(r'задач(?:а|и|у|е|ей|ам|ами|ах)?\s*(?:№|#)\s*(\d+)((?:\s*[,и]\s*(?:№|#)?\s*\d+)*)', text):
        task_ids.append(int(match.group(1)))
        tail = match.group(2) or ''
        task_ids.extend(int(n) for n in re.findall(r'(?:^|[,и])\s*(?:№|#)?\s*(\d+)', tail))
    task_ids = list(dict.fromkeys(task_ids))
    if len(task_ids) >= 2:
        wants_batch = bool(re.search(r'соглас|утверд|подтверд|примен|измен.{0,30}срок|перенес|перенести|подготов.{0,30}измен', text))
        if wants_batch:
            with Session() as session:
                pointer = session.get(Setting, 'latest_change:batch_move:all')
                saved = session.get(Setting, f'pending_change:{pointer.value}') if pointer else None
                envelope = json.loads(saved.value) if saved else None
                draft = envelope['payload'] if envelope else None
                token = pointer.value if pointer else None
            if not draft or not {item['id'] for item in draft['changes']}.issubset(set(task_ids)):
                await m.answer('Нет сохранённого плана для этих задач. Сначала попроси проанализировать задачи и предложить промежуточные сроки. Ничего не изменено.')
                return
            if utcnow() - datetime.fromisoformat(envelope['created_at']) > timedelta(hours=24):
                await m.answer('Сохранённый план устарел. Запроси анализ заново.'); return
            lines = ['Черновик группового изменения сроков (пока НЕ сохранён):']
            for item in draft['changes']:
                old = datetime.fromisoformat(item['old']).astimezone(TZ).strftime('%d.%m.%Y %H:%M') if item['old'] else 'без срока'
                lines.append(f"#{item['id']}: {old} → {item['new']} МСК")
            kb = change_buttons(token, '✅ Применить все изменения')
            await m.answer('\n'.join(lines)+'\n\nВсе изменения применятся вместе только после нажатия кнопки.', reply_markup=kb)
            return
        with Session() as s:
            rows = [s.get(Task, tid) for tid in task_ids]
            missing = [tid for tid, row in zip(task_ids, rows) if row is None]
            if missing:
                await m.answer('Не найдены задачи: '+', '.join('#'+str(tid) for tid in missing)+'. Ничего не изменено.')
                return
            project_ids = {t.project_id for t in rows}
            projects = {pid: s.get(Project, pid).title for pid in project_ids if pid is not None and s.get(Project, pid)}
            task_data = [{
                'id': t.id, 'title': t.title, 'description': t.description,
                'criteria': t.criteria, 'status': t.status, 'priority': t.priority,
                'due_msk': t.due_at.astimezone(TZ).strftime('%d.%m.%Y %H:%M') if t.due_at else None,
                'project_id': t.project_id
            } for t in rows]
        prompt = (
            'Ты опытный проектный менеджер. Анализируй ТОЛЬКО предоставленные реальные задачи '
            'из PostgreSQL. Определи содержательные пересечения, зависимости, порядок выполнения '
            'и предложи конкретные промежуточные сроки, если об этом просят. '
            'Не выдумывай факты, явно помечай предположения и предложенные сроки. '
            'Учитывай текущую дату и сроки исходных задач; не предлагай прошедшие даты. '
            'Не утверждай, что сроки, задачи или зависимости сохранены. '
            'Это ТОЛЬКО консультация, никаких изменений в БД. '
            'Ответ по-русски, кратко и предметно, с номерами задач.'
        )
        try:
            client = AsyncOpenAI(api_key=os.environ['OPENAI_API_KEY'], timeout=35.0)
            response = await client.chat.completions.create(
                model=os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'),
                messages=[
                    {'role': 'system', 'content': prompt},
                    {'role': 'user', 'content': json.dumps({
                        'request': m.text, 'now_msk': localnow().strftime('%d.%m.%Y %H:%M'),
                        'projects': projects, 'tasks': task_data
                    }, ensure_ascii=False)}
                ], temperature=0.2)
            result = response.choices[0].message.content or 'Не удалось подготовить анализ.'
            # Extract structured deadlines from this exact analysis; never infer them from chat history.
            extract = await client.chat.completions.create(
                model=os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'),
                messages=[
                    {'role':'system','content':(
                        'Extract only explicit proposed NEW deadlines for the listed tasks from the analysis. '
                        'Return JSON object {"changes":[{"id":3,"new":"2026-11-03 16:00"}]}. '
                        'Timezone Europe/Moscow. No guesses, no original deadlines, no other IDs. '
                        'If no explicit new deadline for a task, omit it.')},
                    {'role':'user','content':json.dumps({'analysis':result,'task_ids':task_ids},ensure_ascii=False)}
                ],response_format={'type':'json_object'},temperature=0)
            parsed=json.loads(extract.choices[0].message.content or '{}')
            proposed={}
            for item in parsed.get('changes',[]):
                tid=int(item['id'])
                new=str(item['new'])
                if tid not in task_ids or tid in proposed: continue
                parsed_due=date_parse(new)
                if parsed_due <= utcnow(): continue
                proposed[tid]=new
            changes=[]
            with Session() as s:
                for tid in task_ids:
                    t=s.get(Task,tid,with_for_update=True)
                    if tid in proposed and t and t.status not in ('done','cancelled'):
                        old=t.due_at.astimezone(timezone.utc).isoformat() if t.due_at else None
                        if not t.due_at or date_parse(proposed[tid]) != t.due_at.astimezone(timezone.utc):
                            changes.append({'id':tid,'old':old,'new':proposed[tid],'status':t.status})
            if len(changes)>=2:
                with Session.begin() as s:
                    lock_domain(s)
                    save_change(s, 'batch_move', 'all', {'changes': changes})
                suffix='\n\nПредложенные сроки сохранены как черновик. Чтобы увидеть кнопки подтверждения, напиши: «Согласен с планом по задачам '+', '.join('№'+str(i) for i in task_ids)+'. Подготовь изменение сроков». До подтверждения ничего не изменится.'
            else:
                suffix='\n\nНе удалось извлечь как минимум два новых срока. Черновик группового переноса не создан.'
            await m.answer(('Анализ задач из PostgreSQL (предложение, ничего не изменено):\n\n'+result+suffix)[:3900])
        except Exception:
            logging.exception('Multi-task analysis failed')
            await m.answer('Не удалось проанализировать задачи. Никаких изменений не внесено.')
        return
    # Read task IDs from PostgreSQL, never from conversational memory.
    task_match = re.search(r'задач(?:а|и|у|е|ей|у\s+)?\s*(?:№|#)\s*(\d+)\b', text)
    if task_match:
        tid = int(task_match.group(1))
        with Session() as s:
            task_obj = s.get(Task, tid)
            if task_obj:
                title, status, priority = task_obj.title, task_obj.status, task_obj.priority
                due = task_obj.due_at
                criteria, description = task_obj.criteria, task_obj.description
                old_iso = due.isoformat() if due else None
            else:
                title = None
        if title is None:
            await m.answer(f'Задача #{tid} не найдена в базе данных.')
            return
        old_display = due.astimezone(TZ).strftime('%d.%m.%Y %H:%M МСК') if due else 'без срока'
        # Explicit full date only; never infer dates for a database write.
        date_match = re.search(r'\b(20\d{2}-\d{2}-\d{2})[ T](\d{2}:\d{2})\b', text)
        russian_date = re.search(r'\b(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(20\d{2})\s*(?:года)?\s*(?:,|в)?\s*(\d{1,2}):(\d{2})', text)
        
        priority_matches = re.findall(
            r'\b(?:P[1234]|П[1234])\b',
            text,
            re.IGNORECASE
        )

        priority_match = re.search(
            r'\b(?:P[1234]|П[1234])\b',
            priority_matches[-1], re.IGNORECASE
        ) if priority_matches else None

        wants_priority = bool(
            priority_match
            and re.search(
                r'(?:измени|изменить|поменяй|установи|'
                r'повысь|повысить|понизь|понизить|'
                r'поставь|приоритет)',
                text,
                re.IGNORECASE
            )
        )
        wants_start = bool(re.search(
            r'\b(?:приступил|приступила|начал|начала|начинаю|'
            r'приступаю|взял(?:а)?\s+в\s+работу)\b|'
            r'переведи.{0,50}(?:в\s+работу|в\s+статус\s+[«"]?в\s+работе)',
            text,
            re.IGNORECASE
        ))
        wants_move = bool(re.search(r'перенес|перенести|перенеси|измени\s+срок|сдвинь\s+срок|поменяй\s+(?:дату|дедлайн)', text))
        wants_criteria = bool(re.search(r'критери|готовност|услови.{0,12}(?:выполнени|приемк)|что\s+нужно\s+сделать', text))
        wants_submit = bool(re.search(r'закончил|закончила|завершил|завершила|выполнил|выполнила|сделал|сделала|сдать\s+(?:задач|результат)|отчитаться|предоставить.{0,35}провер', text))
        
        if wants_priority:
            new_priority = priority_match.group().upper().replace('П', 'P')

            raise_priority = bool(re.search(r'\b(?:повысь|повысить)\b', text))
            lower_priority = bool(re.search(r'\b(?:понизь|понизить)\b', text))

            if raise_priority and lower_priority:
                await m.answer('Ошибка: выбери одно направление изменения приоритета.')
                return

            if priority in ('P1', 'P2', 'P3', 'P4'):
                old_level = int(priority[1])
                new_level = int(new_priority[1])

                if raise_priority and new_level > old_level:
                    await m.answer(
                        f'Нельзя повысить приоритет с {priority} до {new_priority}: '
                        'P1 — самый высокий, P4 — самый низкий.'
                    )
                    return

                if lower_priority and new_level < old_level:
                    await m.answer(
                        f'Нельзя понизить приоритет с {priority} до {new_priority}: '
                        'P1 — самый высокий, P4 — самый низкий.'
                    )
                    return

            if priority == new_priority:
                await m.answer(
                    f'Задача #{tid} «{title}» уже имеет '
                    f'приоритет {new_priority}.'
                )
                return

            if status in ('done', 'cancelled'):
                await m.answer(
                    f'Задача #{tid} закрыта. '
                    'Изменение приоритета невозможно.'
                )
                return

            with Session.begin() as s:
                lock_domain(s)
                token = save_change(s, 'priority', tid, {
                    'task_id': tid, 'status': status, 'old': priority, 'new': new_priority})
            keyboard = change_buttons(token, '✅ Подтвердить изменение')

            await m.answer(
                f'Изменение приоритета задачи #{tid} «{title}»\n'
                f'Сейчас: {priority}\n'
                f'Новый приоритет: {new_priority}\n\n'
                'До подтверждения приоритет НЕ изменён.',
                reply_markup=keyboard
            )
            return

        if wants_start:
            if status == 'in_progress':
                await m.answer(
                    f'Задача #{tid} «{title}» уже находится в работе.'
                )
                return

            if status != 'planned':
                await m.answer(
                    f'Нельзя перевести задачу #{tid} в работу '
                    f'из текущего статуса: {status}. '
                    'Никаких изменений не внесено.'
                )
                return

            with Session.begin() as s:
                lock_domain(s)
                token = save_change(s, 'status', tid, {
                    'task_id': tid, 'status': status, 'old': status, 'new': 'in_progress'})
            keyboard = change_buttons(token, '✅ Подтвердить начало работы')

            await m.answer(
                f'Изменение статуса задачи #{tid} «{title}»\n'
                f'Сейчас: {status}\n'
                'Новый статус: in_progress (В работе)\n\n'
                'До подтверждения статус НЕ изменён.',
                reply_markup=keyboard
            )
            return        
        if wants_move and not (date_match or russian_date):
            await m.answer(f'Перенос задачи #{tid} «{title}». Сейчас: {old_display}. Укажи новый срок полностью (например, 2026-11-04 18:00). До подтверждения ничего не изменится.')
            return
        if wants_move and (date_match or russian_date):
            try:
                if date_match:
                    new_text = date_match.group(1)+' '+date_match.group(2)
                else:
                    months = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря']
                    day, month, year, hour, minute = russian_date.groups()
                    new_text = f'{year}-{months.index(month)+1:02d}-{int(day):02d} {int(hour):02d}:{minute}'
                new_due = date_parse(new_text)
                if status in ('done', 'cancelled'):
                    await m.answer(f'Задача #{tid} закрыта. Перенос невозможен.')
                    return
                with Session.begin() as s:
                    lock_domain(s)
                    token = save_change(s, 'move', tid, {
                        'task_id': tid, 'status': status, 'old': old_iso, 'new': new_text})
                keyboard = change_buttons(token, '✅ Подтвердить перенос')
                await m.answer(f'Перенос задачи #{tid} «{title}»\nСейчас: {old_display}\nНовый срок: {new_due.astimezone(TZ):%d.%m.%Y %H:%M} МСК\nДо подтверждения срок НЕ изменён.',reply_markup=keyboard)
            except ValueError as exc:
                await m.answer(f'Неверная дата: {exc}')
            return
        if wants_submit:
            if status in ('done', 'cancelled'):
                await m.answer(f'Задача #{tid} уже закрыта (статус: {status}).')
            elif status == 'review':
                await m.answer(f'Задача #{tid} «{title}» уже ожидает проверки. Подтвердить: /verify {tid}; отклонить: /reject {tid} | причина.')
            else:
                await m.answer(f'Для сдачи задачи #{tid} «{title}» предоставь результат: текст, ссылку на документ или описание подтверждающих материалов.\nКритерии готовности: {criteria or "не указаны"}\n\nОтправь: /done {tid} | описание или ссылка на результат\nПосле сдачи я проведу предварительную AI-проверку; окончательно закрываешь задачу только ты. Пока статус не изменён.')
            return
        wants_help = bool(re.search(
            r'не знаю.{0,40}(?:начать|приступить)|с чего начать|перв(?:ый|ого) шаг|'
            r'следующ(?:ий|его) шаг|помоги.{0,50}(?:начать|разобраться|определить)|'
            r'что делать дальше|как приступить|застрял|не приступил|'
            r'препятстви|что мешает|разбей.{0,30}(?:задач|шаг)',text))
        if wants_help:
            if status in ('done','cancelled'):
                await m.answer(f'Задача #{tid} закрыта. Подзадачу не предлагаю.'); return
            with Session() as s:
                parent=s.get(Task,tid,with_for_update=True)
                task_info={'id':tid,'title':title,'description':description,'criteria':criteria,
                           'status':status,'priority':priority,'due':old_display,'project_id':parent.project_id}
            try:
                suggestion=await propose_next_step(task_info,m.text)
                now=localnow()
                if due and due.astimezone(TZ)>now+timedelta(hours=1):
                    step_due=due.astimezone(TZ)
                else:
                    step_due=(now+timedelta(days=1)).replace(hour=18,minute=0,second=0,microsecond=0)
                draft={'title':suggestion['title'],'criteria':suggestion['criteria'],
                       'due':step_due.strftime('%Y-%m-%d %H:%M'),'created_at':utcnow().isoformat()}
                draft.update(task_id=tid, status=status, project_id=task_info['project_id'])
                with Session.begin() as s:
                    lock_domain(s)
                    token = save_change(s, 'next_step', tid, draft)
                keyboard = change_buttons(token, '✅ Создать подзадачу')
                await m.answer(
                    f'Задача #{tid} «{title}»\n\nПервый шаг: {suggestion["title"]}\n'
                    f'Почему: {suggestion["explanation"]}\nКритерий готовности: {suggestion["criteria"]}\n'
                    f'Предлагаемый срок: {step_due:%d.%m.%Y %H:%M} МСК\n\n'
                    'Это только предложение. До подтверждения ничего не создаётся.',reply_markup=keyboard)
            except Exception:
                logging.exception('Next-step proposal failed')
                await m.answer('Не удалось предложить следующий шаг. Ничего не изменено.')
            return
        if wants_criteria:
            await m.answer(f'Критерии готовности задачи #{tid} «{title}» из PostgreSQL:\n{criteria or "Не указаны."}'+(f'\n\nОписание: {description}' if description else '')+'\n\nНикаких изменений не внесено.')
            return
        await m.answer(f'Данные из PostgreSQL:\nЗадача #{tid}: «{title}»\nСтатус: {status}; приоритет: {priority}\nСрок: {old_display}\nКритерии: {criteria or "не указаны"}')
        return
    try:
        intent = await classify_intent(m.text)
    except Exception:
        logging.exception('Intent classification failed')
        await m.answer(
            'Не удалось определить намерение. '
            'Попробуй повторить сообщение. '
            'Задачи не изменены.'
        )
        return

    if intent == 'clarify':
        await m.answer(
            'Уточни, пожалуйста: нужно поставить новую задачу '
            'или ты пока обсуждаешь идею?'
        )
        return

    if intent == 'update_task':
        await m.answer(
            'Укажи ID или точное название задачи и нужное изменение. Задачи не изменены.'
        )
        return

    request_creation = intent == 'create_task'  
    if request_creation:
        try:
            tasks=await draft_tasks(m.text)
            with Session() as s:
                existing_tasks = (
                    s.query(Task)
                    .filter(Task.status.notin_(['done', 'cancelled']))
                    .all()
                )
            existing_task_data = [
                {
                    'id': t.id,
                    'title': t.title,
                    'criteria': t.criteria,
                    'project_id': t.project_id
                }
                for t in existing_tasks
            ]
            duplicates = []
            for new_task in tasks:
                for existing in existing_tasks:
                    if (
                        new_task['title'].strip().casefold()
                        == existing.title.strip().casefold()
                        and new_task['project_id'] == existing.project_id
                    ):
                        duplicates.append(
                            f"#{existing.id} — {existing.title}"
                        )

            if duplicates:
                await m.answer(
                    'Обнаружены уже существующие задачи:\n'
                    + '\n'.join(dict.fromkeys(duplicates))
                    + '\n\nНовый черновик не создан.'
                )
                return
            semantic_ids = await find_semantic_duplicates(
                tasks,
                existing_task_data
            )

            if semantic_ids:
                matches = [
                    f"#{t.id} — {t.title}"
                    for t in existing_tasks
                    if t.id in semantic_ids
                ]

                await m.answer(
                    'Обнаружены возможные смысловые дубликаты:\n'
                    + '\n'.join(matches)
                    + '\n\nНовый черновик не создан. '
                    'Проверь существующие задачи или уточни, '
                    'чем новое поручение отличается от них.'
                )
                return
            draft_id = uuid.uuid4().hex
            with Session.begin() as s:
                lock_domain(s)
                s.add(Setting(
                    key=f'pending_task_draft:{draft_id}',
                    value=json.dumps(tasks, ensure_ascii=False)
                ))
            lines=['Черновик задач (пока НЕ сохранены):']
            for i,t in enumerate(tasks,1):
                lines.append(f"{i}. [{t['priority']}] {t['title']} — {t['due']} МСК; проект #{t['project_id'] or 'без проекта'}\nКритерий: {t['criteria']}\nОценка времени: {t['estimate_minutes']} мин (предварительно)")
                breakdown=t.get('effort_breakdown', [])
                if breakdown:
                    lines.append("\u0420\u0430\u0437\u0431\u0438\u0432\u043a\u0430 \u0442\u0440\u0443\u0434\u043e\u0437\u0430\u0442\u0440\u0430\u0442:")
                    for stage in breakdown:
                        lines.append(f"  - {stage['name']}: {stage['minutes']} \u043c\u0438\u043d")
            keyboard=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text='✅ Утвердить и сохранить',callback_data=f'draft_tasks:approve:{draft_id}'),
                InlineKeyboardButton(text='❌ Отменить',callback_data=f'draft_tasks:cancel:{draft_id}')]])
            await m.answer(('\n\n'.join(lines))[:3900],reply_markup=keyboard)
        except ValueError as e:
            if str(e) == 'Не удалось составить 1–8 задач.':
                await m.answer(
                    'Не могу однозначно определить, какую задачу '
                    'нужно создать. Уточни название или опиши '
                    'конкретное поручение. Задачи не созданы.'
                )
            else:
                logging.exception('Task draft validation failed')
                await m.answer(
                    'Не удалось подготовить корректный черновик. '
                    'Задачи не созданы. Проверь сроки и описание.'
                )
        except Exception:
            logging.exception('Task draft failure')
            await m.answer(
                'Произошла ошибка при подготовке черновика. '
                'Задачи не созданы.'
            )
        return
    try: await m.answer((await answer(m.text))[:3900])
    except Exception:
        logging.exception('LLM failure'); await m.answer('Ошибка модели. Попробуй ещё раз; задачи и календарь не изменены.')

async def send_once(key, text):
    from sqlalchemy.dialects.postgresql import insert
    with Session.begin() as s:
        lock_domain(s)
        claim = s.execute(insert(Notification).values(key=key, created_at=utcnow())
                          .on_conflict_do_nothing(index_elements=['key'])
                          .returning(Notification.id)).scalar_one_or_none()
        if claim is None: return False
    try:
        await bot.send_message(OWNER,text[:3900]); return True
    except Exception:
        logging.exception('Telegram send failure')
        with Session.begin() as s:
            lock_domain(s)
            n=s.get(Notification, claim)
            if n: s.delete(n)
        return False

def morning_risks_text():
    now = localnow()

    with Session() as s:
        tasks = (
            s.query(Task)
            .filter(
                Task.status.notin_(['done', 'cancelled']),
                Task.due_at.isnot(None)
            )
            .all()
        )
        active_projects={p.id for p in s.query(Project).filter_by(status='active')}

        overdue = [
            (t.id, t.title)
            for t in tasks
            if t.due_at.astimezone(TZ) < now and (t.project_id is None or t.project_id in active_projects)
        ]

    if not overdue:
        return '🔎 РИСКИ: просроченных задач нет.'

    lines = [
        f'🔴 ПРОСРОЧЕННЫЕ ЗАДАЧИ: {len(overdue)}'
    ]

    for task_id, title in overdue[:5]:
        lines.append(f'#{task_id} — {title}')

    if len(overdue) > 5:
        lines.append(
            f'И ещё {len(overdue) - 5} просроченных задач.'
        )

    lines.append('Подробный анализ: /risks')

    return '\n'.join(lines)

async def morning():
    if not is_worktime():
        return

    report = (
        '☀️ Утренний брифинг\n\n'
        + morning_risks_text()
        + '\n\n'
        + await daily_text(proactive=True)
        + '\n\nКакие три приоритета утверждаем сегодня?'
    )

    await send_once(
        'morning:' + localnow().date().isoformat(),
        report
    )

async def evening():
    if not is_worktime(): return
    await send_once('evening:'+localnow().date().isoformat(),'Вечерний отчёт. Подтверди результаты и обозначь препятствия:\n\n'+await daily_text(proactive=True))

async def deadlines():
    if not is_worktime(): return
    now=utcnow()
    with Session() as s:
        tasks=s.query(Task).filter(Task.status.notin_(['done','cancelled','review']),Task.due_at.isnot(None),Task.due_at<=now+timedelta(minutes=15)).limit(100).all()
        entries=[(t.id,t.title,t.due_at,t.criteria) for t in tasks if t.project_id is None or (s.get(Project,t.project_id) and s.get(Project,t.project_id).status == 'active')]
    for tid,title,due,criteria in entries:
        if due<=now:
            key=f'overdue:{tid}:{due.isoformat()}'
            msg=f'Контроль: задача #{tid} «{title}» просрочена. Что сделано, что мешает, какой следующий шаг? Для результата: /done {tid} | доказательство'
        else:
            key=f'upcoming:{tid}:{due.isoformat()}'
            msg=f'Через 15 минут дедлайн задачи #{tid}: {title}. Критерии: {criteria}'
        await send_once(key,msg)

async def main():
    from .migrate import upgrade
    from .health import heartbeat, PATH
    upgrade()
    health_task = asyncio.create_task(heartbeat())
    scheduler=AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(morning,'cron',hour=9,minute=0,id='morning',coalesce=True,max_instances=1)
    scheduler.add_job(evening,'cron',hour=18,minute=30,id='evening',coalesce=True,max_instances=1)
    scheduler.add_job(deadlines,'interval',minutes=5,id='deadlines',coalesce=True,max_instances=1)
    scheduler.start()
    try: await dp.start_polling(bot)
    finally:
        health_task.cancel()
        await asyncio.gather(health_task, return_exceptions=True)
        PATH.unlink(missing_ok=True)
        scheduler.shutdown(wait=False); await bot.session.close()

if __name__=='__main__': asyncio.run(main())
