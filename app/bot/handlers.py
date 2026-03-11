import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup

from app.ai.chat_assistant import ChatAssistant, ChatIntent
from app.ai.context_engine import ContextEngine
from app.ai.planner import AIPlanner
from app.ai.recommendations import RecommendationEngine
from app.database.db import SessionLocal
from app.database.models import Activity, EnergyCost, Habit, Task, TaskStatus, TrainingFeedback, User
from app.integrations.google_calendar import GoogleCalendarService
from app.integrations.openai_client import OpenAIClient
from app.services.calendar_domain_service import CalendarDomainService
from app.services.calendar_read_service import CalendarReadService
from app.services.calendar_sync_service import CalendarSyncService
from app.services.habit_tracker import HabitTracker
from app.services.intent_profile_service import IntentProfileService
from app.services.knowledge_service import KnowledgeService
from app.services.query_profile_matcher import QueryProfileMatcher
from app.services.scheduler import AIScheduler
from app.services.sync_service import SyncService
from app.services.task_manager import TaskManager
from app.services.eval_harness import EvalHarnessService
from app.config import get_settings

router = Router()
chat_assistant = ChatAssistant()
settings = get_settings()
BACKLOG_PATH = Path("CHAT_IMPROVEMENT_BACKLOG.md")
PROFILE_MATCHER = QueryProfileMatcher()
TRAINING_QUIZ_STATE_KEY = "training_quiz_state"


def _ensure_user(db, telegram_id: int, name: str) -> User:
    user = db.query(User).filter(User.telegram_id == telegram_id).one_or_none()
    if user:
        if (not user.timezone or user.timezone == "UTC") and settings.timezone and settings.timezone != "UTC":
            user.timezone = settings.timezone
            db.commit()
        return user
    user = User(telegram_id=telegram_id, name=name or "User", timezone=settings.timezone or "UTC")
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add_all(
        [
            Activity(user_id=user.id, name="reading", activity_type="mind", duration_minutes=30, location="home", season="any", energy_cost=EnergyCost.low),
            Activity(user_id=user.id, name="walking", activity_type="health", duration_minutes=45, location="outdoor", season="any", energy_cost=EnergyCost.low),
            Activity(user_id=user.id, name="exercise", activity_type="fitness", duration_minutes=40, location="gym", season="any", energy_cost=EnergyCost.medium),
            Activity(user_id=user.id, name="hobby", activity_type="creative", duration_minutes=60, location="home", season="any", energy_cost=EnergyCost.medium),
        ]
    )
    db.add_all(
        [
            Habit(user_id=user.id, name="exercise", frequency_per_week=3),
            Habit(user_id=user.id, name="visit grandmother", frequency_per_week=1),
            Habit(user_id=user.id, name="call grandmother", frequency_per_week=2),
        ]
    )
    db.commit()
    return user


def _build_plan_payload(db, user: User) -> dict[str, Any]:
    synced_count = SyncService(db).sync_yougile_tasks(user.id)
    task_manager = TaskManager(db)
    tasks = task_manager.list_open_tasks(user.id)
    scheduler = AIScheduler()
    day_start, day_end = scheduler.build_day_window(datetime.utcnow())
    calendar_events = GoogleCalendarService().list_events(user.id, day_start, day_end)
    free_slots = scheduler.detect_free_slots(day_start, day_end, calendar_events)
    scheduled = scheduler.schedule_tasks(tasks, free_slots)
    for item in scheduled:
        updated_task = task_manager.set_schedule(item.task_id, item.start, item.end)
        if updated_task and updated_task.source.value == "yougile" and updated_task.external_ref:
            SyncService(db).yougile.mark_task_scheduled(updated_task.external_ref)
    context = ContextEngine(db, user.id).export_memory()
    ai_text = AIPlanner().explain_plan(datetime.utcnow(), tasks, scheduled, context)
    overdue_habits = HabitTracker(db).get_overdue_habits(user.id)
    overdue_text = ""
    if overdue_habits:
        overdue_text = f"\nOverdue habits: {', '.join(h.name for h in overdue_habits)}"
    lines = [f"{item.start.strftime('%H:%M')}-{item.end.strftime('%H:%M')} {item.title}" for item in scheduled]
    return {"scheduled": scheduled, "lines": lines, "synced_count": synced_count, "ai_text": ai_text, "overdue_text": overdue_text}


def _is_greeting_text(text: str) -> bool:
    clean = re.sub(r"[!.,?\n\r\t]", " ", text.lower()).strip()
    clean = " ".join(clean.split())
    return clean in {"привет", "здравствуй", "здравствуйте", "hello", "hi", "hey"}


def _is_expectation_feedback_text(text: str) -> bool:
    lower = (text or "").strip().lower()
    return lower.startswith("я ожидал") or lower.startswith("я ожидала") or lower.startswith("а я ожидал") or lower.startswith("i expected")


def _normalize_command_text(text: str) -> str:
    lowered = (text or "").strip().lower()
    if lowered.startswith("/"):
        lowered = lowered[1:]
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered


def _training_mode_command(text: str) -> str | None:
    lower = _normalize_command_text(text)
    if lower in {"режим обучения вкл", "режим обучения on", "обучение вкл", "обучение on", "training_on"} or lower.startswith(
        ("режим обучения вкл ", "обучение вкл ", "training_on ")
    ):
        return "on"
    if lower in {"режим обучения выкл", "режим обучения off", "обучение выкл", "обучение off", "training_off"}:
        return "off"
    return None


def _training_on_count(text: str, default_count: int = 30) -> int:
    lower = _normalize_command_text(text)
    match = re.search(r"(?:training_on|обучение вкл|режим обучения вкл)\s+(\d+)", lower)
    if not match:
        return default_count
    try:
        count = int(match.group(1))
    except Exception:
        return default_count
    return max(5, min(100, count))


def _training_quiz_keyboard(session_id: str, item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Верно", callback_data=f"quiz:{session_id}:{item_id}:+"),
                InlineKeyboardButton(text="Неверно", callback_data=f"quiz:{session_id}:{item_id}:-"),
            ]
        ]
    )


def _save_quiz_state(context: ContextEngine, state: dict) -> None:
    context.set_memory(TRAINING_QUIZ_STATE_KEY, json.dumps(state, ensure_ascii=False))


