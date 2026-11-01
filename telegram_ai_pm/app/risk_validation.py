"""Treat model output as untrusted data, including dependency directions."""
def validate_analysis(analysis, tasks):
    if not isinstance(analysis, dict):
        raise ValueError('Некорректный анализ рисков.')
    by_id = {task['id']: task for task in tasks}
    for name in ('dependencies', 'risks', 'next_actions'):
        if not isinstance(analysis.get(name, []), list):
            raise ValueError('Некорректная структура анализа.')
    graph = {}
    for edge in analysis.get('dependencies', []):
        if not isinstance(edge, dict):
            raise ValueError('Некорректная зависимость.')
        before, after = edge.get('before'), edge.get('after')
        if (type(before) is not int or type(after) is not int or before == after
                or before not in by_id or after not in by_id):
            raise ValueError('Некорректные номера задач в зависимости.')
        if by_id[before]['project_id'] is None or by_id[before]['project_id'] != by_id[after]['project_id']:
            raise ValueError('Зависимость между разными проектами.')
        if edge.get('confirmed', False) is not False:
            raise ValueError('Модель не может утверждать зависимости.')
        graph.setdefault(before, set()).add(after)
    visiting, visited = set(), set()

    def visit(node):
        if node in visiting:
            raise ValueError('Модель предложила циклические зависимости.')
        if node not in visited:
            visiting.add(node)
            for child in graph.get(node, set()):
                visit(child)
            visiting.remove(node)
            visited.add(node)

    for node in graph:
        visit(node)
    for risk in analysis.get('risks', []):
        if not isinstance(risk, dict) or risk.get('level') not in ('CRITICAL', 'HIGH', 'MEDIUM'):
            raise ValueError('Некорректный уровень риска.')
        ids = risk.get('task_ids')
        if not isinstance(ids, list) or not ids or any(type(i) is not int or i not in by_id for i in ids):
            raise ValueError('Некорректные задачи в риске.')
        if any(not isinstance(risk.get(key), str) or not risk[key].strip() for key in ('reason', 'action')):
            raise ValueError('Некорректное описание риска.')
    if any(not isinstance(action, str) or not action.strip() for action in analysis.get('next_actions', [])):
        raise ValueError('Некорректные рекомендации.')
