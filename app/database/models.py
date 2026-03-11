from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, Enum as SqlEnum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.db import Base


class PriorityLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class TaskStatus(str, Enum):
    planned = "planned"
    pending = "pending"
    completed = "completed"


class TaskSource(str, Enum):
    manual = "manual"
    yougile = "yougile"


class EnergyCost(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class CalendarProvider(str, Enum):
    google = "google"


class CalendarEventStatus(str, Enum):
    confirmed = "confirmed"
    tentative = "tentative"
    cancelled = "cancelled"


class CalendarVisibility(str, Enum):
    default = "default"
    public = "public"
    private = "private"
    confidential = "confidential"


class CalendarTransparency(str, Enum):
    opaque = "opaque"
    transparent = "transparent"


class CalendarSyncState(str, Enum):
    synced = "synced"
    pending_push = "pending_push"
    pending_delete = "pending_delete"
    push_failed = "push_failed"


class CalendarOutboxOp(str, Enum):
    upsert = "upsert"
    delete = "delete"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    tasks: Mapped[list["Task"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    habits: Mapped[list["Habit"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    memories: Mapped[list["UserMemory"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    activities: Mapped[list["Activity"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    activity_logs: Mapped[list["ActivityLog"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    knowledge_items: Mapped[list["KnowledgeItem"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    conversation_turns: Mapped[list["ConversationTurn"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    learned_qas: Mapped[list["LearnedQA"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    calendar_accounts: Mapped[list["CalendarAccount"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    calendar_sync_states: Mapped[list["CalendarSyncStateModel"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    calendar_events: Mapped[list["CalendarEvent"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    calendar_categories: Mapped[list["CalendarCategory"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    calendar_outbox_items: Mapped[list["CalendarOutbox"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    training_feedback_items: Mapped[list["TrainingFeedback"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    priority: Mapped[PriorityLevel] = mapped_column(SqlEnum(PriorityLevel), default=PriorityLevel.medium)
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(SqlEnum(TaskStatus), default=TaskStatus.pending)
    source: Mapped[TaskSource] = mapped_column(SqlEnum(TaskSource), default=TaskSource.manual)
    external_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    energy_cost: Mapped[EnergyCost] = mapped_column(SqlEnum(EnergyCost), default=EnergyCost.medium)
    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scheduled_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="tasks")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    calendar_event_id: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(255), default="Event")
    start_time: Mapped[datetime] = mapped_column(DateTime)
    end_time: Mapped[datetime] = mapped_column(DateTime)
    event_type: Mapped[str] = mapped_column(String(64), default="calendar")


class Habit(Base):
    __tablename__ = "habits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    frequency_per_week: Mapped[int] = mapped_column(Integer, default=1)
    last_completed: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="habits")


class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    activity_type: Mapped[str] = mapped_column(String(64), default="general")
    duration_minutes: Mapped[int] = mapped_column(Integer)
    location: Mapped[str] = mapped_column(String(64), default="any")
    season: Mapped[str] = mapped_column(String(32), default="any")
    energy_cost: Mapped[EnergyCost] = mapped_column(SqlEnum(EnergyCost), default=EnergyCost.low)

    user: Mapped["User"] = relationship(back_populates="activities")


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    activity_type: Mapped[str] = mapped_column(String(64))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="activity_logs")


class UserMemory(Base):
    __tablename__ = "user_memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    key: Mapped[str] = mapped_column(String(128), index=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="memories")


class KnowledgeItem(Base):
    __tablename__ = "knowledge_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True, nullable=True)
    topic: Mapped[str] = mapped_column(String(128), default="general", index=True)
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(64), default="chat")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User | None"] = relationship(back_populates="knowledge_items")


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user|assistant
    content: Mapped[str] = mapped_column(Text)
    intent: Mapped[str] = mapped_column(String(64), default="general_chat")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="conversation_turns")


class LearnedQA(Base):
    __tablename__ = "learned_qa"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    question_pattern: Mapped[str] = mapped_column(Text)
    answer_template: Mapped[str] = mapped_column(Text)
    confidence: Mapped[int] = mapped_column(Integer, default=50)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="learned_qas")


class CalendarAccount(Base):
    __tablename__ = "calendar_accounts"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_calendar_account_user_provider"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[CalendarProvider] = mapped_column(SqlEnum(CalendarProvider), default=CalendarProvider.google)
    provider_calendar_id: Mapped[str] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="calendar_accounts")


class CalendarSyncStateModel(Base):
    __tablename__ = "calendar_sync_state"
    __table_args__ = (UniqueConstraint("user_id", "provider", "provider_calendar_id", name="uq_calendar_sync_state"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[CalendarProvider] = mapped_column(SqlEnum(CalendarProvider), default=CalendarProvider.google)
    provider_calendar_id: Mapped[str] = mapped_column(String(255), index=True)
    sync_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_full_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_incremental_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    health_status: Mapped[str] = mapped_column(String(32), default="ok")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="calendar_sync_states")


class CalendarCategory(Base):
    __tablename__ = "calendar_categories"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_calendar_category_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    color_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="calendar_categories")
    events: Mapped[list["CalendarEvent"]] = relationship()


class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id", name="uq_calendar_provider_event"),
        UniqueConstraint("user_id", "recurring_event_id", "original_start_at", name="uq_calendar_recurring_instance"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[CalendarProvider] = mapped_column(SqlEnum(CalendarProvider), default=CalendarProvider.google)
    provider_calendar_id: Mapped[str] = mapped_column(String(255), index=True)
    provider_event_id: Mapped[str] = mapped_column(String(255), index=True)
    etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)

    title: Mapped[str] = mapped_column(String(255), default="Event")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    is_all_day: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[CalendarEventStatus] = mapped_column(SqlEnum(CalendarEventStatus), default=CalendarEventStatus.confirmed)
    visibility: Mapped[CalendarVisibility] = mapped_column(SqlEnum(CalendarVisibility), default=CalendarVisibility.default)
    transparency: Mapped[CalendarTransparency] = mapped_column(SqlEnum(CalendarTransparency), default=CalendarTransparency.opaque)
    event_type: Mapped[str] = mapped_column(String(64), default="default")
    color_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    is_recurring_master: Mapped[bool] = mapped_column(Boolean, default=False)
    recurrence_rule_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    recurring_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    original_start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    reminder_use_default: Mapped[bool] = mapped_column(Boolean, default=True)
    conference_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_link: Mapped[str | None] = mapped_column(Text, nullable=True)

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sync_state: Mapped[CalendarSyncState] = mapped_column(SqlEnum(CalendarSyncState), default=CalendarSyncState.synced)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    category_id: Mapped[int | None] = mapped_column(ForeignKey("calendar_categories.id"), nullable=True, index=True)

    user: Mapped["User"] = relationship(back_populates="calendar_events")
    reminders: Mapped[list["CalendarReminder"]] = relationship(back_populates="event", cascade="all, delete-orphan")


class CalendarReminder(Base):
    __tablename__ = "calendar_reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("calendar_events.id"), index=True)
    method: Mapped[str] = mapped_column(String(32), default="popup")
    minutes: Mapped[int] = mapped_column(Integer, default=10)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    event: Mapped["CalendarEvent"] = relationship(back_populates="reminders")


class CalendarOutbox(Base):
    __tablename__ = "calendar_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("calendar_events.id"), index=True)
    provider: Mapped[CalendarProvider] = mapped_column(SqlEnum(CalendarProvider), default=CalendarProvider.google)
    operation: Mapped[CalendarOutboxOp] = mapped_column(SqlEnum(CalendarOutboxOp), default=CalendarOutboxOp.upsert)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    available_after: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="calendar_outbox_items")


class IntentProfile(Base):
    __tablename__ = "intent_profiles"
    __table_args__ = (UniqueConstraint("user_id", "profile_name", name="uq_intent_profile_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True, nullable=True)
    profile_name: Mapped[str] = mapped_column(String(64), index=True)
    token_keywords_json: Mapped[str] = mapped_column(Text, default="[]")
    phrase_keywords_json: Mapped[str] = mapped_column(Text, default="[]")
    aliases_json: Mapped[str] = mapped_column(Text, default="[]")
    threshold: Mapped[float] = mapped_column(Float, default=0.5)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TrainingFeedback(Base):
    __tablename__ = "training_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    expected_intent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    predicted_intent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="training_feedback_items")
