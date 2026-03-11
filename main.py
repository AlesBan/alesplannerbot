import asyncio

from app.bot.bot import run_bot
from app.database.db import Base, SessionLocal, engine
from app.services.intent_profile_service import IntentProfileService
from app.services.knowledge_service import KnowledgeService


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        KnowledgeService.seed_global_knowledge(db)
        IntentProfileService.seed_defaults(db)


if __name__ == "__main__":
    init_db()
    asyncio.run(run_bot())
