from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    disclaimer_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    requests: Mapped[list["ScenarioRequest"]] = relationship(back_populates="user")


class ScenarioRequest(Base):
    """Лог завершених прогонів сценарію. Без PII — тільки факт + статус."""

    __tablename__ = "scenario_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    scenario: Mapped[str] = mapped_column(String(32))  # "salary" | "fine" | "summons"
    status: Mapped[str] = mapped_column(String(16))  # "started" | "completed" | "abandoned"
    plan_chosen: Mapped[str | None] = mapped_column(String(32), nullable=True)
    documents_generated: Mapped[int] = mapped_column(default=0)
    lawyer_contact_requested: Mapped[bool] = mapped_column(default=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship(back_populates="requests")


class ConsultationRequest(Base):
    """Заявка на консультацію юриста — зберігаємо для аудиту і повторного push, якщо first failed."""

    __tablename__ = "consultation_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(150))
    phone: Mapped[str] = mapped_column(String(32))
    field: Mapped[str] = mapped_column(String(64))  # код галузі права
    description: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ScenarioDraft(Base):
    """Чернетка незавершеного сценарію — містить PII, TTL 24 год."""

    __tablename__ = "scenario_drafts"
    __table_args__ = (UniqueConstraint("telegram_id", "scenario", name="uq_draft_user_scenario"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(index=True)
    scenario: Mapped[str] = mapped_column(String(32))
    state: Mapped[int] = mapped_column(Integer)
    data_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
