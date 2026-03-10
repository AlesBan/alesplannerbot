import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.types import Message

from app.ai.chat_assistant import ChatAssistant, ChatIntent
from app.ai.context_engine import ContextEngine
from app.ai.planner import AIPlanner
from app.ai.recommendations import RecommendationEngine
from app.database.db import SessionLocal
from app.database.models import Activity, EnergyCost, Habit, Task, TaskStatus, User
from app.integrations.google_calendar import GoogleCalendarService
from app.integrations.openai_client import OpenAIClient
from app.services.habit_tracker import HabitTracker
from app.services.knowledge_service import KnowledgeService
from app.services.scheduler import AIScheduler
from app.services.sync_service import SyncService
from app.services.task_manager import TaskManager
from app.config import get_settings

router = Router()
chat_assistant = ChatAssistant()
settings = get_settings()
BACKLOG_PATH = Path("CHAT_IMPROVEMENT_BACKLOG.md")


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
        start = event.start_time
        end = event.end_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=ZoneInfo("UTC"))
        if end.tzinfo is None:
            end = end.replace(tzinfo=ZoneInfo("UTC"))
        local_start = start.astimezone(tz)
        local_end = end.astimezone(tz)

        title = (event.title or "Без названия").strip()
        if not title:
            title = "Без названия"

        duration_minutes = int((local_end - local_start).total_seconds() // 60)
        if duration_minutes <= 0:
            time_part = f"{local_start.strftime('%H:%M')} • короткое событие"
        elif duration_minutes >= 23 * 60:
            time_part = "Весь день"
        else:
            time_part = f"{local_start.strftime('%H:%M')}–{local_end.strftime('%H:%M')}"
        lines.append(f"- {time_part}  {title}")
    return "\n".join(lines)


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


def _save_calendar_snapshot(context: ContextEngine, events: list, timezone_name: str, day_label: str) -> None:
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")
    payload = {"date": day_label, "timezone": timezone_name, "events": []}
    for idx, event in enumerate(events, start=1):
        start = event.start_time
        end = event.end_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=ZoneInfo("UTC"))
        if end.tzinfo is None:
            end = end.replace(tzinfo=ZoneInfo("UTC"))
        local_start = start.astimezone(tz)
        local_end = end.astimezone(tz)
        payload["events"].append(
            {
                "index": idx,
                "event_id": event.calendar_event_id or "",
                "title": (event.title or "Без названия").strip() or "Без названия",
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


def _delete_calendar_event_from_snapshot(text: str, context: ContextEngine, gcal: GoogleCalendarService) -> str:
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

    candidates = [row for row in candidates if row.get("event_id")]
    if not candidates:
        return "Не нашел событие в календаре по этому описанию. Укажи время, например: 'удали событие 13:45-14:00 Обед'."
    if len(candidates) > 1:
        pending = {"day_label": day_label, "candidates": candidates[:20]}
        context.set_memory("pending_calendar_delete", json.dumps(pending, ensure_ascii=False))
        rows = "\n".join([f"- #{row['index']} {row['start']}–{row['end']}  {row['title']}" for row in candidates[:8]])
        return "Нашел несколько событий. Укажи номер, например: 'удали #13'\n" + rows

    target = candidates[0]
    ok = gcal.delete_event(target["event_id"])
    if not ok:
        return "Не получилось удалить событие в Google Calendar. Попробуй еще раз через пару секунд."
    context.set_memory("pending_calendar_delete", "")
    return f"Удалил событие: {target['start']}–{target['end']}  {target['title']} ({day_label})."


def _try_resolve_pending_calendar_delete(text: str, context: ContextEngine, gcal: GoogleCalendarService) -> str | None:
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

    ok = gcal.delete_event(selected.get("event_id") or "")
    if not ok:
        return "Не получилось удалить выбранное событие. Попробуй еще раз."
    context.set_memory("pending_calendar_delete", "")
    day_label = pending.get("day_label") or "выбранный день"
    return f"Удалил событие: {selected['start']}–{selected['end']}  {selected['title']} ({day_label})."


@router.message(F.text)
async def natural_chat_handler(message: Message) -> None:
    text = (message.text or "").strip()
    if not text:
        return
    with SessionLocal() as db:
        user = _ensure_user(db, message.from_user.id, message.from_user.full_name if message.from_user else "User")
        ks = KnowledgeService(db, user.id)
        context = ContextEngine(db, user.id)
        ks.add_turn(role="user", content=text, intent="incoming")
        ks.learn_from_message(text)

        route = chat_assistant.route_message(text)
        intent = route["intent"]
        normalized_text = route["normalized_text"]
        response_mode = route["response_mode"]

        if text.lower() == "/start" or intent == ChatIntent.welcome:
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
            await message.answer(reply)
            ks.add_turn(role="assistant", content="Sent welcome message", intent="welcome")
            return

        if intent == ChatIntent.calendar_delete:
            gcal = GoogleCalendarService()
            reply = _delete_calendar_event_from_snapshot(normalized_text, context, gcal)
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="calendar_delete")
            if "Не нашел событие" in reply or "Не получилось" in reply:
                _append_chat_backlog_item(user.telegram_id, text, "calendar_delete_matching_or_api_error")
            # Refresh snapshot after successful deletion.
            if reply.startswith("Удалил событие:"):
                calendar_tz = gcal.get_calendar_timezone() or user.timezone or settings.timezone
                day_start, day_end = _build_user_day_window_utc(calendar_tz)
                day_label = _build_local_day_label(calendar_tz)
                refreshed = gcal.list_events(user.id, day_start, day_end)
                _save_calendar_snapshot(context, refreshed, calendar_tz, day_label)
            return

        if intent == ChatIntent.calendar_delete_pending:
            pending_reply = _try_resolve_pending_calendar_delete(normalized_text, context, GoogleCalendarService())
            if not pending_reply:
                pending_reply = "Уточни, какое событие удалить: например 'удали #13' или 'отмена'."
            await message.answer(pending_reply)
            ks.add_turn(role="assistant", content=pending_reply, intent="calendar_delete_pending")
            if pending_reply.startswith("Удалил событие:"):
                gcal = GoogleCalendarService()
                calendar_tz = gcal.get_calendar_timezone() or user.timezone or settings.timezone
                day_start, day_end = _build_user_day_window_utc(calendar_tz)
                day_label = _build_local_day_label(calendar_tz)
                refreshed = gcal.list_events(user.id, day_start, day_end)
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
        if intent == ChatIntent.add_task:
            parsed_tasks = chat_assistant.parse_tasks_batch(normalized_text)
            created = [
                TaskManager(db).create_task(
                    user_id=user.id,
                    title=item["title"],
                    duration_minutes=item["duration_minutes"],
                    priority=item["priority"],
                    deadline=item["deadline"],
                    energy_cost=item["energy_cost"],
                )
                for item in parsed_tasks
            ]
            plan = _build_plan_payload(db, user)
            created_text = "\n".join([f"- {task.title} ({task.duration_minutes}m)" for task in created[:8]])
            reply = f"Добавил:\n{created_text}"
            if plan["scheduled"]:
                reply += "\n\nПлан:\n" + "\n".join(plan["lines"])
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="add_task")
            ks.maybe_learn_from_dialogue(text, reply)
            return

        if intent == ChatIntent.plan_day:
            # For this user flow, "plan for today" must come strictly from Google Calendar.
            gcal = GoogleCalendarService()
            calendar_tz = gcal.get_calendar_timezone() or user.timezone or settings.timezone
            day_start, day_end = _build_user_day_window_utc(calendar_tz)
            day_label = _build_local_day_label(calendar_tz)
            events = gcal.list_events(user.id, day_start, day_end)
            if events:
                rows = _format_calendar_events(events, calendar_tz, max_items=None)
                reply = ks.reply_with_backend_result(
                    text,
                    operation="plan_day_from_calendar",
                    payload={"date": day_label, "timezone": calendar_tz, "rows_text": rows},
                ) or f"План на {day_label} (из Google Calendar):\n{rows}"
                _save_calendar_snapshot(context, events, calendar_tz, day_label)
            else:
                reply = ks.reply_with_backend_result(
                    text,
                    operation="plan_day_from_calendar_empty",
                    payload={"date": day_label, "timezone": calendar_tz},
                ) or f"В Google Calendar на {day_label} событий нет."
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
            day_start, day_end = _build_user_day_window_utc(calendar_tz)
            events = gcal.list_events(user.id, day_start, day_end)
            yougile_count = SyncService(db).sync_yougile_tasks(user.id)
            ai_client = OpenAIClient()
            ai_state = "подключен" if ai_client.provider != "disabled" else "не подключен"
            ai_label = f"{ai_client.provider}/{ai_client.model}" if ai_client.provider != "disabled" else "disabled"
            calendar_state = "подключен" if events is not None else "ошибка подключения"
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
            day_start, day_end = _build_user_day_window_utc(calendar_tz)
            day_label = _build_local_day_label(calendar_tz)
            events = gcal.list_events(user.id, day_start, day_end)
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

        reply = ks.reply_with_memory(normalized_text)
        if not reply:
            reply = "Понял. Если хочешь, напиши задачи на сегодня/завтра, и я сразу их распланирую."
        await message.answer(reply)
        ks.add_turn(role="assistant", content=reply, intent="general_chat")
        ks.maybe_learn_from_dialogue(text, reply)
