from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.ai.context_engine import ContextEngine
from app.config import get_settings
from app.database.models import AgentRun, AgentStep, CalendarEvent
from app.integrations.yougile import YouGileService
from app.integrations.openai_client import OpenAIClient
from app.services.calendar_domain_service import CalendarDomainService
from app.services.calendar_read_service import CalendarReadService
from app.services.calendar_sync_service import CalendarSyncService


class AgentOrchestrator:
    """
    Lightweight tool-using orchestrator:
    LLM decides next backend action, tool executes, LLM gets tool result.
    """

    def __init__(self, db, user_id: int) -> None:
        self.db = db
        self.user_id = user_id
        self.ai = OpenAIClient()
        self.read = CalendarReadService(db)
        self.domain = CalendarDomainService(db)
        self.sync = CalendarSyncService(db)
        self.context = ContextEngine(db, user_id)
        self.settings = get_settings()

    def run(self, user_message: str, provider_calendar_id: str, max_steps: int = 4) -> str | None:
        if not self.ai.client:
            return None
        run_row = self._begin_run(user_message)
        state: list[dict] = []
        for step_no in range(1, max_steps + 1):
            action = self._plan_next_action(user_message, state)
            if not action:
                self._finish_run(run_row, status="failed", answer=None, error="planner_failed")
                return None
            if action.get("tool") == "final":
                text = (action.get("text") or "").strip()
                self._log_step(run_row, step_no, action, {"final": True, "text": text})
                self._finish_run(run_row, status="completed", answer=text or None, error=None)
                return text or None
            tool_result = self._execute_tool(action, provider_calendar_id)
            self._log_step(run_row, step_no, action, tool_result)
            state.append({"action": action, "result": tool_result})
            if tool_result.get("final"):
                answer = (tool_result.get("text") or "").strip() or None
                self._finish_run(run_row, status="completed", answer=answer, error=None)
                return answer
        self._finish_run(run_row, status="failed", answer=None, error="max_steps_exceeded")
        return None

    def _plan_next_action(self, user_message: str, state: list[dict]) -> dict | None:
        system_prompt = (
            "You are an action-planning orchestrator for a calendar assistant. "
            "Choose one next tool call OR final response. "
            "Return ONLY strict JSON.\n"
            "Allowed tools:\n"
            "1) calendar_day {date:'YYYY-MM-DD' optional, day_offset:int optional}\n"
            "2) calendar_week {date:'YYYY-MM-DD' optional, day_offset:int optional}\n"
            "3) calendar_after {anchor:string}\n"
            "4) calendar_add {title:string, start_time:'HH:MM', end_time:'HH:MM', date:'YYYY-MM-DD' optional, day_offset:int optional}\n"
            "5) calendar_update {ref:string, new_start_time:'HH:MM', new_end_time:'HH:MM'}\n"
            "6) calendar_delete {ref:string}\n"
            "7) connections_check {}\n"
            "8) final {text:string}\n"
            "Rules: prefer getting data first before deletion/add if ambiguous. "
            "If user refers to pronoun (его/ее/it), use calendar_delete with ref='last_focus'."
        )
        payload = {
            "user_message": user_message,
            "tool_history": state,
            "memory": {
                "last_calendar_snapshot": self.context.get_memory("last_calendar_snapshot") or "",
                "last_calendar_focus_event": self.context.get_memory("last_calendar_focus_event") or "",
            },
        }
        raw = self.ai.chat_completion(system_prompt, json.dumps(payload, ensure_ascii=False))
        if not raw or raw.startswith("AI unavailable:") or "AI is disabled" in raw:
            return None
        try:
            start = raw.find("{")
            end = raw.rfind("}")
            if start == -1 or end == -1:
                return None
            data = json.loads(raw[start : end + 1])
            if not isinstance(data, dict):
                return None
            tool = str(data.get("tool") or "").strip()
            if tool not in {
                "calendar_day",
                "calendar_week",
                "calendar_after",
                "calendar_add",
                "calendar_update",
                "calendar_delete",
                "connections_check",
                "final",
            }:
                return None
            return data
        except Exception:
            return None

    def _execute_tool(self, action: dict, provider_calendar_id: str) -> dict:
        tool = action.get("tool")
        if tool == "calendar_day":
            return self._tool_calendar_day(action)
        if tool == "calendar_week":
            return self._tool_calendar_week(action)
        if tool == "calendar_after":
            return self._tool_calendar_after(action)
        if tool == "calendar_add":
            return self._tool_calendar_add(action, provider_calendar_id)
        if tool == "calendar_update":
            return self._tool_calendar_update(action, provider_calendar_id)
        if tool == "calendar_delete":
            return self._tool_calendar_delete(action, provider_calendar_id)
        if tool == "connections_check":
            return self._tool_connections_check()
        return {"ok": False, "error": "unknown_tool"}

    def _save_pending_action(self, action_type: str, payload: dict) -> None:
        expires_at = datetime.utcnow() + timedelta(minutes=15)
        body = {
            "type": action_type,
            "payload": payload,
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        self.context.set_memory("pending_action", json.dumps(body, ensure_ascii=False))

    def _begin_run(self, user_message: str) -> AgentRun:
        row = AgentRun(
            user_id=self.user_id,
            run_id=str(uuid.uuid4()),
            user_message=user_message,
            status="running",
        )
        self.db.add(row)
        self.db.flush()
        return row

    def _log_step(self, run_row: AgentRun, step_no: int, action: dict, result: dict) -> None:
        self.db.add(
            AgentStep(
                run_id=run_row.id,
                step_no=step_no,
                action_json=json.dumps(action, ensure_ascii=False),
                result_json=json.dumps(result, ensure_ascii=False),
            )
        )
        self.db.flush()

    def _finish_run(self, run_row: AgentRun, status: str, answer: str | None, error: str | None) -> None:
        run_row.status = status
        run_row.final_answer = answer
        run_row.error = error
        self.db.commit()

    def _timezone(self) -> str:
        return self.context.get_memory("calendar_timezone") or self.settings.timezone or "UTC"

    def _parse_target_date(self, date_text: str | None, day_offset: int | None = None):
        tz_name = self._timezone()
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        base = datetime.now(tz).date()
        if date_text:
            try:
                return datetime.fromisoformat(date_text).date()
            except Exception:
                pass
            try:
                m = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", date_text)
                if m:
                    return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date()
            except Exception:
                pass
        return base + timedelta(days=int(day_offset or 0))

    def _save_snapshot(self, day_label: str, events: list) -> None:
        tz_name = self._timezone()
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        payload = {"date": day_label, "timezone": tz_name, "events": []}
        for idx, event in enumerate(events, start=1):
            start = event.start_at.replace(tzinfo=timezone.utc).astimezone(tz)
            end = event.end_at.replace(tzinfo=timezone.utc).astimezone(tz)
            payload["events"].append(
                {
                    "index": idx,
                    "local_event_id": event.id,
                    "title": (event.title or "Без названия").strip() or "Без названия",
                    "start": start.strftime("%H:%M"),
                    "end": end.strftime("%H:%M"),
                }
            )
        self.context.set_memory("last_calendar_snapshot", json.dumps(payload, ensure_ascii=False))

    def _tool_calendar_day(self, action: dict) -> dict:
        date_text = action.get("date")
        day_offset = action.get("day_offset")
        target_date = self._parse_target_date(date_text if isinstance(date_text, str) else None, day_offset if isinstance(day_offset, int) else 0)
        day_label, events = self.read.list_date_events(self.user_id, self._timezone(), target_date)
        self._save_snapshot(day_label, events)
        rows = CalendarReadService.format_events(events, self._timezone(), max_items=None)
        return {"ok": True, "date": day_label, "count": len(events), "rows": rows}

    def _tool_calendar_week(self, action: dict) -> dict:
        date_text = action.get("date")
        day_offset = action.get("day_offset")
        start_date = self._parse_target_date(date_text if isinstance(date_text, str) else None, day_offset if isinstance(day_offset, int) else 0)
        lines: list[str] = []
        total = 0
        for i in range(7):
            target = start_date + timedelta(days=i)
            day_label, events = self.read.list_date_events(self.user_id, self._timezone(), target)
            total += len(events)
            if events:
                rows = CalendarReadService.format_events(events, self._timezone(), max_items=6)
                lines.append(f"{day_label}:\n{rows}")
            else:
                lines.append(f"{day_label}: событий нет.")
        return {"ok": True, "count": total, "rows": "\n\n".join(lines)}

    def _tool_calendar_after(self, action: dict) -> dict:
        anchor = str(action.get("anchor") or "").strip().lower()
        raw = self.context.get_memory("last_calendar_snapshot")
        if not raw:
            return {"ok": False, "error": "no_snapshot"}
        try:
            snap = json.loads(raw)
            events = snap.get("events") or []
        except Exception:
            return {"ok": False, "error": "bad_snapshot"}
        pivot = None
        m_range = re.search(r"(\d{1,2}:\d{2})\s*[–-]\s*(\d{1,2}:\d{2})", anchor)
        m_time = re.search(r"(\d{1,2}:\d{2})", anchor)
        if m_range:
            pivot = m_range.group(2)
        elif m_time:
            pivot = m_time.group(1)
        else:
            for row in events:
                title = (row.get("title") or "").lower()
                if anchor and anchor in title:
                    pivot = row.get("end")
                    self.context.set_memory(
                        "last_calendar_focus_event",
                        json.dumps(
                            {
                                "date": snap.get("date"),
                                "local_event_id": row.get("local_event_id"),
                                "title": row.get("title"),
                                "start": row.get("start"),
                                "end": row.get("end"),
                            },
                            ensure_ascii=False,
                        ),
                    )
                    break
        if not pivot:
            return {"ok": False, "error": "anchor_not_found"}
        tail = [row for row in events if row.get("start", "") >= pivot]
        lines = "\n".join([f"- {row['start']}–{row['end']}  {row['title']}" for row in tail[:30]]) if tail else "После этого событий нет."
        return {"ok": True, "pivot": pivot, "rows": lines}

    def _tool_calendar_add(self, action: dict, provider_calendar_id: str) -> dict:
        title = str(action.get("title") or "").strip()
        start_time = str(action.get("start_time") or "").strip()
        end_time = str(action.get("end_time") or "").strip()
        if not title or not re.match(r"^\d{1,2}:\d{2}$", start_time) or not re.match(r"^\d{1,2}:\d{2}$", end_time):
            return {"ok": False, "error": "invalid_add_params"}
        target_date = self._parse_target_date(action.get("date"), action.get("day_offset") if isinstance(action.get("day_offset"), int) else 0)
        tz_name = self._timezone()
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        sh, sm = map(int, start_time.split(":"))
        eh, em = map(int, end_time.split(":"))
        start_local = datetime(target_date.year, target_date.month, target_date.day, sh, sm, tzinfo=tz)
        end_local = datetime(target_date.year, target_date.month, target_date.day, eh, em, tzinfo=tz)
        if end_local <= start_local:
            end_local = end_local + timedelta(days=1)
        self._save_pending_action(
            "calendar_add_confirm",
            {
                "title": title,
                "start_utc": start_local.astimezone(timezone.utc).replace(tzinfo=None).isoformat(),
                "end_utc": end_local.astimezone(timezone.utc).replace(tzinfo=None).isoformat(),
                "timezone": tz_name,
                "day_label": target_date.strftime("%d.%m.%Y"),
            },
        )
        return {
            "ok": True,
            "text": f"Подтверди добавление: {start_local.strftime('%H:%M')}–{end_local.strftime('%H:%M')} {title}. Ответь: да / нет.",
            "final": True,
        }

    def _resolve_ref_candidates(self, ref: str, events: list[dict]) -> list[dict]:
        candidates = []
        if ref in {"last_focus", "его", "ее", "её", "it", "this", "that"}:
            f_raw = self.context.get_memory("last_calendar_focus_event")
            if f_raw:
                try:
                    focus = json.loads(f_raw)
                    candidates = [row for row in events if int(row.get("local_event_id") or 0) == int(focus.get("local_event_id") or 0)]
                except Exception:
                    candidates = []
        if candidates:
            return [row for row in candidates if row.get("local_event_id")]
        m_idx = re.search(r"(?:#|номер|id)\s*(\d+)", ref)
        m_range = re.search(r"(\d{1,2}:\d{2})\s*[–-]\s*(\d{1,2}:\d{2})", ref)
        if m_idx:
            idx = int(m_idx.group(1))
            candidates = [row for row in events if int(row.get("index") or 0) == idx]
        elif m_range:
            s, e = m_range.group(1), m_range.group(2)
            candidates = [row for row in events if row.get("start") == s and row.get("end") == e]
        elif ref:
            candidates = [row for row in events if ref in (row.get("title") or "").lower()]
        return [row for row in candidates if row.get("local_event_id")]

    def _tool_calendar_update(self, action: dict, provider_calendar_id: str) -> dict:
        ref = str(action.get("ref") or "").strip().lower()
        new_start = str(action.get("new_start_time") or "").strip()
        new_end = str(action.get("new_end_time") or "").strip()
        if not ref or not re.match(r"^\d{1,2}:\d{2}$", new_start) or not re.match(r"^\d{1,2}:\d{2}$", new_end):
            return {"ok": False, "error": "invalid_update_params"}

        raw = self.context.get_memory("last_calendar_snapshot")
        if not raw:
            return {"ok": False, "error": "no_snapshot"}
        try:
            snap = json.loads(raw)
            events = snap.get("events") or []
            snap_date = str(snap.get("date") or "")
        except Exception:
            return {"ok": False, "error": "bad_snapshot"}

        candidates = self._resolve_ref_candidates(ref, events)
        if not candidates:
            return {"ok": False, "error": "not_found"}
        if len(candidates) > 1:
            rows = "\n".join([f"- #{row['index']} {row['start']}–{row['end']} {row['title']}" for row in candidates[:8]])
            return {"ok": True, "final": True, "text": f"Нашел несколько событий. Уточни номер:\n{rows}"}

        target = candidates[0]
        event_id = int(target["local_event_id"])
        event = self.db.query(CalendarEvent).filter(CalendarEvent.id == event_id, CalendarEvent.user_id == self.user_id).one_or_none()
        if not event:
            return {"ok": False, "error": "event_missing"}
        tz_name = self._timezone()
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        try:
            target_date = datetime.strptime(snap_date, "%d.%m.%Y").date()
        except Exception:
            target_date = event.start_at.replace(tzinfo=timezone.utc).astimezone(tz).date()
        sh, sm = map(int, new_start.split(":"))
        eh, em = map(int, new_end.split(":"))
        start_local = datetime(target_date.year, target_date.month, target_date.day, sh, sm, tzinfo=tz)
        end_local = datetime(target_date.year, target_date.month, target_date.day, eh, em, tzinfo=tz)
        if end_local <= start_local:
            end_local += timedelta(days=1)
        patch = {
            "start_at": start_local.astimezone(timezone.utc).replace(tzinfo=None),
            "end_at": end_local.astimezone(timezone.utc).replace(tzinfo=None),
        }
        self._save_pending_action(
            "calendar_update_confirm",
            {
                "local_event_id": event_id,
                "title": target["title"],
                "new_start_utc": patch["start_at"].isoformat(),
                "new_end_utc": patch["end_at"].isoformat(),
                "new_start": new_start,
                "new_end": new_end,
            },
        )
        return {"ok": True, "final": True, "text": f"Подтверди перенос: {target['title']} на {new_start}–{new_end}. Ответь: да / нет."}

    def _tool_calendar_delete(self, action: dict, provider_calendar_id: str) -> dict:
        ref = str(action.get("ref") or "").strip().lower()
        raw = self.context.get_memory("last_calendar_snapshot")
        if not raw:
            return {"ok": False, "error": "no_snapshot"}
        try:
            snap = json.loads(raw)
            events = snap.get("events") or []
        except Exception:
            return {"ok": False, "error": "bad_snapshot"}

        candidates = self._resolve_ref_candidates(ref, events)
        if not candidates:
            return {"ok": False, "error": "not_found"}
        if len(candidates) > 1:
            rows = "\n".join([f"- #{row['index']} {row['start']}–{row['end']} {row['title']}" for row in candidates[:8]])
            return {"ok": True, "final": True, "text": f"Нашел несколько событий. Уточни номер:\n{rows}"}

        target = candidates[0]
        self._save_pending_action(
            "calendar_delete_confirm",
            {
                "local_event_id": int(target["local_event_id"]),
                "title": target["title"],
                "start": target["start"],
                "end": target["end"],
                "day_label": snap.get("date"),
            },
        )
        self.context.set_memory(
            "last_calendar_focus_event",
            json.dumps(
                {
                    "date": snap.get("date"),
                    "local_event_id": target.get("local_event_id"),
                    "title": target.get("title"),
                    "start": target.get("start"),
                    "end": target.get("end"),
                },
                ensure_ascii=False,
            ),
        )
        return {"ok": True, "final": True, "text": f"Подтверди удаление: {target['start']}–{target['end']} {target['title']}. Ответь: да / нет."}

    def _tool_connections_check(self) -> dict:
        ai_state = "подключен" if self.ai.provider != "disabled" else "не подключен"
        yougile_count = len(YouGileService().fetch_tasks(user_id=self.user_id))
        text = (
            "Статус интеграций:\n"
            f"- Calendar: локальная БД + Google sync\n"
            f"- YouGile: доступен, получено задач {yougile_count}\n"
            f"- AI: {ai_state} ({self.ai.provider}/{self.ai.model})"
        )
        return {"ok": True, "final": True, "text": text}
