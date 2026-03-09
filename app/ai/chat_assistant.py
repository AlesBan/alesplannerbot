import json
import re
from datetime import datetime, timedelta
from enum import Enum

from dateutil import parser as date_parser

from app.database.models import EnergyCost, PriorityLevel
from app.integrations.openai_client import OpenAIClient


class ChatIntent(str, Enum):
    add_task = "add_task"
    plan_day = "plan_day"
    suggest_free = "suggest_free"
    weekly_report = "weekly_report"
    sync = "sync"
    calendar_check = "calendar_check"
    yougile_check = "yougile_check"
    connections_check = "connections_check"
    complete_task = "complete_task"
    general_chat = "general_chat"


class ChatAssistant:
    def __init__(self) -> None:
        self.openai = OpenAIClient()

    def detect_intent(self, text: str) -> ChatIntent:
        lower = text.lower().strip()
        if "/start" in lower:
            return ChatIntent.general_chat
        if self._is_connections_query(lower):
            return ChatIntent.connections_check
        if self._is_calendar_query(lower):
            return ChatIntent.calendar_check
        if self._is_yougile_query(lower):
            return ChatIntent.yougile_check
        if any(k in lower for k in ["добавь задачу", "add task", "задача", "запланируй задачу"]):
            return ChatIntent.add_task
        if any(k in lower for k in ["сегодня", "завтра", "послезавтра", "today", "tomorrow"]) and any(
            k in lower for k in ["надо", "нужно", "сделать", "задач", "task", "todo", "plan task", "добав"]
        ):
            return ChatIntent.add_task
        if any(k in lower for k in ["план на день", "распланируй", "спланируй", "plan day", "plan my day"]):
            return ChatIntent.plan_day
        if any(k in lower for k in ["чем заняться", "свободное время", "free time", "suggest activity"]):
            return ChatIntent.suggest_free
        if any(k in lower for k in ["отчет", "report", "продуктивность", "неделя"]):
            return ChatIntent.weekly_report
        if any(k in lower for k in ["синк", "sync", "yougile"]):
            return ChatIntent.sync
        if any(k in lower for k in ["выполнил задачу", "complete task", "done task"]):
            return ChatIntent.complete_task
        return ChatIntent.general_chat

    @staticmethod
    def _is_calendar_query(lower: str) -> bool:
        calendar_markers = [
            "календар",
            "клаендар",
            "calendar",
            "расписани",
            "планы на сегодня",
            "что сегодня",
            "события на сегодня",
        ]
        return any(marker in lower for marker in calendar_markers)

    @staticmethod
    def _is_yougile_query(lower: str) -> bool:
        return any(marker in lower for marker in ["yougile", "югиле", "югайл", "юджайл"])

    def _is_connections_query(self, lower: str) -> bool:
        asks_status = any(marker in lower for marker in ["подключ", "доступ", "работа", "статус", "connected"])
        target = self._is_calendar_query(lower) or self._is_yougile_query(lower) or any(
            marker in lower for marker in ["openai", "deepseek", "chatgpt", "ии", "ai"]
        )
        return asks_status and target

    def parse_task_from_text(self, text: str) -> dict:
        parsed = self._parse_with_llm(text)
        if parsed:
            return parsed
        return self._parse_with_rules(text)

    def parse_tasks_batch(self, text: str) -> list[dict]:
        llm_result = self._parse_batch_with_llm(text)
        if llm_result:
            return llm_result
        return self._parse_batch_with_rules(text)

    def _parse_batch_with_llm(self, text: str) -> list[dict] | None:
        system_prompt = (
            "Extract user tasks from a planning message. Return ONLY strict JSON array. "
            "Each item has keys: title, duration_minutes, priority, energy_cost, deadline_iso. "
            "priority and energy_cost in [low, medium, high], deadline_iso may be null. "
            "If message contains 'today/tomorrow', map deadlines accordingly."
        )
        raw = self.openai.chat_completion(system_prompt, text)
        if not raw or "AI is disabled" in raw or raw.startswith("AI unavailable:"):
            return None
        try:
            start = raw.find("[")
            end = raw.rfind("]")
            if start == -1 or end == -1:
                return None
            rows = json.loads(raw[start : end + 1])
            tasks: list[dict] = []
            for row in rows:
                title = (row.get("title") or "").strip()
                if not title:
                    continue
                duration = max(10, int(row.get("duration_minutes") or 30))
                pr = str(row.get("priority") or "medium").lower()
                en = str(row.get("energy_cost") or "medium").lower()
                deadline_iso = row.get("deadline_iso")
                deadline = date_parser.parse(deadline_iso) if deadline_iso else None
                tasks.append(
                    {
                        "title": title,
                        "duration_minutes": duration,
                        "priority": PriorityLevel(pr if pr in {"low", "medium", "high"} else "medium"),
                        "energy_cost": EnergyCost(en if en in {"low", "medium", "high"} else "medium"),
                        "deadline": deadline,
                    }
                )
            return tasks or None
        except Exception:
            return None

    def _parse_with_llm(self, text: str) -> dict | None:
        system_prompt = (
            "Extract task fields from user text and return ONLY strict JSON "
            "with keys: title, duration_minutes, priority, energy_cost, deadline_iso. "
            "priority in [low, medium, high], energy_cost in [low, medium, high], deadline_iso may be null."
        )
        raw = self.openai.chat_completion(system_prompt, text)
        if not raw or "AI is disabled" in raw or raw.startswith("AI unavailable:"):
            return None
        try:
            start = raw.find("{")
            end = raw.rfind("}")
            if start == -1 or end == -1:
                return None
            data = json.loads(raw[start : end + 1])
            title = (data.get("title") or "").strip()
            if not title:
                return None
            duration = int(data.get("duration_minutes") or 30)
            priority = str(data.get("priority") or "medium").lower()
            energy = str(data.get("energy_cost") or "medium").lower()
            deadline_iso = data.get("deadline_iso")
            deadline = date_parser.parse(deadline_iso) if deadline_iso else None
            return {
                "title": title,
                "duration_minutes": max(10, duration),
                "priority": PriorityLevel(priority if priority in {"low", "medium", "high"} else "medium"),
                "energy_cost": EnergyCost(energy if energy in {"low", "medium", "high"} else "medium"),
                "deadline": deadline,
            }
        except Exception:
            return None

    def _parse_with_rules(self, text: str) -> dict:
        lower = text.lower()
        clean = text.strip()

        duration = 30
        minutes_match = re.search(r"(\d+)\s*(мин|minute|min)", lower)
        hours_match = re.search(r"(\d+)\s*(час|hour|h)\b", lower)
        if minutes_match:
            duration = int(minutes_match.group(1))
        elif hours_match:
            duration = int(hours_match.group(1)) * 60

        priority = PriorityLevel.medium
        if any(k in lower for k in ["срочно", "важно", "urgent", "high priority"]):
            priority = PriorityLevel.high
        elif any(k in lower for k in ["не срочно", "когда-нибудь", "low priority"]):
            priority = PriorityLevel.low

        energy = EnergyCost.medium
        if any(k in lower for k in ["легк", "easy", "simple"]):
            energy = EnergyCost.low
        elif any(k in lower for k in ["сложн", "hard", "deep work"]):
            energy = EnergyCost.high

        deadline = None
        if "завтра" in lower:
            deadline = datetime.utcnow() + timedelta(days=1)
        else:
            try:
                deadline = date_parser.parse(clean, fuzzy=True)
            except Exception:
                deadline = None

        title = re.sub(
            r"(?i)(добавь задачу|add task|задача|нужно|please|сделай|поставь|create task|for|на)\s*",
            "",
            clean,
        ).strip(" .,-")
        if not title:
            title = clean[:80]

        return {
            "title": title,
            "duration_minutes": max(10, duration),
            "priority": priority,
            "energy_cost": energy,
            "deadline": deadline,
        }

    def _parse_batch_with_rules(self, text: str) -> list[dict]:
        lower = text.lower()
        now = datetime.utcnow()
        day_offsets = {
            "сегодня": 0,
            "today": 0,
            "завтра": 1,
            "tomorrow": 1,
            "послезавтра": 2,
        }

        # Split message by day markers while preserving marker context.
        marker_pattern = r"(?i)\b(сегодня|today|завтра|tomorrow|послезавтра)\b"
        parts = re.split(marker_pattern, text)
        tasks: list[dict] = []
        current_offset = None

        idx = 0
        while idx < len(parts):
            part = parts[idx].strip()
            if part.lower() in day_offsets:
                current_offset = day_offsets[part.lower()]
                idx += 1
                if idx < len(parts):
                    content = parts[idx]
                else:
                    content = ""
            else:
                content = part
            idx += 1

            for item in self._split_items(content):
                normalized = item.strip(" .,-")
                if len(normalized) < 3:
                    continue
                cleaned = re.sub(r"(?i)\b(надо|нужно|сделать|please|to|make|do)\b", "", normalized).strip(" .,-")
                if not cleaned:
                    continue
                single = self._parse_with_rules(cleaned)
                if current_offset is not None:
                    target_day = now + timedelta(days=current_offset)
                    single["deadline"] = target_day.replace(hour=20, minute=0, second=0, microsecond=0)
                tasks.append(single)

        if tasks:
            return tasks
        return [self._parse_with_rules(text)]

    @staticmethod
    def _split_items(content: str) -> list[str]:
        if not content.strip():
            return []
        chunks = re.split(r"[\n;,]+", content)
        items: list[str] = []
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            # Handle "and" lists: "docs and gym".
            and_split = re.split(r"(?i)\s+\b(и|and)\b\s+", chunk)
            merged: list[str] = []
            temp = ""
            for token in and_split:
                token = token.strip()
                if not token or token.lower() in {"и", "and"}:
                    if temp:
                        merged.append(temp.strip())
                        temp = ""
                    continue
                if not temp:
                    temp = token
                else:
                    temp = f"{temp} {token}"
            if temp:
                merged.append(temp.strip())
            items.extend(merged or [chunk])
        return items
