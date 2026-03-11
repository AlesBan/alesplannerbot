from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.database.models import CalendarEvent


class CalendarReadService:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def build_day_window_utc(timezone_name: str, day_offset: int = 0) -> tuple[datetime, datetime]:
        try:
            tz = ZoneInfo(timezone_name)
        except Exception:
            tz = ZoneInfo("UTC")
        target_day = (datetime.now(tz) + timedelta(days=day_offset)).date()
        day_start_local = datetime.combine(target_day, datetime.min.time(), tzinfo=tz)
        day_end_local = day_start_local + timedelta(days=1)
        return day_start_local.astimezone(timezone.utc).replace(tzinfo=None), day_end_local.astimezone(timezone.utc).replace(tzinfo=None)

    def list_day_events(self, user_id: int, timezone_name: str, day_offset: int = 0) -> tuple[str, list[CalendarEvent]]:
        day_start, day_end = self.build_day_window_utc(timezone_name, day_offset)
        try:
            tz = ZoneInfo(timezone_name)
        except Exception:
            tz = ZoneInfo("UTC")
        day_label = datetime.now(tz).date() + timedelta(days=day_offset)
        rows = (
            self.db.query(CalendarEvent)
            .filter(
                CalendarEvent.user_id == user_id,
                CalendarEvent.deleted_at.is_(None),
                CalendarEvent.start_at < day_end,
                CalendarEvent.end_at > day_start,
            )
            .order_by(CalendarEvent.start_at.asc())
            .all()
        )
        return day_label.strftime("%d.%m.%Y"), rows

    @staticmethod
    def format_events(events: list[CalendarEvent], timezone_name: str, max_items: int | None = None) -> str:
        if not events:
            return "На сегодня событий нет."
        try:
            tz = ZoneInfo(timezone_name)
        except Exception:
            tz = ZoneInfo("UTC")
        lines: list[str] = []
        iter_events = events if max_items is None else events[:max_items]
        for event in iter_events:
            start = event.start_at.replace(tzinfo=timezone.utc).astimezone(tz)
            end = event.end_at.replace(tzinfo=timezone.utc).astimezone(tz)
            if event.is_all_day:
                time_part = "Весь день"
            else:
                duration_minutes = int((end - start).total_seconds() // 60)
                if duration_minutes <= 0:
                    time_part = f"{start.strftime('%H:%M')} • короткое событие"
                else:
                    time_part = f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"
            lines.append(f"- {time_part}  {(event.title or 'Без названия').strip() or 'Без названия'}")
        return "\n".join(lines)
