from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Habit


class HabitTracker:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_overdue_habits(self, user_id: int) -> list[Habit]:
        habits = list(self.db.scalars(select(Habit).where(Habit.user_id == user_id)))
        now = datetime.utcnow()
        overdue: list[Habit] = []
        for habit in habits:
            if habit.frequency_per_week <= 0:
                continue
            allowed_gap_days = max(1, int(7 / habit.frequency_per_week))
            due_time = (habit.last_completed or (now - timedelta(days=30))) + timedelta(days=allowed_gap_days)
            if due_time < now:
                overdue.append(habit)
        return overdue

    def complete_habit(self, habit_id: int) -> Habit | None:
        habit = self.db.get(Habit, habit_id)
        if not habit:
            return None
        habit.last_completed = datetime.utcnow()
        self.db.commit()
        self.db.refresh(habit)
        return habit
