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
    User,
)


# --- users ---------------------------------------------------------------

async def upsert_user(session: AsyncSession, tg_id: int, username: str | None) -> None:
    """Register a user from /start without changing their authorized flag."""
    stmt = (
        pg_insert(User)
        .values(tg_id=tg_id, username=username, authorized=False)
        .on_conflict_do_update(index_elements=[User.tg_id], set_={"username": username})
    )
    await session.execute(stmt)
    await session.commit()


async def set_authorized(
    session: AsyncSession, tg_id: int, value: bool, username: str | None = None
) -> None:
    """Grant/revoke access; creates the row if the user never pressed /start."""
    stmt = (
        pg_insert(User)
        .values(tg_id=tg_id, username=username, authorized=value)
        .on_conflict_do_update(index_elements=[User.tg_id], set_={"authorized": value})
    )
    await session.execute(stmt)
    await session.commit()


async def is_authorized(session: AsyncSession, tg_id: int) -> bool:
    row = await session.get(User, tg_id)
    return bool(row and row.authorized)


async def get_user(session: AsyncSession, tg_id: int) -> User | None:
    return await session.get(User, tg_id)


async def list_users(session: AsyncSession) -> list[User]:
    rows = await session.execute(select(User).order_by(User.created_at))
    return list(rows.scalars().all())


async def list_authorized_ids(session: AsyncSession) -> list[int]:
    rows = await session.execute(select(User.tg_id).where(User.authorized.is_(True)))
    return [r[0] for r in rows.all()]


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


# --- sent messages -------------------------------------------------------

async def get_sent(session: AsyncSession, review_id: str) -> list[SentMessage]:
    rows = await session.execute(
        select(SentMessage).where(SentMessage.review_id == review_id)
    )
    return list(rows.scalars().all())


async def was_sent_to(session: AsyncSession, review_id: str, tg_id: int) -> bool:
    rows = await session.execute(
        select(SentMessage.id).where(
            SentMessage.review_id == review_id, SentMessage.tg_id == tg_id
        )
    )
    return rows.first() is not None


async def record_sent(
    session: AsyncSession,
    review_id: str,
    tg_id: int,
    message_id: int,
    clip_attached: bool = False,
) -> None:
    session.add(
        SentMessage(
            review_id=review_id,
            tg_id=tg_id,
            message_id=message_id,
            clip_attached=clip_attached,
        )
    )
    await session.commit()


async def mark_clip_attached(session: AsyncSession, sent_id: int) -> None:
    await session.execute(
        update(SentMessage).where(SentMessage.id == sent_id).values(clip_attached=True)
    )
    await session.commit()
