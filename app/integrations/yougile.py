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
        self._cached_api_base: str | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        token = self._resolve_api_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _resolve_api_token(self) -> str:
        if self._cached_token:
            return self._cached_token
        if self.settings.yougile_api_key and self.settings.yougile_api_key != "yougile-api":
            self._cached_token = self.settings.yougile_api_key
            self._cached_api_base = self._candidate_api_bases()[0]
            return self._cached_token
        token, api_base = self._create_key_via_credentials()
        self._cached_token = token or self.settings.yougile_api_key
        if api_base:
            self._cached_api_base = api_base
        return self._cached_token

    def _candidate_api_bases(self) -> list[str]:
        bases: list[str] = []
        raw = (self.base_url or "").strip().rstrip("/")
        if raw:
            bases.append(raw if raw.endswith("/api-v2") else f"{raw}/api-v2")
        bases.append("https://ru.yougile.com/api-v2")
        bases.append("https://api.yougile.com/api-v2")
        deduped: list[str] = []
        for base in bases:
            if base not in deduped:
                deduped.append(base)
        return deduped

    def _extract_items(self, payload) -> list[dict]:
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("content", "tasks", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return []

    def _pick_company(self, companies: list[dict]) -> dict | None:
        if not companies:
            return None
        wanted_id = (self.settings.yougile_company_id or "").strip()
        wanted_name = (self.settings.yougile_company_name or "").strip().lower()
        if wanted_id:
            for company in companies:
                if str(company.get("id") or "").strip() == wanted_id:
                    return company
        if wanted_name:
            for company in companies:
                if wanted_name in str(company.get("name") or "").strip().lower():
                    return company
        return companies[0]

    def _create_key_via_credentials(self) -> tuple[str | None, str | None]:
        if not self.settings.yougile_email or not self.settings.yougile_password:
            return None, None
        for api_base in self._candidate_api_bases():
            with httpx.Client(base_url=api_base, timeout=20) as client:
                try:
                    companies_resp = client.post(
                        "/auth/companies",
                        json={
                            "login": self.settings.yougile_email,
                            "password": self.settings.yougile_password,
                            "name": self.settings.yougile_company_name or "",
                        },
                    )
                    if companies_resp.status_code >= 400:
                        continue
                    companies = self._extract_items(companies_resp.json())
                    company = self._pick_company(companies)
                    company_id = str((company or {}).get("id") or "").strip()
                    if not company_id:
                        continue

                    keys_resp = client.post(
                        "/auth/keys/get",
                        json={
                            "login": self.settings.yougile_email,
                            "password": self.settings.yougile_password,
                            "companyId": company_id,
                        },
                    )
                    if keys_resp.status_code < 400:
                        keys_list = self._extract_items(keys_resp.json())
                        if keys_list:
                            key = str(keys_list[0].get("key") or keys_list[0].get("Key") or "").strip()
                            if key:
                                return key, api_base

                    create_resp = client.post(
                        "/auth/keys",
                        json={
                            "login": self.settings.yougile_email,
                            "password": self.settings.yougile_password,
                            "companyId": company_id,
                        },
                    )
                    if create_resp.status_code in {200, 201}:
                        key = str((create_resp.json() or {}).get("key") or "").strip()
                        if key:
                            return key, api_base
                except Exception:
                    continue
        return None, None

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

    def _parse_deadline(self, raw_deadline):
        if not raw_deadline:
            return None
        if isinstance(raw_deadline, str):
            try:
                return date_parser.parse(raw_deadline)
            except Exception:
                return None
        if isinstance(raw_deadline, (int, float)):
            ts = int(raw_deadline)
            if ts > 10_000_000_000:
                ts = ts // 1000
            try:
                return datetime.utcfromtimestamp(ts)
            except Exception:
                return None
        if isinstance(raw_deadline, dict):
            value = raw_deadline.get("deadline") or raw_deadline.get("startDate")
            return self._parse_deadline(value)
        return None

    def _fetch_tasks_direct(self, client: httpx.Client) -> list[dict]:
        for path in ("/tasks", "/tasks?limit=200"):
            try:
                response = client.get(path, headers=self._headers())
                if response.status_code >= 400:
                    continue
                rows = self._extract_items(response.json())
                if rows:
                    return rows
            except Exception:
                continue
        return []

    def _fetch_tasks_via_columns(self, client: httpx.Client) -> list[dict]:
        try:
            response = client.get("/columns", headers=self._headers())
            if response.status_code >= 400:
                return []
            columns = self._extract_items(response.json())
        except Exception:
            return []
        collected: list[dict] = []
        for column in columns[:30]:
            column_id = str(column.get("id") or "").strip()
            if not column_id:
                continue
            try:
                response = client.get(f"/tasks?columnId={column_id}", headers=self._headers())
                if response.status_code >= 400:
                    continue
                for task in self._extract_items(response.json()):
                    if task not in collected:
                        collected.append(task)
            except Exception:
                continue
        return collected

    def fetch_tasks(self, user_id: int) -> list[dict]:
        _ = user_id
        if not self._resolve_api_token():
            return []

        raw_tasks: list[dict] = []
        api_base = self._cached_api_base or self._candidate_api_bases()[0]
        with httpx.Client(base_url=api_base, timeout=20) as client:
            raw_tasks = self._fetch_tasks_direct(client)
            if not raw_tasks:
                raw_tasks = self._fetch_tasks_via_columns(client)

        tasks: list[dict] = []
        for row in raw_tasks:
            title = row.get("title") or row.get("name") or "Untitled YouGile task"
            deadline_raw = row.get("deadline") or row.get("dueDate") or row.get("due_at")
            deadline = self._parse_deadline(deadline_raw)
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
