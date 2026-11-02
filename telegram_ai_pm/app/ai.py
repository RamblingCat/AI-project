import os, json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from openai import AsyncOpenAI
from .db import Session, Message, Task, Project

client = AsyncOpenAI(api_key=os.environ['OPENAI_API_KEY'])
SYSTEM = '''Ты персональный ИИ-проджект-менеджер. Общайся по-русски, кратко, требовательно и уважительно.
Инициативность active, требовательность firm, планирование autonomous, контроль 09:00–19:00 Europe/Moscow.
Управляй рабочими и личными проектами. Разбивай цели на этапы, объясняй зависимости, предлагай реалистичные сроки и критерии готовности.
Никогда не утверждай, что сохранил проект, задачу или событие, если нет подтверждения программного инструмента.
Не выдумывай статусы, выполненные действия, события календаря или сроки.
Не объявляй задачу выполненной без evidence и явного подтверждения владельца.
Новые планы и существенные изменения требуют утверждения владельца.
При просрочке спрашивай фактический статус, препятствие и конкретный следующий шаг.
Пиши не более одного-двух содержательных вопросов за раз. Уважай рабочие часы и /pause.
Для создания задач из обычного сообщения предложи запрос «Создай задачи: ...» и подтверждение кнопкой; /task остаётся альтернативой. Не утверждай, что изменение задачи сохранено, без программного подтверждения.
В режиме беседы давай полезный план, но не изображай, будто черновик уже сохранён. При упоминании задачи по номеру не угадывай её название: ориентируйся только на актуальные данные из базы.'''

async def answer(text):
    with Session() as s:
        tasks = s.query(Task).filter(Task.status.notin_(['done','cancelled'])).order_by(Task.due_at.asc().nullslast()).limit(30).all()
        projects = s.query(Project).order_by(Project.id.desc()).limit(15).all()
        history = s.query(Message).order_by(Message.id.desc()).limit(12).all()[::-1]
        from .services.planning import task_summary
        task_context = []
        for task in tasks:
            info = task_summary(s, task)
            task_context.append(dict(id=task.id, title=task.title, status=task.status,
                priority=task.priority, due=str(task.due_at), criteria=task.criteria,
                parent_task_id=task.parent_task_id, estimate=info['estimate'],
                unestimated=info['unestimated'], actual=info['actual'], progress=f"{info['done']}/{info['count']}"))
        context = 'Проекты: ' + str([(p.id,p.title,p.goal,p.status,str(p.planned_due_at),str(p.manual_due_at)) for p in projects]) + '\nЗадачи: ' + str(task_context)
        context += '\nОценка родителя агрегирована по детям. Для общего времени считай только листья, не суммируй родителей с детьми. Равные дедлайны не доказывают пересечение работ.'
        messages = [{'role':'system','content':SYSTEM+'\n'+context}] + [{'role':m.role,'content':m.content} for m in history] + [{'role':'user','content':text}]
    result = await client.chat.completions.create(model=os.getenv('OPENAI_MODEL','gpt-4.1-mini'), messages=messages, temperature=0.3)
    output = result.choices[0].message.content or 'Не удалось сформировать ответ.'
    with Session.begin() as s:
        s.add(Message(role='user', content=text[:12000])); s.add(Message(role='assistant', content=output[:12000]))
    return output

async def classify_intent(text):
    """Определяет намерение. Не изменяет базу данных."""
    instructions = (
        'Ты классификатор намерений пользователя Telegram-бота '
        'для управления проектами. '
        'Определи намерение по смыслу сообщения, а не по ключевым словам. '
        'Верни только JSON с одним полем intent. '
        'Допустимые значения: '
        'create_task, update_task, question, clarify. '
        'create_task — пользователь явно поручает выполнить работу '
        'или просит поставить новую задачу. '
        'update_task — пользователь просит изменить существующую задачу, '
        'её срок, статус или приоритет. '
        'question — пользователь задаёт вопрос, обсуждает планы '
        'или запрашивает информацию без поручения. '
        'clarify — непонятно, хочет ли пользователь создать задачу. '
        'Не путай обсуждение возможной работы с поручением. '
        'Если сообщение содержит одновременно вопрос и явное поручение, '
        'выбирай create_task. '
        'Просьбу напомнить выполнить конкретную работу классифицируй как create_task. Например: напомни завтра подготовить отчёт ? create_task. Напоминание о событии без поручения не обязательно является созданием задачи. '
        'Примеры: '
        '"Подготовь три объявления до пятницы" — create_task. '
        '"Нужно завтра проверить фотографии" — create_task. '
        '"Как лучше подготовить объявления?" — question. '
        '"Надо бы когда-нибудь заняться сайтом" — clarify. '
        '"Перенеси задачу №3 на пятницу" — update_task.'
    )

    with Session() as s:
        history = (
            s.query(Message)
            .order_by(Message.id.desc())
            .limit(12)
            .all()[::-1]
        )

    history_messages = [
        {'role': m.role, 'content': m.content}
        for m in history
        if m.role in ('user', 'assistant')
    ]

    response = await client.chat.completions.create(
        model=os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'),
        messages=[
            {'role': 'system', 'content': instructions},
            *history_messages,
            {'role': 'user', 'content': text}
        ],
        response_format={'type': 'json_object'},
        temperature=0
    )

    data = json.loads(response.choices[0].message.content or '{}')
    intent = data.get('intent')

    allowed = {'create_task', 'update_task', 'question', 'clarify'}

    if intent not in allowed:
        raise ValueError(f'Некорректное намерение: {intent}')

    return intent

