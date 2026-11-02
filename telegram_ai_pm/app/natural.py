"""Natural-language proposals and complete paginated previews."""
import json
import re
from .db import Session, Task, Project, Setting
from .workflow import change_buttons, CLOSED, PRIORITIES
from .schemas import task_data, classify_plan, deadline, string, TZ, scale_evidence
from .settings import SETTINGS
from .resolution import resolve, normalize
from .intents import propose_request
from .services.proposals import prepare
from .services.planning import task_summary, project_summary, calibration


def task_lines(item, prefix=''):
    minutes = sum(stage['minutes'] for stage in item['effort_breakdown'])
    lines = [f'{prefix}{item["title"]} [{item["priority"]}] — {item["due"] or "срок не задан"}',
             f'Критерий: {item["criteria"]}', f'Описание: {item["description"] or "—"}',
             f'Оценка: {minutes} мин; {item["complexity"]}/{item["task_type"]}; проект: {item["project_id"] or "—"}']
    lines.extend(f'  {stage["name"]}: {stage["minutes"]} мин' for stage in item['effort_breakdown'])
    if item['children']:
        known = sum(sum(s['minutes'] for s in child['effort_breakdown']) for child in item['children'])
        lines.append(f'Итог родителя по подзадачам: {known} мин; собственная оценка не прибавляется.')
        for index, child in enumerate(item['children'], 1):
            lines.extend(task_lines(child, prefix + f'  {index}. '))
    return lines


async def send_pages(message, lines, keyboard=None):
    text = '\n'.join(lines)
    pages = []
    while text:
        if len(text) <= 3500:
            pages.append(text); break
        split = text.rfind('\n', 0, 3500)
        if split < 1:
            split = 3500
        pages.append(text[:split]); text = text[split:].lstrip('\n')
    # The button is sent only after every part of this exact proposal is shown.
    for index, page in enumerate(pages):
        kwargs = {'reply_markup': keyboard} if keyboard is not None and index == len(pages) - 1 else {}
        await message.answer(page, **kwargs)


def local_intent(text):
    """Deterministic relative-date operation for an explicit task ID."""
    if re.search(r'(?:подзадач|задач)\w*\s*[#№]\s*\d+', text, re.I):
        relative = re.search(r'\b(послезавтра|завтра|сегодня|через \d+ дн(?:я|ей|ь))\b', text, re.I)
        if relative and re.search('перенес|перенеси|срок|дедлайн', text, re.I):
            return dict(operation='task_update', target='эта', changes={'due': relative[1].lower()})
    return None


def legacy_supported(text):
    if len(re.findall(r'[#№]\s*\d+', text)) >= 2:
        return True
    if not re.search(r'задач\w*\s*[#№]\s*\d+', text, re.I):
        return False
    if re.search('назван|переимен|описан|критери.*измени|измени.*критери|подзадач|оценк|минут|расписан', text, re.I):
        return False
    return bool(re.search(r'\b[пp][1234]\b|начал|начинаю|приступил|взял.*работу|готовност', text, re.I)
                or (re.search('перенес|срок|дедлайн', text, re.I) and re.search(r'20\d\d-\d\d-\d\d', text)))


