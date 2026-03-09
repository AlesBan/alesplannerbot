import asyncio

from app.bot.bot import run_bot
from app.database.db import Base, engine


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    init_db()
    asyncio.run(run_bot())
