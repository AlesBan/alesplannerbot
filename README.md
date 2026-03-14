# Telegram AI Life Assistant

Production-ready modular Python project for a Telegram AI assistant that helps with planning, scheduling, habits, and work-life balance.

## Features

- Telegram bot with commands: `/start`, `/add`, `/plan`, `/sync`, `/free`, `/report`
- Natural chat mode (no commands required): bot detects intent from plain messages
- Learning conversation memory: bot stores dialogue, learns Q/A patterns, and adapts responses over time
- Voice-to-task flow (voice message -> transcription -> task creation)
- AI planning engine with burnout-aware prompting
- LLM provider support:
  - DeepSeek (`DEEPSEEK_API_KEY`) - preferred when provided
  - OpenAI (`OPENAI_API_KEY`) - fallback
- AI scheduler:
  - reads calendar events
  - detects free slots
  - prioritizes tasks by deadline and priority
  - matches tasks by energy profile
  - enforces rest buffers and daily work cap
- Context memory layer (`user_memory`) with helper API (`get_memory`, `set_memory`)
- Knowledge base layer:
  - `knowledge_items` (global + user-specific knowledge snippets)
  - `conversation_turns` (chat history)
  - `learned_qa` (auto-learned question/answer patterns)
- Recommendation engine for activity suggestions based on free time, fatigue, season, and location
- Habit tracking with overdue detection
- FastAPI endpoints for health, scheduling, and reporting
- FastAPI sync endpoint: `POST /sync/yougile/{telegram_id}`
- Background jobs (APScheduler):
  - morning plan notification
  - evening review notification
  - periodic YouGile sync
  - proactive nudges for overdue habits and urgent unscheduled tasks
- Docker-ready deployment

## Project Structure

```text
app/
  bot/
    bot.py
    handlers.py
    voice_handler.py
  ai/
    planner.py
    recommendations.py
    context_engine.py
  services/
    task_manager.py
    scheduler.py
    habit_tracker.py
    notifications.py
  integrations/
    google_calendar.py
    yougile.py
    openai_client.py
  database/
    models.py
    db.py
  utils/
    time_utils.py
    voice_utils.py
  main.py
  config.py
main.py
```

## Setup

1. Create environment:
   - `python -m venv .venv`
   - `.venv\Scripts\activate` (Windows) or `source .venv/bin/activate` (Linux/macOS)
2. Install dependencies:
   - `pip install -r requirements.txt`
3. Configure env:
   - `copy .env.example .env` (Windows) or `cp .env.example .env`
   - Fill tokens and API keys.
   - For Google Calendar, set `GOOGLE_CREDENTIALS_PATH` to your service account JSON (or OAuth client secret JSON).
   - For YouGile, set either `YOU_GILE_API_KEY` or `YOU_GILE_EMAIL` + `YOU_GILE_PASSWORD`.

## Run

### Telegram Bot

- `python main.py`

### FastAPI

- `uvicorn app.main:app --reload`

## Docker

- `docker compose up --build`

Service-specific Dockerfiles are used for faster builds:

- `Dockerfile.bot` + `requirements-bot.txt`
- `Dockerfile.api` + `requirements-api.txt`
- shared deps in `requirements-base.txt`

## Cloud Deploy (Supported Region)

Use Render in `frankfurt` region to run bot/API from a supported location.

1. Push latest code to GitHub.
2. In Render: **New +** -> **Blueprint** -> select this repository.
3. Render reads `render.yaml` and creates:
   - `life-ai-bot` (worker)
   - `life-ai-api` (web)
   - `life-ai-db` (Postgres)
4. Set required secret env vars in Render:
   - `TELEGRAM_BOT_TOKEN`
   - `OPENAI_API_KEY` (or `DEEPSEEK_API_KEY`)
   - `GOOGLE_CALENDAR_ID`
   - `GOOGLE_CREDENTIALS_JSON` (paste full JSON content)
   - `YOU_GILE_API_KEY` (or `YOU_GILE_EMAIL` + `YOU_GILE_PASSWORD`)
5. Deploy and check:
   - API health: `https://<life-ai-api-domain>/health`
   - Bot logs in Render dashboard.

### Optional: Faster CI-driven deploy

This repository includes `.github/workflows/deploy-render.yml`:

- Runs fast Python compile checks on every push to `main` (no full pip install)
- Detects changed paths
- Triggers only the needed Render service deploy (`bot`, `api`, or both)

Configure GitHub repository secrets:

- `RENDER_API_KEY`
- `RENDER_BOT_SERVICE_ID`
- `RENDER_API_SERVICE_ID`

If you want this workflow to be the only deploy trigger, switch `autoDeploy` to `false` in `render.yaml` after secrets are configured.

### Prebuilt Images (GHCR)

This repository includes `.github/workflows/build-ghcr-images.yml`:

- Builds `linux/amd64` images for bot and API
- Pushes to GHCR:
  - `ghcr.io/<owner>/life-ai-bot:latest` and `:<commit_sha>`
  - `ghcr.io/<owner>/life-ai-api:latest` and `:<commit_sha>`
- Uses Buildx cache to speed up subsequent builds

For image-backed Render deploys, `.github/workflows/deploy-render-ghcr.yml` triggers Render deploy hooks with `imgURL` after GHCR build succeeds.
Required GitHub secrets:

- `RENDER_BOT_DEPLOY_HOOK`
- `RENDER_API_DEPLOY_HOOK`

`render.yaml` is configured for image-backed services (`runtime: image`) and references GHCR images.

## Data Model Highlights

- `tasks` includes `energy_cost` for energy-aware planning
- `user_memory` stores long-term context: `last_workout`, `weekly_work_hours`, etc.
- `activities` stores activity catalog for free-slot suggestions
- `habits` tracks recurring behaviors and overdue reminders

## AI Planner Prompt Role

The planner prompt is in `app/ai/planner.py` and enforces:

- priority with deadlines
- realistic schedule
- rest-time protection
- burnout prevention
- work-life balance recommendations

## Notes for Production

- Replace integration stubs in `app/integrations/` with full API clients.
- Add background scheduler (e.g. APScheduler/Celery) for timed morning/evening notifications.
- Add Alembic migrations for schema evolution.
- Add tests for planner and scheduler edge-cases.
