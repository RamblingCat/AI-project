"""Validation and single-use, versioned confirmations shared by all handlers."""
import json
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .db import Setting, utcnow

PRIORITIES = ('P1', 'P2', 'P3', 'P4')
CLOSED = ('done', 'cancelled')
TRANSITIONS = {
    'planned': {'in_progress', 'cancelled'},
    'in_progress': {'review', 'cancelled'},
    'review': {'done', 'in_progress', 'cancelled'},
    'done': set(),
    'cancelled': set(),
}


def require_transition(old, new):
    if new not in TRANSITIONS.get(old, set()):
        raise ValueError(f'Недопустимый переход статуса: {old} → {new}.')


def future_due(value):
    try:
        due = datetime.strptime(value, '%Y-%m-%d %H:%M').replace(
            tzinfo=ZoneInfo('Europe/Moscow')).astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise ValueError('Дата должна быть в формате ГГГГ-ММ-ДД ЧЧ:ММ') from None
    if due <= utcnow():
        raise ValueError('Новый срок должен быть в будущем.')
    return due


def validate_task_items(tasks):
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 8:
        raise ValueError('Некорректное количество задач.')
    for item in tasks:
        if not isinstance(item, dict):
            raise ValueError('Некорректная задача.')
        for key, limit in (('title', 300), ('criteria', 2000)):
            value = item.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError('Некорректное название или критерии.')
        if item.get('priority') not in PRIORITIES:
            raise ValueError('Некорректный приоритет.')
        pid = item.get('project_id')
        if pid is not None and (type(pid) is not int or pid <= 0):
            raise ValueError('Некорректный проект.')
        future_due(item.get('due'))
        stages = item.get('effort_breakdown')
        if not isinstance(stages, list) or not 1 <= len(stages) <= 8:
            raise ValueError('Некорректные этапы.')
        for stage in stages:
            if (not isinstance(stage, dict) or not isinstance(stage.get('name'), str)
                    or not stage['name'].strip() or len(stage['name']) > 200
                    or type(stage.get('minutes')) is not int or not 1 <= stage['minutes'] <= 60000):
                raise ValueError('Некорректный этап.')
        estimate = sum(stage['minutes'] for stage in stages)
        if type(item.get('estimate_minutes')) is not int or item['estimate_minutes'] != estimate or estimate > 60000:
            raise ValueError('Оценка не совпадает с суммой этапов.')


def save_change(session, kind, scope, payload):
    """A new proposal supersedes the previous proposal for the same scope."""
    from sqlalchemy import text
    lock = int.from_bytes(hashlib.blake2b(f'{kind}:{scope}'.encode(), digest_size=8).digest(), 'big', signed=True)
    session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock})
    token = uuid.uuid4().hex
    pointer_key = f'latest_change:{kind}:{scope}'
    pointer = session.get(Setting, pointer_key, with_for_update=True)
    if pointer:
        previous = session.get(Setting, f'pending_change:{pointer.value}')
        if previous:
            session.delete(previous)
        pointer.value = token
    else:
        session.add(Setting(key=pointer_key, value=token))
    session.add(Setting(key=f'pending_change:{token}', value=json.dumps({
        'kind': kind, 'scope': str(scope), 'payload': payload,
        'created_at': utcnow().isoformat(),
    }, ensure_ascii=False)))
    return token


def load_change(session, token):
    setting = session.get(Setting, f'pending_change:{token}', with_for_update=True)
    if not setting:
        raise ValueError('Предложение устарело или уже обработано.')
    draft = json.loads(setting.value)
    pointer = session.get(Setting, f'latest_change:{draft["kind"]}:{draft["scope"]}')
    if not pointer or pointer.value != token:
        raise ValueError('Предложение заменено новым.')
    if utcnow() - datetime.fromisoformat(draft['created_at']) > timedelta(hours=24):
        raise ValueError('Предложение устарело. Запроси его заново.')
    return setting, draft


def change_buttons(token, label='✅ Подтвердить'):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data=f'change:approve:{token}'),
        InlineKeyboardButton(text='❌ Отменить', callback_data=f'change:cancel:{token}'),
    ]])
