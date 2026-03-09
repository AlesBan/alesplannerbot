from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.handlers import router as command_router
from app.bot.voice_handler import router as voice_router
from app.config import get_settings
from app.services.background_jobs import BackgroundJobs


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(command_router)
    dp.include_router(voice_router)
    return dp


async def run_bot() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    bot = Bot(token=settings.telegram_bot_token)
    dp = create_dispatcher()
    jobs = BackgroundJobs(bot)
    jobs.start()
    try:
        await dp.start_polling(bot)
    finally:
        jobs.shutdown()
