import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

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


async def _set_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Запуск и меню"),
            BotCommand(command="now", description="Что у меня на сейчас"),
            BotCommand(command="today", description="Планы на сегодня"),
            BotCommand(command="agent_trace", description="Показать шаги агента"),
            BotCommand(command="agent_trace_last", description="Полный JSON последнего trace"),
            BotCommand(command="training_on", description="Старт сессии вопросов"),
            BotCommand(command="training_off", description="Выключить режим обучения"),
            BotCommand(command="training_show", description="Показать чему бот научился"),
            BotCommand(command="training_forget", description="Забыть последнее обучение"),
            BotCommand(command="training_results", description="Статистика сессии"),
            BotCommand(command="training_analyze", description="Анализ и автообучение"),
            BotCommand(command="training_status", description="Текущий статус обучения"),
            BotCommand(command="training_report", description="Отчет последней сессии"),
            BotCommand(command="training_rollback", description="Откат последнего автообучения"),
        ]
    )


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
            await _set_bot_commands(bot)
            await dp.start_polling(bot)
            return
        except Exception:
            logger.exception("Bot polling crashed. Restarting in 5 seconds.")
            await asyncio.sleep(5)
        finally:
            jobs.shutdown()
            await bot.session.close()
