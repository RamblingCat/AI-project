from .db import Task, TaskDependency, Project


def add_dependency(session, before_id, after_id):
    if before_id == after_id:
        raise ValueError('Задача не может зависеть от себя.')
    before, after = session.get(Task, before_id), session.get(Task, after_id)
    if not before or not after:
        raise ValueError('Задача не найдена.')
    if before.project_id is None or before.project_id != after.project_id:
        raise ValueError('Зависимости разрешены только внутри одного проекта.')
    # All graph writers lock the project before reading edges, serializing cycle checks.
    session.get(Project, before.project_id, with_for_update=True)
    edges = session.query(TaskDependency).all()
    graph = {}
    for edge in edges:
        graph.setdefault(edge.before_id, []).append(edge.after_id)
    stack, seen = [after_id], set()
    while stack:
        node = stack.pop()
        if node == before_id:
            raise ValueError('Зависимость создаёт цикл.')
        if node not in seen:
            seen.add(node)
            stack.extend(graph.get(node, []))
    if not session.get(TaskDependency, (before_id, after_id)):
        session.add(TaskDependency(before_id=before_id, after_id=after_id))
