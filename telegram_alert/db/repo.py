"""Data-access helpers.  Each function takes a session and is transaction-light."""

from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_alert.db.models import (
    Processed,
    ScheduleEntry,
    SentMessage,
    Settings,
)


# --- settings ------------------------------------------------------------

async def get_settings_row(session: AsyncSession) -> Settings:
    row = await session.get(Settings, 1)
    if row is None:  # defensive; init_db should have created it
        row = Settings(id=1, mode="schedule")
        session.add(row)
        await session.commit()
    return row


async def set_mode(session: AsyncSession, mode: str) -> None:
    # Switching mode always clears any temporary schedule override.
    await session.execute(
        update(Settings)
        .where(Settings.id == 1)
        .values(mode=mode, override="none", override_until=None)
    )
    await session.commit()


async def set_override(
    session: AsyncSession, override: str, until: float | None
) -> None:
    await session.execute(
        update(Settings)
        .where(Settings.id == 1)
        .values(override=override, override_until=until)
    )
    await session.commit()


# --- schedule ------------------------------------------------------------

async def list_schedule(session: AsyncSession, weekday: int | None = None) -> list[ScheduleEntry]:
    stmt = select(ScheduleEntry)
    if weekday is not None:
        stmt = stmt.where(ScheduleEntry.weekday == weekday)
    stmt = stmt.order_by(ScheduleEntry.weekday, ScheduleEntry.start_min)
    rows = await session.execute(stmt)
    return list(rows.scalars().all())


async def add_schedule(session: AsyncSession, weekday: int, start_min: int, end_min: int) -> None:
    session.add(ScheduleEntry(weekday=weekday, start_min=start_min, end_min=end_min))
    await session.commit()


async def delete_schedule(session: AsyncSession, entry_id: int) -> None:
    await session.execute(delete(ScheduleEntry).where(ScheduleEntry.id == entry_id))
    await session.commit()


async def clear_weekday(session: AsyncSession, weekday: int) -> None:
    await session.execute(delete(ScheduleEntry).where(ScheduleEntry.weekday == weekday))
    await session.commit()


async def copy_weekday(session: AsyncSession, src_weekday: int, dst_weekdays: list[int]) -> None:
    src = await list_schedule(session, src_weekday)
    for wd in dst_weekdays:
        await session.execute(delete(ScheduleEntry).where(ScheduleEntry.weekday == wd))
        for e in src:
            session.add(ScheduleEntry(weekday=wd, start_min=e.start_min, end_min=e.end_min))
    await session.commit()


# --- processed (media idempotency) --------------------------------------

async def get_processed(session: AsyncSession, review_id: str) -> Processed | None:
    return await session.get(Processed, review_id)

async def ensure_processed(session: AsyncSession, review_id: str, camera: str | None) -> Processed:
    stmt = (
        pg_insert(Processed)
        .values(review_id=review_id, camera=camera)
        .on_conflict_do_nothing(index_elements=[Processed.review_id])
    )
    await session.execute(stmt)
    await session.commit()
    row = await session.get(Processed, review_id)
    assert row is not None
    return row


async def mark_snap_archived(session: AsyncSession, review_id: str) -> None:
    await session.execute(
        update(Processed).where(Processed.review_id == review_id).values(snap_archived=True)
    )
    await session.commit()


async def mark_clip_archived(session: AsyncSession, review_id: str) -> None:
    await session.execute(
        update(Processed).where(Processed.review_id == review_id).values(clip_archived=True)
    )
    await session.commit()


# --- sent message (one per review, posted to the group) ------------------

async def get_sent(session: AsyncSession, review_id: str) -> SentMessage | None:
    return await session.get(SentMessage, review_id)


async def record_sent(
    session: AsyncSession,
    review_id: str,
    message_id: int,
    clip_attached: bool = False,
) -> None:
    stmt = (
        pg_insert(SentMessage)
        .values(
            review_id=review_id,
            message_id=message_id,
            clip_attached=clip_attached,
        )
        .on_conflict_do_nothing(index_elements=[SentMessage.review_id])
    )
    await session.execute(stmt)
    await session.commit()


async def mark_clip_attached(session: AsyncSession, review_id: str) -> None:
    await session.execute(
        update(SentMessage)
        .where(SentMessage.review_id == review_id)
        .values(clip_attached=True)
    )
    await session.commit()