def _load_quiz_state(context: ContextEngine) -> dict | None:
    raw = context.get_memory(TRAINING_QUIZ_STATE_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _clear_quiz_state(context: ContextEngine) -> None:
    context.set_memory(TRAINING_QUIZ_STATE_KEY, "")


def _send_training_quiz_item_text(state: dict, index: int) -> str | None:
    items = state.get("items") or []
    total = len(items)
    if index < 0 or index >= total:
        return None
    item = items[index]
    question = (item.get("question") or "").strip()
    answer = (item.get("answer") or "").strip()
    return (
        f"Вопрос {index + 1}/{total}\n"
        f"{question}\n\n"
        f"Ответ бота:\n{answer}\n\n"
        "Оцени ответ кнопкой ниже."
    )


def _intent_add_phrase_command(text: str) -> tuple[str, str] | None:
    lower = (text or "").strip().lower()
    prefix = "добавь фразу в профиль "
    if not lower.startswith(prefix):
        return None
    raw = (text or "").strip()[len(prefix) :]
    if ":" not in raw:
        return None
    profile, phrase = raw.split(":", 1)
    profile = profile.strip().lower()
    phrase = phrase.strip()
    if not profile or not phrase:
        return None
    return profile, phrase


def _intent_set_threshold_command(text: str) -> tuple[str, float] | None:
    match = re.match(r"^\s*измени\s+порог\s+([a-zA-Z0-9_\-]+)\s+([0-9]+(?:\.[0-9]+)?)\s*$", (text or "").strip(), re.IGNORECASE)
    if not match:
        return None
    profile = match.group(1).strip().lower()
    try:
        value = float(match.group(2))
    except Exception:
        return None
    return profile, value


def _main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Что у меня на сейчас"), KeyboardButton(text="Покажи события на сегодня")],
            [KeyboardButton(text="Режим обучения вкл"), KeyboardButton(text="Режим обучения выкл")],
            [KeyboardButton(text="Покажи чему ты научился"), KeyboardButton(text="Забудь последнее обучение")],
        ],
        resize_keyboard=True,
        selective=True,
    )


def _allow_greeting_reply(text: str, ks: KnowledgeService) -> bool:
    if not _is_greeting_text(text):
        return False
    recent = ks.get_recent_turns(limit=6)
    now = datetime.utcnow()
    for turn in reversed(recent):
        if turn.role != "assistant":
            continue
        content = (turn.content or "").lower()
        if any(token in content for token in ["привет", "здрав", "hello", "hi"]):
            if (now - turn.created_at).total_seconds() < 20 * 60:
                return False
            break
    return True


def _format_calendar_events(events: list, timezone_name: str, max_items: int | None = 20) -> str:
    if not events:
        return "На сегодня событий нет."
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")

    lines: list[str] = []
    iter_events = events if max_items is None else events[:max_items]
    for event in iter_events:
        start = getattr(event, "start_at", None) or getattr(event, "start_time", None)
        end = getattr(event, "end_at", None) or getattr(event, "end_time", None)
        if not start or not end:
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=ZoneInfo("UTC"))
        if end.tzinfo is None:
            end = end.replace(tzinfo=ZoneInfo("UTC"))
        local_start = start.astimezone(tz)
        local_end = end.astimezone(tz)

        title = (getattr(event, "title", None) or "Без названия").strip()
        if not title:
            title = "Без названия"

        is_all_day = bool(getattr(event, "is_all_day", False))
        duration_minutes = int((local_end - local_start).total_seconds() // 60)
        if is_all_day:
            time_part = "Весь день"
        elif duration_minutes <= 0:
            time_part = f"{local_start.strftime('%H:%M')} • короткое событие"
        elif duration_minutes >= 23 * 60:
            time_part = "Весь день"
        else:
            time_part = f"{local_start.strftime('%H:%M')}–{local_end.strftime('%H:%M')}"
        lines.append(f"- {time_part}  {title}")
    return "\n".join(lines)

def _extract_bedtime_from_events(events: list, timezone_name: str) -> str | None:
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")
    candidates: list[tuple[datetime, str]] = []
    for event in events:
        title = (getattr(event, "title", None) or "").strip()
        if not title:
            continue
        lower_title = title.lower()
        # Prefer explicit bedtime event names.
        if "в кровати" not in lower_title and "bed" not in lower_title:
            continue
        start = getattr(event, "start_at", None) or getattr(event, "start_time", None)
        if not start:
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=ZoneInfo("UTC"))
        local_start = start.astimezone(tz)
        candidates.append((local_start, title))
    if not candidates:
        return None
    # Return first upcoming bedtime for today, else first found.
    now_local = datetime.now(tz)
    upcoming = sorted([row for row in candidates if row[0] >= now_local], key=lambda x: x[0])
    pick = upcoming[0] if upcoming else sorted(candidates, key=lambda x: x[0])[0]
    return pick[0].strftime("%H:%M")


def _format_current_focus(events: list, timezone_name: str) -> str:
    if not events:
        return "Сейчас активного дела в календаре нет."
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")
    now_local = datetime.now(tz)
    normalized: list[dict] = []
    for event in events:
        start = getattr(event, "start_at", None) or getattr(event, "start_time", None)
        end = getattr(event, "end_at", None) or getattr(event, "end_time", None)
        if not start or not end:
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=ZoneInfo("UTC"))
        if end.tzinfo is None:
            end = end.replace(tzinfo=ZoneInfo("UTC"))
        local_start = start.astimezone(tz)
        local_end = end.astimezone(tz)
        normalized.append(
            {
                "title": (getattr(event, "title", None) or "Без названия").strip() or "Без названия",
                "start": local_start,
                "end": local_end,
                "is_all_day": bool(getattr(event, "is_all_day", False)),
            }
        )
    if not normalized:
        return "Сейчас активного дела в календаре нет."
    normalized.sort(key=lambda x: x["start"])

    current = None
    next_event = None
    for row in normalized:
        if row["is_all_day"]:
            # All-day event is considered current for context, but timed event has priority.
            if current is None:
                current = row
            continue
        if row["start"] <= now_local < row["end"] or (row["start"] == row["end"] and abs((now_local - row["start"]).total_seconds()) < 600):
            current = row
            break
        if row["start"] > now_local and next_event is None:
            next_event = row

    if not current:
        # If no current timed event, keep closest upcoming.
        for row in normalized:
            if row["start"] > now_local:
                next_event = row
                break
        if next_event:
            return f"Сейчас свободно. Следующее: {next_event['start'].strftime('%H:%M')}–{next_event['end'].strftime('%H:%M')} {next_event['title']}."
        return "Сейчас по календарю активного дела нет."

    if current["is_all_day"]:
        if next_event:
            return f"Сейчас: {current['title']} (весь день). Ближайшее по времени: {next_event['start'].strftime('%H:%M')}–{next_event['end'].strftime('%H:%M')} {next_event['title']}."
        return f"Сейчас: {current['title']} (весь день)."

    for row in normalized:
        if row["start"] >= current["end"]:
            next_event = row
            break
    if next_event:
        return (
            f"Сейчас: {current['start'].strftime('%H:%M')}–{current['end'].strftime('%H:%M')} {current['title']}. "
            f"Дальше: {next_event['start'].strftime('%H:%M')}–{next_event['end'].strftime('%H:%M')} {next_event['title']}."
        )
    return f"Сейчас: {current['start'].strftime('%H:%M')}–{current['end'].strftime('%H:%M')} {current['title']}."


