import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.handlers import router as command_router
from app.bot.voice_handler import router as voice_router
from app.config import get_settings
from app.services.background_jobs import BackgroundJobs

logger = logging.getLogger(__name__)


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(command_router)
    dp.include_router(voice_router)
    return dp


async def run_bot() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    # Keep worker process alive on transient polling/runtime errors.
    while True:
        bot = Bot(token=settings.telegram_bot_token)
        dp = create_dispatcher()
        jobs = BackgroundJobs(bot)
        jobs.start()
        try:
            await dp.start_polling(bot)
            return
        except Exception:
            logger.exception("Bot polling crashed. Restarting in 5 seconds.")
            await asyncio.sleep(5)
        finally:
            jobs.shutdown()
            await bot.session.close()
