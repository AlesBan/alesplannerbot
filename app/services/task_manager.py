from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import EnergyCost, PriorityLevel, Task, TaskSource, TaskStatus


class TaskManager:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_task(
        self,
        user_id: int,
        title: str,
        duration_minutes: int,
        priority: PriorityLevel = PriorityLevel.medium,
        deadline: datetime | None = None,
        source: TaskSource = TaskSource.manual,
        energy_cost: EnergyCost = EnergyCost.medium,
        external_ref: str | None = None,
    ) -> Task:
        task = Task(
            user_id=user_id,
            title=title,
            duration_minutes=duration_minutes,
            priority=priority,
            deadline=deadline,
            status=TaskStatus.pending,
            source=source,
            external_ref=external_ref,
            energy_cost=energy_cost,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def upsert_external_task(
        self,
        user_id: int,
        external_ref: str,
        title: str,
        duration_minutes: int,
        priority: PriorityLevel,
        deadline: datetime | None,
        energy_cost: EnergyCost,
        source: TaskSource = TaskSource.yougile,
    ) -> Task:
        existing = self.db.scalar(select(Task).where(Task.user_id == user_id, Task.external_ref == external_ref))
        if existing:
            existing.title = title
            existing.duration_minutes = duration_minutes
            existing.priority = priority
            existing.deadline = deadline
            existing.energy_cost = energy_cost
            if existing.status == TaskStatus.completed:
                existing.status = TaskStatus.pending
            self.db.commit()
            self.db.refresh(existing)
            return existing
        return self.create_task(
            user_id=user_id,
            title=title,
            duration_minutes=duration_minutes,
            priority=priority,
            deadline=deadline,
            source=source,
            energy_cost=energy_cost,
            external_ref=external_ref,
        )

    def list_open_tasks(self, user_id: int) -> list[Task]:
        stmt = (
            select(Task)
            .where(Task.user_id == user_id, Task.status.in_([TaskStatus.pending, TaskStatus.planned]))
            .order_by(Task.deadline.is_(None), Task.deadline.asc())
        )
        return list(self.db.scalars(stmt))

    def mark_completed(self, task_id: int) -> Task | None:
        task = self.db.get(Task, task_id)
        if not task:
            return None
        task.status = TaskStatus.completed
        self.db.commit()
        self.db.refresh(task)
        return task

    def set_schedule(self, task_id: int, start: datetime, end: datetime) -> Task | None:
        task = self.db.get(Task, task_id)
        if not task:
            return None
        task.scheduled_start = start
        task.scheduled_end = end
        task.status = TaskStatus.planned
        self.db.commit()
        self.db.refresh(task)
        return task
