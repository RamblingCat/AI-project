--
-- PostgreSQL database dump
--

\restrict RXpANnXEFOeKHnrySNaYzIg7XFpaGJ0RAEh790WrjhtM4v8IQaxsYBn5fQmBRsI

-- Dumped from database version 16.15 (Debian 16.15-1.pgdg13+2)
-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg13+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: event_log; Type: TABLE; Schema: public; Owner: pm
--

CREATE TABLE public.event_log (
    id integer NOT NULL,
    kind character varying(60) NOT NULL,
    object_id integer,
    detail json,
    created_at timestamp with time zone
);


ALTER TABLE public.event_log OWNER TO pm;

--
-- Name: event_log_id_seq; Type: SEQUENCE; Schema: public; Owner: pm
--

CREATE SEQUENCE public.event_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.event_log_id_seq OWNER TO pm;

--
-- Name: event_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: pm
--

ALTER SEQUENCE public.event_log_id_seq OWNED BY public.event_log.id;


--
-- Name: evidence; Type: TABLE; Schema: public; Owner: pm
--

CREATE TABLE public.evidence (
    id integer NOT NULL,
    task_id integer NOT NULL,
    content text NOT NULL,
    verified boolean,
    created_at timestamp with time zone
);


ALTER TABLE public.evidence OWNER TO pm;

--
-- Name: evidence_id_seq; Type: SEQUENCE; Schema: public; Owner: pm
--

CREATE SEQUENCE public.evidence_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.evidence_id_seq OWNER TO pm;

--
-- Name: evidence_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: pm
--

ALTER SEQUENCE public.evidence_id_seq OWNED BY public.evidence.id;


--
-- Name: messages; Type: TABLE; Schema: public; Owner: pm
--

CREATE TABLE public.messages (
    id integer NOT NULL,
    role character varying(15) NOT NULL,
    content text NOT NULL,
    created_at timestamp with time zone
);


ALTER TABLE public.messages OWNER TO pm;

--
-- Name: messages_id_seq; Type: SEQUENCE; Schema: public; Owner: pm
--

CREATE SEQUENCE public.messages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.messages_id_seq OWNER TO pm;

--
-- Name: messages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: pm
--

ALTER SEQUENCE public.messages_id_seq OWNED BY public.messages.id;


--
-- Name: notifications; Type: TABLE; Schema: public; Owner: pm
--

CREATE TABLE public.notifications (
    id integer NOT NULL,
    key character varying(150) NOT NULL,
    created_at timestamp with time zone
);


ALTER TABLE public.notifications OWNER TO pm;

--
-- Name: notifications_id_seq; Type: SEQUENCE; Schema: public; Owner: pm
--

CREATE SEQUENCE public.notifications_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.notifications_id_seq OWNER TO pm;

--
-- Name: notifications_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: pm
--

ALTER SEQUENCE public.notifications_id_seq OWNED BY public.notifications.id;


--
-- Name: projects; Type: TABLE; Schema: public; Owner: pm
--

CREATE TABLE public.projects (
    id integer NOT NULL,
    title character varying(250) NOT NULL,
    goal text,
    success_criteria text,
    status character varying(30) NOT NULL,
    scope character varying(20),
    created_at timestamp with time zone
);


ALTER TABLE public.projects OWNER TO pm;

--
-- Name: projects_id_seq; Type: SEQUENCE; Schema: public; Owner: pm
--

CREATE SEQUENCE public.projects_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.projects_id_seq OWNER TO pm;

--
-- Name: projects_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: pm
--

ALTER SEQUENCE public.projects_id_seq OWNED BY public.projects.id;


--
-- Name: settings; Type: TABLE; Schema: public; Owner: pm
--

CREATE TABLE public.settings (
    key character varying(80) NOT NULL,
    value text NOT NULL
);


ALTER TABLE public.settings OWNER TO pm;

--
-- Name: tasks; Type: TABLE; Schema: public; Owner: pm
--

CREATE TABLE public.tasks (
    id integer NOT NULL,
    project_id integer,
    title character varying(300) NOT NULL,
    description text,
    criteria text,
    status character varying(30),
    priority character varying(2),
    due_at timestamp with time zone,
    estimate_minutes integer,
    reschedules integer,
    last_reminded_at timestamp with time zone,
    last_checked_at timestamp with time zone,
    created_at timestamp with time zone
);


ALTER TABLE public.tasks OWNER TO pm;

--
-- Name: tasks_id_seq; Type: SEQUENCE; Schema: public; Owner: pm
--

