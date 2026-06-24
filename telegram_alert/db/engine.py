"""Async engine / session factory and schema bootstrap."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from telegram_alert.db.models import Base, Settings


def make_engine(dsn: str) -> AsyncEngine:
    return create_async_engine(dsn, pool_pre_ping=True, future=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Create tables (idempotent) and ensure the settings singleton exists."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        existing = await session.get(Settings, 1)
        if existing is None:
            session.add(Settings(id=1, mode="schedule"))
            await session.commit()
