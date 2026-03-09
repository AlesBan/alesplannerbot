from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from app.ai.context_engine import ContextEngine
from app.ai.planner import AIPlanner
from app.ai.recommendations import RecommendationEngine
from app.config import get_settings
from app.database.db import Base, SessionLocal, engine, get_db
from app.database.models import Task, TaskStatus, User
from app.integrations.google_calendar import GoogleCalendarService
from app.services.knowledge_service import KnowledgeService
from app.services.scheduler import AIScheduler
from app.services.sync_service import SyncService
from app.services.task_manager import TaskManager

app = FastAPI(title="Life AI Assistant API")


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        KnowledgeService.seed_global_knowledge(db)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/plan/{telegram_id}")
def build_plan(telegram_id: int, db: Session = Depends(get_db)) -> dict:
    user = db.query(User).filter(User.telegram_id == telegram_id).one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

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
    user = db.query(User).filter(User.telegram_id == telegram_id).one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    count = SyncService(db).sync_yougile_tasks(user.id)
    return {"synced_tasks": count}


@app.get("/report/{telegram_id}")
def weekly_report(telegram_id: int, db: Session = Depends(get_db)) -> dict:
    user = db.query(User).filter(User.telegram_id == telegram_id).one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

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
