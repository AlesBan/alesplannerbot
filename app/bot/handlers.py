from datetime import datetime, timedelta, timezone
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


async def _send_welcome(message: Message) -> None:
    await message.answer(
        "Работаем в формате обычного чата.\n"
        "Пиши свободно: 'Сегодня надо ...', 'Завтра ...'.\n"
        "Я сам понимаю задачи, запоминаю контекст и адаптирую стиль ответа под тебя."
    )


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


def _format_calendar_events(events: list, timezone_name: str) -> str:
    if not events:
        return "На сегодня событий нет."
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")

    lines: list[str] = []
    for event in events[:15]:
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


@router.message(F.text)
async def natural_chat_handler(message: Message) -> None:
    text = (message.text or "").strip()
    if not text:
        return
    with SessionLocal() as db:
        user = _ensure_user(db, message.from_user.id, message.from_user.full_name if message.from_user else "User")
        ks = KnowledgeService(db, user.id)
        ks.add_turn(role="user", content=text, intent="incoming")
        ks.learn_from_message(text)

        if text.lower() in {"/start", "start", "привет", "hello"}:
            ContextEngine(db, user.id).set_memory("weekly_work_hours", "0")
            ContextEngine(db, user.id).set_memory("weekly_rest_hours", "0")
            await _send_welcome(message)
            ks.add_turn(role="assistant", content="Sent welcome message", intent="welcome")
            return

        route = chat_assistant.route_message(text)
        intent = route["intent"]
        normalized_text = route["normalized_text"]
        response_mode = route["response_mode"]
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
            plan = _build_plan_payload(db, user)
            reply = "Не вижу подходящих слотов сегодня." if not plan["scheduled"] else "Обновил план дня:\n" + "\n".join(plan["lines"])
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
            day_start, day_end = _build_user_day_window_utc(user.timezone or settings.timezone)
            events = GoogleCalendarService().list_events(user.id, day_start, day_end)
            yougile_count = SyncService(db).sync_yougile_tasks(user.id)
            ai_client = OpenAIClient()
            ai_state = "подключен" if ai_client.provider != "disabled" else "не подключен"
            ai_label = f"{ai_client.provider}/{ai_client.model}" if ai_client.provider != "disabled" else "disabled"
            calendar_state = "подключен" if events is not None else "ошибка подключения"
            yougile_state = "подключен" if yougile_count >= 0 else "ошибка подключения"
            reply = (
                "Статус интеграций:\n"
                f"- Calendar: {calendar_state}, событий сегодня: {len(events)}\n"
                f"- YouGile: {yougile_state}, задач после sync: {yougile_count}\n"
                f"- AI: {ai_state} ({ai_label})"
            )
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="connections_check")
            ks.maybe_learn_from_dialogue(text, reply)
            return

        if intent == ChatIntent.calendar_check:
            day_start, day_end = _build_user_day_window_utc(user.timezone or settings.timezone)
            events = GoogleCalendarService().list_events(user.id, day_start, day_end)
            if events:
                rows = _format_calendar_events(events, user.timezone or settings.timezone)
                header = "Да, календарь подключен. События на сегодня:"
                if response_mode == "calendar_exact":
                    header = "Как в календаре (без преобразований формата), события на сегодня:"
                reply = header + "\n" + rows
            else:
                reply = "Календарь подключен, но на сегодня событий не найдено (или календарь не расшарен сервис-аккаунту)."
            await message.answer(reply)
            ks.add_turn(role="assistant", content=reply, intent="calendar_check")
            ks.maybe_learn_from_dialogue(text, reply)
            return

        reply = ks.reply_with_memory(normalized_text)
        if not reply:
            reply = "Понял. Если хочешь, напиши задачи на сегодня/завтра, и я сразу их распланирую."
        await message.answer(reply)
        ks.add_turn(role="assistant", content=reply, intent="general_chat")
        ks.maybe_learn_from_dialogue(text, reply)