def _build_user_day_window_utc(timezone_name: str, day_offset: int = 0) -> tuple[datetime, datetime]:
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")
    now_local = datetime.now(tz)
    target_day = (now_local + timedelta(days=day_offset)).date()
    day_start_local = datetime.combine(target_day, datetime.min.time(), tzinfo=tz)
    day_end_local = day_start_local + timedelta(days=1)
    return day_start_local.astimezone(timezone.utc), day_end_local.astimezone(timezone.utc)


def _build_local_day_label(timezone_name: str, day_offset: int = 0) -> str:
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")
    day = (datetime.now(tz) + timedelta(days=day_offset)).date()
    return day.strftime("%d.%m.%Y")


def _parse_hhmm(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value)
    if not match:
        return None
    hh = int(match.group(1))
    mm = int(match.group(2))
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        return None
    return hh, mm


def _resolve_calendar_add_params(text: str, params: dict, timezone_name: str) -> dict | None:
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")

    day_offset = int(params.get("day_offset") or 0)
    now_local = datetime.now(tz)
    target_day = (now_local + timedelta(days=day_offset)).date()

    start_str = (params.get("start_time") or "").strip()
    end_str = (params.get("end_time") or "").strip()

    if not start_str or not end_str:
        range_match = re.search(r"(\d{1,2}:\d{2})\s*[–-]\s*(\d{1,2}:\d{2})", text)
        if range_match:
            start_str = start_str or range_match.group(1)
            end_str = end_str or range_match.group(2)

    start_hm = _parse_hhmm(start_str)
    end_hm = _parse_hhmm(end_str)
    if not start_hm or not end_hm:
        return None

    start_local = datetime(target_day.year, target_day.month, target_day.day, start_hm[0], start_hm[1], tzinfo=tz)
    end_local = datetime(target_day.year, target_day.month, target_day.day, end_hm[0], end_hm[1], tzinfo=tz)
    if end_local <= start_local:
        end_local = end_local + timedelta(days=1)

    title = (params.get("title") or "").strip()
    if not title:
        cleaned = re.sub(r"\b\d{1,2}:\d{2}\b", "", text)
        cleaned = re.sub(r"[–-]", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,-")
        title = cleaned or "Новое событие"

    return {
        "title": title,
        "start_local": start_local,
        "end_local": end_local,
        "day_label": target_day.strftime("%d.%m.%Y"),
    }


def _save_calendar_snapshot(context: ContextEngine, events: list, timezone_name: str, day_label: str) -> None:
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")
    payload = {"date": day_label, "timezone": timezone_name, "events": []}
    for idx, event in enumerate(events, start=1):
        start = getattr(event, "start_at", None) or getattr(event, "start_time", None)
        end = getattr(event, "end_at", None) or getattr(event, "end_time", None)
        if not start or not end:
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=ZoneInfo("UTC"))
        if end.tzinfo is None:
            end = end.replace(tzinfo=ZoneInfo("UTC"))
        local_start = start.astimezone(tz)
        local_end = end.astimezone(tz)
        payload["events"].append(
            {
                "index": idx,
                "event_id": getattr(event, "provider_event_id", None) or getattr(event, "calendar_event_id", None) or "",
                "local_event_id": getattr(event, "id", None),
                "title": (getattr(event, "title", None) or "Без названия").strip() or "Без названия",
                "start": local_start.strftime("%H:%M"),
                "end": local_end.strftime("%H:%M"),
            }
        )
    context.set_memory("last_calendar_snapshot", json.dumps(payload, ensure_ascii=False))


def _answer_calendar_follow_up(text: str, context: ContextEngine) -> str | None:
    raw = context.get_memory("last_calendar_snapshot")
    if not raw:
        return None
    try:
        snapshot = json.loads(raw)
        events = snapshot.get("events") or []
        day_label = snapshot.get("date") or "неизвестная дата"
    except Exception:
        return None
    if not events:
        return None

    lower = text.lower()
    if any(k in lower for k in ["на какое число", "какое число", "какая дата", "за какой день", "что за день"]):
        return f"Это расписание за {day_label}."

    if "после" in lower:
        range_match = re.search(r"(\d{1,2}:\d{2})\s*[–-]\s*(\d{1,2}:\d{2})", text)
        time_match = re.search(r"(\d{1,2}:\d{2})", text)
        pivot = None
        if range_match:
            pivot = range_match.group(2)
        elif time_match:
            pivot = time_match.group(1)
        if not pivot:
            # Support "после <название события>" using the last snapshot.
            subject = lower.split("после", 1)[1].strip(" .:-")
            anchor = None
            for row in events:
                title = (row.get("title") or "").lower()
                if subject and subject in title:
                    anchor = row
                    break
            if not anchor:
                return None
            pivot = anchor.get("end")
        tail = [row for row in events if row.get("start", "") >= (pivot or "00:00")]
        if not tail:
            return f"После {pivot} на {day_label} событий больше нет."
        lines = "\n".join([f"- {row['start']}–{row['end']}  {row['title']}" for row in tail[:30]])
        return f"После {pivot} ({day_label}):\n{lines}"

    return None


