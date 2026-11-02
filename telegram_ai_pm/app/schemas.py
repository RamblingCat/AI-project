"""Strict inputs shared by model output, commands and approved proposals."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import re
from .settings import SETTINGS
from .workflow import future_due, PRIORITIES

TZ = ZoneInfo('Europe/Moscow')
COMPLEXITIES = ('simple', 'standard', 'complex')
TASK_TYPES = ('list', 'document', 'research', 'analysis', 'implementation', 'review', 'other')


def string(value, name, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'Некорректное поле {name}.')
    return value.strip()


def stages(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise ValueError('Нужно от 1 до 8 этапов оценки.')
    result = []
    for stage in value:
        if not isinstance(stage, dict) or set(stage) != {'name', 'minutes'}:
            raise ValueError('Некорректный этап оценки.')
        name = string(stage['name'], 'name', 200)
        minutes = stage['minutes']
        if type(minutes) is not int or not 1 <= minutes <= 60000:
            raise ValueError('Некорректная длительность этапа.')
        result.append(dict(name=name, minutes=minutes))
    if sum(s['minutes'] for s in result) > 60000:
        raise ValueError('Слишком большая оценка.')
    return result


def deadline(value, previous=None, now=None):
    if value is None:
        return None
    now = (now or datetime.now(TZ)).astimezone(TZ)
    value = string(value, 'due', 50).lower()
    if value in ('завтра', 'сегодня', 'послезавтра') or re.fullmatch(r'через \d+ дн(?:я|ей|ь)', value):
        days = {'сегодня': 0, 'завтра': 1, 'послезавтра': 2}.get(value)
        if days is None:
            days = int(re.search(r'\d+', value).group())
        old = previous.astimezone(TZ) if previous else None
        date = (now + timedelta(days=days)).replace(hour=old.hour if old else SETTINGS.default_hour,
            minute=old.minute if old else 0, second=0, microsecond=0)
    else:
        try:
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                old = previous.astimezone(TZ) if previous else None
                value += f' {old.hour if old else SETTINGS.default_hour:02d}:{old.minute if old else 0:02d}'
            date = datetime.strptime(value, '%Y-%m-%d %H:%M').replace(tzinfo=TZ)
        except ValueError:
            raise ValueError('Укажи срок YYYY-MM-DD HH:MM или относительную дату.') from None
    if date <= now:
        raise ValueError('Новый срок должен быть в будущем.')
    return date.astimezone(timezone.utc)


def task_data(data):
    if not isinstance(data, dict):
        raise ValueError('Некорректная задача.')
    allowed = {'title', 'description', 'criteria', 'priority', 'due', 'effort_breakdown',
               'complexity', 'task_type', 'project_id', 'children', 'workstream'}
    if set(data) - allowed:
        raise ValueError('Неизвестные поля задачи.')
    result = dict(title=string(data.get('title'), 'title', 300),
        criteria=string(data.get('criteria'), 'criteria'), priority=data.get('priority', 'P2'),
        description=data.get('description', ''), due=data.get('due'),
        effort_breakdown=stages(data.get('effort_breakdown')),
        complexity=data.get('complexity', 'standard'), task_type=data.get('task_type', 'other'),
        project_id=data.get('project_id'), workstream=data.get('workstream', 'general'))
    if result['priority'] not in PRIORITIES or result['complexity'] not in COMPLEXITIES or result['task_type'] not in TASK_TYPES:
        raise ValueError('Некорректная категория или приоритет.')
    if not isinstance(result['description'], str) or len(result['description']) > 10000:
        raise ValueError('Некорректное описание.')
    if result['project_id'] is not None and (type(result['project_id']) is not int or result['project_id'] <= 0):
        raise ValueError('Некорректный проект.')
    if result['due'] is not None:
        result['due'] = deadline(result['due']).astimezone(TZ).strftime('%Y-%m-%d %H:%M')
    result['workstream'] = string(result['workstream'], 'workstream', 100)
    children = data.get('children', [])
    if not isinstance(children, list) or len(children) > SETTINGS.max_tasks:
        raise ValueError('Слишком много подзадач.')
    if any(not isinstance(child, dict) or child.get('children') for child in children):
        raise ValueError('Разрешено только два уровня задач.')
    result['children'] = [task_data(child) for child in children]
    if len({c['title'].casefold() for c in result['children']}) != len(children):
        raise ValueError('Повторяющиеся подзадачи.')
    return result


def classify_plan(items, project_requested=False, ambiguous=False):
    if ambiguous:
        return 'clarify'
    results = len(items)
    streams = len({item['workstream'] for item in items})
    if project_requested or (results >= SETTINGS.project_results and streams >= SETTINGS.project_workstreams):
        return 'project'
    if results == 1:
        signals = scale_evidence(items)
        if not items[0]['children'] and signals['estimate_minutes'] >= SETTINGS.project_effort_minutes:
            return 'clarify'
        return 'task_with_subtasks' if items[0]['children'] else 'task'
    return 'clarify'


def scale_evidence(items):
    leaf_items = [child for item in items for child in (item['children'] or [item])]
    dates = [deadline(item['due']) for item in leaf_items if item['due']]
    horizon = max((d - datetime.now(timezone.utc)).total_seconds() / 86400 for d in dates) if dates else None
    return dict(independent_results=len(items), workstreams=len({item['workstream'] for item in items}),
                separately_accepted_parts=sum(len(item['children']) for item in items),
                estimate_minutes=sum(sum(stage['minutes'] for stage in item['effort_breakdown']) for item in leaf_items),
                horizon_days=round(horizon, 1) if horizon is not None else None,
                long_horizon=horizon is not None and horizon >= SETTINGS.project_horizon_days)
