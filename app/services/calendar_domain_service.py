from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.database.models import (
    CalendarEvent,
    CalendarOutbox,
    CalendarOutboxOp,
    CalendarProvider,
    CalendarReminder,
    CalendarSyncState,
    User,
)


class CalendarDomainService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _ensure_user(self, user_id: int) -> User:
        user = self.db.query(User).filter(User.id == user_id).one_or_none()
        if not user:
            raise ValueError("User not found")
        return user

    def list_day_events(self, user_id: int, day_start_utc: datetime, day_end_utc: datetime) -> list[CalendarEvent]:
        self._ensure_user(user_id)
        return (
            self.db.query(CalendarEvent)
            .filter(
                CalendarEvent.user_id == user_id,
                CalendarEvent.deleted_at.is_(None),
                CalendarEvent.start_at < day_end_utc,
                CalendarEvent.end_at > day_start_utc,
            )
            .order_by(CalendarEvent.start_at.asc())
            .all()
        )

    def upsert_event_from_google(self, user_id: int, provider_calendar_id: str, provider_event_id: str) -> CalendarEvent:
        existing = (
            self.db.query(CalendarEvent)
            .filter(
                CalendarEvent.user_id == user_id,
                CalendarEvent.provider == CalendarProvider.google,
                CalendarEvent.provider_calendar_id == provider_calendar_id,
                CalendarEvent.provider_event_id == provider_event_id,
            )
            .one_or_none()
        )
        if existing:
            return existing
        event = CalendarEvent(
            user_id=user_id,
            provider=CalendarProvider.google,
            provider_calendar_id=provider_calendar_id,
            provider_event_id=provider_event_id,
        )
        self.db.add(event)
        return event

    def create_local_event(
        self,
        user_id: int,
        provider_calendar_id: str,
        title: str,
        start_at: datetime,
        end_at: datetime,
        timezone_name: str,
        details: dict[str, Any] | None = None,
    ) -> CalendarEvent:
        self._ensure_user(user_id)
        details = details or {}
        event = CalendarEvent(
            user_id=user_id,
            provider=CalendarProvider.google,
            provider_calendar_id=provider_calendar_id,
            provider_event_id=f"local-{int(datetime.utcnow().timestamp() * 1000000)}",
            title=title.strip() or "Event",
            description=details.get("description"),
            location=details.get("location"),
            start_at=start_at,
            end_at=end_at,
            timezone=timezone_name or "UTC",
            is_all_day=bool(details.get("is_all_day", False)),
            event_type=details.get("event_type") or "default",
            color_id=details.get("color_id"),
            recurrence_rule_text=details.get("recurrence_rule_text"),
            reminder_use_default=bool(details.get("reminder_use_default", True)),
            sync_state=CalendarSyncState.pending_push,
        )
        self.db.add(event)
        self.db.flush()
        self._replace_reminders(event, details.get("reminders") or [])
        self._enqueue_outbox(user_id, event.id, CalendarOutboxOp.upsert)
        self.db.commit()
        self.db.refresh(event)
        return event

    def update_local_event(
        self,
        user_id: int,
        event_id: int,
        patch: dict[str, Any],
    ) -> CalendarEvent | None:
        event = self.db.query(CalendarEvent).filter(CalendarEvent.id == event_id, CalendarEvent.user_id == user_id).one_or_none()
        if not event:
            return None
        if "title" in patch:
            event.title = (patch["title"] or "").strip() or event.title
        if "description" in patch:
            event.description = patch["description"]
        if "location" in patch:
            event.location = patch["location"]
        if "start_at" in patch and patch["start_at"]:
            event.start_at = patch["start_at"]
        if "end_at" in patch and patch["end_at"]:
            event.end_at = patch["end_at"]
        if "timezone" in patch and patch["timezone"]:
            event.timezone = patch["timezone"]
        if "is_all_day" in patch:
            event.is_all_day = bool(patch["is_all_day"])
        if "color_id" in patch:
            event.color_id = patch["color_id"]
        if "event_type" in patch:
            event.event_type = patch["event_type"] or event.event_type
        if "recurrence_rule_text" in patch:
            event.recurrence_rule_text = patch["recurrence_rule_text"]
            event.is_recurring_master = bool(event.recurrence_rule_text)
        if "reminder_use_default" in patch:
            event.reminder_use_default = bool(patch["reminder_use_default"])
        if "reminders" in patch and isinstance(patch["reminders"], list):
            self._replace_reminders(event, patch["reminders"])

        event.sync_state = CalendarSyncState.pending_push
        self._enqueue_outbox(user_id, event.id, CalendarOutboxOp.upsert)
        self.db.commit()
        self.db.refresh(event)
        return event

    def delete_local_event(self, user_id: int, event_id: int) -> bool:
        event = self.db.query(CalendarEvent).filter(CalendarEvent.id == event_id, CalendarEvent.user_id == user_id).one_or_none()
        if not event:
            return False
        event.deleted_at = datetime.utcnow()
        event.sync_state = CalendarSyncState.pending_delete
        self._enqueue_outbox(user_id, event.id, CalendarOutboxOp.delete)
        self.db.commit()
        return True

    def mark_push_failed(self, item: CalendarOutbox, error: str) -> None:
        item.retry_count += 1
        item.available_after = datetime.utcnow() + timedelta(minutes=min(60, 2 ** min(item.retry_count, 6)))
        item.error = error[:1000]
        event = self.db.query(CalendarEvent).filter(CalendarEvent.id == item.event_id).one_or_none()
        if event:
            event.sync_state = CalendarSyncState.push_failed
        self.db.commit()

    def mark_push_success(self, item: CalendarOutbox) -> None:
        event = self.db.query(CalendarEvent).filter(CalendarEvent.id == item.event_id).one_or_none()
        if event:
            event.sync_state = CalendarSyncState.synced
            event.last_synced_at = datetime.utcnow()
        self.db.delete(item)
        self.db.commit()

    def _replace_reminders(self, event: CalendarEvent, reminders: list[dict[str, Any]]) -> None:
        self.db.query(CalendarReminder).filter(CalendarReminder.event_id == event.id).delete()
        for reminder in reminders:
            method = (reminder.get("method") or "popup").strip() or "popup"
            minutes = int(reminder.get("minutes") or 10)
            self.db.add(CalendarReminder(event_id=event.id, method=method, minutes=max(0, minutes)))

    def _enqueue_outbox(self, user_id: int, event_id: int, op: CalendarOutboxOp) -> None:
        existing = (
            self.db.query(CalendarOutbox)
            .filter(CalendarOutbox.user_id == user_id, CalendarOutbox.event_id == event_id, CalendarOutbox.operation == op)
            .one_or_none()
        )
        if existing:
            existing.available_after = datetime.utcnow()
            existing.error = None
            return
        self.db.add(
            CalendarOutbox(
                user_id=user_id,
                event_id=event_id,
                provider=CalendarProvider.google,
                operation=op,
            )
        )
