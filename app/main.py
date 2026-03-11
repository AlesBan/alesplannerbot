from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Query
from dateutil import parser as date_parser
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.ai.context_engine import ContextEngine
from app.ai.planner import AIPlanner
from app.ai.recommendations import RecommendationEngine
from app.config import get_settings
from app.database.db import Base, SessionLocal, engine, get_db
from app.database.models import Task, TaskStatus, User
from app.integrations.google_calendar import GoogleCalendarService
from app.services.calendar_domain_service import CalendarDomainService
from app.services.calendar_read_service import CalendarReadService
from app.services.calendar_sync_service import CalendarSyncService
from app.services.knowledge_service import KnowledgeService
from app.services.scheduler import AIScheduler
from app.services.sync_service import SyncService
from app.services.task_manager import TaskManager

app = FastAPI(title="Life AI Assistant API")


class CalendarEventCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    start_utc_iso: str
    end_utc_iso: str
    timezone: str = "UTC"
    description: str | None = None
    location: str | None = None
    is_all_day: bool = False
    recurrence_rule_text: str | None = None
    reminders: list[dict] = Field(default_factory=list)


class CalendarEventPatchIn(BaseModel):
    title: str | None = None
    start_utc_iso: str | None = None
    end_utc_iso: str | None = None
    timezone: str | None = None
    description: str | None = None
    location: str | None = None
    is_all_day: bool | None = None
    recurrence_rule_text: str | None = None
    reminders: list[dict] | None = None


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        KnowledgeService.seed_global_knowledge(db)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _get_user_by_telegram(db: Session, telegram_id: int) -> User:
    user = db.query(User).filter(User.telegram_id == telegram_id).one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.post("/plan/{telegram_id}")
def build_plan(telegram_id: int, db: Session = Depends(get_db)) -> dict:
    user = _get_user_by_telegram(db, telegram_id)

    synced_count = SyncService(db).sync_yougile_tasks(user.id)
    task_manager = TaskManager(db)
    tasks = task_manager.list_open_tasks(user.id)
    scheduler = AIScheduler(get_settings().max_daily_work_minutes)
    day_start, day_end = scheduler.build_day_window(datetime.utcnow())
    events = GoogleCalendarService().list_events(user.id, day_start, day_end)
    free_slots = scheduler.detect_free_slots(day_start, day_end, events)
    scheduled = scheduler.schedule_tasks(tasks, free_slots)
    for item in scheduled:
        task_manager.set_schedule(item.task_id, item.start, item.end)

    ai_notes = AIPlanner().explain_plan(datetime.utcnow(), tasks, scheduled, ContextEngine(db, user.id).export_memory())
    return {
        "yougile_synced": synced_count,
        "scheduled": [
            {"task_id": s.task_id, "title": s.title, "start": s.start.isoformat(), "end": s.end.isoformat()}
            for s in scheduled
        ],
        "ai_notes": ai_notes,
    }


@app.post("/sync/yougile/{telegram_id}")
def sync_yougile(telegram_id: int, db: Session = Depends(get_db)) -> dict:
    user = _get_user_by_telegram(db, telegram_id)
    count = SyncService(db).sync_yougile_tasks(user.id)
    return {"synced_tasks": count}


@app.get("/report/{telegram_id}")
def weekly_report(telegram_id: int, db: Session = Depends(get_db)) -> dict:
    user = _get_user_by_telegram(db, telegram_id)

    task_manager = TaskManager(db)
    open_tasks = len(task_manager.list_open_tasks(user.id))
    completed_count = db.query(Task).filter(Task.user_id == user.id, Task.status == TaskStatus.completed).count()
    context = ContextEngine(db, user.id)
    fatigue = RecommendationEngine(db).estimate_fatigue(
        float(context.get_memory("weekly_work_hours") or 0),
        float(context.get_memory("weekly_rest_hours") or 0),
    )
    return {
        "open_tasks": open_tasks,
        "completed_count": completed_count,
        "fatigue_score": fatigue,
    }


@app.get("/calendar/day/{telegram_id}")
def calendar_day(
    telegram_id: int,
    day_offset: int = Query(default=0, ge=-30, le=30),
    db: Session = Depends(get_db),
) -> dict:
    user = _get_user_by_telegram(db, telegram_id)
    gcal = GoogleCalendarService()
    calendar_id = get_settings().google_calendar_id
    calendar_tz = gcal.get_calendar_timezone() or user.timezone or get_settings().timezone
    sync = CalendarSyncService(db)
    sync.pull_incremental(user.id, calendar_id)
    day_label, events = CalendarReadService(db).list_day_events(user.id, calendar_tz, day_offset)
    return {
        "date": day_label,
        "timezone": calendar_tz,
        "events": [
            {
                "id": e.id,
                "title": e.title,
                "description": e.description,
                "location": e.location,
                "start": e.start_at.isoformat(),
                "end": e.end_at.isoformat(),
                "is_all_day": e.is_all_day,
                "status": e.status.value,
                "event_type": e.event_type,
                "color_id": e.color_id,
                "recurrence_rule_text": e.recurrence_rule_text,
                "provider_event_id": e.provider_event_id,
            }
            for e in events
        ],
    }


