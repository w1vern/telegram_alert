"""RabbitMQ (AMQP) topology, publishing and a retry-aware consume helper.

Durability model
----------------
Two durable work queues on the same RabbitMQ that hosts the MQTT plugin:

  * ``jobs``   — media stage (Frigate -> MinIO), idempotent, safe to retry.
  * ``outbox`` — the *only* proxy-dependent stage (send to Telegram).

Delayed retry is done the canonical RabbitMQ way: on failure the message is
republished to a per-stage, per-step retry queue that has a TTL and dead-letters
back to the main queue when the TTL expires.  Nothing is acked until it is
either done or handed to a retry queue, so a crash never loses work — and the
media is already in MinIO regardless.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import aio_pika
from aio_pika import DeliveryMode, ExchangeType
from aio_pika.abc import (
    AbstractIncomingMessage,
    AbstractRobustConnection,
    AbstractRobustExchange,
)

from telegram_alert.config import AmqpSettings

log = logging.getLogger(__name__)

# Routing keys (also the dead-letter targets for the retry queues).
RK_JOBS = "jobs"
RK_OUTBOX = "outbox"

# Stop retrying after this many attempts to avoid an unkillable poison message.
# With the default backoff the last steps are ~10 min, so this is many days.
MAX_ATTEMPTS = 2000

Handler = Callable[[dict, int], Awaitable[None]]


class Broker:
    def __init__(self, cfg: AmqpSettings) -> None:
        self._cfg = cfg
        self._conn: AbstractRobustConnection | None = None
        self._pub_channel = None
        self._exchange: AbstractRobustExchange | None = None
        # stage -> main queue name
        self._main = {RK_JOBS: cfg.jobs_queue, RK_OUTBOX: cfg.outbox_queue}

    async def connect(self) -> None:
        backoff = 2.0
        while True:
            try:
                self._conn = await aio_pika.connect_robust(
                    host=self._cfg.host,
                    port=self._cfg.port,
                    login=self._cfg.user,
                    password=self._cfg.password.get_secret_value(),
                    virtualhost=self._cfg.vhost,
                    ssl=self._cfg.tls,
                    client_properties={"connection_name": "dacha-alert-bot"},
                )
                break
            except Exception as e:  # noqa: BLE001 - retry transient broker outages
                log.warning(
                    "AMQP connect failed (%s); retrying in %.0fs", e, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
        self._pub_channel = await self._conn.channel(publisher_confirms=True)
        self._exchange = await self._pub_channel.declare_exchange(
            self._cfg.exchange, ExchangeType.DIRECT, durable=True
        )
        await self._declare_stage(RK_JOBS, self._cfg.jobs_queue)
        await self._declare_stage(RK_OUTBOX, self._cfg.outbox_queue)
        log.info("AMQP connected, topology declared")

    async def _declare_stage(self, rk: str, queue_name: str) -> None:
        assert self._pub_channel and self._exchange
        main_q = await self._pub_channel.declare_queue(queue_name, durable=True)
        await main_q.bind(self._exchange, routing_key=rk)
        for i, delay in enumerate(self._cfg.retry_delays):
            retry_q = await self._pub_channel.declare_queue(
                f"{queue_name}.retry.{i}",
                durable=True,
                arguments={
                    "x-message-ttl": delay * 1000,
                    "x-dead-letter-exchange": self._cfg.exchange,
                    "x-dead-letter-routing-key": rk,  # back to the main queue
                },
            )
            await retry_q.bind(self._exchange, routing_key=f"{rk}.retry.{i}")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()

    # --- publishing ------------------------------------------------------

    async def _publish(self, routing_key: str, body: bytes, attempt: int = 0) -> None:
        assert self._exchange is not None
        msg = aio_pika.Message(
            body,
            content_type="application/json",
            delivery_mode=DeliveryMode.PERSISTENT,
            headers={"x-attempt": attempt},
        )
        await self._exchange.publish(msg, routing_key=routing_key)

    async def publish_job(self, body: bytes) -> None:
        await self._publish(RK_JOBS, body)

    async def publish_outbox(self, body: bytes) -> None:
        await self._publish(RK_OUTBOX, body)

    async def _publish_retry(self, stage: str, body: bytes, attempt: int) -> None:
        assert self._exchange is not None
        step = min(attempt, len(self._cfg.retry_delays) - 1)
        msg = aio_pika.Message(
            body,
            content_type="application/json",
            delivery_mode=DeliveryMode.PERSISTENT,
            headers={"x-attempt": attempt},
        )
        await self._exchange.publish(msg, routing_key=f"{stage}.retry.{step}")

    # --- consuming -------------------------------------------------------

    async def consume(self, stage: str, handler: Handler) -> None:
        """Start consuming a stage.  ``handler(payload, attempt)`` must raise to
        trigger a delayed retry, or return to ack.  Never returns."""
        assert self._conn is not None
        queue_name = self._main[stage]
        channel = await self._conn.channel()
        await channel.set_qos(prefetch_count=self._cfg.prefetch)
        queue = await channel.declare_queue(queue_name, durable=True)

        async def on_message(message: AbstractIncomingMessage) -> None:
            attempt = int(message.headers.get("x-attempt", 0)) if message.headers else 0
            try:
                payload = json.loads(message.body)
                await handler(payload, attempt)
            except Exception:  # noqa: BLE001 - one bad message must not kill the loop
                next_attempt = attempt + 1
                if next_attempt >= MAX_ATTEMPTS:
                    log.exception(
                        "%s message exceeded MAX_ATTEMPTS, dropping: %s",
                        stage,
                        message.body[:500],
                    )
                    await message.ack()
                    return
                log.warning(
                    "%s handler failed (attempt %d), scheduling retry",
                    stage,
                    attempt,
                    exc_info=True,
                )
                await self._publish_retry(stage, message.body, next_attempt)
                await message.ack()  # original handed off to the retry queue
            else:
                await message.ack()

        await queue.consume(on_message)
        log.info("Consuming stage %r (queue %s)", stage, queue_name)
