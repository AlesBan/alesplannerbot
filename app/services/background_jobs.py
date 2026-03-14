from datetime import datetime
import logging
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.database.db import SessionLocal
from app.database.models import User
from app.services.calendar_sync_service import CalendarSyncService
from app.services.notifications import NotificationService
from app.services.sync_service import SyncService


class BackgroundJobs:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.settings = get_settings()
        self.scheduler = AsyncIOScheduler(
            timezone=self.settings.timezone,
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
        )
        self.logger = logging.getLogger(__name__)

    async def _morning_plan_job(self) -> None:
        now = datetime.now(ZoneInfo(self.settings.timezone))
        if now.hour != self.settings.morning_plan_hour:
            return
        with SessionLocal() as db:
            users = db.query(User).all()
            notifier = NotificationService(db, self.bot)
            for user in users:
                await notifier.send_morning_plan(user)

    async def _evening_review_job(self) -> None:
        now = datetime.now(ZoneInfo(self.settings.timezone))
        if now.hour != self.settings.evening_review_hour:
            return
        with SessionLocal() as db:
            users = db.query(User).all()
            notifier = NotificationService(db, self.bot)
            for user in users:
                await notifier.send_evening_review(user)

    async def _sync_yougile_job(self) -> None:
        with SessionLocal() as db:
            users = db.query(User).all()
            sync_service = SyncService(db)
            for user in users:
                sync_service.sync_yougile_tasks(user.id)

    async def _calendar_incremental_sync_job(self) -> None:
        with SessionLocal() as db:
            users = db.query(User).all()
            sync = CalendarSyncService(db)
            calendar_id = self.settings.google_calendar_id
            for user in users:
                try:
                    sync.pull_incremental(user.id, calendar_id)
                except Exception:
                    self.logger.exception("Incremental calendar sync failed for user_id=%s", user.id)
                    continue

    async def _calendar_weekly_full_sync_job(self) -> None:
        with SessionLocal() as db:
            users = db.query(User).all()
            sync = CalendarSyncService(db)
            calendar_id = self.settings.google_calendar_id
            for user in users:
                try:
                    sync.import_full_window(user.id, calendar_id, years_back=3, years_forward=5)
                except Exception:
                    self.logger.exception("Weekly full calendar sync failed for user_id=%s", user.id)
                    continue

    async def _proactive_nudge_job(self) -> None:
        with SessionLocal() as db:
            users = db.query(User).all()
            notifier = NotificationService(db, self.bot)
            for user in users:
                await notifier.send_proactive_check(user)

    def start(self) -> None:
        self.scheduler.add_job(self._morning_plan_job, trigger="cron", minute="0")
        self.scheduler.add_job(self._evening_review_job, trigger="cron", minute="10")
        self.scheduler.add_job(self._sync_yougile_job, trigger="interval", minutes=30)
        # Local-first calendar maintenance: frequent incremental + weekly full reconciliation.
        self.scheduler.add_job(self._calendar_incremental_sync_job, trigger="interval", minutes=30)
        self.scheduler.add_job(self._calendar_weekly_full_sync_job, trigger="cron", day_of_week="sun", hour="3", minute="20")
        self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
