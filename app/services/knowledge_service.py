import json
import re
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

    def is_training_enabled(self) -> bool:
        raw = (self.context.get_memory("training_mode") or "on").strip().lower()
        return raw not in {"off", "0", "false", "no"}

    def set_training_enabled(self, enabled: bool) -> None:
        self.context.set_memory("training_mode", "on" if enabled else "off")

    def list_taught_pairs(self, limit: int = 10) -> list[LearnedQA]:
        return list(
            self.db.scalars(
                select(LearnedQA)
                .where(LearnedQA.user_id == self.user_id, LearnedQA.confidence >= 80)
                .order_by(LearnedQA.updated_at.desc())
                .limit(limit)
            )
        )

    def forget_last_taught_pair(self) -> LearnedQA | None:
        latest = self.db.scalar(
            select(LearnedQA)
            .where(LearnedQA.user_id == self.user_id, LearnedQA.confidence >= 80)
            .order_by(LearnedQA.updated_at.desc())
            .limit(1)
        )
        if not latest:
            return None
        snapshot = LearnedQA(
            user_id=latest.user_id,
            question_pattern=latest.question_pattern,
            answer_template=latest.answer_template,
            confidence=latest.confidence,
        )
        self.db.delete(latest)
        self.db.commit()
        return snapshot

    def learn_from_message(self, text: str) -> None:
        lower = text.lower()
        if lower.startswith("запомни") or lower.startswith("remember"):
            fact = text.split(" ", 1)[1].strip() if " " in text else ""
            if fact:
                self.add_knowledge(topic="user_preference", content=fact, source="explicit_memory")
                self.context.set_memory("last_explicit_fact", fact)

        if "мне нравится" in lower or "i prefer" in lower:
            self.add_knowledge(topic="preferences", content=text.strip(), source="implicit_preference")

        # Teacher-student feedback loop: user explicitly says expected answer style.
        if self.is_training_enabled() and any(token in lower for token in ["я ждал", "я ожидал", "ожидал от тебя", "expected from you"]):
            self.add_knowledge(topic="response_preference", content=text.strip(), source="teacher_feedback")
            if any(token in lower for token in ["сейчас", "на сейчас", "right now", "current"]):
                self.context.set_memory("style_current_focus", "single_current_then_next")

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

    @staticmethod
    def _normalize_tokens(text: str) -> set[str]:
        lowered = re.sub(r"[^a-zA-Zа-яА-Я0-9\s]", " ", (text or "").lower())
        tokens = [t.strip() for t in lowered.split() if len(t.strip()) >= 3]
        stop_words = {
            "что",
            "как",
            "это",
            "тебя",
            "меня",
            "сейчас",
            "today",
            "now",
            "with",
            "это",
            "the",
            "and",
        }
        return {t for t in tokens if t not in stop_words}

    def find_taught_answer(self, user_message: str) -> str | None:
        """
        Deterministic "student learned from teacher" retrieval.
        Prioritizes high-confidence taught pairs and matches by token overlap.
        """
        msg_tokens = self._normalize_tokens(user_message)
        if not msg_tokens:
            return None
        qas = list(
            self.db.scalars(
                select(LearnedQA)
                .where(LearnedQA.user_id == self.user_id, LearnedQA.confidence >= 80)
                .order_by(LearnedQA.updated_at.desc())
                .limit(120)
            )
        )
        best_answer = None
        best_score = 0.0
        for qa in qas:
            q_tokens = self._normalize_tokens(qa.question_pattern)
            if not q_tokens:
                continue
            overlap = len(msg_tokens & q_tokens)
            if overlap == 0:
                continue
            score = overlap / max(1, min(len(msg_tokens), len(q_tokens)))
            if score > best_score and overlap >= 2:
                best_score = score
                best_answer = qa.answer_template
        if best_score >= 0.55 and best_answer:
            return best_answer.strip()
        return None

    def register_expectation_feedback(self, feedback_text: str) -> dict:
        """
        Learns from explicit teacher feedback:
        User: "я ожидал, что ты ответишь: ...".
        Stores a high-confidence learned QA using the previous user question.
        """
        if not self.is_training_enabled():
            return {"ok": False, "reason": "training_disabled"}

        expected_match = re.search(r"(?:я\s+ожидал[а]?(?:,\s*что\s*ты\s*ответишь)?\s*[:\-]?\s*)(.+)$", feedback_text.strip(), re.I)
        expected_answer = expected_match.group(1).strip() if expected_match else ""
        if not expected_answer:
            return {"ok": False, "reason": "empty_expected"}

        turns = self.get_recent_turns(limit=14)
        # We need the previous user request before this feedback message.
        prev_user_question = None
        seen_current = False
        for turn in reversed(turns):
            if turn.role == "user" and turn.content.strip() == feedback_text.strip() and not seen_current:
                seen_current = True
                continue
            if seen_current and turn.role == "user":
                prev_user_question = turn.content.strip()
                break

        if not prev_user_question:
            return {"ok": False, "reason": "question_not_found"}

        existing = self.db.scalar(
            select(LearnedQA)
            .where(
                LearnedQA.user_id == self.user_id,
                LearnedQA.question_pattern == prev_user_question,
            )
            .limit(1)
        )
        if existing:
            existing.answer_template = expected_answer
            existing.confidence = 95
            existing.updated_at = datetime.utcnow()
        else:
            self.db.add(
                LearnedQA(
                    user_id=self.user_id,
                    question_pattern=prev_user_question,
                    answer_template=expected_answer,
                    confidence=95,
                )
            )
        self.db.commit()
        self.add_knowledge(topic="teacher_feedback", content=f"Q: {prev_user_question} | A: {expected_answer}", source="teacher_feedback")
        return {"ok": True, "question": prev_user_question, "expected": expected_answer}

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
            "Do not use template greetings or canned intros. "
            "Do not list capabilities unless user explicitly asks. "
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
            "Never invent data. If payload contains schedule rows, preserve times/titles exactly. "
            "Never output synthetic 'План:' blocks or guessed task lists that are absent in payload."
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
