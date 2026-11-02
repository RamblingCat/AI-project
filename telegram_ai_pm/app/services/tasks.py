import json
from sqlalchemy import text, func
from ..db import (Task, Project, TaskDependency, EventLog, TaskEstimateRevision,
                  TaskTimeEntry, utcnow)
from ..workflow import CLOSED, PRIORITIES, require_transition
from ..schemas import task_data, stages, deadline, string, COMPLEXITIES, TASK_TYPES


def lock(session):
    session.execute(text('SELECT pg_advisory_xact_lock(782364701)'))


def get_task(session, tid, editable=True):
    task = session.get(Task, tid, with_for_update=True, populate_existing=True)
    if not task:
        raise ValueError('Задача не найдена.')
    if editable and task.status in CLOSED:
        raise ValueError('Задача закрыта.')
    return task


def event(session, kind, tid, detail):
    session.add(EventLog(kind=kind, object_id=tid, detail=detail))


def estimate_snapshot(session, task, reason):
    if task.estimate_minutes is not None:
        session.add(TaskEstimateRevision(task_id=task.id, minutes=task.estimate_minutes,
            breakdown=json.loads(task.effort_breakdown or '[]'), complexity=task.complexity,
            task_type=task.task_type, reason=reason))


def create_task(session, data, parent_id=None, project_id=None):
    lock(session)
    data = task_data(data)
    pid = project_id if project_id is not None else data['project_id']
    if parent_id is not None:
        parent = get_task(session, parent_id)
        if parent.parent_task_id is not None:
            raise ValueError('Подзадача не может иметь подзадач.')
        if session.query(TaskTimeEntry).filter_by(task_id=parent.id).first():
            raise ValueError('У родителя уже учтено фактическое время. Сначала согласуй его распределение; автоматического двойного учёта не будет.')
        if pid is not None and pid != parent.project_id:
            raise ValueError('Проект ребёнка должен совпадать с проектом родителя.')
        pid = parent.project_id
        if data['children']:
            raise ValueError('Разрешено только два уровня задач.')
    if pid is not None:
        project = session.get(Project, pid, with_for_update=True)
        if not project or project.status not in ('planned', 'active'):
            raise ValueError('Добавлять задачи можно в планируемый или активный проект.')
    if session.query(Task).filter(Task.project_id == pid, Task.parent_task_id == parent_id,
            Task.status.notin_(CLOSED), func.lower(Task.title) == data['title'].lower()).first():
        raise ValueError('Такая задача уже есть в этом разделе проекта.')
    task = Task(title=data['title'], criteria=data['criteria'], description=data['description'],
                priority=data['priority'], status='planned', project_id=pid, parent_task_id=parent_id,
                due_at=deadline(data['due']), estimate_minutes=sum(s['minutes'] for s in data['effort_breakdown']),
                effort_breakdown=json.dumps(data['effort_breakdown'], ensure_ascii=False),
                complexity=data['complexity'], task_type=data['task_type'])
    session.add(task)
    session.flush()
    estimate_snapshot(session, task, 'created')
    event(session, 'task_created', task.id, {'parent_task_id': parent_id, 'source': 'approved_plan'})
    for child in data['children']:
        create_task(session, child, parent_id=task.id, project_id=pid)
    from ..dependencies import validate_dependency_graph
    validate_dependency_graph(session)
    return task


def can_finish(session, task):
    if session.query(Task).filter(Task.parent_task_id == task.id, Task.status.notin_(CLOSED)).first():
        raise ValueError('Сначала заверши или явно отмени действующие подзадачи.')