CREATE SEQUENCE public.tasks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.tasks_id_seq OWNER TO pm;

--
-- Name: tasks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: pm
--

ALTER SEQUENCE public.tasks_id_seq OWNED BY public.tasks.id;


--
-- Name: event_log id; Type: DEFAULT; Schema: public; Owner: pm
--

ALTER TABLE ONLY public.event_log ALTER COLUMN id SET DEFAULT nextval('public.event_log_id_seq'::regclass);


--
-- Name: evidence id; Type: DEFAULT; Schema: public; Owner: pm
--

ALTER TABLE ONLY public.evidence ALTER COLUMN id SET DEFAULT nextval('public.evidence_id_seq'::regclass);


--
-- Name: messages id; Type: DEFAULT; Schema: public; Owner: pm
--

ALTER TABLE ONLY public.messages ALTER COLUMN id SET DEFAULT nextval('public.messages_id_seq'::regclass);


--
-- Name: notifications id; Type: DEFAULT; Schema: public; Owner: pm
--

ALTER TABLE ONLY public.notifications ALTER COLUMN id SET DEFAULT nextval('public.notifications_id_seq'::regclass);


--
-- Name: projects id; Type: DEFAULT; Schema: public; Owner: pm
--

ALTER TABLE ONLY public.projects ALTER COLUMN id SET DEFAULT nextval('public.projects_id_seq'::regclass);


--
-- Name: tasks id; Type: DEFAULT; Schema: public; Owner: pm
--

ALTER TABLE ONLY public.tasks ALTER COLUMN id SET DEFAULT nextval('public.tasks_id_seq'::regclass);


--
-- Data for Name: event_log; Type: TABLE DATA; Schema: public; Owner: pm
--

