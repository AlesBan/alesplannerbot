import json
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ai.context_engine import ContextEngine
from app.database.models import ConversationTurn, KnowledgeItem, LearnedQA
from app.integrations.openai_client import OpenAIClient


GLOBAL_KNOWLEDGE_SEED = [
    ("productivity", "Break complex tasks into chunks of 25-60 minutes with short breaks."),
    ("wellbeing", "Protect rest time daily to avoid burnout and maintain long-term productivity."),
    ("planning", "Schedule high-energy tasks in the morning and medium/low tasks later."),
    ("habits", "Track habits weekly and use small consistent actions over perfection."),
    ("communication", "When uncertain, ask one clarifying question instead of making wrong assumptions."),
]


class KnowledgeService:
    def __init__(self, db: Session, user_id: int) -> None:
        self.db = db
        self.user_id = user_id
        self.context = ContextEngine(db, user_id)
        self.ai = OpenAIClient()

    @staticmethod
    def seed_global_knowledge(db: Session) -> None:
        existing = db.scalar(select(KnowledgeItem.id).where(KnowledgeItem.user_id.is_(None)).limit(1))
        if existing:
            return
        for topic, content in GLOBAL_KNOWLEDGE_SEED:
            db.add(KnowledgeItem(user_id=None, topic=topic, content=content, source="seed"))
        db.commit()

    def add_turn(self, role: str, content: str, intent: str = "general_chat") -> None:
        turn = ConversationTurn(user_id=self.user_id, role=role, content=content, intent=intent)
        self.db.add(turn)
        self.db.commit()

    def learn_from_message(self, text: str) -> None:
        lower = text.lower()
        if lower.startswith("запомни") or lower.startswith("remember"):
            fact = text.split(" ", 1)[1].strip() if " " in text else ""
            if fact:
                self.add_knowledge(topic="user_preference", content=fact, source="explicit_memory")
                self.context.set_memory("last_explicit_fact", fact)

        if "мне нравится" in lower or "i prefer" in lower:
            self.add_knowledge(topic="preferences", content=text.strip(), source="implicit_preference")

    def add_knowledge(self, topic: str, content: str, source: str = "chat") -> None:
        self.db.add(
            KnowledgeItem(
                user_id=self.user_id,
                topic=topic[:128],
                content=content.strip(),
                source=source[:64],
            )
        )
        self.db.commit()

    def learn_qa_pair(self, question: str, answer: str, confidence: int = 60) -> None:
        self.db.add(
            LearnedQA(
                user_id=self.user_id,
                question_pattern=question.strip(),
                answer_template=answer.strip(),
                confidence=max(1, min(100, confidence)),
            )
        )
        self.db.commit()

    def get_recent_turns(self, limit: int = 8) -> list[ConversationTurn]:
        stmt = (
            select(ConversationTurn)
            .where(ConversationTurn.user_id == self.user_id)
            .order_by(ConversationTurn.created_at.desc())
            .limit(limit)
        )
        return list(reversed(list(self.db.scalars(stmt))))

    def get_relevant_knowledge(self, query: str, limit: int = 8) -> list[str]:
        tokens = [token.strip().lower() for token in query.replace(",", " ").split() if len(token.strip()) > 3]
        filters = []
        for token in tokens[:8]:
            filters.append(KnowledgeItem.content.ilike(f"%{token}%"))
            filters.append(KnowledgeItem.topic.ilike(f"%{token}%"))

        base_stmt = select(KnowledgeItem).where(
            or_(KnowledgeItem.user_id == self.user_id, KnowledgeItem.user_id.is_(None)),
        )
        if filters:
            base_stmt = base_stmt.where(or_(*filters))
        rows = list(self.db.scalars(base_stmt.order_by(KnowledgeItem.updated_at.desc()).limit(limit)))

        qa_stmt = select(LearnedQA).where(LearnedQA.user_id == self.user_id).order_by(LearnedQA.updated_at.desc()).limit(limit)
        qas = list(self.db.scalars(qa_stmt))
        qa_rows = [f"Q: {item.question_pattern} | A: {item.answer_template}" for item in qas]
        return [f"[{row.topic}] {row.content}" for row in rows] + qa_rows

    def reply_with_memory(self, user_message: str, allow_greeting: bool = True) -> str:
        memory = self.context.export_memory()
        memory_blob = "\n".join([f"- {k}: {v}" for k, v in memory.items()]) or "- empty"
        knowledge_blob = "\n".join(self.get_relevant_knowledge(user_message, limit=10)) or "- empty"
        turns_blob = "\n".join([f"{turn.role}: {turn.content}" for turn in self.get_recent_turns(limit=6)]) or "- empty"

        system_prompt = (
            "You are a personal AI life assistant and chat-orchestrator. "
            "Reply in Russian, concise and useful. "
            "Adapt to user's style based on past dialogue and learned knowledge. "
            "Your job is to decide when to call tool-backed actions versus normal chat. "
            "Calendar/YouGile are execution backends; chat layer controls intent and wording. "
            "Never invent tool results. If data is missing, ask one concise clarifying question. "
            "Keep dialogue continuity; do not restart conversation tone on each message. "
            "If user asks a broad question, give structured practical guidance. "
            "If user gives plans, acknowledge and suggest concrete next action."
        )
        if not allow_greeting:
            system_prompt += " Do not greet the user in this reply."
        user_prompt = (
            f"User message:\n{user_message}\n\n"
            f"Long-term memory:\n{memory_blob}\n\n"
            f"Knowledge base:\n{knowledge_blob}\n\n"
            f"Recent dialogue:\n{turns_blob}\n\n"
            "Generate a personalized response in Russian."
        )
        response = self.ai.chat_completion(system_prompt, user_prompt)
        if not response or response.startswith("AI unavailable:") or "AI is disabled" in response:
            return ""
        return response.strip()

    def reply_with_backend_result(self, user_message: str, operation: str, payload: dict) -> str:
        """
        Generate a natural, non-template response from backend facts.
        """
        payload_json = json.dumps(payload, ensure_ascii=False)
        system_prompt = (
            "You are a personal assistant chat layer. "
            "Do not use canned/template phrases. "
            "Respond naturally in Russian in user's style. "
            "You receive backend operation facts and must explain result clearly. "
            "Never invent data. If payload contains schedule rows, preserve times/titles exactly."
        )
        user_prompt = (
            f"User message:\n{user_message}\n\n"
            f"Operation:\n{operation}\n\n"
            f"Payload (JSON):\n{payload_json}\n\n"
            "Write a concise helpful reply in Russian."
        )
        response = self.ai.chat_completion(system_prompt, user_prompt)
        if not response or response.startswith("AI unavailable:") or "AI is disabled" in response:
            return ""
        return response.strip()

    def maybe_learn_from_dialogue(self, user_text: str, assistant_text: str) -> None:
        if len(user_text) < 8 or len(assistant_text) < 8:
            return
        if "?" in user_text or user_text.lower().startswith(("как", "what", "how", "почему", "зачем")):
            self.learn_qa_pair(question=user_text[:700], answer=assistant_text[:1200], confidence=55)
        self.add_knowledge(topic="dialogue_summary", content=f"User: {user_text} | Assistant: {assistant_text}", source="auto_dialogue")
        self.context.set_memory("last_chat_at", datetime.utcnow().isoformat())