def _append_chat_backlog_item(user_id: int, user_text: str, issue: str) -> None:
    try:
        BACKLOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not BACKLOG_PATH.exists():
            BACKLOG_PATH.write_text("# Chat Improvement Backlog\n\n", encoding="utf-8")
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        row = f"- [{ts}] user={user_id} issue={issue} | message={user_text.strip()}\n"
        existing = BACKLOG_PATH.read_text(encoding="utf-8")
        if row in existing:
            return
        BACKLOG_PATH.write_text(existing + row, encoding="utf-8")
    except Exception:
        pass


def _delete_calendar_event_from_snapshot(
    text: str,
    context: ContextEngine,
    calendar_domain: CalendarDomainService,
    calendar_sync: CalendarSyncService,
    user_id: int,
    provider_calendar_id: str,
) -> str:
    raw = context.get_memory("last_calendar_snapshot")
    if not raw:
        return "Сначала покажу события на день, чтобы выбрать точное событие для удаления."
    try:
        snapshot = json.loads(raw)
    except Exception:
        return "Не удалось прочитать последний список событий. Скажи: 'покажи события на сегодня', затем повтори удаление."
    events = snapshot.get("events") or []
    day_label = snapshot.get("date") or "указанный день"
    if not events:
        return f"На {day_label} событий нет."

    lower = text.lower()
    match_by_index = re.search(r"(?:#|номер\s*|id\s*)(\d+)", lower)
    range_match = re.search(r"(\d{1,2}:\d{2})\s*[–-]\s*(\d{1,2}:\d{2})", text)
    title_hint = lower
    for noise in ["удали", "удалить", "delete", "remove", "убери", "событие", "встречу", "встреча", "календарь", "из", "на", "-", "—"]:
        title_hint = title_hint.replace(noise, " ")
    title_hint = " ".join(title_hint.split())

    candidates = events
    if match_by_index:
        idx = int(match_by_index.group(1))
        candidates = [row for row in events if int(row.get("index") or 0) == idx]
    elif range_match:
        start_time, end_time = range_match.group(1), range_match.group(2)
        candidates = [row for row in events if row.get("start") == start_time and row.get("end") == end_time]
        if title_hint:
            narrowed = [row for row in candidates if title_hint in (row.get("title") or "").lower()]
            if narrowed:
                candidates = narrowed
    elif title_hint:
        candidates = [row for row in events if title_hint in (row.get("title") or "").lower()]

    candidates = [row for row in candidates if row.get("local_event_id")]
    if not candidates:
        return "Не нашел событие в календаре по этому описанию. Укажи время, например: 'удали событие 13:45-14:00 Обед'."
    if len(candidates) > 1:
        pending = {"day_label": day_label, "candidates": candidates[:20]}
        context.set_memory("pending_calendar_delete", json.dumps(pending, ensure_ascii=False))
        rows = "\n".join([f"- #{row['index']} {row['start']}–{row['end']}  {row['title']}" for row in candidates[:8]])
        return "Нашел несколько событий. Укажи номер, например: 'удали #13'\n" + rows

    target = candidates[0]
    ok = calendar_domain.delete_local_event(user_id, int(target["local_event_id"]))
    if not ok:
        return "Не получилось удалить событие. Попробуй еще раз через пару секунд."
    calendar_sync.sync_now(user_id, provider_calendar_id)
    context.set_memory("pending_calendar_delete", "")
    return f"Удалил событие: {target['start']}–{target['end']}  {target['title']} ({day_label})."


def _try_resolve_pending_calendar_delete(
    text: str,
    context: ContextEngine,
    calendar_domain: CalendarDomainService,
    calendar_sync: CalendarSyncService,
    user_id: int,
    provider_calendar_id: str,
) -> str | None:
    raw = context.get_memory("pending_calendar_delete")
    if not raw:
        return None
    try:
        pending = json.loads(raw)
    except Exception:
        context.set_memory("pending_calendar_delete", "")
        return None
    candidates = pending.get("candidates") or []
    if not candidates:
        context.set_memory("pending_calendar_delete", "")
        return None

    lower = text.lower().strip()
    if lower in {"отмена", "cancel", "не надо", "не удаляй"}:
        context.set_memory("pending_calendar_delete", "")
        return "Ок, удаление отменил."

    idx_match = re.search(r"(?:#|номер\s*|id\s*|удали\s*#?)(\d+)", lower)
    time_match = re.search(r"(\d{1,2}:\d{2})\s*[–-]\s*(\d{1,2}:\d{2})", text)
    title_hint = " ".join(lower.replace("удали", " ").replace("событие", " ").split())

    selected = None
    if idx_match:
        idx = int(idx_match.group(1))
        selected = next((row for row in candidates if int(row.get("index") or 0) == idx), None)
    elif time_match:
        start_time, end_time = time_match.group(1), time_match.group(2)
        selected = next((row for row in candidates if row.get("start") == start_time and row.get("end") == end_time), None)
    elif title_hint:
        selected = next((row for row in candidates if title_hint in (row.get("title") or "").lower()), None)

    if not selected:
        rows = "\n".join([f"- #{row['index']} {row['start']}–{row['end']}  {row['title']}" for row in candidates[:8]])
        return "Не понял, какое именно удалить. Напиши номер:\n" + rows

    local_event_id = int(selected.get("local_event_id") or 0)
    ok = local_event_id > 0 and calendar_domain.delete_local_event(user_id, local_event_id)
    if not ok:
        return "Не получилось удалить выбранное событие. Попробуй еще раз."
    calendar_sync.sync_now(user_id, provider_calendar_id)
    context.set_memory("pending_calendar_delete", "")
    day_label = pending.get("day_label") or "выбранный день"
    return f"Удалил событие: {selected['start']}–{selected['end']}  {selected['title']} ({day_label})."


class _TextProxyMessage:
    def __init__(self, original: Message, text: str) -> None:
        self._original = original
        self.text = text
        self.from_user = original.from_user
        self.bot = original.bot

    async def answer(self, *args, **kwargs):
        return await self._original.answer(*args, **kwargs)


async def _handle_incoming_text(message: Message, text: str, source: str = "text") -> None:
    _ = source
    proxy = _TextProxyMessage(message, text)
    await natural_chat_handler(proxy)  # Reuse the same orchestrated text pipeline.


