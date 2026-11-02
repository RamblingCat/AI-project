from ..db import Project, Task, utcnow
from ..workflow import CLOSED
from ..schemas import deadline, string
from .tasks import lock, event

TRANSITIONS = {'draft': {'planned', 'active', 'cancelled'}, 'planned': {'active', 'cancelled'},
               'active': {'paused', 'completed', 'cancelled'}, 'paused': {'active', 'cancelled'},
               'completed': set(), 'cancelled': set()}


def update_project(session, pid, changes):
    lock(session)
    project = session.get(Project, pid, with_for_update=True, populate_existing=True)
    if not project or project.status in ('completed', 'cancelled'):
        raise ValueError('Проект отсутствует или закрыт.')
    if not changes or set(changes) - {'status', 'manual_due', 'title', 'description', 'goal', 'success_criteria'}:
        raise ValueError('Некорректное изменение проекта.')
    old = {}
    if 'status' in changes:
        new = changes['status']
        if new not in TRANSITIONS.get(project.status, set()):
            raise ValueError('Недопустимый переход статуса проекта.')
        if new in ('completed', 'cancelled') and session.query(Task).filter(
                Task.project_id == pid, Task.status.notin_(CLOSED)).first():
            raise ValueError('У проекта есть незавершённые задачи; их изменения требуют отдельного согласования.')
        old['status'], project.status = project.status, new
        if new == 'active' and project.started_at is None:
            project.started_at = utcnow()
    if 'manual_due' in changes:
        old['manual_due'] = str(project.manual_due_at)
        project.manual_due_at = deadline(changes['manual_due'])
    for field in ('title', 'description', 'goal', 'success_criteria'):
        if field in changes:
            old[field] = getattr(project, field)
            setattr(project, field, string(changes[field], field, 250 if field == 'title' else 10000))
    session.flush()
    session.refresh(project)
    event(session, 'project_updated', pid, {'old': old, 'changes': changes})
    return project
