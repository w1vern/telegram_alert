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
        if job.action == "attach_clip":
            await self._attach_clip(job)
        else:
            # photo_alert (event) and clip (/clip) are identical: a photo with a
            # caption and, when the clip is archived, a button linking to the
            # MinIO video. Only the caption text differs.
            await self._send_photo(job)

    async def _recipients(self) -> list[int]:
        async with self._sf() as session:
            ids = await repo.list_authorized_ids(session)
        # Superusers always receive alerts, even if they never pressed /start.
        return list(dict.fromkeys([*ids, *self._s.telegram.superuser_ids]))

    async def _send_photo(self, job: OutboxJob) -> None:
        assert job.snap_key
        data = await self._storage.get(job.snap_key)
        tz = self._s.app.tzinfo
        if job.action == "clip":
            caption = clip_caption(job.camera, job.ts, tz)
        else:
            caption = alert_caption(job.camera, job.ts, tz)

        # If the clip is already archived (always for /clip; for events once the
        # clip is ready), attach the video button right away.
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
                log.warning("photo to %s skipped permanently: %s", uid, e)
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