@router.callback_query(F.data.startswith("quiz:"))
async def training_quiz_callback_handler(callback: CallbackQuery) -> None:
    payload = (callback.data or "").split(":")
    if len(payload) != 4:
        await callback.answer("Некорректные данные кнопки.", show_alert=False)
        return
    _prefix, session_id, raw_item_id, mark = payload
    if mark not in {"+", "-"}:
        await callback.answer("Некорректная оценка.", show_alert=False)
        return
    try:
        item_id = int(raw_item_id)
    except Exception:
        await callback.answer("Некорректный id вопроса.", show_alert=False)
        return

    if not callback.from_user:
        await callback.answer("Пользователь не найден.", show_alert=False)
        return

    with SessionLocal() as db:
        user = _ensure_user(db, callback.from_user.id, callback.from_user.full_name if callback.from_user else "User")
        ks = KnowledgeService(db, user.id)
        context = ContextEngine(db, user.id)
        state = _load_quiz_state(context)
        if not state or not state.get("active"):
            await callback.answer("Сессия обучения не активна. Напиши /training_on.", show_alert=False)
            return
        if state.get("session_id") != session_id:
            await callback.answer("Старая сессия. Запусти /training_on.", show_alert=False)
            return

        items = state.get("items") or []
        current_index = int(state.get("index") or 0)
        if current_index < 0 or current_index >= len(items):
            await callback.answer("Сессия завершена.", show_alert=False)
            return
        current_item = items[current_index]
        if int(current_item.get("id") or -1) != item_id:
            await callback.answer("Оцени текущий вопрос.", show_alert=False)
            return

        feedback = TrainingFeedback(
            user_id=user.id,
            session_id=session_id,
            question=str(current_item.get("question") or ""),
            answer=str(current_item.get("answer") or ""),
            expected_intent=(current_item.get("expected_intent") or None),
            predicted_intent=(current_item.get("predicted_intent") or None),
            score=float(current_item.get("score")) if current_item.get("score") is not None else None,
            is_correct=(mark == "+"),
        )
        db.add(feedback)
        db.commit()

        # Avoid duplicate rating on same message.
        if callback.message:
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

        next_index = current_index + 1
        state["index"] = next_index
        _save_quiz_state(context, state)

        await callback.answer("Сохранено.", show_alert=False)

        if next_index >= len(items):
            total = len(items)
            positives = (
                db.query(TrainingFeedback)
                .filter(TrainingFeedback.user_id == user.id, TrainingFeedback.session_id == session_id, TrainingFeedback.is_correct.is_(True))
                .count()
            )
            negatives = total - positives
            reply = (
                f"Сессия завершена.\n"
                f"Итого: {total}\n"
                f"Верно: {positives}\n"
                f"Неверно: {negatives}\n\n"
                "Позже можем дообучить бота по этим реакциям."
            )
            _clear_quiz_state(context)
            await callback.message.answer(reply) if callback.message else await callback.bot.send_message(callback.from_user.id, reply)
            ks.add_turn(role="assistant", content=reply, intent="training_quiz_completed")
            return

        next_item = items[next_index]
        text = _send_training_quiz_item_text(state, next_index)
        if text:
            kb = _training_quiz_keyboard(session_id, int(next_item.get("id") or next_index + 1))
            await callback.message.answer(text, reply_markup=kb) if callback.message else await callback.bot.send_message(
                callback.from_user.id, text, reply_markup=kb
            )
            ks.add_turn(role="assistant", content=text, intent="training_quiz_question")