async def handle(message, actor):
    text = message.text or ''
    if text.startswith('/') or legacy_supported(text):
        return False
    try:
        with Session() as s:
            tasks = s.query(Task).filter(Task.status.notin_(CLOSED)).order_by(Task.id).limit(200).all()
            projects = s.query(Project).order_by(Project.id).limit(100).all()
            context = dict(tasks=[dict(id=t.id, title=t.title, project_id=t.project_id, parent_task_id=t.parent_task_id,
                                      status=t.status, priority=t.priority) for t in tasks],
                           projects=[dict(id=p.id, title=p.title, status=p.status) for p in projects])
        intent = local_intent(text) or await propose_request(text, context)
        operation = intent['operation']
        if operation == 'clarify':
            await message.answer(string(intent.get('question'), 'question', 1000))
            return True
        if operation == 'question':
            # Advisory conversation must never fall through into a creation branch.
            from .ai import answer
            await message.answer((await answer(text))[:3900])
            return True
        target = None
        data = {}
        if operation == 'create_plan':
            if not isinstance(intent.get('items'), list) or not 1 <= len(intent['items']) <= SETTINGS.max_tasks:
                raise ValueError('Некорректный размер плана.')
            items = [task_data(item) for item in intent['items']]
            explicit_project = bool(re.search(r'созда\w*\s+(?:новый\s+)?проект|новый проект', text, re.I))
            kind = classify_plan(items, project_requested=explicit_project, ambiguous=intent.get('ambiguous', False))
            if kind == 'clarify':
                await message.answer('Уточни: это отдельные поручения или один проект с общей целью? Ничего не сохранено.')
                return True
            project = intent.get('project') if kind == 'project' else None
            if kind == 'project' and not project:
                await message.answer('Поручение похоже на проект. Уточни общую цель и критерий успеха проекта.')
                return True
            for item in items:
                estimate = sum(s['minutes'] for s in item['effort_breakdown'])
                if item['complexity'] == 'simple' and estimate > SETTINGS.simple_review_minutes:
                    await message.answer(f'Для простой задачи «{item["title"]}» предложено {estimate} минут. Нужно исследование или достаточно краткого результата? Оценка не сохранена.')
                    return True
            with Session() as s:
                for item in items:
                    if item['project_id']:
                        p = s.get(Project, item['project_id'])
                        if not p or normalize(p.title) not in normalize(text):
                            raise ValueError('Укажи проект явно; автоматически привязывать задачу нельзя.')
                existing = s.query(Task).filter(Task.status.notin_(CLOSED)).all()
                from .ai import find_semantic_duplicates
                duplicate_items = items + [child for item in items for child in item['children']]
                existing_data = [dict(id=t.id, title=t.title, criteria=t.criteria, project_id=t.project_id) for t in existing]
                if any(a['title'].casefold() == b['title'].casefold() and a['project_id'] == b['project_id']
                       for a in duplicate_items for b in existing_data):
                    raise ValueError('Уже существует задача с таким названием в этом проекте.')
                matches = await find_semantic_duplicates(duplicate_items, existing_data) if not project else []
                if matches:
                    raise ValueError('Возможные дубликаты: ' + ', '.join('#' + str(i) for i in matches))
                stats = [calibration(s, item['task_type'], item['complexity']) for item in items]
            data = dict(items=items, project=project)
            lines = [f'Черновик: {kind}. До подтверждения ничего не создано.']
            evidence = scale_evidence(items)
            lines.append(f'Масштаб: {evidence["independent_results"]} результатов, {evidence["workstreams"]} направлений; '
                         f'{evidence["separately_accepted_parts"]} отдельно принимаемых частей; '
                         f'{evidence["estimate_minutes"]} мин по листовым работам; горизонт {evidence["horizon_days"] or "не задан"} дней.')
            if project:
                lines += [f'Проект: {project.get("title")}', f'Цель: {project.get("goal")}',
                          f'Успех: {project.get("success_criteria")}', f'Описание: {project.get("description", "—")}',
                          f'Ручной срок: {project.get("manual_due") or "не задан"}; после сохранения статус planned.']
            for index, item in enumerate(items, 1):
                lines += task_lines(item, f'{index}. ')
                stat = stats[index - 1]
                lines.append(f'Калибровка: {stat["samples"]} наблюдений; ' +
                             (f'среднее факт/план {stat["factor"]:.2f}, оценка не изменена автоматически.' if stat['sufficient'] else 'данных недостаточно.'))
        else:
            kind = 'project' if operation == 'project_update' else 'task'
            with Session() as s:
                row, clarification = resolve(s, kind, intent.get('target'), text, actor)
                if clarification:
                    await message.answer(clarification)
                    return True
                target = row.id
                title = row.title
                if operation == 'subtasks':
                    data = dict(items=[task_data(item) for item in intent.get('items', [])])
                    if any(item['children'] for item in data['items']):
                        raise ValueError('Разрешено только два уровня задач.')
                    for item in data['items']:
                        item['project_id'] = row.project_id
                    count = re.search(r'(?:на|добавь|создай)\s*(\d+|три|две|два|четыре)\s+подзадач', text, re.I)
                    if count:
                        number = {'три': 3, 'две': 2, 'два': 2, 'четыре': 4}.get(count[1].lower())
                        if number is None:
                            number = int(count[1])
                        if number != len(data['items']):
                            raise ValueError('Число предложенных подзадач не совпало с запросом.')
                    lines = [f'Структура задачи #{target} «{title}»:']
                    for index, item in enumerate(data['items'], 1):
                        lines += task_lines(item, f'{index}. ')
                    if intent.get('analysis_only') is True or not re.search(r'созда\w*|добав\w*|разбе\w*|разлож\w*|сохран\w*', text, re.I):
                        await send_pages(message, ['Только предложение структуры, без сохранения.'] + lines)
                        return True
                elif operation in ('task_update', 'project_update'):
                    data = intent.get('changes')
                    if not isinstance(data, dict):
                        raise ValueError('Некорректные изменения.')
                    if kind == 'task' and data.get('priority') in ('raise', 'lower'):
                        index = PRIORITIES.index(row.priority) + (-1 if data['priority'] == 'raise' else 1)
                        if index not in range(4):
                            raise ValueError('Приоритет уже на предельном уровне.')
                        data['priority'] = PRIORITIES[index]
                    if kind == 'task' and 'priority' in data and not re.search(r'\b[PП][1234]\b', text, re.I):
                        direction = -1 if re.search('повысь|повысить', text, re.I) else (1 if re.search('понизь|понизить', text, re.I) else 0)
                        if direction:
                            index = PRIORITIES.index(row.priority) + direction
                            if index not in range(4):
                                raise ValueError('Приоритет уже на предельном уровне.')
                            data['priority'] = PRIORITIES[index]
                    if 'due' in data and data['due'] is not None:
                        data['due'] = deadline(data['due'], previous=row.due_at).astimezone(TZ).strftime('%Y-%m-%d %H:%M')
                    lines = [f'Изменение {"проекта" if kind == "project" else "задачи"} #{target} «{title}»:']
                    for key, value in data.items():
                        old_key = {'due': 'due_at', 'manual_due': 'manual_due_at', 'scheduled_start': 'scheduled_start_at', 'scheduled_end': 'scheduled_end_at'}.get(key, key)
                        lines.append(f'{key}: {getattr(row, old_key, "—")} → {value}')
                elif operation == 'time':
                    data = dict(minutes=intent.get('minutes'), comment=intent.get('comment', ''))
                    lines = [f'Записать фактическое время задачи #{target} «{title}»: {data["minutes"]} мин; {data["comment"]}']
                else:
                    raise ValueError('Неизвестная операция.')
        with Session.begin() as s:
            token, payload = prepare(s, operation, target, data, actor)
            if target is not None:
                s.merge(Setting(key=f'context:{actor}:{kind}', value=str(target)))
        lines += ['Риски и последствия:'] + (payload['warnings'] or ['Конфликтов по имеющимся данным не обнаружено.'])
        lines += ['Изменения только после подтверждения. Другие задачи автоматически не переносятся.']
        await send_pages(message, lines, change_buttons(token))
    except (ValueError, KeyError, TypeError) as exc:
        await message.answer(f'Изменения не внесены: {exc}')
    return True
