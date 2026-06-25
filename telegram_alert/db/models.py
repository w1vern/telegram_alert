"""SQLAlchemy 2.0 ORM models.

Postgres holds only "ideologically clean" state: the singleton settings row, the
notification schedule, media idempotency markers and the review -> group message
map (needed both for send idempotency and for attaching the clip button later).
The bot is bound to a single Telegram group, so there is no user table.
Transient retry/queue state lives in RabbitMQ, not here.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Settings(Base):
    """Global singleton (id == 1).

    ``mode`` is the global tri-state: "off" / "always" / "schedule"
    (see :class:`telegram_alert.modes.AlertMode`).
    """

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    mode: Mapped[str] = mapped_column(String(16), default="schedule")
    # Temporary manual deviation in SCHEDULE mode: "none" / "mute" / "unmute"
    # (see :class:`telegram_alert.modes.ScheduleOverride`). Cleared on mode change.
    override: Mapped[str] = mapped_column(String(16), default="none", server_default="none")
    # Epoch seconds when the override expires (mute/unmute are always temporary).
    # NULL when there is no active override.
    override_until: Mapped[float | None] = mapped_column(Float, nullable=True)


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
    """One row per review: the Telegram photo message posted to the group.

    Enables idempotent sends (don't repost on retry) and lets us attach the clip
    button to the exact message later.
    """

    __tablename__ = "sent_messages"

    review_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger)
    clip_attached: Mapped[bool] = mapped_column(Boolean, default=False)
