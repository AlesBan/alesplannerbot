# Telegram AI Life Assistant

Production-ready modular Python project for a Telegram AI assistant that helps with planning, scheduling, habits, and work-life balance.

## Features

- Telegram bot with commands: `/start`, `/add`, `/plan`, `/sync`, `/free`, `/report`
- Voice-to-task flow (voice message -> transcription -> task creation)
- AI planning engine with burnout-aware prompting
- AI scheduler:
  - reads calendar events
  - detects free slots
  - prioritizes tasks by deadline and priority
  - matches tasks by energy profile
  - enforces rest buffers and daily work cap
- Context memory layer (`user_memory`) with helper API (`get_memory`, `set_memory`)
- Recommendation engine for activity suggestions based on free time, fatigue, season, and location
- Habit tracking with overdue detection
- FastAPI endpoints for health, scheduling, and reporting
- FastAPI sync endpoint: `POST /sync/yougile/{telegram_id}`
- Background jobs (APScheduler):
  - morning plan notification
  - evening review notification
  - periodic YouGile sync
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
