import re
from .db import Task, Project, Setting


def normalize(value):
    return ' '.join(re.findall(r'[\w]+', value.casefold().replace('ё', 'е')))


def resolve(session, kind, query, request, actor):
    model = Project if kind == 'project' else Task
    word = 'проект' if kind == 'project' else '(?:подзадач|задач)'
    match = re.search(word + r'\w*\s*[#№]\s*(\d+)\b', request, re.I)
    if match:
        row = session.get(model, int(match[1]))
        if not row:
            raise ValueError('Запись с таким ID не найдена.')
        return row, None
    if query in ('эта', 'этот', 'this'):
        saved = session.get(Setting, f'context:{actor}:{kind}')
        row = session.get(model, int(saved.value)) if saved else None
        if row:
            return row, None
        raise ValueError('Укажи ID или название: однозначного контекста нет.')
    if not isinstance(query, str) or not normalize(query):
        raise ValueError('Укажи ID или название.')
    needle = normalize(query)
    # The resolver cannot silently accept an entity name invented by the model.
    if needle not in normalize(request):
        raise ValueError('Укажи точное название или ID записи.')
    rows = session.query(model).order_by(model.id).all()
    exact = [row for row in rows if normalize(row.title) == needle]
    candidates = exact if exact else [row for row in rows if needle in normalize(row.title)]
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates:
        raise ValueError('Совпадение не найдено. Укажи ID.')
    lines = ['Найдено несколько вариантов. Укажи ID:']
    for row in candidates[:20]:
        project = session.get(Project, row.project_id) if kind == 'task' and row.project_id else None
        lines.append(f'#{row.id} {row.title} [{row.status}]; проект: {project.title if project else "—"}')
    return None, '\n'.join(lines)
