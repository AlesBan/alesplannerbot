from datetime import datetime

from aiogram import Bot
from sqlalchemy.orm import Session

from app.ai.context_engine import ContextEngine
from app.database.models import Task, TaskStatus, User


class NotificationService:
    def __init__(self, db: Session, bot: Bot) -> None:
        self.db = db
        self.bot = bot

    async def send_morning_plan(self, user: User) -> None:
        tasks = (
            self.db.query(Task)
            .filter(Task.user_id == user.id, Task.status == TaskStatus.planned)
            .order_by(Task.scheduled_start.asc())
            .all()
        )
        if not tasks:
            await self.bot.send_message(user.telegram_id, "Good morning. No plan yet. Use /plan to build your day.")
            return
        lines = [f"{t.scheduled_start.strftime('%H:%M')}-{t.scheduled_end.strftime('%H:%M')} {t.title}" for t in tasks if t.scheduled_start and t.scheduled_end]
        await self.bot.send_message(user.telegram_id, "Good morning.\nToday:\n" + "\n".join(lines))

    async def send_evening_review(self, user: User) -> None:
        pending = self.db.query(Task).filter(Task.user_id == user.id, Task.status != TaskStatus.completed).all()
        done = self.db.query(Task).filter(Task.user_id == user.id, Task.status == TaskStatus.completed).count()
        text = "\n".join([f"- {task.title}" for task in pending[:5]]) or "- none"
        await self.bot.send_message(
            user.telegram_id,
            "Evening review.\n"
            f"Completed tasks: {done}\n"
            f"Unfinished tasks:\n{text}\n"
            "Reply with: move task <id> to tomorrow",
        )

        context = ContextEngine(self.db, user.id)
        current = float(context.get_memory("weekly_rest_hours") or 0)
        context.set_memory("weekly_rest_hours", str(current + 1))

    @staticmethod
    def should_send(now: datetime, target_hour: int) -> bool:
        return now.hour == target_hour and now.minute < 10
