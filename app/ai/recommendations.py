from datetime import datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.database.models import Activity, ActivityLog, EnergyCost
from app.services.scheduler import energy_level_for_hour


class RecommendationEngine:
    def __init__(self, db: Session) -> None:
        self.db = db

    def suggest_activities(
        self,
        user_id: int,
        free_minutes: int,
        location: str = "any",
        season: str = "any",
        fatigue_score: float = 0.5,
    ) -> list[Activity]:
        slot_energy = energy_level_for_hour(datetime.utcnow().hour)
        max_energy = self._max_energy_for_fatigue(slot_energy, fatigue_score)

        stmt = select(Activity).where(
            Activity.user_id == user_id,
            Activity.duration_minutes <= free_minutes,
            and_(Activity.location.in_(["any", location]), Activity.season.in_(["any", season])),
            Activity.energy_cost.in_(max_energy),
        )
        items = list(self.db.scalars(stmt))
        ranked = sorted(items, key=lambda activity: self._last_done_penalty(user_id, activity.name))
        return ranked[:5]

    def _last_done_penalty(self, user_id: int, activity_name: str) -> float:
        last_log = self.db.scalar(
            select(ActivityLog)
            .where(ActivityLog.user_id == user_id, ActivityLog.activity_type == activity_name)
            .order_by(ActivityLog.date.desc())
            .limit(1)
        )
        if not last_log:
            return 0
        age = datetime.utcnow() - last_log.date
        return max(0, 1000 - age.total_seconds())

    @staticmethod
    def _max_energy_for_fatigue(slot_energy: EnergyCost, fatigue_score: float) -> list[EnergyCost]:
        if fatigue_score > 0.75:
            return [EnergyCost.low]
        if fatigue_score > 0.5:
            return [EnergyCost.low, EnergyCost.medium]
        allowed = [EnergyCost.low, EnergyCost.medium, EnergyCost.high]
        return [e for e in allowed if RecommendationEngine._energy_rank(e) <= RecommendationEngine._energy_rank(slot_energy)]

    @staticmethod
    def _energy_rank(value: EnergyCost) -> int:
        return {EnergyCost.low: 0, EnergyCost.medium: 1, EnergyCost.high: 2}[value]

    def estimate_fatigue(self, weekly_work_hours: float, weekly_rest_hours: float) -> float:
        if weekly_work_hours <= 0:
            return 0.3
        balance = weekly_rest_hours / weekly_work_hours if weekly_work_hours else 1
        if balance < 0.3:
            return 0.9
        if balance < 0.5:
            return 0.7
        return 0.4