COPY public.event_log (id, kind, object_id, detail, created_at) FROM stdin;
1	project_draft	1	{}	2026-10-29 22:21:35.289003+00
2	project_approved	1	{}	2026-10-29 22:22:36.255138+00
3	task_created	1	{}	2026-10-29 22:24:19.001822+00
4	evidence_submitted	1	{}	2026-10-29 22:25:22.203655+00
5	evidence_rejected	1	{"reason": "\\u0422\\u0435\\u0441\\u0442\\u043e\\u0432\\u0430\\u044f \\u0441\\u0434\\u0430\\u0447\\u0430 \\u043e\\u0442\\u043a\\u043b\\u043e\\u043d\\u0435\\u043d\\u0430. \\u041d\\u0435 \\u043f\\u0440\\u0435\\u0434\\u043e\\u0441\\u0442\\u0430\\u0432\\u043b\\u0435\\u043d\\u044b 10 \\u0444\\u043e\\u0442\\u043e\\u0433\\u0440\\u0430\\u0444\\u0438\\u0439 \\u043f\\u0440\\u043e\\u0435\\u043a\\u0442\\u043e\\u0432, 3 \\u0440\\u0435\\u043a\\u043b\\u0430\\u043c\\u043d\\u044b\\u0445 \\u0442\\u0435\\u043a\\u0441\\u0442\\u0430 \\u0438 \\u0441\\u043f\\u0438\\u0441\\u043e\\u043a \\u043f\\u0440\\u0435\\u0438\\u043c\\u0443\\u0449\\u0435\\u0441\\u0442\\u0432 \\u043a\\u043e\\u043c\\u043f\\u0430\\u043d\\u0438\\u0438. \\u0417\\u0430\\u0434\\u0430\\u0447\\u0430 \\u0442\\u0440\\u0435\\u0431\\u0443\\u0435\\u0442 \\u0432\\u044b\\u043f\\u043e\\u043b\\u043d\\u0435\\u043d\\u0438\\u044f."}	2026-10-29 22:25:58.517197+00
6	task_created	2	{"source": "approved_chat_draft"}	2026-10-30 06:44:18.665437+00
7	task_created	3	{"source": "approved_chat_draft"}	2026-10-30 06:44:18.671618+00
8	task_created	4	{"source": "approved_chat_draft"}	2026-10-30 06:44:18.675574+00
9	deadline_moved	2	{"old": "2026-11-03T15:00:00+00:00", "new": "2026-11-04T15:00:00+00:00", "source": "approved_chat_move"}	2026-10-30 06:56:05.380446+00
10	evidence_submitted	4	{}	2026-10-30 06:58:18.380203+00
11	evidence_rejected	4	{"reason": "\\u0422\\u0435\\u0441\\u0442\\u043e\\u0432\\u0430\\u044f \\u0441\\u0434\\u0430\\u0447\\u0430. \\u0424\\u0430\\u043a\\u0442\\u0438\\u0447\\u0435\\u0441\\u043a\\u0430\\u044f \\u0440\\u0430\\u0431\\u043e\\u0442\\u0430 \\u043d\\u0435 \\u0432\\u044b\\u043f\\u043e\\u043b\\u043d\\u0435\\u043d\\u0430, \\u0437\\u0430\\u0434\\u0430\\u0447\\u0430 \\u0434\\u043e\\u043b\\u0436\\u043d\\u0430 \\u043e\\u0441\\u0442\\u0430\\u0432\\u0430\\u0442\\u044c\\u0441\\u044f \\u043e\\u0442\\u043a\\u0440\\u044b\\u0442\\u043e\\u0439."}	2026-10-30 06:58:46.817682+00
12	evidence_submitted	4	{}	2026-10-30 07:11:13.88284+00
13	evidence_rejected	4	{"reason": "AI-\\u043f\\u0440\\u043e\\u0432\\u0435\\u0440\\u043a\\u0430 \\u043f\\u043e\\u0434\\u0442\\u0432\\u0435\\u0440\\u0434\\u0438\\u043b\\u0430 \\u043e\\u0442\\u0441\\u0443\\u0442\\u0441\\u0442\\u0432\\u0438\\u0435 \\u0433\\u043e\\u0442\\u043e\\u0432\\u043e\\u0433\\u043e \\u0441\\u043f\\u0438\\u0441\\u043a\\u0430 \\u043f\\u0440\\u0435\\u0438\\u043c\\u0443\\u0449\\u0435\\u0441\\u0442\\u0432. \\u0417\\u0430\\u0434\\u0430\\u0447\\u0430 \\u043d\\u0435 \\u0432\\u044b\\u043f\\u043e\\u043b\\u043d\\u0435\\u043d\\u0430."}	2026-10-30 07:12:47.665275+00
14	task_created	5	{"source": "approved_next_step", "parent_task_id": 3}	2026-10-30 07:19:11.582749+00
15	deadline_moved	4	{"old": "2026-11-03T15:00:00+00:00", "new": "2026-11-01T15:00:00+00:00", "source": "approved_batch_move"}	2026-10-30 07:42:54.95152+00
16	deadline_moved	5	{"old": "2026-11-03T15:00:00+00:00", "new": "2026-10-31T15:00:00+00:00", "source": "approved_batch_move"}	2026-10-30 07:42:54.951528+00
17	task_created	6	{"source": "approved_chat_draft"}	2026-11-01 08:25:41.474136+00
18	deadline_moved	6	{"old": "2026-11-10T15:00:00+00:00", "new": "2026-11-12T15:00:00+00:00", "source": "approved_chat_move"}	2026-11-01 08:27:23.246818+00
19	task_status_changed	6	{"old": "planned", "new": "in_progress", "source": "approved_chat_status"}	2026-11-01 08:43:26.292083+00
20	task_status_changed	2	{"old": "planned", "new": "in_progress", "source": "approved_chat_status"}	2026-11-01 08:48:26.599964+00
21	task_priority_changed	6	{"old": "P2", "new": "P1", "source": "approved_chat_priority"}	2026-11-01 17:14:05.79919+00
22	task_priority_changed	6	{"old": "P1", "new": "P2", "source": "approved_chat_priority"}	2026-11-01 17:28:40.789267+00
23	task_priority_changed	6	{"old": "P2", "new": "P1", "source": "approved_chat_priority"}	2026-11-01 17:30:27.058249+00
\.


--
-- Data for Name: evidence; Type: TABLE DATA; Schema: public; Owner: pm
--

COPY public.evidence (id, task_id, content, verified, created_at) FROM stdin;
1	1	ТЕСТ: рекламные материалы не подготовлены. Проверка механизма сдачи задачи.	f	2026-10-29 22:25:22.207946+00
2	4	ТЕСТ: проверка механизма сдачи результата, фактическая работа ещё не выполнена.	f	2026-10-30 06:58:18.383739+00
3	4	Я подумал над преимуществами компании, но пока не составил и не оформил список.	f	2026-10-30 07:11:13.8892+00
\.


--
-- Data for Name: messages; Type: TABLE DATA; Schema: public; Owner: pm
--

