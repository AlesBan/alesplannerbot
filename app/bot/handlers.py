from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from dateutil import parser as date_parser

from app.ai.context_engine import ContextEngine
from app.ai.planner import AIPlanner
from app.ai.recommendations import RecommendationEngine
from app.database.db import SessionLocal
from app.database.models import Activity, EnergyCost, Habit, PriorityLevel, Task, TaskStatus, User
from app.integrations.google_calendar import GoogleCalendarService
from app.services.habit_tracker import HabitTracker
from app.services.scheduler import AIScheduler
from app.services.sync_service import SyncService
from app.services.task_manager import TaskManager

router = Router()


class AddTaskState(StatesGroup):
    title = State()
    duration = State()
    priority = State()
    deadline = State()
    energy_cost = State()


def _ensure_user(db, telegram_id: int, name: str) -> User:
    user = db.query(User).filter(User.telegram_id == telegram_id).one_or_none()
    if user:
        return user

    user = User(telegram_id=telegram_id, name=name or "User", timezone="UTC")
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


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    with SessionLocal() as db:
        user = _ensure_user(db, message.from_user.id, message.from_user.full_name if message.from_user else "User")
        ContextEngine(db, user.id).set_memory("weekly_work_hours", "0")
        ContextEngine(db, user.id).set_memory("weekly_rest_hours", "0")
    await message.answer(
        "Life AI Assistant is ready.\n"
        "Commands:\n"
        "/add - add task\n"
        "/plan - build daily plan\n"
        "/sync - sync tasks from YouGile\n"
        "/free - suggest activities\n"
        "/report - weekly productivity report"
    )


@router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext) -> None:
    await state.set_state(AddTaskState.title)
    await message.answer("Enter task title:")


@router.message(AddTaskState.title)
async def add_task_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text.strip())
    await state.set_state(AddTaskState.duration)
    await message.answer("Duration in minutes:")


@router.message(AddTaskState.duration)
async def add_task_duration(message: Message, state: FSMContext) -> None:
    if not message.text or not message.text.isdigit():
        await message.answer("Please send an integer, e.g. 45")
        return
    await state.update_data(duration_minutes=int(message.text))
    await state.set_state(AddTaskState.priority)
    await message.answer("Priority (low/medium/high):")


@router.message(AddTaskState.priority)
async def add_task_priority(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip().lower()
    if value not in {"low", "medium", "high"}:
        await message.answer("Use one of: low, medium, high")
        return
    await state.update_data(priority=value)
    await state.set_state(AddTaskState.deadline)
    await message.answer("Deadline (YYYY-MM-DD HH:MM) or 'none':")


@router.message(AddTaskState.deadline)
async def add_task_deadline(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip().lower()
    deadline = None
    if value != "none":
        try:
            deadline = date_parser.parse(value)
        except Exception:
            await message.answer("Cannot parse deadline. Example: 2026-03-10 16:30 or none")
            return
    await state.update_data(deadline=deadline.isoformat() if deadline else None)
    await state.set_state(AddTaskState.energy_cost)
    await message.answer("Energy cost (low/medium/high):")


@router.message(AddTaskState.energy_cost)
async def add_task_energy(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip().lower()
    if value not in {"low", "medium", "high"}:
        await message.answer("Use one of: low, medium, high")
        return

    data = await state.get_data()
    with SessionLocal() as db:
        user = _ensure_user(db, message.from_user.id, message.from_user.full_name if message.from_user else "User")
        task = TaskManager(db).create_task(
            user_id=user.id,
            title=data["title"],
            duration_minutes=data["duration_minutes"],
            priority=PriorityLevel(data["priority"]),
            deadline=date_parser.parse(data["deadline"]) if data["deadline"] else None,
            energy_cost=EnergyCost(value),
        )
    await state.clear()
    await message.answer(f"Task added: {task.title} ({task.duration_minutes} min, {task.priority.value}, {task.energy_cost.value})")


@router.message(Command("plan"))
async def cmd_plan(message: Message) -> None:
    with SessionLocal() as db:
        user = _ensure_user(db, message.from_user.id, message.from_user.full_name if message.from_user else "User")
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
            names = ", ".join(h.name for h in overdue_habits)
            overdue_text = f"\nOverdue habits: {names}"

    if not scheduled:
        await message.answer("No tasks could be scheduled today. Try lowering workload or adding more free time.")
        return

    lines = [f"{item.start.strftime('%H:%M')}-{item.end.strftime('%H:%M')} {item.title}" for item in scheduled]
    await message.answer(
        "Today's optimized plan:\n"
        + "\n".join(lines)
        + f"\n\nSynced from YouGile: {synced_count}\n\nAI notes:\n{ai_text}{overdue_text}"
    )


@router.message(Command("sync"))
async def cmd_sync(message: Message) -> None:
    with SessionLocal() as db:
        user = _ensure_user(db, message.from_user.id, message.from_user.full_name if message.from_user else "User")
        count = SyncService(db).sync_yougile_tasks(user.id)
    await message.answer(f"YouGile sync completed. Imported/updated tasks: {count}")


@router.message(Command("free"))
async def cmd_free(message: Message) -> None:
    with SessionLocal() as db:
        user = _ensure_user(db, message.from_user.id, message.from_user.full_name if message.from_user else "User")
        context = ContextEngine(db, user.id)
        weekly_work_hours = float(context.get_memory("weekly_work_hours") or 25)
        weekly_rest_hours = float(context.get_memory("weekly_rest_hours") or 20)
        fatigue = RecommendationEngine(db).estimate_fatigue(weekly_work_hours, weekly_rest_hours)
        suggestions = RecommendationEngine(db).suggest_activities(
            user_id=user.id,
            free_minutes=60,
            location=context.get_memory("location") or "any",
            season=context.get_memory("season") or "any",
            fatigue_score=fatigue,
        )
    if not suggestions:
        await message.answer("No activity suggestions found. Add activities to the database first.")
        return
    text = "\n".join([f"- {item.name} ({item.duration_minutes} min, {item.energy_cost.value})" for item in suggestions])
    await message.answer(f"You have free time. Suggested activities:\n{text}")


@router.message(Command("report"))
async def cmd_report(message: Message) -> None:
    with SessionLocal() as db:
        user = _ensure_user(db, message.from_user.id, message.from_user.full_name if message.from_user else "User")
        tasks = TaskManager(db).list_open_tasks(user.id)
        completed_count = db.query(Task).filter(Task.user_id == user.id, Task.status == TaskStatus.completed).count()
        context = ContextEngine(db, user.id)
        weekly_work = context.get_memory("weekly_work_hours") or "0"
        weekly_rest = context.get_memory("weekly_rest_hours") or "0"
    await message.answer(
        "Weekly productivity report:\n"
        f"- Open/planned tasks: {len(tasks)}\n"
        f"- Weekly work hours: {weekly_work}\n"
        f"- Weekly rest hours: {weekly_rest}\n"
        f"- Completed tasks: {completed_count}\n"
        "Tip: keep rest hours at least 50% of work hours."
    )


@router.message(F.text.startswith("move task"))
async def dynamic_reschedule_hint(message: Message) -> None:
    text = (message.text or "").strip().lower()
    if text.startswith("move task") and text.endswith("to tomorrow"):
        await message.answer("Dynamic reschedule accepted. Use /plan to rebuild tomorrow's schedule with updated constraints.")
