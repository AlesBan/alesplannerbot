from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import UserMemory


class ContextEngine:
    def __init__(self, db: Session, user_id: int) -> None:
        self.db = db
        self.user_id = user_id

    def get_memory(self, key: str) -> str | None:
        stmt = select(UserMemory).where(UserMemory.user_id == self.user_id, UserMemory.key == key)
        memory = self.db.scalar(stmt)
        return memory.value if memory else None

    def set_memory(self, key: str, value: str, commit: bool = True) -> UserMemory:
        stmt = select(UserMemory).where(UserMemory.user_id == self.user_id, UserMemory.key == key)
        memory = self.db.scalar(stmt)
        if memory:
            memory.value = value
        else:
            memory = UserMemory(user_id=self.user_id, key=key, value=value)
            self.db.add(memory)
        if commit:
            self.db.commit()
            self.db.refresh(memory)
        else:
            self.db.flush()
        return memory

    def export_memory(self) -> dict[str, str]:
        stmt = select(UserMemory).where(UserMemory.user_id == self.user_id)
        return {mem.key: mem.value for mem in self.db.scalars(stmt)}
