"""Validated mutations, optimistic snapshots and atomic application."""
import hashlib
import json
from ..db import Task, Project, TaskDependency, Setting
from ..workflow import save_change
from ..schemas import task_data, deadline, string, TZ
from .tasks import lock, get_task, create_task, update_task, record_time, event
from .projects import update_project
from .planning import forecast


def context_version(session, pids):
    tasks = session.query(Task).order_by(Task.id).all()
    tasks = [t for t in tasks if t.project_id in pids]
    ids = {t.id for t in tasks}
    projects = session.query(Project).order_by(Project.id).all()
    edges = [(e.before_id, e.after_id) for e in session.query(TaskDependency).order_by(
        TaskDependency.before_id, TaskDependency.after_id) if e.before_id in ids or e.after_id in ids]
    data = [[(t.id, t.revision) for t in tasks],
            [(p.id, p.revision) for p in projects if p.id in pids], edges]
    return hashlib.sha256(json.dumps(data).encode()).hexdigest()


def prepare(session, operation, target, data, actor):
    lock(session)
    if operation not in ('create_plan', 'subtasks', 'task_update', 'project_update', 'time'):
        raise ValueError('Неизвестная операция.')
    pids = set()
    if operation in ('subtasks', 'task_update', 'time'):
        task = get_task(session, target, editable=operation != 'time')
        pids.add(task.project_id)
    elif operation == 'project_update':
        project = session.get(Project, target)
        if not project or project.status in ('completed', 'cancelled'):
            raise ValueError('Проект отсутствует или закрыт.')
        pids.add(project.id)
    if operation in ('task_update', 'project_update'):
        if not isinstance(data, dict):
            raise ValueError('Некорректное изменение.')
        data = dict(data)
        keys = ('due', 'scheduled_start', 'scheduled_end') if operation == 'task_update' else ('manual_due',)
        for key in keys:
            if key in data and data[key] is not None:
                old = task.due_at if key == 'due' else None
                data[key] = deadline(data[key], previous=old).astimezone(TZ).strftime('%Y-%m-%d %H:%M')
    if operation in ('create_plan', 'subtasks'):
        if not isinstance(data, dict) or set(data) - {'items', 'project'}:
            raise ValueError('Некорректный план.')
        items = data.get('items')
        from ..settings import SETTINGS
        if not isinstance(items, list) or not 1 <= len(items) <= SETTINGS.max_tasks:
            raise ValueError('Некорректное число задач.')
        items = [task_data(item) for item in items]
        if len({item['title'].casefold() for item in items}) != len(items):
            raise ValueError('План содержит повторяющиеся задачи.')
        if sum(1 + len(item['children']) for item in items) > SETTINGS.max_tasks:
            raise ValueError('Уменьши число задач в одном плане.')
        if operation == 'subtasks':
            if task.parent_task_id is not None or any(item['children'] for item in items):
                raise ValueError('Разрешено только два уровня задач.')
            if data.get('project'):
                raise ValueError('Подзадачи не создают новый проект.')
        for item in items:
            if not data.get('project'):
                pids.add(item['project_id'])
        data = {'items': items, 'project': data.get('project')}
        project = data['project']
        if project:
            if not isinstance(project, dict) or set(project) - {'title', 'description', 'goal', 'success_criteria', 'manual_due'}:
                raise ValueError('Некорректный проект.')
            for field in ('title', 'goal', 'success_criteria'):
                string(project.get(field), field, 250 if field == 'title' else 2000)
            if not isinstance(project.get('description', ''), str) or len(project.get('description', '')) > 10000:
                raise ValueError('Некорректное описание проекта.')
            if project.get('manual_due'):
                project = dict(project)
                project['manual_due'] = deadline(project['manual_due']).astimezone(TZ).strftime('%Y-%m-%d %H:%M')
                data['project'] = project
            if any(item['project_id'] is not None for item in items):
                raise ValueError('Задачи нового проекта не могут ссылаться на другой проект.')
    expected = context_version(session, pids)
    # Validate actual mutation in a savepoint, then discard every simulated write.
    payload = dict(operation=operation, target_id=target, data=data, actor=actor,
                   project_ids=sorted(pids, key=lambda p: -1 if p is None else p), context=expected)
    savepoint = session.begin_nested()
    try:
        apply(session, payload, actor, operation_id='preview')
        session.flush()
        warnings = forecast(session)
        if operation == 'task_update' and 'due' in data:
            session.refresh(task)
            new_project = session.get(Project, task.project_id) if task.project_id else None
            if new_project:
                session.refresh(new_project)
                from .planning import display_due
                warnings.append(f'Новый срок проекта #{new_project.id}: {display_due(new_project.planned_due_at)}.')
    finally:
        savepoint.rollback()
    # No row changes, histories or EventLog from the preview survive (sequence gaps are normal).
    session.expire_all()
    payload['warnings'] = warnings
    token = save_change(session, 'domain', f'{operation}:{target or "new"}', payload)
    return token, payload


def apply(session, payload, actor, operation_id):
    lock(session)
    if payload['actor'] != actor:
        raise ValueError('Нет доступа к этому предложению.')
    if context_version(session, set(payload['project_ids'])) != payload['context']:
        raise ValueError('Данные изменились после подготовки. Запроси новый черновик.')
    operation, tid, data = payload['operation'], payload['target_id'], payload['data']
    if operation == 'create_plan':
        pid = None
        if data.get('project'):
            values = data['project']
            if any(p.title.casefold() == values['title'].casefold() for p in session.query(Project)):
                raise ValueError('Проект с таким названием уже существует. Уточни запрос.')
            project = Project(title=values['title'], goal=values['goal'], success_criteria=values['success_criteria'],
                              description=values.get('description', ''), status='planned',
                              manual_due_at=deadline(values.get('manual_due')))
            session.add(project)
            session.flush()
            pid = project.id
            event(session, 'project_created', pid, {'source': 'approved_plan'})
        created = [create_task(session, item, project_id=pid).id for item in data['items']]
        if pid:
            session.merge(Setting(key=f'context:{actor}:project', value=str(pid)))
        if len(created) == 1:
            session.merge(Setting(key=f'context:{actor}:task', value=str(created[0])))
        return ('Проект #' + str(pid) + ' и ' if pid else '') + 'задачи сохранены: ' + ', '.join('#' + str(i) for i in created)
    if operation == 'subtasks':
        parent = get_task(session, tid)
        created = [create_task(session, item, parent_id=tid, project_id=parent.project_id).id for item in data['items']]
        return f'Подзадачи задачи #{tid}: ' + ', '.join('#' + str(i) for i in created)
    if operation == 'task_update':
        update_task(session, tid, data)
        return f'Задача #{tid} изменена.'
    if operation == 'project_update':
        update_project(session, tid, data)
        return f'Проект #{tid} изменён.'
    if operation == 'time':
        if not isinstance(data, dict) or set(data) - {'minutes', 'comment'}:
            raise ValueError('Некорректная запись времени.')
        record_time(session, tid, data.get('minutes'), operation_id, data.get('comment', ''))
        return f'Время задачи #{tid} сохранено: {data["minutes"]} мин.'
    raise ValueError('Неизвестная операция.')
