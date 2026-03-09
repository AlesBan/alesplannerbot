from pathlib import Path

from aiogram import Router
from aiogram.types import Message

from app.database.db import SessionLocal
from app.database.models import EnergyCost, PriorityLevel
from app.services.task_manager import TaskManager
from app.bot.handlers import _ensure_user
from app.utils.voice_utils import transcribe_voice

router = Router()


@router.message(lambda message: bool(message.voice))
async def handle_voice_task(message: Message) -> None:
    file_info = await message.bot.get_file(message.voice.file_id)
    temp_path = Path("tmp_voice.ogg")
    await message.bot.download_file(file_info.file_path, destination=temp_path)

    try:
        transcript = transcribe_voice(str(temp_path))
        title = transcript[:100] if transcript else "Voice task"
        with SessionLocal() as db:
            user = _ensure_user(db, message.from_user.id, message.from_user.full_name if message.from_user else "User")
            task = TaskManager(db).create_task(
                user_id=user.id,
                title=title,
                duration_minutes=30,
                priority=PriorityLevel.medium,
                energy_cost=EnergyCost.medium,
            )
        await message.answer(f"Voice task captured: {task.title}")
    finally:
        if temp_path.exists():
            temp_path.unlink()
