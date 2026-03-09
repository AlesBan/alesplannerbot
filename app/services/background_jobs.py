from datetime import datetime

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.database.db import SessionLocal
from app.database.models import User
from app.services.notifications import NotificationService
from app.services.sync_service import SyncService


class BackgroundJobs:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.settings = get_settings()
        self.scheduler = AsyncIOScheduler(timezone=self.settings.timezone)

    async def _morning_plan_job(self) -> None:
        now = datetime.now()
        if now.hour != self.settings.morning_plan_hour:
            return
        with SessionLocal() as db:
            users = db.query(User).all()
            notifier = NotificationService(db, self.bot)
            for user in users:
                await notifier.send_morning_plan(user)

    async def _evening_review_job(self) -> None:
        now = datetime.now()
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

    def start(self) -> None:
        self.scheduler.add_job(self._morning_plan_job, trigger="cron", minute="0")
        self.scheduler.add_job(self._evening_review_job, trigger="cron", minute="10")
        self.scheduler.add_job(self._sync_yougile_job, trigger="interval", minutes=30)
        self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
