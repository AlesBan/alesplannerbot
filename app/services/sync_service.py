from sqlalchemy.orm import Session

from app.integrations.yougile import YouGileService
from app.services.task_manager import TaskManager


class SyncService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.task_manager = TaskManager(db)
        self.yougile = YouGileService()

    def sync_yougile_tasks(self, user_id: int) -> int:
        imported = self.yougile.fetch_tasks(user_id=user_id)
        count = 0
        for item in imported:
            self.task_manager.upsert_external_task(
                user_id=user_id,
                external_ref=item["external_ref"],
                title=item["title"],
                duration_minutes=item["duration_minutes"],
                priority=item["priority"],
                deadline=item["deadline"],
                energy_cost=item["energy_cost"],
                source=item["source"],
            )
            count += 1
        return count