async def find_semantic_duplicates(new_tasks, existing_tasks):
    """Never expose tasks from another project to a duplicate comparison."""
    matches = []
    for project_id in dict.fromkeys(t['project_id'] for t in new_tasks):
        proposed = [t for t in new_tasks if t['project_id'] == project_id]
        existing = [t for t in existing_tasks if t['project_id'] == project_id]
        matches.extend(await _find_project_duplicates(proposed, existing))
    return list(dict.fromkeys(matches))


async def _find_project_duplicates(new_tasks, existing_tasks):
    """Находит возможные смысловые дубликаты. Базу данных не изменяет."""
    if not new_tasks or not existing_tasks:
        return []

    instructions = (
        'Ты проверяешь задачи на смысловое дублирование. '
        'Сравни новые задачи с существующими. '
        'Дубликат — это задача, выполнение которой полностью '
        'или существенно повторяет уже существующую работу. '
        'Учитывай цель, ожидаемый результат и критерии готовности. '
        'Разные этапы одного проекта не обязательно являются дубликатами. '
        'Если существующая задача шире новой и лишь включает её '
        'как один из этапов, не считай это полным дубликатом. '
        'Например, "Подготовить материалы для рекламы" и '
        '"Написать три рекламных текста" — не полные дубликаты. '
        'Если две задачи требуют практически одинакового конечного '
        'результата, считай их вероятными дубликатами, '
        'даже если формулировки различаются. '
        'Сравнивай только задачи одного проекта. '
        'Если совпадение сомнительно, не объявляй его доказанным. '
        'Верни только JSON: {"duplicate_ids": []}. '
        'В массив включай ID существующих задач '
        'с вероятным существенным совпадением.'
    )

    payload = json.dumps(
        {
            'new_tasks': new_tasks,
            'existing_tasks': existing_tasks
        },
        ensure_ascii=False
    )

    response = await client.chat.completions.create(
        model=os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'),
        messages=[
            {'role': 'system', 'content': instructions},
            {'role': 'user', 'content': payload}
        ],
        response_format={'type': 'json_object'},
        temperature=0
    )

    data = json.loads(response.choices[0].message.content or '{}')
    ids = data.get('duplicate_ids', [])

    if not isinstance(ids, list):
        raise ValueError('Некорректный результат проверки дубликатов.')

    allowed_ids = {t['id'] for t in existing_tasks}
    if any(type(i) is not int or i not in allowed_ids for i in ids):
        raise ValueError('Модель вернула неизвестные ID задач.')

    return list(dict.fromkeys(ids))

