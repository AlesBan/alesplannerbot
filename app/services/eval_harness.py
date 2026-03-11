from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.models import CalendarAccount, User, UserMemory
from app.services.calendar_read_service import CalendarReadService
from app.services.intent_profile_service import IntentProfileService
from app.services.query_profile_matcher import QueryProfileMatcher


RUNS_DIR = Path("eval_runs")
TEMPLATES_PATH = Path(__file__).resolve().parents[1] / "data" / "eval_question_templates.json"
DEFAULT_USER_QUESTIONS_PATH = RUNS_DIR / "user-questions.csv"


class EvalHarnessService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.matcher = QueryProfileMatcher()

    @staticmethod
    def _load_templates() -> dict[str, list[str]]:
        if not TEMPLATES_PATH.exists():
            return {"current_focus": [], "bedtime": [], "today_plan": []}
        payload = json.loads(TEMPLATES_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"current_focus": [], "bedtime": [], "today_plan": []}
        return {
            "current_focus": [str(v) for v in payload.get("current_focus", [])],
            "bedtime": [str(v) for v in payload.get("bedtime", [])],
            "today_plan": [str(v) for v in payload.get("today_plan", [])],
        }

    @staticmethod
    def _save_run(run_data: dict) -> None:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        path = RUNS_DIR / f"{run_data['run_id']}.json"
        path.write_text(json.dumps(run_data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def load_run(run_id: str) -> dict | None:
        path = RUNS_DIR / f"{run_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def create_questions_template(path: str | None = None, rows: int = 30) -> str:
        import csv

        target = Path(path) if path else DEFAULT_USER_QUESTIONS_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        samples = [
            ("current_focus", "Что у меня на сейчас?"),
            ("bedtime", "Когда я должен быть в кровати?"),
            ("today_plan", "Покажи события на сегодня"),
            ("", "Что мне делать прямо сейчас?"),
            ("", "Во сколько ложиться сегодня?"),
            ("", "Что запланировано на сегодня?"),
        ]
        with target.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["expected_intent(optional)", "question"])
            for i in range(rows):
                row = samples[i % len(samples)]
                writer.writerow([row[0], row[1]])
        return str(target.resolve())

    @staticmethod
    def _load_questions_from_file(path: str) -> list[tuple[str, str]]:
        import csv

        source = Path(path)
        if not source.exists():
            return []
        ext = source.suffix.lower()
        rows: list[tuple[str, str]] = []
        if ext == ".csv":
            with source.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    q = (row.get("question") or "").strip()
                    if not q:
                        continue
                    expected = (row.get("expected_intent(optional)") or row.get("expected_intent") or "").strip().lower()
                    rows.append((expected, q))
            return rows
        if ext == ".txt":
            for line in source.read_text(encoding="utf-8").splitlines():
                raw = line.strip()
                if not raw or raw.startswith("#"):
                    continue
                if "|" in raw:
                    expected, q = raw.split("|", 1)
                    rows.append((expected.strip().lower(), q.strip()))
                else:
                    rows.append(("", raw))
            return rows
        if ext == ".json":
            payload = json.loads(source.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        q = str(item.get("question") or "").strip()
                        if not q:
                            continue
                        expected = str(item.get("expected_intent") or "").strip().lower()
                        rows.append((expected, q))
                    elif isinstance(item, str):
                        q = item.strip()
                        if q:
                            rows.append(("", q))
            return rows
        return []

    @staticmethod
    def rate_item(run_id: str, item_id: int, rating: str, note: str = "") -> dict | None:
        run = EvalHarnessService.load_run(run_id)
        if not run:
            return None
        for item in run.get("items", []):
            if int(item.get("id") or -1) == int(item_id):
                item["rating"] = rating
                item["note"] = note
                item["rated_at"] = datetime.now(timezone.utc).isoformat()
                break
        EvalHarnessService._save_run(run)
        return run

    @staticmethod
    def _format_current_focus(events: list, timezone_name: str) -> str:
        if not events:
            return "Сейчас активного дела в календаре нет."
        try:
            tz = ZoneInfo(timezone_name)
        except Exception:
            tz = ZoneInfo("UTC")
        now_local = datetime.now(tz)
        normalized: list[tuple[datetime, datetime, str]] = []
        for event in events:
            title = (event.title or "Без названия").strip() or "Без названия"
            start = event.start_at.replace(tzinfo=timezone.utc).astimezone(tz)
            end = event.end_at.replace(tzinfo=timezone.utc).astimezone(tz)
            normalized.append((start, end, title))
        normalized.sort(key=lambda x: x[0])
        current = next((row for row in normalized if row[0] <= now_local < row[1]), None)
        if current:
            return f"Сейчас: {current[0].strftime('%H:%M')}–{current[1].strftime('%H:%M')} {current[2]}."
        upcoming = next((row for row in normalized if row[0] > now_local), None)
        if upcoming:
            return f"Сейчас свободно. Следующее: {upcoming[0].strftime('%H:%M')}–{upcoming[1].strftime('%H:%M')} {upcoming[2]}."
        return "На сегодня активных дел больше нет."

    @staticmethod
    def _extract_bedtime(events: list, timezone_name: str) -> str | None:
        try:
            tz = ZoneInfo(timezone_name)
        except Exception:
            tz = ZoneInfo("UTC")
        matches: list[datetime] = []
        for event in events:
            title = (event.title or "").lower()
            if "в кровати" in title or "bed" in title or "сон" == title.strip():
                start = event.start_at.replace(tzinfo=timezone.utc).astimezone(tz)
                matches.append(start)
        if not matches:
            return None
        matches.sort()
        return matches[0].strftime("%H:%M")

    @staticmethod
    def _format_day(events: list, timezone_name: str) -> str:
        return CalendarReadService.format_events(events, timezone_name, max_items=None)

    def _answer_question(self, question: str, user_id: int, timezone_name: str) -> tuple[str, str, float]:
        read = CalendarReadService(self.db)
        _, events = read.list_day_events(user_id, timezone_name, 0)
        profiles = IntentProfileService(self.db).get_profiles(user_id)
        profile, score = self.matcher.classify(question, profiles, candidates=("current_focus", "bedtime"))

        lower = (question or "").strip().lower()
        looks_like_today = any(token in lower for token in ["сегодня", "распис", "план", "событи"])
        if profile == "bedtime":
            bedtime = self._extract_bedtime(events, timezone_name)
            if bedtime:
                return f"В {bedtime}.", "bedtime", score
            return "Не нашел время 'В кровати' в событиях на сегодня.", "bedtime", score
        if profile == "current_focus":
            return self._format_current_focus(events, timezone_name), "current_focus", score
        if looks_like_today:
            return self._format_day(events, timezone_name), "today_plan", score
        return self._format_current_focus(events, timezone_name), "fallback_current_focus", score

    def _resolve_timezone_name(self, user: User) -> str:
        settings_tz = get_settings().timezone or "UTC"

        mem = (
            self.db.query(UserMemory)
            .filter(UserMemory.user_id == user.id, UserMemory.key == "calendar_timezone")
            .one_or_none()
        )
        memory_tz = (mem.value or "").strip() if mem else ""
        if memory_tz:
            try:
                ZoneInfo(memory_tz)
                return memory_tz
            except Exception:
                pass

        account = (
            self.db.query(CalendarAccount)
            .filter(CalendarAccount.user_id == user.id, CalendarAccount.is_primary.is_(True))
            .one_or_none()
        )
        account_tz = (account.timezone or "").strip() if account else ""
        if account_tz:
            try:
                ZoneInfo(account_tz)
                return account_tz
            except Exception:
                pass

        user_tz = (user.timezone or "").strip()
        if user_tz and user_tz.upper() != "UTC":
            try:
                ZoneInfo(user_tz)
                return user_tz
            except Exception:
                pass

        if settings_tz and settings_tz.upper() != "UTC":
            try:
                ZoneInfo(settings_tz)
                return settings_tz
            except Exception:
                pass

        return "UTC"

    def generate_run(self, telegram_id: int, count: int = 100, questions_source_path: str | None = None) -> dict:
        user = self.db.query(User).filter(User.telegram_id == telegram_id).one_or_none()
        if not user:
            raise ValueError("User not found")
        timezone_name = self._resolve_timezone_name(user)
        populated: list[tuple[str, str]] = []
        source_used = "templates"
        if questions_source_path:
            from_file = self._load_questions_from_file(questions_source_path)
            if from_file:
                populated = from_file
                source_used = str(Path(questions_source_path))

        if not populated:
            templates = self._load_templates()
            buckets = [("current_focus", 0.4), ("bedtime", 0.3), ("today_plan", 0.3)]
            for key, ratio in buckets:
                qty = max(1, int(count * ratio))
                choices = templates.get(key, []) or []
                for _ in range(qty):
                    if choices:
                        populated.append((key, random.choice(choices)))
            while len(populated) < count:
                key = random.choice(["current_focus", "bedtime", "today_plan"])
                choices = templates.get(key, []) or []
                populated.append((key, random.choice(choices) if choices else "Что у меня на сейчас?"))
            random.shuffle(populated)

        items: list[dict] = []
        selected_rows = populated if questions_source_path and source_used != "templates" else populated[:count]
        for idx, (expected_intent, question) in enumerate(selected_rows, start=1):
            answer, predicted_intent, score = self._answer_question(question, user.id, timezone_name)
            items.append(
                {
                    "id": idx,
                    "question": question,
                    "answer": answer,
                    "expected_intent": expected_intent,
                    "predicted_intent": predicted_intent,
                    "score": round(float(score), 4),
                    "rating": "",
                    "note": "",
                }
            )

        run = {
            "run_id": str(uuid.uuid4()),
            "telegram_id": telegram_id,
            "timezone": timezone_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": source_used,
            "items": items,
        }
        self._save_run(run)
        return run

    @staticmethod
    def _looks_like_time_answer(text: str) -> bool:
        import re

        return bool(re.search(r"\b([01]?\d|2[0-3]):[0-5]\d\b", text or ""))

    @classmethod
    def _autograde_item(cls, item: dict) -> tuple[str, str]:
        expected = str(item.get("expected_intent") or "")
        predicted = str(item.get("predicted_intent") or "")
        answer = str(item.get("answer") or "").strip()
        if not answer:
            return "-", "empty answer"
        if expected != predicted and not (expected == "today_plan" and predicted == "fallback_current_focus"):
            return "-", f"intent mismatch: expected={expected}, predicted={predicted}"
        if expected == "bedtime" and not cls._looks_like_time_answer(answer):
            return "-", "bedtime answer has no time"
        if expected == "today_plan" and "-" not in answer:
            return "-", "today plan answer is not event-list-like"
        if expected == "current_focus" and ("сейчас" not in answer.lower() and "следующее" not in answer.lower()):
            return "-", "current focus phrasing missing"
        return "+", "autograde pass"

    @classmethod
    def auto_rate_run(cls, run_id: str) -> dict | None:
        run = cls.load_run(run_id)
        if not run:
            return None
        good = 0
        bad = 0
        for item in run.get("items", []):
            rating, note = cls._autograde_item(item)
            item["rating"] = rating
            item["note"] = note
            item["rated_at"] = datetime.now(timezone.utc).isoformat()
            if rating == "+":
                good += 1
            else:
                bad += 1
        run["autograded"] = True
        run["autograde_summary"] = {
            "good": good,
            "bad": bad,
            "accuracy": round(good / max(1, (good + bad)), 4),
        }
        cls._save_run(run)
        return run
