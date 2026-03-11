from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dateutil import parser as date_parser

from app.database.models import (
    CalendarEvent,
    CalendarEventStatus,
    CalendarSyncState,
    CalendarTransparency,
    CalendarVisibility,
)


class GoogleCalendarMapper:
    @staticmethod
    def _parse_event_time(raw: dict[str, Any], field: str) -> tuple[datetime, bool, str]:
        node = raw.get(field) or {}
        tz = node.get("timeZone") or "UTC"
        if node.get("date"):
            dt = date_parser.parse(node["date"])
            return dt, True, tz
        dt = date_parser.parse(node.get("dateTime") or "")
        return dt, False, tz

    @staticmethod
    def _to_naive_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt
        return dt.astimezone(timezone.utc).replace(tzinfo=None)

    @classmethod
    def apply_google_payload_to_event(
        cls,
        event: CalendarEvent,
        raw: dict[str, Any],
        provider_calendar_id: str,
        user_id: int,
    ) -> CalendarEvent:
        start_dt, is_all_day, start_tz = cls._parse_event_time(raw, "start")
        end_dt, _, end_tz = cls._parse_event_time(raw, "end")

        reminders = raw.get("reminders") or {}
        recurrence = raw.get("recurrence") or []
        recurrence_rule = "\n".join(recurrence).strip() or None

        event.user_id = user_id
        event.provider_calendar_id = provider_calendar_id
        event.provider_event_id = raw.get("id") or event.provider_event_id
        event.etag = raw.get("etag")
        event.sequence = int(raw.get("sequence") or 0)
        event.title = (raw.get("summary") or "Event").strip() or "Event"
        event.description = raw.get("description")
        event.location = raw.get("location")
        event.start_at = cls._to_naive_utc(start_dt)
        event.end_at = cls._to_naive_utc(end_dt)
        event.timezone = start_tz or end_tz or "UTC"
        event.is_all_day = is_all_day

        status_raw = (raw.get("status") or "confirmed").lower()
        visibility_raw = (raw.get("visibility") or "default").lower()
        transparency_raw = (raw.get("transparency") or "opaque").lower()

        event.status = CalendarEventStatus(status_raw if status_raw in {"confirmed", "tentative", "cancelled"} else "confirmed")
        event.visibility = CalendarVisibility(
            visibility_raw if visibility_raw in {"default", "public", "private", "confidential"} else "default"
        )
        event.transparency = CalendarTransparency(transparency_raw if transparency_raw in {"opaque", "transparent"} else "opaque")
        event.event_type = raw.get("eventType") or "default"
        event.color_id = raw.get("colorId")

        event.is_recurring_master = bool(recurrence_rule)
        event.recurrence_rule_text = recurrence_rule
        event.recurring_event_id = raw.get("recurringEventId")
        original_start = raw.get("originalStartTime") or {}
        original_raw = original_start.get("dateTime") or original_start.get("date")
        if original_raw:
            event.original_start_at = cls._to_naive_utc(date_parser.parse(original_raw))

        event.reminder_use_default = bool(reminders.get("useDefault", True))
        conference = raw.get("conferenceData") or {}
        conference_entry_points = conference.get("entryPoints") or []
        event.conference_link = (
            next((point.get("uri") for point in conference_entry_points if point.get("uri")), None) if conference_entry_points else None
        )
        event.html_link = raw.get("htmlLink")
        updated_raw = raw.get("updated")
        event.source_updated_at = cls._to_naive_utc(date_parser.parse(updated_raw)) if updated_raw else None
        event.last_synced_at = datetime.utcnow()
        event.deleted_at = datetime.utcnow() if status_raw == "cancelled" else None
        event.sync_state = CalendarSyncState.synced
        return event

    @classmethod
    def event_to_google_payload(cls, event: CalendarEvent) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "summary": event.title,
            "description": event.description or "",
            "location": event.location or "",
            "status": event.status.value,
            "visibility": event.visibility.value,
            "transparency": event.transparency.value,
            "eventType": event.event_type or "default",
        }
        if event.color_id:
            payload["colorId"] = event.color_id
        if event.is_all_day:
            payload["start"] = {"date": event.start_at.date().isoformat(), "timeZone": event.timezone}
            payload["end"] = {"date": event.end_at.date().isoformat(), "timeZone": event.timezone}
        else:
            start_utc = event.start_at.replace(tzinfo=timezone.utc)
            end_utc = event.end_at.replace(tzinfo=timezone.utc)
            payload["start"] = {"dateTime": start_utc.isoformat(), "timeZone": "UTC"}
            payload["end"] = {"dateTime": end_utc.isoformat(), "timeZone": "UTC"}

        if event.recurrence_rule_text:
            payload["recurrence"] = [row.strip() for row in event.recurrence_rule_text.splitlines() if row.strip()]
        payload["reminders"] = {"useDefault": event.reminder_use_default}
        return payload