def update_task(session, tid, changes):
    lock(session)
    task = get_task(session, tid)
    allowed = {'title', 'description', 'criteria', 'priority', 'due', 'status',
               'effort_breakdown', 'complexity', 'task_type', 'scheduled_start', 'scheduled_end'}
    if not isinstance(changes, dict) or not changes or set(changes) - allowed:
        raise ValueError('Некорректное изменение задачи.')
    before = {}
    for field in ('title', 'description', 'criteria'):
        if field in changes:
            before[field] = getattr(task, field)
            setattr(task, field, string(changes[field], field, 300 if field == 'title' else 10000))
    if 'priority' in changes:
        if changes['priority'] not in PRIORITIES:
            raise ValueError('Допустимы P1–P4.')
        before['priority'], task.priority = task.priority, changes['priority']
    if 'due' in changes:
        before['due'] = task.due_at.isoformat() if task.due_at else None
        task.due_at = deadline(changes['due'], previous=task.due_at)
        task.reschedules += 1
    for field, choices in (('complexity', COMPLEXITIES), ('task_type', TASK_TYPES)):
        if field in changes:
            if changes[field] not in choices:
                raise ValueError('Некорректная категория.')
            before[field] = getattr(task, field)
            setattr(task, field, changes[field])
    if 'effort_breakdown' in changes:
        breakdown = stages(changes['effort_breakdown'])
        before['estimate_minutes'] = task.estimate_minutes
        task.estimate_minutes = sum(s['minutes'] for s in breakdown)
        task.effort_breakdown = json.dumps(breakdown, ensure_ascii=False)
        estimate_snapshot(session, task, 'reestimated')
    if 'status' in changes:
        new = changes['status']
        if new in ('review', 'done'):
            raise ValueError('Для сдачи и приёмки используй /done и /verify с доказательством.')
        if task.status == 'review' and new == 'in_progress':
            raise ValueError('Для возврата с проверки используй /reject с причиной.')
        require_transition(task.status, new)
        if new == 'cancelled':
            can_finish(session, task)
            dependent = session.query(Task).join(TaskDependency, TaskDependency.after_id == Task.id).filter(
                TaskDependency.before_id == tid, Task.status.notin_(CLOSED)).first()
            if dependent:
                raise ValueError(f'Есть действующая зависимая задача #{dependent.id}; сначала согласуй её отмену или удаление зависимости.')
        if new == 'in_progress' and task.project_id is not None:
            project = session.get(Project, task.project_id)
            if project.status != 'active':
                raise ValueError('Сначала активируй проект.')
        before['status'], task.status = task.status, new
        if new == 'in_progress':
            estimate_snapshot(session, task, 'start')
    for field in ('scheduled_start', 'scheduled_end'):
        if field in changes:
            before[field] = str(getattr(task, field + '_at'))
            setattr(task, field + '_at', deadline(changes[field]))
    if (task.scheduled_start_at is None) != (task.scheduled_end_at is None):
        raise ValueError('Для расписания укажи начало и конец.')
    if task.scheduled_start_at and task.scheduled_end_at <= task.scheduled_start_at:
        raise ValueError('Конец работы должен быть позже начала.')
    session.flush()
    session.refresh(task)
    event(session, 'task_updated', tid, {'old': before, 'changes': changes})
    return task


def record_time(session, tid, minutes, operation_id, comment=''):
    lock(session)
    task = get_task(session, tid, editable=False)
    if task.status == 'cancelled' or type(minutes) is not int or not 1 <= minutes <= 60000:
        raise ValueError('Некорректное фактическое время или отменённая задача.')
    if not isinstance(comment, str) or len(comment) > 2000:
        raise ValueError('Некорректный комментарий к времени.')
    if session.query(Task).filter(Task.parent_task_id == tid, Task.status != 'cancelled').first():
        raise ValueError('Укажи фактическое время по подзадачам, чтобы избежать двойного учёта.')
    if session.query(TaskTimeEntry).filter_by(operation_id=operation_id).first():
        return
    session.add(TaskTimeEntry(task_id=tid, minutes=minutes, operation_id=operation_id, comment=comment))
    event(session, 'time_recorded', tid, {'minutes': minutes, 'operation_id': operation_id})