COPY public.messages (id, role, content, created_at) FROM stdin;
1	user	Привет! Ты мой AI Project Manager. Кратко расскажи, как ты будешь помогать мне управлять рабочими и личными проектами.	2026-10-29 22:04:00.192869+00
2	assistant	Привет! Я помогу разбивать проекты на этапы с понятными сроками и критериями готовности, контролировать выполнение задач, выявлять и устранять препятствия, предлагать реалистичные планы и напоминать о дедлайнах. Для сохранения и утверждения проектов и задач используй команды /project, /task и /approve. Готов начать?	2026-10-29 22:04:00.192876+00
3	user	Запуск тестовой рекламы мебельного производства «Омега26» в Пятигорске и КМВ. Цель — получить новые заказы на кухни, шкафы и другую корпусную мебель. Рекламный бюджет — 100 000 рублей в месяц. Нужно подготовить рекламные материалы, запустить тест и оценить результаты.	2026-10-29 22:05:10.764812+00
4	assistant	Проект понятен. Предлагаю разбить на этапы:\n\n1. Подготовка рекламных материалов (тексты, креативы) — 3 рабочих дня.\n2. Запуск тестовой рекламы в Пятигорске и КМВ — 1 день.\n3. Мониторинг и сбор данных — 14 дней.\n4. Анализ результатов и выводы — 2 дня.\n\nКритерии готовности: готовые материалы, запущенная реклама с бюджетом, отчет по результатам.\n\nПодтверждаешь план? Если да, предложу команды для создания проекта и задач.	2026-10-29 22:05:10.764817+00
5	user	Сегодня утверждаю три приоритета по проекту Омега26: 1) отобрать 10 фотографий готовой мебели; 2) подготовить 3 рекламных текста; 3) составить список преимуществ компании. Зафиксируй эти приоритеты в проекте и покажи, что именно ты сохранил.	2026-10-30 06:31:37.309861+00
6	assistant	Приоритеты принял, но для фиксации нужно создать задачи через команду /task. Предлагаю так:\n\n1. /task Отобрать 10 фотографий готовой мебели — срок 3 дня.\n2. /task Подготовить 3 рекламных текста — срок 3 дня.\n3. /task Составить список преимуществ компании — срок 3 дня.\n\nПосле создания покажу сохранённые задачи. Готов?	2026-10-30 06:31:37.309867+00
7	user	Создай в проекте «Запуск рекламы Омега26» три задачи:\n1. Отобрать 10 фотографий реализованных проектов.\n2. Подготовить 3 рекламных текста.\n3. Составить список преимуществ компании.\nДля всех задач установи срок 3 ноября 2026 года, 18:00 МСК, приоритет P1.\nСначала покажи задачи на согласование. Без моего подтверждения ничего не сохраняй.	2026-10-30 06:40:26.832094+00
8	assistant	Вот три задачи для согласования:\n\n1. Отобрать 10 фотографий реализованных проектов, срок 2026-11-03 18:00 МСК, приоритет P1.\n2. Подготовить 3 рекламных текста, срок 2026-11-03 18:00 МСК, приоритет P1.\n3. Составить список преимуществ компании, срок 2026-11-03 18:00 МСК, приоритет P1.\n\nПодтверждаете создание этих задач?	2026-10-30 06:40:26.8321+00
9	user	подтверждаю создание всех трех задач	2026-10-30 06:41:26.207752+00
10	assistant	Задачи приняты к созданию. Чтобы сохранить их в системе, пожалуйста, выполните команды:\n\n/task Отобрать 10 фотографий реализованных проектов /due 2026-11-03T18:00 /priority P1 /project 1  \n/task Подготовить 3 рекламных текста /due 2026-11-03T18:00 /priority P1 /project 1  \n/task Составить список преимуществ компании /due 2026-11-03T18:00 /priority P1 /project 1\n\nПосле выполнения сообщите, чтобы я проверил сохранение.	2026-10-30 06:41:26.207759+00
11	user	Перенеси срок задачи №2 на 4 ноября 2026 года, 18:00 МСК. Сначала запроси моё подтверждение.	2026-10-30 06:46:47.286369+00
12	assistant	Предлагаю перенести срок задачи №2 «Подготовить 3 рекламных текста» с 3 ноября 2026, 18:00 МСК на 4 ноября 2026, 18:00 МСК. Подтверждаете изменение?	2026-10-30 06:46:47.286374+00
13	user	Отмена. Ничего не изменяй. Покажи точное название и текущий срок задачи №2 из базы данных, а не по памяти диалога.	2026-10-30 06:47:46.301345+00
14	assistant	Задача №2: «Подготовить 3 рекламных текста», срок 2026-11-03 15:00:00+00:00 (что соответствует 18:00 МСК 3 ноября 2026). Нужно что-то изменить?	2026-10-30 06:47:46.301369+00
15	user	Я согласен с предложенным планом по задачам №3, №4 и №5. Подготовь изменение сроков всех трёх задач и покажи мне подтверждение перед сохранением. Без моего подтверждения ничего не меняй.	2026-10-30 07:30:22.897459+00
16	assistant	Напомню текущие задачи и сроки:\n\n- №3 «Подготовить 3 рекламных текста» — срок 2026-11-03 15:00 UTC (18:00 МСК 3 ноября 2026)  \n- №4 «Составить список преимуществ компании» — срок 2026-11-03 15:00 UTC (18:00 МСК 3 ноября 2026)  \n- №5 «Собрать информацию о целевой аудитории и ключевых преимуществах продукта» — срок 2026-11-03 15:00 UTC (18:00 МСК 3 ноября 2026)  \n\nКакой новый срок предлагаете установить для всех трёх?	2026-10-30 07:30:22.897466+00
17	user	Согласен с планом по задачам №3, №4, №5. Подготовь изменение сроков.	2026-10-30 07:35:07.280586+00
18	assistant	Уточните, пожалуйста, на какой конкретно срок нужно перенести задачи №3, №4 и №5? Без точной даты не могу подготовить изменения.	2026-10-30 07:35:07.280592+00
19	user	Как лучше подготовить три рекламных объявления для Омега26, чтобы они привлекали клиентов?	2026-11-01 07:50:18.679254+00
20	assistant	Для эффективных рекламных объявлений Омега26 рекомендую:\n\n1. Чётко выделить 3–5 ключевых преимуществ компании (качество, индивидуальный подход, сроки, гарантия).  \n2. Использовать язык, понятный целевой аудитории, с акцентом на их потребности и боли.  \n3. Включить призыв к действию (звонок, заявка, консультация).  \n4. Добавить визуальные элементы — фото реализованных проектов.  \n5. Сделать тексты короткими, ёмкими и запоминающимися.\n\nДля следующего шага: хотите, чтобы я помог составить черновики трёх рекламных текстов?	2026-11-01 07:50:18.679264+00
21	user	Нам нужно подготовить рекламные объявления для Омега26. Как лучше организовать эту работу?	2026-11-01 07:52:19.029065+00
22	assistant	Организуем работу так:\n\n1. Сначала соберите и оформите список ключевых преимуществ компании и продукта (задачи №4 и №5). Это база для текстов.  \n2. Параллельно отберите 10 качественных фотографий реализованных проектов (задача №2).  \n3. На основе преимуществ и фото подготовьте 3 рекламных текста (задача №3).  \n4. Проведите проверку и корректировку материалов.  \n5. Утвердите финальные объявления и запустите рекламу.\n\nДля начала подтвердите, что задачи №2, №3, №4 и №5 актуальны и сроки приемлемы.	2026-11-01 07:52:19.02907+00
23	user	Давай обсудим две задачи: подготовить рекламные объявления для Омега26 и разработать новый сайт. Какую лучше выполнить первой?	2026-11-01 08:07:40.762362+00
24	assistant	Приоритет отдайте задаче «Подготовить рекламные объявления для Омега26». Это даст быстрый результат — новые заказы и обратную связь от клиентов. Разработка сайта — более длительный процесс, который можно вести параллельно или после запуска рекламы. Подтвердите, с чего начнём?	2026-11-01 08:07:40.76237+00
\.


