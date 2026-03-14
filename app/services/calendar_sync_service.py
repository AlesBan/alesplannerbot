from __future__ import annotations

from datetime import datetime, timedelta, timezone

from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app.database.models import (
    CalendarCategory,
    CalendarEvent,
    CalendarOutbox,
    CalendarOutboxOp,
    CalendarProvider,
    CalendarReminder,
    CalendarSyncStateModel,
)
from app.integrations.google_calendar import GoogleCalendarService
from app.services.calendar_domain_service import CalendarDomainService
from app.services.calendar_mapper import GoogleCalendarMapper


class CalendarSyncService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.google = GoogleCalendarService()
        self.domain = CalendarDomainService(db)

    def pull_incremental(self, user_id: int, provider_calendar_id: str) -> int:
        state = (
            self.db.query(CalendarSyncStateModel)
            .filter(
                CalendarSyncStateModel.user_id == user_id,
                CalendarSyncStateModel.provider == CalendarProvider.google,
                CalendarSyncStateModel.provider_calendar_id == provider_calendar_id,
            )
            .one_or_none()
        )
        if not state:
            state = CalendarSyncStateModel(
                user_id=user_id,
                provider=CalendarProvider.google,
                provider_calendar_id=provider_calendar_id,
            )
            self.db.add(state)
            self.db.flush()

        try:
            events_raw, next_sync_token = self.google.list_events_raw(provider_calendar_id, sync_token=state.sync_token)
        except HttpError as exc:
            # sync token can expire (410). Fallback to a full sync.
            status = getattr(getattr(exc, "resp", None), "status", None)
            if status == 410 or "Sync token is no longer valid" in str(exc):
                state.sync_token = None
                self.db.commit()
                events_raw, next_sync_token = self.google.list_events_raw(provider_calendar_id, sync_token=None)
            else:
                raise
        upserted = 0
        for raw in events_raw:
            provider_event_id = raw.get("id")
            if not provider_event_id:
                continue
            event = self.domain.upsert_event_from_google(user_id, provider_calendar_id, provider_event_id)
            GoogleCalendarMapper.apply_google_payload_to_event(event, raw, provider_calendar_id, user_id)
            self._apply_google_side_entities(event, raw, user_id)
            upserted += 1

        state.sync_token = next_sync_token or state.sync_token
        state.last_incremental_sync_at = datetime.utcnow()
        if not state.last_full_sync_at:
            state.last_full_sync_at = datetime.utcnow()
        state.health_status = "ok"
        state.last_error = None
        self.db.commit()
        return upserted

    def import_full_window(
        self,
        user_id: int,
        provider_calendar_id: str,
        years_back: int = 2,
        years_forward: int = 3,
    ) -> int:
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=365 * max(1, years_back))
        end = now + timedelta(days=365 * max(1, years_forward))
        events_raw, next_sync_token = self.google.list_events_raw(
            provider_calendar_id,
            sync_token=None,
            start=start,
            end=end,
            single_events=True,
            order_by="startTime",
        )
        upserted = 0
        for raw in events_raw:
            provider_event_id = raw.get("id")
            if not provider_event_id:
                continue
            event = self.domain.upsert_event_from_google(user_id, provider_calendar_id, provider_event_id)
            GoogleCalendarMapper.apply_google_payload_to_event(event, raw, provider_calendar_id, user_id)
            self._apply_google_side_entities(event, raw, user_id)
            upserted += 1

        state = (
            self.db.query(CalendarSyncStateModel)
            .filter(
                CalendarSyncStateModel.user_id == user_id,
                CalendarSyncStateModel.provider == CalendarProvider.google,
                CalendarSyncStateModel.provider_calendar_id == provider_calendar_id,
            )
            .one_or_none()
        )
        if not state:
            state = CalendarSyncStateModel(
                user_id=user_id,
                provider=CalendarProvider.google,
                provider_calendar_id=provider_calendar_id,
            )
            self.db.add(state)
        state.sync_token = next_sync_token or state.sync_token
        state.last_full_sync_at = datetime.utcnow()
        state.last_incremental_sync_at = datetime.utcnow()
        state.health_status = "ok"
        state.last_error = None
        self.db.commit()
        return upserted

    def import_range(self, user_id: int, provider_calendar_id: str, start_utc: datetime, end_utc: datetime) -> int:
        events_raw, next_sync_token = self.google.list_events_raw(
            provider_calendar_id,
            sync_token=None,
            start=start_utc,
            end=end_utc,
            single_events=True,
            order_by="startTime",
        )
        upserted = 0
        for raw in events_raw:
            provider_event_id = raw.get("id")
            if not provider_event_id:
                continue
            event = self.domain.upsert_event_from_google(user_id, provider_calendar_id, provider_event_id)
            GoogleCalendarMapper.apply_google_payload_to_event(event, raw, provider_calendar_id, user_id)
            self._apply_google_side_entities(event, raw, user_id)
            upserted += 1

        state = (
            self.db.query(CalendarSyncStateModel)
            .filter(
                CalendarSyncStateModel.user_id == user_id,
                CalendarSyncStateModel.provider == CalendarProvider.google,
                CalendarSyncStateModel.provider_calendar_id == provider_calendar_id,
            )
            .one_or_none()
        )
        if not state:
            state = CalendarSyncStateModel(
                user_id=user_id,
                provider=CalendarProvider.google,
                provider_calendar_id=provider_calendar_id,
            )
            self.db.add(state)
        state.sync_token = next_sync_token or state.sync_token
        state.last_full_sync_at = datetime.utcnow()
        state.last_incremental_sync_at = datetime.utcnow()
        state.health_status = "ok"
        state.last_error = None
        self.db.commit()
        return upserted

    def push_outbox(self, user_id: int, provider_calendar_id: str, limit: int = 50) -> dict[str, int]:
        rows = (
            self.db.query(CalendarOutbox)
            .filter(
                CalendarOutbox.user_id == user_id,
                CalendarOutbox.provider == CalendarProvider.google,
                CalendarOutbox.available_after <= datetime.utcnow(),
            )
            .order_by(CalendarOutbox.created_at.asc())
            .limit(limit)
            .all()
        )
        pushed = 0
        failed = 0
        for item in rows:
            event = self.db.query(CalendarEvent).filter(CalendarEvent.id == item.event_id).one_or_none()
            if not event:
                self.db.delete(item)
                self.db.commit()
                continue
            try:
                if item.operation == CalendarOutboxOp.delete:
                    if event.provider_event_id and not event.provider_event_id.startswith("local-"):
                        deleted = self.google.delete_event(event.provider_event_id, calendar_id=provider_calendar_id)
                        if not deleted:
                            raise RuntimeError("Google delete_event returned false")
                    self.domain.mark_push_success(item)
                else:
                    payload = GoogleCalendarMapper.event_to_google_payload(event)
                    if event.provider_event_id and not event.provider_event_id.startswith("local-"):
                        updated = self.google.update_event_raw(provider_calendar_id, event.provider_event_id, payload)
                    else:
                        created = self.google.add_event_raw(provider_calendar_id, payload)
                        updated = created
                    GoogleCalendarMapper.apply_google_payload_to_event(event, updated, provider_calendar_id, user_id)
                    self.domain.mark_push_success(item)
                pushed += 1
            except Exception as exc:
                self.domain.mark_push_failed(item, str(exc))
                failed += 1
        return {"pushed": pushed, "failed": failed}

    def sync_now(self, user_id: int, provider_calendar_id: str) -> dict[str, int]:
        pushed = self.push_outbox(user_id, provider_calendar_id)
        pulled_count = self.pull_incremental(user_id, provider_calendar_id)
        return {"pushed": pushed["pushed"], "push_failed": pushed["failed"], "pulled": pulled_count}

    def _apply_google_side_entities(self, event: CalendarEvent, raw: dict, user_id: int) -> None:
        # Store category-like grouping locally using Google color/event type.
        category_name = (raw.get("eventType") or "default").strip().lower()
        color_id = raw.get("colorId")
        if color_id:
            category_name = f"color:{color_id}"
        category = (
            self.db.query(CalendarCategory)
            .filter(CalendarCategory.user_id == user_id, CalendarCategory.name == category_name)
            .one_or_none()
        )
        if not category:
            category = CalendarCategory(user_id=user_id, name=category_name, color_id=color_id)
            self.db.add(category)
            self.db.flush()
        event.category_id = category.id

        # Persist explicit reminder overrides from Google event payload.
        reminders = raw.get("reminders") or {}
        overrides = reminders.get("overrides") or []
        self.db.query(CalendarReminder).filter(CalendarReminder.event_id == event.id).delete()
        for reminder in overrides:
            method = (reminder.get("method") or "popup").strip() or "popup"
            try:
                minutes = max(0, int(reminder.get("minutes") or 0))
            except Exception:
                minutes = 0
            self.db.add(CalendarReminder(event_id=event.id, method=method, minutes=minutes))
