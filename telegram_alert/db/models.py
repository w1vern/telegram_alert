"""SQLAlchemy 2.0 ORM models.

Postgres holds only "ideologically clean" state: users, the singleton settings
row, the notification schedule, media idempotency markers and the per-user
message map (needed both for fan-out idempotency and for attaching the clip
button later).  Transient retry/queue state lives in RabbitMQ, not here.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    """Anyone who pressed /start becomes a "known" user (authorized=False).
    A superuser grants access by flipping ``authorized``."""

    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    authorized: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Settings(Base):
    """Global singleton (id == 1).  All authorized users share these.

    ``mode`` is the global tri-state: "off" / "always" / "schedule"
    (see :class:`telegram_alert.modes.AlertMode`).
    """

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    mode: Mapped[str] = mapped_column(String(16), default="schedule")


class ScheduleEntry(Base):
    """A presence window.  Inside it, motion is expected -> suppress alerts.

    Minutes from midnight in the dacha timezone.  ``end_min <= start_min`` means
    the window wraps past midnight (e.g. 22:00-02:00).
    """

    __tablename__ = "schedule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    weekday: Mapped[int] = mapped_column(Integer)  # 0=Mon .. 6=Sun
    start_min: Mapped[int] = mapped_column(Integer)
    end_min: Mapped[int] = mapped_column(Integer)


class Processed(Base):
    """Media idempotency per review_id."""

    __tablename__ = "processed"

    review_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    camera: Mapped[str | None] = mapped_column(String(128), nullable=True)
    snap_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    clip_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SentMessage(Base):
    """One row per (review, user) Telegram photo message.

    Enables idempotent fan-out (don't resend on retry) and lets us attach the
    clip button to the exact message later.
    """

    __tablename__ = "sent_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[str] = mapped_column(String(255), index=True)
    tg_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    clip_attached: Mapped[bool] = mapped_column(Boolean, default=False)
