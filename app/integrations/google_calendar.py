from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

from dateutil import parser as date_parser
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as OAuthCredentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import get_settings
from app.database.models import Event


class GoogleCalendarService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.scopes = [scope.strip() for scope in self.settings.google_scopes.split(",") if scope.strip()]

    def _get_credentials(self):
        credentials_path = Path(self.settings.google_credentials_path)
        token_path = Path(self.settings.google_token_path)
        if not credentials_path.exists() and self.settings.google_credentials_json:
            credentials_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                payload = json.loads(self.settings.google_credentials_json)
                credentials_path.write_text(json.dumps(payload), encoding="utf-8")
            except Exception:
                credentials_path.write_text(self.settings.google_credentials_json, encoding="utf-8")
        if not credentials_path.exists():
            raise FileNotFoundError(f"Google credentials file not found: {credentials_path}")

        # Service-account path is the easiest production option for backend workloads.
        try:
            return ServiceAccountCredentials.from_service_account_file(str(credentials_path), scopes=self.scopes)
        except Exception:
            pass

        creds = None
        if token_path.exists():
            creds = OAuthCredentials.from_authorized_user_file(str(token_path), self.scopes)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), self.scopes)
                creds = flow.run_local_server(port=0)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds

    def _client(self):
        creds = self._get_credentials()
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    def get_calendar_timezone(self) -> str:
        try:
            service = self._client()
            meta = service.calendars().get(calendarId=self.settings.google_calendar_id).execute()
            return meta.get("timeZone") or self.settings.timezone
        except Exception:
            return self.settings.timezone

    def list_events(self, user_id: int, start: datetime, end: datetime) -> list[Event]:
        try:
            service = self._client()
            result = (
                service.events()
                .list(
                    calendarId=self.settings.google_calendar_id,
                    timeMin=start.astimezone(timezone.utc).isoformat(),
                    timeMax=end.astimezone(timezone.utc).isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
        except HttpError:
            return []

        events: list[Event] = []
        for raw in result.get("items", []):
            start_raw = raw.get("start", {}).get("dateTime") or raw.get("start", {}).get("date")
            end_raw = raw.get("end", {}).get("dateTime") or raw.get("end", {}).get("date")
            if not start_raw or not end_raw:
                continue
            start_time = date_parser.parse(start_raw)
            end_time = date_parser.parse(end_raw)
            if start_time.tzinfo:
                start_time = start_time.astimezone(timezone.utc).replace(tzinfo=None)
            if end_time.tzinfo:
                end_time = end_time.astimezone(timezone.utc).replace(tzinfo=None)
            events.append(
                Event(
                    user_id=user_id,
                    calendar_event_id=raw.get("id", ""),
                    title=raw.get("summary") or "Event",
                    start_time=start_time,
                    end_time=end_time,
                    event_type="calendar",
                )
            )
        return events

    def add_event(self, user_id: int, title: str, start: datetime, end: datetime) -> dict:
        _ = user_id
        service = self._client()
        body = {
            "summary": title,
            "start": {"dateTime": start.astimezone(timezone.utc).isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end.astimezone(timezone.utc).isoformat(), "timeZone": "UTC"},
        }
        created = service.events().insert(calendarId=self.settings.google_calendar_id, body=body).execute()
        return {"id": created.get("id"), "title": title, "start": start.isoformat(), "end": end.isoformat()}

    def move_event(self, event_id: str, new_start: datetime, new_end: datetime) -> dict:
        service = self._client()
        event = service.events().get(calendarId=self.settings.google_calendar_id, eventId=event_id).execute()
        event["start"] = {"dateTime": new_start.astimezone(timezone.utc).isoformat(), "timeZone": "UTC"}
        event["end"] = {"dateTime": new_end.astimezone(timezone.utc).isoformat(), "timeZone": "UTC"}
        updated = service.events().update(calendarId=self.settings.google_calendar_id, eventId=event_id, body=event).execute()
        return {"id": updated.get("id"), "new_start": new_start.isoformat(), "new_end": new_end.isoformat()}

    def detect_free_time(self, start: datetime, end: datetime, busy_events: list[Event]) -> list[tuple[datetime, datetime]]:
        windows = [(start, end)]
        for event in sorted(busy_events, key=lambda x: x.start_time):
            next_windows = []
            for ws, we in windows:
                if event.end_time <= ws or event.start_time >= we:
                    next_windows.append((ws, we))
                    continue
                if ws < event.start_time:
                    next_windows.append((ws, event.start_time))
                if event.end_time < we:
                    next_windows.append((event.end_time, we))
            windows = next_windows
        return [(ws, we) for ws, we in windows if we - ws >= timedelta(minutes=30)]