@app.get("/calendar/week/{telegram_id}")
def calendar_week(telegram_id: int, db: Session = Depends(get_db)) -> dict:
    user = _get_user_by_telegram(db, telegram_id)
    gcal = GoogleCalendarService()
    calendar_id = get_settings().google_calendar_id
    calendar_tz = gcal.get_calendar_timezone() or user.timezone or get_settings().timezone
    sync = CalendarSyncService(db)
    sync.pull_incremental(user.id, calendar_id)
    read = CalendarReadService(db)
    days: list[dict] = []
    for offset in range(7):
        day_label, events = read.list_day_events(user.id, calendar_tz, offset)
        days.append(
            {
                "date": day_label,
                "events": [{"id": e.id, "title": e.title, "start": e.start_at.isoformat(), "end": e.end_at.isoformat()} for e in events],
            }
        )
    return {"timezone": calendar_tz, "days": days}


@app.post("/calendar/events/{telegram_id}")
def create_calendar_event(telegram_id: int, payload: CalendarEventCreateIn, db: Session = Depends(get_db)) -> dict:
    user = _get_user_by_telegram(db, telegram_id)
    start_at = date_parser.parse(payload.start_utc_iso)
    end_at = date_parser.parse(payload.end_utc_iso)
    if start_at.tzinfo:
        start_at = start_at.astimezone(timezone.utc).replace(tzinfo=None)
    if end_at.tzinfo:
        end_at = end_at.astimezone(timezone.utc).replace(tzinfo=None)
    calendar_id = get_settings().google_calendar_id
    event = CalendarDomainService(db).create_local_event(
        user.id,
        calendar_id,
        payload.title,
        start_at,
        end_at,
        payload.timezone,
        details={
            "description": payload.description,
            "location": payload.location,
            "is_all_day": payload.is_all_day,
            "recurrence_rule_text": payload.recurrence_rule_text,
            "reminders": payload.reminders,
        },
    )
    sync_result = CalendarSyncService(db).sync_now(user.id, calendar_id)
    return {"event_id": event.id, "sync": sync_result}


@app.patch("/calendar/events/{telegram_id}/{event_id}")
def patch_calendar_event(telegram_id: int, event_id: int, payload: CalendarEventPatchIn, db: Session = Depends(get_db)) -> dict:
    user = _get_user_by_telegram(db, telegram_id)
    patch: dict = payload.model_dump(exclude_unset=True)
    if patch.get("start_utc_iso"):
        parsed = date_parser.parse(patch.pop("start_utc_iso"))
        patch["start_at"] = parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    if patch.get("end_utc_iso"):
        parsed = date_parser.parse(patch.pop("end_utc_iso"))
        patch["end_at"] = parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    updated = CalendarDomainService(db).update_local_event(user.id, event_id, patch)
    if not updated:
        raise HTTPException(status_code=404, detail="Event not found")
    sync_result = CalendarSyncService(db).sync_now(user.id, get_settings().google_calendar_id)
    return {"event_id": updated.id, "sync": sync_result}


@app.delete("/calendar/events/{telegram_id}/{event_id}")
def delete_calendar_event(telegram_id: int, event_id: int, db: Session = Depends(get_db)) -> dict:
    user = _get_user_by_telegram(db, telegram_id)
    ok = CalendarDomainService(db).delete_local_event(user.id, event_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Event not found")
    sync_result = CalendarSyncService(db).sync_now(user.id, get_settings().google_calendar_id)
    return {"deleted": True, "sync": sync_result}


@app.post("/calendar/sync/pull/{telegram_id}")
def calendar_pull(telegram_id: int, db: Session = Depends(get_db)) -> dict:
    user = _get_user_by_telegram(db, telegram_id)
    pulled = CalendarSyncService(db).pull_incremental(user.id, get_settings().google_calendar_id)
    return {"pulled": pulled}


@app.post("/calendar/sync/push/{telegram_id}")
def calendar_push(telegram_id: int, db: Session = Depends(get_db)) -> dict:
    user = _get_user_by_telegram(db, telegram_id)
    return CalendarSyncService(db).push_outbox(user.id, get_settings().google_calendar_id)


@app.post("/calendar/sync/full/{telegram_id}")
def calendar_full_import(telegram_id: int, years_back: int = 2, years_forward: int = 3, db: Session = Depends(get_db)) -> dict:
    user = _get_user_by_telegram(db, telegram_id)
    imported = CalendarSyncService(db).import_full_window(
        user.id,
        get_settings().google_calendar_id,
        years_back=years_back,
        years_forward=years_forward,
    )
    return {"imported": imported, "years_back": years_back, "years_forward": years_forward}
