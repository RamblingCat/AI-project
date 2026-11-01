# Telegram AI Project Manager — MVP 1.0

## Features
- Owner-only Telegram bot (text only), Russian interface.
- Project drafts and explicit approval.
- Tasks with deadlines, priorities, acceptance criteria, optional project link.
- Evidence submission and **manual owner verification** (no automatic artifact inspection).
- PostgreSQL persistence and event log.
- Proactive morning 09:00, evening 18:30 and 5-minute deadline polling (Europe/Moscow).
- `/pause` and `/resume`.
- Google Calendar read-only events (optional).
- OpenAI conversational planning; **chat does not automatically write projects/tasks**. Use commands to persist them.

## Start
1. Create a Telegram bot with @BotFather and obtain BOT_TOKEN. Start a conversation with it.
2. Get your numeric Telegram user ID from a trusted method; use it for OWNER_TELEGRAM_ID.
3. Copy `.env.example` to `.env`. Set BOT_TOKEN, OPENAI_API_KEY, OWNER_TELEGRAM_ID and a strong database password in DATABASE_URL.
4. Create a separate `.env` variable `POSTGRES_PASSWORD` with the **same** password as in DATABASE_URL. URL-encode special characters in DATABASE_URL or use an alphanumeric password.
5. `mkdir -p secrets`.
6. Optional Calendar: create Google Cloud OAuth desktop client, enable Google Calendar API, download client JSON as `secrets/google_client.json`. On your local machine: `pip install -r requirements.txt`, then `python google_auth.py`. Copy `secrets/google_token.json` into the server `secrets/` folder. The token has calendar read-only scope.
7. `docker compose up -d --build` and `docker compose logs -f bot`.
8. In Telegram send `/start`, `/help`, then create a project and task.

**Important**: do not publish `.env`, OAuth client secrets, refresh tokens, or your bot token. Run only one bot/scheduler instance. Google OAuth token refresh in this MVP is in-memory and may require reauthorization if the refresh token is revoked. For production use a secure persistent token store.

## Example
```
/project Реклама Омега26 | Получать целевые заявки | Кампания запущена, аналитика проверена | work
/approve 1
/task Настроить Метрику | 2026-11-05 16:00 | P1 | Цели настроены и тестовое событие проверено | 1
/today
/done 1 | https://example.com/report
/verify 1
```

## Caveats / next iteration
- Prototype, not audited for production or security. Validate in staging.
- A project plan generated in chat is advisory; commands persist records. Autonomous plan generation + approval flow is the next milestone.
- Calendar read-only; calendar writes and event change notifications are not implemented.
- Evidence is saved as a text reference and owner-verified, not technically checked by the LLM.
- Notifications are deduplicated by database key; transient Telegram delivery failures are retried on the next scheduler tick, but exactly-once delivery cannot be guaranteed.
- Scheduled messages are deterministic templates. LLM is used for direct chat only in this version.
- No automatic repeat escalation after 30 minutes; no weekly summary yet.
- Database migrations, tests, rate limits, monitoring, backup/restore and encrypted secret management are required before production use.
- Telegram messages are truncated to fit message length limits; use pagination for large task sets.
