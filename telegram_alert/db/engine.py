"""Async engine / session factory and app-data bootstrap.

The database *schema* is owned by Alembic migrations (run by the ``migrate``
compose service or ``make migration``), not by the app. This module only builds
the engine / session factory and seeds the settings singleton row.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from telegram_alert.db.models import Settings


def make_engine(dsn: str) -> AsyncEngine:
    return create_async_engine(dsn, pool_pre_ping=True, future=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Ensure the settings singleton exists (the schema comes from migrations)."""
    async with session_factory() as session:
        existing = await session.get(Settings, 1)
        if existing is None:
            session.add(Settings(id=1, mode="schedule"))
            await session.commit()