async def draft_tasks(text):
    """Propose tasks only. This function never writes to the database."""
    now=datetime.now(ZoneInfo('Europe/Moscow'))
    with Session() as s:
        projects=s.query(Project).filter_by(status='active').order_by(Project.id.desc()).limit(20).all()
        context=[{'id':p.id,'title':p.title} for p in projects]
        with Session() as s:
             history = (
                s.query(Message)
                .order_by(Message.id.desc())
                .limit(12)
                .all()[::-1]
        )

        history_messages = [
        {'role': m.role, 'content': m.content}
        for m in history
        if m.role in ('user', 'assistant')
    ]
    instructions=(
        'Ты извлекаешь задачи из сообщения владельца для предварительного согласования. '
        'Верни строго JSON объект с ключом tasks (массив 1-8 объектов). '
        'Каждый объект: title (строка), criteria (проверяемый результат), priority (P1/P2/P3/P4), '
        'due (YYYY-MM-DD HH:MM, московское время), project_id (ID активного проекта или null), effort_breakdown (array of work stages, each with name and minutes), estimate_minutes must equal the sum of all stage minutes. Break complex tasks into 2-8 meaningful stages; simple tasks may have one stage. '
        'Не выдумывай дополнительные задачи, отсутствующие в запросе. '
        'Используй историю переписки только для уточнения смысла текущего поручения. '
        'Если пользователь ссылается на предыдущие сообщения, '
        'создавай черновик только тогда, когда можно однозначно определить поручение. '
        'Если обсуждалось несколько возможных задач и пользователь не уточнил, '
        'какую именно нужно создать, верни JSON {"tasks": []}. '
        'Не придумывай название, содержание или проект задачи при недостатке информации. '
        'При указании «через N дней» вычисляй дату от текущей московской даты. '
        'Если время не задано, используй 18:00 МСК. Если срок не задан, используй 3 дня. '
        'Если проект явно назван, выбери его из списка; иначе null. '
        'Не считай задачи выполненными. Сейчас: '+now.strftime('%Y-%m-%d %H:%M')+'. '
        'Активные проекты: '+json.dumps(context,ensure_ascii=False)
    )
    response=await client.chat.completions.create(
        model=os.getenv('OPENAI_MODEL','gpt-4.1-mini'),
        messages=[
            {'role': 'system', 'content': instructions},
            *history_messages,
            {'role': 'user', 'content': text}
],
        response_format={'type':'json_object'},temperature=0)
    data=json.loads(response.choices[0].message.content or '{}')
    tasks=data.get('tasks',[])
    if not isinstance(tasks,list) or not 1<=len(tasks)<=8: raise ValueError('Не удалось составить 1–8 задач.')
    ids={p['id'] for p in context}
    result=[]
    for t in tasks:
        title=str(t['title']).strip()[:300]; criteria=str(t['criteria']).strip()[:2000]
        priority=str(t['priority']).upper(); due=str(t['due'])
        from .workflow import future_due
        future_due(due)
        breakdown=t.get('effort_breakdown')
        if not isinstance(breakdown, list) or not 1 <= len(breakdown) <= 8:
            raise ValueError('Invalid effort_breakdown')

        validated_breakdown=[]
        for stage in breakdown:
            if not isinstance(stage, dict):
                raise ValueError('Invalid effort stage')
            name=stage.get('name')
            minutes=stage.get('minutes')
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
                raise ValueError('Invalid effort stage name')
            if type(minutes) is not int or not 1 <= minutes <= 60000:
                raise ValueError('Invalid effort stage minutes')
            validated_breakdown.append({
                'name': name.strip(),
                'minutes': minutes
            })

        estimate=sum(stage['minutes'] for stage in validated_breakdown)
        if type(t.get('estimate_minutes')) is not int or t['estimate_minutes'] != estimate:
            raise ValueError('Оценка не совпадает с суммой этапов.')
        if estimate > 60000:
            raise ValueError('Total effort estimate exceeds limit')
        pid=t.get('project_id')
        if pid is not None and type(pid) is not int:
            raise ValueError('Некорректный ID проекта.')
        if not title or not criteria or priority not in ('P1','P2','P3','P4') or (pid is not None and pid not in ids):
            raise ValueError('Модель предложила некорректную задачу.')
        result.append({'title':title,'criteria':criteria,'priority':priority,'due':due,'project_id':pid,'estimate_minutes':estimate,'effort_breakdown':validated_breakdown})
    return result


async def assess_evidence(title, criteria, proof):
    """Advisory assessment only; never changes task status or verification."""
    instructions = (
        'Ты проверяешь предоставленный результат по критериям задачи. '
        'Не утверждай, что документ или ссылка проверены, если содержимое не предоставлено. '
        'Если дано только описание, оцени полноту описания и запроси проверяемые материалы. '
        'Не объявляй задачу завершённой и не выдавай окончательного вердикта. '
        'Кратко по-русски: 1) что подтверждено текстом, 2) что не подтверждено, '
        '3) какие материалы запросить у владельца. Не более 1000 символов.'
    )
    payload=json.dumps({'task':title,'criteria':criteria,'submitted_evidence':proof},ensure_ascii=False)
    response=await client.chat.completions.create(
        model=os.getenv('OPENAI_MODEL','gpt-4.1-mini'),
        messages=[{'role':'system','content':instructions},{'role':'user','content':payload}],
        temperature=0)
    return (response.choices[0].message.content or 'Не удалось оценить результат.')[:1600]


async def propose_next_step(task_info, user_request):
    """Advisory planning; does not write anything to PostgreSQL."""
    prompt = (
        'Ты опытный проектный менеджер. По актуальной карточке задачи и запросу владельца '
        'предложи ровно ОДИН небольшой конкретный первый шаг, который можно выполнить за 30–90 минут. '
        'Верни строго JSON-объект с полями: explanation (почему именно этот шаг), '
        'title (краткое название подзадачи), criteria (измеримый проверяемый результат). '
        'Не выдумывай выполненную работу, не обещай создание задачи, не изменяй сроки. '
        'Если информации мало, сделай разумное предложение и явно обозначь допущение. '
        'Все строки на русском языке, не более 350 символов каждая.'
    )
    response = await client.chat.completions.create(
        model=os.getenv('OPENAI_MODEL','gpt-4.1-mini'),
        messages=[
            {'role':'system','content':prompt},
            {'role':'user','content':json.dumps(
                {'task':task_info,'request':user_request},ensure_ascii=False)}
        ],
        response_format={'type':'json_object'},temperature=0.2
    )
    data=json.loads(response.choices[0].message.content or '{}')
    result={}
    for key, limit in (('explanation',350),('title',180),('criteria',350)):
        value=data.get(key)
        if not isinstance(value,str) or not value.strip():
            raise ValueError('Модель не предложила проверяемый следующий шаг.')
        result[key]=value.strip()[:limit]
    return result
