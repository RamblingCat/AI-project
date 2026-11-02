from .db import Task, TaskDependency, Project


def add_dependency(session, before_id, after_id):
    from .services.tasks import lock
    lock(session)
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
    session.flush()
    validate_dependency_graph(session)


def validate_dependency_graph(session):
    tasks = session.query(Task).all()
    by_id = {t.id: t for t in tasks}
    children = {}
    for task in tasks:
        if task.parent_task_id is not None:
            children.setdefault(task.parent_task_id, []).append(task.id)
    graph = {}
    for edge in session.query(TaskDependency):
        before, after = by_id[edge.before_id], by_id[edge.after_id]
        if before.parent_task_id == after.id or after.parent_task_id == before.id:
            raise ValueError('Зависимости между родителем и ребёнком запрещены.')
        for a in children.get(before.id, [before.id]):
            for b in children.get(after.id, [after.id]):
                graph.setdefault(a, set()).add(b)
    visiting, visited = set(), set()
    def visit(node):
        if node in visiting:
            raise ValueError('Зависимости создают цикл с учётом подзадач.')
        if node not in visited:
            visiting.add(node)
            for child in graph.get(node, []):
                visit(child)
            visiting.remove(node)
            visited.add(node)
    for node in graph:
        visit(node)
