"""MQTT ingress: subscribe to frigate/reviews and hand alerts to the durable
``jobs`` queue as fast as possible.

We do NO slow work here (no media download): the moment a relevant review
arrives we publish a job to RabbitMQ (persistent, confirmed) and move on, so a
stuck download or dead proxy can never block ingestion.  A stable client id +
non-clean session means a short broker blip won't drop events.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl

import aiomqtt

from telegram_alert.broker.amqp import Broker
from telegram_alert.broker.jobs import MediaJob
from telegram_alert.config import MqttSettings

log = logging.getLogger(__name__)


class MqttIngress:
    def __init__(self, cfg: MqttSettings, broker: Broker) -> None:
        self._cfg = cfg
        self._broker = broker

    async def run(self) -> None:
        backoff = 1.0
        while True:
            try:
                await self._run_once()
                backoff = 1.0
            except aiomqtt.MqttError as e:
                log.warning("MQTT connection error: %s; reconnecting in %.0fs", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Unexpected MQTT error; reconnecting in %.0fs", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def _run_once(self) -> None:
        tls_context = ssl.create_default_context() if self._cfg.tls else None
        async with aiomqtt.Client(
            hostname=self._cfg.host,
            port=self._cfg.port,
            username=self._cfg.user,
            password=(self._cfg.password.get_secret_value() if self._cfg.password else None),
            identifier=self._cfg.client_id,
            clean_session=False,  # persistent session: survive short blips
            tls_context=tls_context,
            keepalive=30,
        ) as client:
            await client.subscribe(self._cfg.topic, qos=1)
            log.info("Subscribed to %s", self._cfg.topic)
            async for message in client.messages:
                await self._on_message(bytes(message.payload))

    async def _on_message(self, raw: bytes) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("Non-JSON MQTT payload, ignoring")
            return

        msg_type = data.get("type")
        after = data.get("after") or {}
        if after.get("severity") != "alert":
            return
        if msg_type not in ("new", "end"):
            return  # "update" between new/end is ignored

        review_id = after.get("id")
        camera = after.get("camera") or "camera"
        detections = (after.get("data") or {}).get("detections") or []
        if not review_id or not detections:
            log.warning("Alert review missing id/detections, ignoring: %s", review_id)
            return

        job = MediaJob(
            type=msg_type,
            review_id=review_id,
            camera=camera,
            event_id=detections[0],
            ts=float(after.get("start_time") or 0.0),
        )
        await self._broker.publish_job(job.model_dump_json().encode())
        log.info("Queued %s job for review %s (%s)", msg_type, review_id, camera)
