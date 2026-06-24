"""Outbox stage: the only proxy-dependent consumer.

Pulls ``outbox`` jobs and performs the actual Telegram I/O.  Media is already in
MinIO, so this stage just fetches bytes / presigns links and sends.  Network /
proxy failures are re-raised so the broker reschedules a delayed retry; the job
keeps waiting until the proxy is alive again.  Permanent per-user errors (user
blocked the bot, message gone) are skipped so they don't poison the job.

Fan-out is idempotent via the ``sent_messages`` table: a retried job won't
re-send to users who already got the photo.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import BufferedInputFile
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_alert.broker.amqp import Broker
from telegram_alert.broker.jobs import OutboxJob
from telegram_alert.config import Settings
from telegram_alert.db import repo
from telegram_alert.services.storage import MinioStorage
from telegram_alert.telegram.notify import (
    alert_caption,
    clip_caption,
    clip_keyboard,
    clip_unavailable_keyboard,
)

log = logging.getLogger(__name__)

# Errors that mean "this recipient/message is permanently unusable" — skip,
# don't retry the whole job.
_PERMANENT = (TelegramForbiddenError, TelegramBadRequest)


class OutboxSender:
    def __init__(
        self,
        settings: Settings,
        broker: Broker,
        bot: Bot,
        storage: MinioStorage,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._s = settings
        self._broker = broker
        self._bot = bot
        self._storage = storage
        self._sf = session_factory

    async def start(self) -> None:
        await self._broker.consume("outbox", self._handle)

    async def _handle(self, payload: dict, attempt: int) -> None:
        job = OutboxJob.model_validate(payload)
        if job.action == "photo_alert":
            await self._send_photo_alert(job)
        elif job.action == "clip":
            await self._send_clip(job)
        else:
            await self._attach_clip(job)

    async def _send_clip(self, job: OutboxJob) -> None:
        """On-demand /clip: upload the clip bytes as a playable video (Telegram's
        fetch-by-URL is unreliable). Fall back to the snapshot photo if there's
        no clip or the video upload is rejected."""
        caption = clip_caption(job.camera, job.ts, self._s.app.tzinfo)
        clip_bytes = await self._storage.get(job.clip_key) if job.clip_key else None
        snap = await self._storage.get(job.snap_key) if job.snap_key else None

        for uid in await self._recipients():
            async with self._sf() as session:
                if await repo.was_sent_to(session, job.review_id, uid):
                    continue
            msg = None
            if clip_bytes is not None:
                video = BufferedInputFile(clip_bytes, filename="clip.mp4")
                try:
                    msg = await self._bot.send_video(
                        uid, video, caption=caption, supports_streaming=True
                    )
                except TelegramForbiddenError:
                    continue  # user blocked the bot
                except TelegramBadRequest as e:
                    log.warning("send_video failed for %s, falling back to photo: %s", uid, e)
                    msg = None
            if msg is None and snap is not None:
                photo = BufferedInputFile(snap, filename="snap.jpg")
                try:
                    msg = await self._bot.send_photo(uid, photo, caption=caption)
                except _PERMANENT as e:
                    log.warning("clip photo fallback to %s skipped: %s", uid, e)
                    continue
            # Network/proxy errors propagate -> the whole job is retried later.
            if msg is not None:
                async with self._sf() as session:
                    await repo.record_sent(
                        session, job.review_id, uid, msg.message_id, clip_attached=True
                    )

    async def _recipients(self) -> list[int]:
        async with self._sf() as session:
            ids = await repo.list_authorized_ids(session)
        # Superusers always receive alerts, even if they never pressed /start.
        return list(dict.fromkeys([*ids, *self._s.telegram.superuser_ids]))

    async def _send_photo_alert(self, job: OutboxJob) -> None:
        assert job.snap_key
        data = await self._storage.get(job.snap_key)
        caption = alert_caption(job.camera, job.ts, self._s.app.tzinfo)

        # If the clip is already archived (e.g. /test), attach the button now.
        kb = None
        if job.clip_key:
            url = await self._storage.presigned_get(job.clip_key)
            kb = clip_keyboard(url)

        for uid in await self._recipients():
            async with self._sf() as session:
                if await repo.was_sent_to(session, job.review_id, uid):
                    continue
            photo = BufferedInputFile(data, filename="snap.jpg")
            try:
                msg = await self._bot.send_photo(uid, photo, caption=caption, reply_markup=kb)
            except _PERMANENT as e:
                log.warning("photo_alert to %s skipped permanently: %s", uid, e)
                continue
            # Any other error (network/proxy/5xx) propagates -> delayed retry.
            async with self._sf() as session:
                await repo.record_sent(
                    session, job.review_id, uid, msg.message_id, clip_attached=bool(job.clip_key)
                )

    async def _attach_clip(self, job: OutboxJob) -> None:
        if job.clip_key:
            url = await self._storage.presigned_get(job.clip_key)
            kb = clip_keyboard(url)
        else:
            kb = clip_unavailable_keyboard()

        async with self._sf() as session:
            sent = await repo.get_sent(session, job.review_id)

        for s in sent:
            if s.clip_attached:
                continue
            try:
                await self._bot.edit_message_reply_markup(
                    chat_id=s.tg_id, message_id=s.message_id, reply_markup=kb
                )
            except _PERMANENT as e:
                log.warning("attach_clip to %s/%s skipped: %s", s.tg_id, s.message_id, e)
            # network errors propagate -> retry the job
            async with self._sf() as session:
                await repo.mark_clip_attached(session, s.id)
