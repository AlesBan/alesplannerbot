from pathlib import Path

from aiogram import Router
from aiogram.types import Message

from app.bot.handlers import _handle_incoming_text
from app.utils.voice_utils import transcribe_voice

router = Router()


@router.message(lambda message: bool(message.voice))
async def handle_voice_task(message: Message) -> None:
    file_info = await message.bot.get_file(message.voice.file_id)
    temp_path = Path("tmp_voice.ogg")
    await message.bot.download_file(file_info.file_path, destination=temp_path)

    try:
        transcript = transcribe_voice(str(temp_path))
        if not transcript:
            await message.answer("Не удалось распознать голосовое сообщение. Попробуй отправить еще раз.")
            return
        await _handle_incoming_text(message, transcript, source="voice")
    finally:
        if temp_path.exists():
            temp_path.unlink()