--
-- Data for Name: notifications; Type: TABLE DATA; Schema: public; Owner: pm
--

COPY public.notifications (id, key, created_at) FROM stdin;
1	morning:2026-10-30	2026-10-30 06:30:00.063648+00
2	overdue:5:2026-10-31T15:00:00+00:00	2026-11-01 06:35:32.935556+00
\.


--
-- Data for Name: projects; Type: TABLE DATA; Schema: public; Owner: pm
--

COPY public.projects (id, title, goal, success_criteria, status, scope, created_at) FROM stdin;
1	Запуск рекламы Омега26	Получить новые заказы на корпусную мебель в Пятигорске и КМВ с рекламным бюджетом 100000 рублей в месяц	Провести рекламный тест, собрать статистику по обращениям, стоимости заявок и заключённым договорам, подготовить итоговый отчёт	active	work	2026-10-29 22:21:35.267328+00
\.


--
-- Data for Name: settings; Type: TABLE DATA; Schema: public; Owner: pm
--

COPY public.settings (key, value) FROM stdin;
\.


--
-- Data for Name: tasks; Type: TABLE DATA; Schema: public; Owner: pm
--

COPY public.tasks (id, project_id, title, description, criteria, status, priority, due_at, estimate_minutes, reschedules, last_reminded_at, last_checked_at, created_at) FROM stdin;
1	1	Подготовить материалы для рекламы Омега26		Собраны минимум 10 фотографий реализованных проектов, подготовлены 3 рекламных текста и список преимуществ компании; результат предоставлен на проверку	in_progress	P1	2026-11-03 15:00:00+00	\N	0	\N	\N	2026-10-29 22:24:18.996167+00
3	1	Подготовить 3 рекламных текста		Созданы и оформлены 3 рекламных текста	planned	P1	2026-11-03 15:00:00+00	\N	0	\N	\N	2026-10-30 06:44:18.668981+00
4	1	Составить список преимуществ компании		Составлен и оформлен список преимуществ компании	in_progress	P1	2026-11-01 15:00:00+00	\N	1	\N	\N	2026-10-30 06:44:18.673176+00
5	1	Собрать информацию о целевой аудитории и ключевых преимуществах продукта		Собран и оформлен список из 5-7 ключевых характеристик целевой аудитории и 3-5 преимуществ продукта	planned	P1	2026-10-31 15:00:00+00	\N	1	\N	\N	2026-10-30 07:19:11.575419+00
2	1	Отобрать 10 фотографий		Выбрано и собрано 10 фотографий	in_progress	P1	2026-11-04 15:00:00+00	\N	1	\N	\N	2026-10-30 06:44:18.660731+00
6	\N	Провести тестирование системы распознавания поручений		Проверить 5 разных формулировок и записать результаты	in_progress	P1	2026-11-12 15:00:00+00	\N	1	\N	\N	2026-11-01 08:25:41.450377+00
\.


