from datetime import datetime

import httpx
from dateutil import parser as date_parser

from app.config import get_settings
from app.database.models import EnergyCost, PriorityLevel, TaskSource


class YouGileService:
    """
    YouGile API integration based on API v2.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.base_url = self.settings.yougile_base_url.rstrip("/")
        self._cached_token: str | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        token = self._resolve_api_token()
        if token:
            # Keep both styles to be tolerant across workspace configurations.
            headers["Authorization"] = f"Bearer {token}"
            headers["X-API-KEY"] = token
        return headers

    def _resolve_api_token(self) -> str:
        if self._cached_token:
            return self._cached_token
        if self.settings.yougile_api_key and self.settings.yougile_api_key != "yougile-api":
            self._cached_token = self.settings.yougile_api_key
            return self.settings.yougile_api_key
        token = self._login_for_token()
        self._cached_token = token or self.settings.yougile_api_key
        return self._cached_token

    def _login_for_token(self) -> str | None:
        if not self.settings.yougile_email or not self.settings.yougile_password:
            return None
        payload = {"email": self.settings.yougile_email, "password": self.settings.yougile_password}
        with httpx.Client(base_url=self.base_url, timeout=15) as client:
            for path in ("/auth/login", "/login", "/api-v2/auth/login"):
                try:
                    response = client.post(path, json=payload)
                    if response.status_code >= 400:
                        continue
                    data = response.json()
                    for token_key in ("token", "access_token", "apiKey"):
                        token = data.get(token_key)
                        if token:
                            return token
                except Exception:
                    continue
        return None

    def _normalize_priority(self, raw: str | None) -> PriorityLevel:
        value = (raw or "").lower()
        if "high" in value or value in {"3", "urgent"}:
            return PriorityLevel.high
        if "low" in value or value == "1":
            return PriorityLevel.low
        return PriorityLevel.medium

    def _normalize_energy(self, raw: str | None) -> EnergyCost:
        value = (raw or "").lower()
        if value in {"high", "hard"}:
            return EnergyCost.high
        if value in {"low", "easy"}:
            return EnergyCost.low
        return EnergyCost.medium

    def _normalize_duration(self, raw_duration) -> int:
        if isinstance(raw_duration, int) and raw_duration > 0:
            return raw_duration
        if isinstance(raw_duration, str) and raw_duration.isdigit():
            return int(raw_duration)
        return 60

    def _extract_tasks(self, payload: dict) -> list[dict]:
        for key in ("tasks", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return []

    def fetch_tasks(self, user_id: int) -> list[dict]:
        _ = user_id
        if not self._resolve_api_token():
            return []

        endpoints = ["/tasks", "/api-v2/tasks", "/projects/tasks"]
        raw_tasks: list[dict] = []
        with httpx.Client(base_url=self.base_url, timeout=15) as client:
            for path in endpoints:
                try:
                    response = client.get(path, headers=self._headers())
                    if response.status_code >= 400:
                        continue
                    payload = response.json()
                    raw_tasks = self._extract_tasks(payload if isinstance(payload, dict) else {"items": payload})
                    if raw_tasks:
                        break
                except Exception:
                    continue

        tasks: list[dict] = []
        for row in raw_tasks:
            title = row.get("title") or row.get("name") or "Untitled YouGile task"
            deadline_raw = row.get("deadline") or row.get("dueDate") or row.get("due_at")
            deadline = date_parser.parse(deadline_raw) if deadline_raw else None
            tasks.append(
                {
                    "external_ref": str(row.get("id") or row.get("_id") or f"yg-{title}"),
                    "title": title,
                    "duration_minutes": self._normalize_duration(row.get("duration_minutes") or row.get("estimate")),
                    "priority": self._normalize_priority(row.get("priority")),
                    "deadline": deadline,
                    "source": TaskSource.yougile,
                    "energy_cost": self._normalize_energy(row.get("energy_cost")),
                    "status": row.get("status", "open"),
                }
            )
        return tasks

    def mark_task_scheduled(self, external_ref: str) -> None:
        if not self._resolve_api_token():
            return
        with httpx.Client(base_url=self.base_url, timeout=15) as client:
            for path in (f"/tasks/{external_ref}", f"/api-v2/tasks/{external_ref}"):
                try:
                    response = client.patch(path, headers=self._headers(), json={"status": "scheduled", "scheduledAt": datetime.utcnow().isoformat()})
                    if response.status_code < 400:
                        return
                except Exception:
                    continue
