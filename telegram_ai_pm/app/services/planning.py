from sqlalchemy import func
from ..db import Task, Project, TaskDependency, TaskTimeEntry, TaskEstimateRevision, utcnow
from ..workflow import CLOSED
from ..settings import SETTINGS
from ..schemas import TZ


def display_due(value):
    return value.astimezone(TZ).strftime('%Y-%m-%d %H:%M МСК') if value else 'не задан'


def leaves(tasks):
    parents = {t.parent_task_id for t in tasks if t.status != 'cancelled' and t.parent_task_id}
    return [t for t in tasks if t.status != 'cancelled' and t.id not in parents]


def totals(session, tasks):
    work = leaves(tasks)
    ids = [t.id for t in work]
    entries = dict(session.query(TaskTimeEntry.task_id, func.sum(TaskTimeEntry.minutes))
                   .filter(TaskTimeEntry.task_id.in_(ids)).group_by(TaskTimeEntry.task_id).all()) if ids else {}
    starts, originals = {}, {}
    revisions = session.query(TaskEstimateRevision).filter(TaskEstimateRevision.task_id.in_(ids),
        TaskEstimateRevision.reason.in_(('start', 'created'))).order_by(TaskEstimateRevision.id).all() if ids else []
    for revision in revisions:
        (starts if revision.reason == 'start' else originals).setdefault(revision.task_id, revision.minutes)
    baselines = [starts.get(t.id, originals.get(t.id)) for t in work]
    baseline_total = sum(baselines) if baselines and all(value is not None for value in baselines) else None
    deviation = (sum(entries.values()) - baseline_total if work and baseline_total is not None
                 and all(t.status == 'done' and t.id in entries for t in work) else None)
    return dict(done=sum(t.status == 'done' for t in work), count=len(work),
                estimate=sum(t.estimate_minutes or 0 for t in work),
                unestimated=sum(t.estimate_minutes is None for t in work),
                actual=sum(entries.values()), unreported=sum(t.id not in entries for t in work),
                baseline_estimate=baseline_total, deviation_minutes=deviation)


def task_summary(session, task):
    children = session.query(Task).filter_by(parent_task_id=task.id).order_by(Task.id).all()
    return totals(session, children if any(c.status != 'cancelled' for c in children) else [task])


def project_summary(session, project):
    tasks = session.query(Task).filter_by(project_id=project.id).order_by(Task.id).all()
    return dict(project_id=project.id, status=project.status, **totals(session, tasks),
                undated=sum(t.status not in CLOSED and t.due_at is None for t in tasks),
                auto_due=project.auto_due_at, manual_due=project.manual_due_at,
                planned_due=project.planned_due_at)


def forecast(session, changes=None, manual_changes=None):
    """Read-only simulation. Never infer schedule overlap from deadlines alone."""
    changes, manual_changes = changes or {}, manual_changes or {}
    tasks = {t.id: t for t in session.query(Task).all()}
    def value(t, key):
        return changes.get(t.id, {}).get(key, getattr(t, key))
    active = {i: t for i, t in tasks.items() if value(t, 'status') not in CLOSED}
    risks = []
    for t in active.values():
        due = value(t, 'due_at')
        if due and due < utcnow():
            risks.append(f'Просрочена задача #{t.id}.')
        parent = active.get(t.parent_task_id)
        if parent and due and value(parent, 'due_at') and due > value(parent, 'due_at'):
            risks.append(f'Подзадача #{t.id} заканчивается позже родительской #{parent.id}. Согласуй срок родителя.')
    for edge in session.query(TaskDependency).all():
        before, after = active.get(edge.before_id), active.get(edge.after_id)
        if not before or not after:
            continue
        a, b = value(before, 'due_at'), value(after, 'due_at')
        if a and b and a > b:
            risks.append(f'Конфликт зависимости #{before.id} → #{after.id}: предшественник заканчивается позже. Согласуй новый срок зависимой задачи.')
    work = leaves(list(active.values()))
    for index, a in enumerate(work):
        for b in work[index + 1:]:
            sa, ea = value(a, 'scheduled_start_at'), value(a, 'scheduled_end_at')
            sb, eb = value(b, 'scheduled_start_at'), value(b, 'scheduled_end_at')
            if sa and ea and sb and eb and sa < eb and sb < ea:
                risks.append(f'Пересекаются явные интервалы работ #{a.id} и #{b.id}.')
    for p in session.query(Project).all():
        dates = [value(t, 'due_at') for t in active.values() if t.project_id == p.id and value(t, 'due_at')]
        auto = max(dates, default=None)
        manual = manual_changes.get(p.id, p.manual_due_at)
        if auto and manual and auto > manual:
            risks.append(f'Проект #{p.id}: расчётный срок {display_due(auto)} позже ручного {display_due(manual)}. Ручной срок сохранён.')
        if any(t.project_id == p.id and t.id in changes for t in tasks.values()) and auto != p.auto_due_at:
            risks.append(f'Проект #{p.id}: расчётный срок {display_due(p.auto_due_at)} → {display_due(auto)}; итоговый {display_due(manual or auto)}.')
    return risks


def calibration(session, task_type, complexity):
    observations = []
    for task in session.query(Task).filter_by(status='done', task_type=task_type, complexity=complexity):
        if session.query(Task).filter_by(parent_task_id=task.id).first():
            continue
        estimate = session.query(TaskEstimateRevision).filter_by(task_id=task.id, reason='start').order_by(TaskEstimateRevision.id).first()
        actual = session.query(func.sum(TaskTimeEntry.minutes)).filter_by(task_id=task.id).scalar()
        if estimate and estimate.minutes and actual:
            observations.append(actual / estimate.minutes)
    count = len(observations)
    return dict(samples=count, sufficient=count >= SETTINGS.calibration_samples,
                factor=(sum(observations) / count if count >= SETTINGS.calibration_samples else None))
