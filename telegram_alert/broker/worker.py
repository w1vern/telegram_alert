"""Media stage: consume ``jobs``, archive Frigate media to MinIO, then enqueue
the proxy-dependent Telegram step onto ``outbox`` when appropriate.

Everything here is idempotent and proxy-independent: it only talks to Frigate
and MinIO, so it keeps working even while Telegram is unreachable.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_alert.broker.amqp import Broker
from telegram_alert.broker.jobs import MediaJob, OutboxJob
from telegram_alert.config import Settings
from telegram_alert.db import repo
from telegram_alert.modes import parse_mode, parse_override
from telegram_alert.services.frigate import ClipNotReady, FrigateClient
from telegram_alert.services.schedule import Window, should_notify
from telegram_alert.services.storage import MinioStorage

log = logging.getLogger(__name__)


class MediaWorker:
    def __init__(
        self,
        settings: Settings,
        broker: Broker,
        frigate: FrigateClient,
        storage: MinioStorage,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._s = settings
        self._broker = broker
        self._frigate = frigate
        self._storage = storage
        self._sf = session_factory

    async def start(self) -> None:
        await self._broker.consume("jobs", self._handle)

    async def _handle(self, payload: dict, attempt: int) -> None:
        job = MediaJob.model_validate(payload)
        if job.on_demand:
            await self._handle_clip(job)
        elif job.type == "new":
            await self._handle_new(job)
        else:
            await self._handle_end(job)

    # --- /clip: snapshot (fallback) + fixed-length recording, delivered now ---

    async def _handle_clip(self, job: MediaJob) -> None:
        async with self._sf() as session:
            await repo.ensure_processed(session, job.review_id, job.camera)

        # Snapshot is the fallback artifact; always archive the current frame.
        snap_key = MinioStorage.snap_key(job.review_id)
        if not await self._storage.exists(snap_key):
            data = await self._frigate.get_camera_snapshot(job.camera)
            await self._storage.put(snap_key, data, "image/jpeg")
        async with self._sf() as session:
            await repo.mark_snap_archived(session, job.review_id)

        # Fixed-length clip from continuous recordings, ending a couple seconds
        # in the past so the recording segment is already finalized.
        clip_key = MinioStorage.clip_key(job.review_id)
        clip_available = True
        if not await self._storage.exists(clip_key):
            end = time.time() - 2
            start = end - (job.clip_seconds or self._s.frigate.clip_seconds)
            try:
                data = await self._frigate.get_recording_clip(job.camera, start, end)
            except ClipNotReady:
                clip_available = False
            else:
                await self._storage.put(clip_key, data, "video/mp4")
                async with self._sf() as session:
                    await repo.mark_clip_archived(session, job.review_id)

        out = OutboxJob(
            action="clip",
            review_id=job.review_id,
            camera=job.camera,
            ts=job.ts,
            snap_key=snap_key,
            clip_key=clip_key if clip_available else None,
        )
        await self._broker.publish_outbox(out.model_dump_json().encode())

    # --- new: snapshot -> archive -> maybe enqueue photo alert ----------

    async def _handle_new(self, job: MediaJob) -> None:
        snap_key = MinioStorage.snap_key(job.review_id)
        async with self._sf() as session:
            await repo.ensure_processed(session, job.review_id, job.camera)

        if not await self._storage.exists(snap_key):
            data = await self._frigate.get_snapshot(job.event_id)
            await self._storage.put(snap_key, data, "image/jpeg")
        async with self._sf() as session:
            await repo.mark_snap_archived(session, job.review_id)

        if not await self._should_notify_now():
            log.info("review %s suppressed by schedule/settings; archived only", job.review_id)
            return

        out = OutboxJob(
            action="photo_alert",
            review_id=job.review_id,
            camera=job.camera,
            ts=job.ts,
            snap_key=snap_key,
        )
        await self._broker.publish_outbox(out.model_dump_json().encode())

    # --- end: clip -> archive -> attach button --------------------------

    async def _handle_end(self, job: MediaJob) -> None:
        # Only bother with the clip if a photo alert actually went out.
        async with self._sf() as session:
            sent = await repo.get_sent(session, job.review_id)
        if not sent:
            log.info("review %s ended but no alert was sent; skipping clip", job.review_id)
            return

        clip_key = MinioStorage.clip_key(job.review_id)
        clip_available = True
        if not await self._storage.exists(clip_key):
            try:
                data = await self._frigate.get_clip(job.event_id)
            except ClipNotReady:
                log.warning("clip for review %s never became ready", job.review_id)
                clip_available = False
            else:
                await self._storage.put(clip_key, data, "video/mp4")
                async with self._sf() as session:
                    await repo.mark_clip_archived(session, job.review_id)

        out = OutboxJob(
            action="attach_clip",
            review_id=job.review_id,
            camera=job.camera,
            ts=job.ts,
            clip_key=clip_key if clip_available else None,
        )
        await self._broker.publish_outbox(out.model_dump_json().encode())

    # --- suppression ----------------------------------------------------

    async def _should_notify_now(self) -> bool:
        now = datetime.now(tz=self._s.app.tzinfo)
        async with self._sf() as session:
            row = await repo.get_settings_row(session)
            entries = await repo.list_schedule(session)
        windows = [Window(e.weekday, e.start_min, e.end_min) for e in entries]
        return should_notify(
            now, parse_mode(row.mode), windows, parse_override(row.override)
        )
