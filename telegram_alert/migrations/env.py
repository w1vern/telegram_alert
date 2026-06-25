"""Alembic migration environment (async).

The engine and target metadata are taken from the application itself so the
schema and connection settings have a single source of truth:

* connection URL  -> ``telegram_alert.config.get_settings().db.dsn``
* target metadata -> ``telegram_alert.db.models.Base.metadata``

The app uses ``postgresql+asyncpg`` (an async DBAPI), so migrations run inside
an asyncio event loop, per the Alembic async cookbook.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from telegram_alert.config import get_settings
from telegram_alert.db.models import Base

config = context.config

# Inject the runtime DSN so we never duplicate credentials in alembic.ini.
config.set_main_option("sqlalchemy.url", get_settings().db.dsn)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """Run migrations without a DBAPI connection (emit SQL to a script)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    # When invoked programmatically from the app (engine.run_migrations) a live
    # connection is shared via config.attributes so migrations reuse the app's
    # engine instead of opening a second one inside a nested event loop.
    connectable = config.attributes.get("connection", None)
    if connectable is None:
        asyncio.run(run_async_migrations())
    else:
        do_run_migrations(connectable)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
