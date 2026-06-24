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
        else:
            await self._attach_clip(job)

    async def _send_photo_alert(self, job: OutboxJob) -> None:
        assert job.snap_key
        data = await self._storage.get(job.snap_key)
        caption = alert_caption(job.camera, job.ts, self._s.app.tzinfo)

        async with self._sf() as session:
            user_ids = await repo.list_user_ids(session)

        for uid in user_ids:
            async with self._sf() as session:
                if await repo.was_sent_to(session, job.review_id, uid):
                    continue
            photo = BufferedInputFile(data, filename="snap.jpg")
            try:
                msg = await self._bot.send_photo(uid, photo, caption=caption)
            except _PERMANENT as e:
                log.warning("photo_alert to %s skipped permanently: %s", uid, e)
                continue
            # Any other error (network/proxy/5xx) propagates -> delayed retry.
            async with self._sf() as session:
                await repo.record_sent(session, job.review_id, uid, msg.message_id)

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