--
-- Name: event_log_id_seq; Type: SEQUENCE SET; Schema: public; Owner: pm
--

SELECT pg_catalog.setval('public.event_log_id_seq', 23, true);


--
-- Name: evidence_id_seq; Type: SEQUENCE SET; Schema: public; Owner: pm
--

SELECT pg_catalog.setval('public.evidence_id_seq', 3, true);


--
-- Name: messages_id_seq; Type: SEQUENCE SET; Schema: public; Owner: pm
--

SELECT pg_catalog.setval('public.messages_id_seq', 24, true);


--
-- Name: notifications_id_seq; Type: SEQUENCE SET; Schema: public; Owner: pm
--

SELECT pg_catalog.setval('public.notifications_id_seq', 2, true);


--
-- Name: projects_id_seq; Type: SEQUENCE SET; Schema: public; Owner: pm
--

SELECT pg_catalog.setval('public.projects_id_seq', 1, true);


--
-- Name: tasks_id_seq; Type: SEQUENCE SET; Schema: public; Owner: pm
--

SELECT pg_catalog.setval('public.tasks_id_seq', 6, true);


--
-- Name: event_log event_log_pkey; Type: CONSTRAINT; Schema: public; Owner: pm
--

ALTER TABLE ONLY public.event_log
    ADD CONSTRAINT event_log_pkey PRIMARY KEY (id);


--
-- Name: evidence evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: pm
--

ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_pkey PRIMARY KEY (id);


--
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: public; Owner: pm
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);


--
-- Name: notifications notifications_key_key; Type: CONSTRAINT; Schema: public; Owner: pm
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_key_key UNIQUE (key);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: pm
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);


--
-- Name: projects projects_pkey; Type: CONSTRAINT; Schema: public; Owner: pm
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_pkey PRIMARY KEY (id);


--
-- Name: settings settings_pkey; Type: CONSTRAINT; Schema: public; Owner: pm
--

ALTER TABLE ONLY public.settings
    ADD CONSTRAINT settings_pkey PRIMARY KEY (key);


--
-- Name: tasks tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: pm
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_pkey PRIMARY KEY (id);


--
-- Name: evidence evidence_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: pm
--

ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);


--
-- Name: tasks tasks_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: pm
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);


--
-- PostgreSQL database dump complete
--

\unrestrict RXpANnXEFOeKHnrySNaYzIg7XFpaGJ0RAEh790WrjhtM4v8IQaxsYBn5fQmBRsI

