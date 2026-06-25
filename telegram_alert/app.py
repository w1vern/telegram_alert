"""Compose all components and run them concurrently."""

from __future__ import annotations

import asyncio
import logging

from telegram_alert.broker.amqp import Broker
from telegram_alert.broker.worker import MediaWorker
from telegram_alert.config import get_settings
from telegram_alert.db import repo
from telegram_alert.db.engine import init_db, make_engine, make_sessionmaker
from telegram_alert.logging_conf import setup_logging
from telegram_alert.mqtt.consumer import MqttIngress
from telegram_alert.services.frigate import FrigateClient
from telegram_alert.services.storage import MinioStorage
from telegram_alert.telegram.bot import build_bot, build_dispatcher
from telegram_alert.telegram.commands import setup_commands
from telegram_alert.telegram.sender import OutboxSender

log = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.app.log_level)
    log.info("Starting dacha alert manager")

    engine = make_engine(settings.db.dsn)
    session_factory = make_sessionmaker(engine)
    await init_db(engine, session_factory)

    storage = MinioStorage(settings.minio)
    await storage.ensure_bucket()
    frigate = FrigateClient(settings.frigate)

    broker = Broker(settings.amqp)
    await broker.connect()

    bot = build_bot(settings.telegram)
    dp = build_dispatcher()

    media = MediaWorker(settings, broker, frigate, storage, session_factory)
    sender = OutboxSender(settings, broker, bot, storage, session_factory)
    # These register consumers and return; messages are processed in background.
    await media.start()
    await sender.start()

    mqtt = MqttIngress(settings.mqtt, broker)

    # Remove any leftover webhook (it would make getUpdates return 409). Keep
    # pending updates so commands/alerts queued during downtime aren't lost.
    me = await bot.get_me()
    log.info("Telegram bot @%s id=%s; ensuring polling mode", me.username, me.id)
    await bot.delete_webhook(drop_pending_updates=False)
    async with session_factory() as session:
        authorized_ids = await repo.list_authorized_ids(session)
    await setup_commands(bot, settings.telegram.superuser_ids, authorized_ids)

    try:
        await asyncio.gather(
            dp.start_polling(
                bot,
                session_factory=session_factory,
                settings=settings,
                broker=broker,
                frigate=frigate,
            ),
            mqtt.run(),
        )
    finally:
        log.info("Shutting down")
        await broker.close()
        await frigate.close()
        await bot.session.close()
        await engine.dispose()


def run() -> None:
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Stopped")