@router.message(F.text)
async def natural_chat_handler(message: Message) -> None:
    text = (message.text or "").strip()
    if not text:
        return
    with SessionLocal() as db:
        user = _ensure_user(db, message.from_user.id, message.from_user.full_name if message.from_user else "User")
        ks = KnowledgeService(db, user.id)
        context = ContextEngine(db, user.id)
        calendar_read = CalendarReadService(db)
        calendar_domain = CalendarDomainService(db)
        calendar_sync = CalendarSyncService(db)
        intent_profile_service = IntentProfileService(db)
        provider_calendar_id = settings.google_calendar_id
        intent_profiles = intent_profile_service.get_profiles(user.id)
        ks.add_turn(role="user", content=text, intent="incoming")
        ks.learn_from_message(text)
        normalized_cmd = _normalize_command_text(text)
        cmd_profile, _cmd_profile_score = PROFILE_MATCHER.classify(
            normalized_cmd,
            intent_profiles,
            candidates=("current_focus", "bedtime"),
        )

        training_cmd = _training_mode_command(text)
        if training_cmd == "on":
            ks.set_training_enabled(True)
            count = _training_on_count(text, default_count=30)
            run = EvalHarnessService(db).generate_run(user.telegram_id, count=count)
            items = run.get("items") or []
            if not items:
                reply = "Не получилось запустить тренировку: список вопросов пуст."
                await message.answer(reply)
                ks.add_turn(role="assistant", content=reply, intent="training_mode_on_failed")
                return
            state = {
                "active": True,
                "session_id": run.get("run_id"),
                "index": 0,
                "items": items,
            }
            _save_quiz_state(context, state)
            intro = (
                f"Режим обучения включен. Запускаю сессию на {len(items)} вопросов.\n"
                "После каждого ответа жми: Верно или Неверно."
            )
            first_text = _send_training_quiz_item_text(state, 0) or "Стартуем."
            first = items[0]
            kb = _training_quiz_keyboard(state["session_id"], int(first.get("id") or 1))
            await message.answer(intro)
            await message.answer(first_text, reply_markup=kb)
            ks.add_turn(role="assistant", content=intro, intent="training_mode_on")
            ks.add_turn(role="assistant", content=first_text, intent="training_quiz_question")
            return
        if training_cmd == "off":
            ks.set_training_enabled(False)
            _clear_quiz_state(context)
            reply = "Ок, режим обучения выключен. Сессию вопросов остановил."
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="training_mode_off")
            return

        if normalized_cmd in {"профили интентов", "интент профили", "intent profiles"}:
            rows = intent_profile_service.list_profiles(user.id)
            if not rows:
                reply = "Профили интентов пока не настроены."
            else:
                lines = [
                    f"- {row['name']}: threshold={row['threshold']:.2f}, phrases={row['phrases_count']}, tokens={row['tokens_count']}"
                    for row in rows
                ]
                reply = "Профили интентов:\n" + "\n".join(lines)
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="intent_profiles_list")
            return

        add_phrase_cmd = _intent_add_phrase_command(text)
        if add_phrase_cmd:
            profile_name, phrase = add_phrase_cmd
            ok = intent_profile_service.add_phrase(user.id, profile_name, phrase)
            if ok:
                reply = f"Ок, добавил фразу в профиль `{profile_name}`: {phrase}"
            else:
                reply = (
                    f"Не нашел профиль `{profile_name}`. "
                    "Сначала проверь доступные профили командой: 'профили интентов'."
                )
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="intent_profile_add_phrase")
            return

        set_threshold_cmd = _intent_set_threshold_command(text)
        if set_threshold_cmd:
            profile_name, threshold = set_threshold_cmd
            ok = intent_profile_service.set_threshold(user.id, profile_name, threshold)
            if ok:
                reply = f"Ок, порог профиля `{profile_name}` обновлен: {threshold:.2f}"
            else:
                reply = (
                    f"Не получилось обновить порог для `{profile_name}`. "
                    "Порог должен быть в диапазоне 0..1, а профиль должен существовать."
                )
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="intent_profile_set_threshold")
            return

        if normalized_cmd in {"покажи чему ты научился", "чему ты научился", "покажи обучение"}:
            rows = ks.list_taught_pairs(limit=8)
            if not rows:
                reply = "Пока нет сохраненных обучающих примеров."
            else:
                lines = []
                for idx, row in enumerate(rows, start=1):
                    q = " ".join((row.question_pattern or "").split())[:90]
                    a = " ".join((row.answer_template or "").split())[:110]
                    lines.append(f"{idx}) Q: {q}\n   A: {a}")
                reply = "Вот чему я научился:\n" + "\n".join(lines)
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="training_show")
            return
        if normalized_cmd in {"training_show"}:
            rows = ks.list_taught_pairs(limit=8)
            if not rows:
                reply = "Пока нет сохраненных обучающих примеров."
            else:
                lines = []
                for idx, row in enumerate(rows, start=1):
                    q = " ".join((row.question_pattern or "").split())[:90]
                    a = " ".join((row.answer_template or "").split())[:110]
                    lines.append(f"{idx}) Q: {q}\n   A: {a}")
                reply = "Вот чему я научился:\n" + "\n".join(lines)
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="training_show")
            return

        if normalized_cmd in {"забудь последнее обучение", "удали последнее обучение", "forget last training", "training_forget"}:
            forgotten = ks.forget_last_taught_pair()
            if not forgotten:
                reply = "Нечего удалять: обучающих примеров пока нет."
            else:
                q = " ".join((forgotten.question_pattern or "").split())[:100]
                reply = f"Удалил последнее обучение для вопроса: {q}"
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="training_forget_last")
            return

        if normalized_cmd in {"training_results", "результаты обучения", "статистика обучения"}:
            last = (
                db.query(TrainingFeedback.session_id)
                .filter(TrainingFeedback.user_id == user.id)
                .order_by(TrainingFeedback.created_at.desc())
                .first()
            )
            if not last:
                reply = "Пока нет оценок по кнопкам Верно/Неверно."
            else:
                session_id = last[0]
                total = db.query(TrainingFeedback).filter(
                    TrainingFeedback.user_id == user.id, TrainingFeedback.session_id == session_id
                ).count()
                good = db.query(TrainingFeedback).filter(
                    TrainingFeedback.user_id == user.id,
                    TrainingFeedback.session_id == session_id,
                    TrainingFeedback.is_correct.is_(True),
                ).count()
                bad = total - good
                reply = f"Последняя сессия {session_id[:8]}...\nИтого: {total}\nВерно: {good}\nНеверно: {bad}"
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="training_results")
            return

        # Fast-path for latency-sensitive queries: skip LLM routing entirely.
        if normalized_cmd in {"now"} or cmd_profile == "current_focus":
            calendar_tz = context.get_memory("calendar_timezone") or user.timezone or settings.timezone
            _, events = calendar_read.list_day_events(user.id, calendar_tz, 0)
            reply = _format_current_focus(events, calendar_tz)
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="current_focus")
            ks.maybe_learn_from_dialogue(text, reply)
            return

        if normalized_cmd in {"today", "покажи события на сегодня"}:
            calendar_tz = context.get_memory("calendar_timezone") or user.timezone or settings.timezone
            day_label = _build_local_day_label(calendar_tz)
            _, events = calendar_read.list_day_events(user.id, calendar_tz, 0)
            if events:
                rows = _format_calendar_events(events, calendar_tz, max_items=None)
                reply = f"События на {day_label}:\n{rows}"
                _save_calendar_snapshot(context, events, calendar_tz, day_label)
            else:
                reply = f"На {day_label} событий нет."
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="calendar_check")
            return

        if _is_expectation_feedback_text(text):
            learned = ks.register_expectation_feedback(text)
            if learned.get("ok"):
                reply = "Принял. Запомнил этот формат ответа и буду использовать его в похожих вопросах."
            elif learned.get("reason") == "training_disabled":
                reply = "Сейчас режим обучения выключен. Включи: 'режим обучения вкл'."
            else:
                reply = "Принял. Чтобы обучить точно, напиши: 'я ожидал: <как нужно отвечать>'."
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="teacher_feedback")
            return

        route = chat_assistant.route_message(text)
        intent = route["intent"]
        normalized_text = route["normalized_text"]
        response_mode = route["response_mode"]
        params = route.get("params") if isinstance(route.get("params"), dict) else {}
        routed_profile, _routed_profile_score = PROFILE_MATCHER.classify(
            normalized_text,
            intent_profiles,
            candidates=("current_focus", "bedtime"),
        )

        if text.lower() == "/start":
            ContextEngine(db, user.id).set_memory("weekly_work_hours", "0")
            ContextEngine(db, user.id).set_memory("weekly_rest_hours", "0")
            reply = ks.reply_with_backend_result(
                text,
                operation="welcome",
                payload={
                    "mode": "chat_first",
                    "capabilities": ["calendar_read_write", "yougile_sync", "task_planning", "habit_support"],
                },
            ) or "Готов работать в формате чата и помогать с календарем, задачами и планированием."
            await message.answer(reply, reply_markup=_main_menu_keyboard())
            ks.add_turn(role="assistant", content="Sent welcome message", intent="welcome")
            return

        if intent == ChatIntent.calendar_add or intent == ChatIntent.add_task:
            gcal = GoogleCalendarService()
            calendar_tz = gcal.get_calendar_timezone() or user.timezone or settings.timezone
            payload = _resolve_calendar_add_params(normalized_text, params, calendar_tz)
            if not payload:
                reply = ks.reply_with_backend_result(
                    text,
                    operation="calendar_add_need_details",
                    payload={"required": ["title", "start_time", "end_time"], "example": "добавь 20:30-21:00 Рисование"},
                ) or "Уточни время и название, например: 'добавь 20:30-21:00 Рисование'."
                await message.answer(reply)
                ks.add_turn(role="assistant", content=reply, intent="calendar_add")
                return

            calendar_domain.create_local_event(
                user.id,
                provider_calendar_id,
                payload["title"],
                payload["start_local"].astimezone(timezone.utc).replace(tzinfo=None),
                payload["end_local"].astimezone(timezone.utc).replace(tzinfo=None),
                calendar_tz,
                details={"description": params.get("description"), "location": params.get("location")},
            )
            calendar_sync.sync_now(user.id, provider_calendar_id)
            _, refreshed = calendar_read.list_day_events(user.id, calendar_tz, 0)
            _save_calendar_snapshot(context, refreshed, calendar_tz, payload["day_label"])
            # Deterministic factual confirmation for critical write action.
            reply = (
                f"Готово. Добавил в календарь: "
                f"{payload['start_local'].strftime('%H:%M')}–{payload['end_local'].strftime('%H:%M')} "
                f"{payload['title']} ({payload['day_label']})."
            )
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="calendar_add")
            return

        if intent == ChatIntent.calendar_delete:
            reply = _delete_calendar_event_from_snapshot(
                normalized_text,
                context,
                calendar_domain,
                calendar_sync,
                user.id,
                provider_calendar_id,
            )
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="calendar_delete")
            if "Не нашел событие" in reply or "Не получилось" in reply:
                _append_chat_backlog_item(user.telegram_id, text, "calendar_delete_matching_or_api_error")
            # Refresh snapshot after successful deletion.
            if reply.startswith("Удалил событие:"):
                calendar_tz = GoogleCalendarService().get_calendar_timezone() or user.timezone or settings.timezone
                day_label = _build_local_day_label(calendar_tz)
                _, refreshed = calendar_read.list_day_events(user.id, calendar_tz, 0)
                _save_calendar_snapshot(context, refreshed, calendar_tz, day_label)
            return

        if intent == ChatIntent.calendar_delete_pending:
            pending_reply = _try_resolve_pending_calendar_delete(
                normalized_text,
                context,
                calendar_domain,
                calendar_sync,
                user.id,
                provider_calendar_id,
            )
            if not pending_reply:
                pending_reply = "Уточни, какое событие удалить: например 'удали #13' или 'отмена'."
            await message.answer(pending_reply)
            ks.add_turn(role="assistant", content=pending_reply, intent="calendar_delete_pending")
            if pending_reply.startswith("Удалил событие:"):
                calendar_tz = GoogleCalendarService().get_calendar_timezone() or user.timezone or settings.timezone
                day_label = _build_local_day_label(calendar_tz)
                _, refreshed = calendar_read.list_day_events(user.id, calendar_tz, 0)
                _save_calendar_snapshot(context, refreshed, calendar_tz, day_label)
            return

        if intent == ChatIntent.calendar_follow_up:
            follow_up = _answer_calendar_follow_up(normalized_text, context)
            if not follow_up:
                follow_up = "Уточни вопрос по последнему списку календаря, и я отвечу точечно."
            await message.answer(follow_up)
            ks.add_turn(role="assistant", content=follow_up, intent="calendar_follow_up")
            return

        if intent == ChatIntent.calendar_modify_help:
            reply = ks.reply_with_backend_result(
                text,
                operation="calendar_modify_help",
                payload={
                    "supported_actions": ["delete_event", "move_event", "add_event"],
                    "examples": ["удали событие 13:45-14:00 Обед", "удали событие #13"],
                    "requires_snapshot": True,
                },
            ) or "Могу менять календарь: удалять, переносить и добавлять события."
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="calendar_modify_help")
            return
        if intent == ChatIntent.plan_day:
            # Local-first: read from local calendar store, sync with Google as secondary path.
            gcal = GoogleCalendarService()
            calendar_tz = gcal.get_calendar_timezone() or user.timezone or settings.timezone
            day_label = _build_local_day_label(calendar_tz)
            _, events = calendar_read.list_day_events(user.id, calendar_tz, 0)
            if not events:
                # Fallback sync when local cache is empty.
                calendar_sync.pull_incremental(user.id, provider_calendar_id)
                _, events = calendar_read.list_day_events(user.id, calendar_tz, 0)
            if events:
                rows = _format_calendar_events(events, calendar_tz, max_items=None)
                reply = ks.reply_with_backend_result(
                    text,
                    operation="plan_day_from_calendar",
                    payload={"date": day_label, "timezone": calendar_tz, "rows_text": rows},
                ) or f"План на {day_label}:\n{rows}"
                _save_calendar_snapshot(context, events, calendar_tz, day_label)
            else:
                reply = ks.reply_with_backend_result(
                    text,
                    operation="plan_day_from_calendar_empty",
                    payload={"date": day_label, "timezone": calendar_tz},
                ) or f"На {day_label} событий нет."
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="plan_day")
            ks.maybe_learn_from_dialogue(text, reply)
            return

        if intent == ChatIntent.suggest_free:
            context = ContextEngine(db, user.id)
            fatigue = RecommendationEngine(db).estimate_fatigue(
                float(context.get_memory("weekly_work_hours") or 25),
                float(context.get_memory("weekly_rest_hours") or 20),
            )
            suggestions = RecommendationEngine(db).suggest_activities(
                user_id=user.id,
                free_minutes=60,
                location=context.get_memory("location") or "any",
                season=context.get_memory("season") or "any",
                fatigue_score=fatigue,
            )
            if suggestions:
                reply = "Вот идеи:\n" + "\n".join([f"- {item.name} ({item.duration_minutes}m)" for item in suggestions])
            else:
                reply = "Пока нет подходящих идей. Напиши, сколько у тебя свободного времени и где ты."
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="suggest_free")
            ks.maybe_learn_from_dialogue(text, reply)
            return

        if intent == ChatIntent.weekly_report:
            tasks = TaskManager(db).list_open_tasks(user.id)
            completed_count = db.query(Task).filter(Task.user_id == user.id, Task.status == TaskStatus.completed).count()
            context = ContextEngine(db, user.id)
            reply = (
                "Отчет за неделю:\n"
                f"- Open/planned: {len(tasks)}\n"
                f"- Completed: {completed_count}\n"
                f"- Work hours: {context.get_memory('weekly_work_hours') or '0'}\n"
                f"- Rest hours: {context.get_memory('weekly_rest_hours') or '0'}"
            )
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="weekly_report")
            ks.maybe_learn_from_dialogue(text, reply)
            return

        if intent == ChatIntent.complete_task:
            maybe_id = next((token for token in normalized_text.split() if token.isdigit()), None)
            if maybe_id:
                task = TaskManager(db).mark_completed(int(maybe_id))
                reply = f"Отметил выполненной: {task.title}" if task else "Не нашел задачу с таким id."
            else:
                reply = "Напиши id задачи, например: 'выполнил задачу 12'"
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="complete_task")
            if "Не нашел задачу" in reply or "Напиши id задачи" in reply:
                _append_chat_backlog_item(user.telegram_id, text, "task_id_fallback_triggered")
            ks.maybe_learn_from_dialogue(text, reply)
            return

        if intent == ChatIntent.sync:
            count = SyncService(db).sync_yougile_tasks(user.id)
            reply = f"Синхронизировал задачи из YouGile: {count}"
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="sync")
            ks.maybe_learn_from_dialogue(text, reply)
            return

        if intent == ChatIntent.yougile_check:
            count = SyncService(db).sync_yougile_tasks(user.id)
            if count > 0:
                reply = f"Да, YouGile подключен. Сейчас подтянул задач: {count}."
            else:
                reply = "YouGile формально подключен, но задач не пришло (0). Нужна проверка API токена/доступа к workspace."
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="yougile_check")
            ks.maybe_learn_from_dialogue(text, reply)
            return

        if intent == ChatIntent.connections_check:
            gcal = GoogleCalendarService()
            calendar_tz = gcal.get_calendar_timezone() or user.timezone or settings.timezone
            _, events = calendar_read.list_day_events(user.id, calendar_tz, 0)
            yougile_count = SyncService(db).sync_yougile_tasks(user.id)
            ai_client = OpenAIClient()
            ai_state = "подключен" if ai_client.provider != "disabled" else "не подключен"
            ai_label = f"{ai_client.provider}/{ai_client.model}" if ai_client.provider != "disabled" else "disabled"
            calendar_state = "локальная БД (primary)"
            yougile_state = "подключен" if yougile_count >= 0 else "ошибка подключения"
            reply = (
                "Статус интеграций:\n"
                f"- Calendar: {calendar_state}, TZ: {calendar_tz}, событий сегодня: {len(events)}\n"
                f"- YouGile: {yougile_state}, задач после sync: {yougile_count}\n"
                f"- AI: {ai_state} ({ai_label})"
            )
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="connections_check")
            ks.maybe_learn_from_dialogue(text, reply)
            return

        if intent == ChatIntent.calendar_check:
            gcal = GoogleCalendarService()
            calendar_tz = gcal.get_calendar_timezone() or user.timezone or settings.timezone
            day_label = _build_local_day_label(calendar_tz)
            _, events = calendar_read.list_day_events(user.id, calendar_tz, 0)
            if not events:
                # Fallback sync when local cache is empty.
                calendar_sync.pull_incremental(user.id, provider_calendar_id)
                _, events = calendar_read.list_day_events(user.id, calendar_tz, 0)
            if events:
                is_exact = response_mode == "calendar_exact"
                rows = _format_calendar_events(events, calendar_tz, max_items=None if is_exact else 20)
                reply = ks.reply_with_backend_result(
                    text,
                    operation="calendar_check",
                    payload={
                        "date": day_label,
                        "timezone": calendar_tz,
                        "exact_mode": is_exact,
                        "rows_text": rows,
                    },
                ) or f"События на {day_label}:\n{rows}"
                _save_calendar_snapshot(context, events, calendar_tz, day_label)
            else:
                reply = ks.reply_with_backend_result(
                    text,
                    operation="calendar_check_empty",
                    payload={"date": day_label, "timezone": calendar_tz},
                ) or "На сегодня в календаре событий не найдено."
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="calendar_check")
            return

        if intent == ChatIntent.current_focus or routed_profile == "current_focus":
            gcal = GoogleCalendarService()
            calendar_tz = gcal.get_calendar_timezone() or user.timezone or settings.timezone
            context.set_memory("calendar_timezone", calendar_tz)
            _, events = calendar_read.list_day_events(user.id, calendar_tz, 0)
            if not events:
                calendar_sync.pull_incremental(user.id, provider_calendar_id)
                _, events = calendar_read.list_day_events(user.id, calendar_tz, 0)
            # Teacher-student override should apply here too, not only in general chat.
            taught = ks.find_taught_answer(normalized_text)
            if taught:
                reply = taught
                await message.answer(reply)
                ks.add_turn(role="assistant", content=reply, intent="current_focus")
                ks.maybe_learn_from_dialogue(text, reply)
                return
            # For "bedtime" style questions use deterministic rule from calendar.
            if routed_profile == "bedtime":
                bedtime = _extract_bedtime_from_events(events, calendar_tz)
                if bedtime:
                    reply = f"В {bedtime}."
                    await message.answer(reply)
                    ks.add_turn(role="assistant", content=reply, intent="current_focus")
                    ks.maybe_learn_from_dialogue(text, reply)
                    return
            reply = _format_current_focus(events, calendar_tz)
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="current_focus")
            ks.maybe_learn_from_dialogue(text, reply)
            return

        taught = ks.find_taught_answer(normalized_text)
        if taught:
            reply = taught
        else:
            reply = ks.reply_with_memory(normalized_text, allow_greeting=False)
        if not reply:
            reply = "Понял. Если хочешь, напиши задачи на сегодня/завтра, и я сразу их распланирую."
        await message.answer(reply)
        ks.add_turn(role="assistant", content=reply, intent="general_chat")
        ks.maybe_learn_from_dialogue(text, reply)
