"""Outbox stage: the only proxy-dependent consumer.

Pulls ``outbox`` jobs and performs the actual Telegram I/O.  Media is already in
MinIO, so this stage just fetches bytes / presigns links and posts a single
message to the bound group.  Network / proxy failures are re-raised so the
broker reschedules a delayed retry; the job keeps waiting until the proxy is
alive again.  Permanent errors (chat gone, bot removed) are skipped so they
don't poison the job.

Sends are idempotent via the ``sent_messages`` table: a retried job won't repost
a review that was already delivered.
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
    record_caption,
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
        elif job.action == "record":
            await self._send_record(job)
        else:
            # photo_alert (event) and clip (/clip) are identical: a photo with a
            # caption and, when the clip is archived, a button linking to the
            # MinIO video. Only the caption text differs.
            await self._send_photo(job)

    async def _send_photo(self, job: OutboxJob) -> None:
        assert job.snap_key
        # Idempotency: a retried job must not repost a review already delivered.
        async with self._sf() as session:
            if await repo.get_sent(session, job.review_id) is not None:
                return

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

        chat_id = self._s.telegram.chat_id
        photo = BufferedInputFile(data, filename="snap.jpg")
        try:
            msg = await self._bot.send_photo(chat_id, photo, caption=caption, reply_markup=kb)
        except _PERMANENT as e:
            log.warning("photo for review %s skipped permanently: %s", job.review_id, e)
            return
        # Any other error (network/proxy/5xx) propagates -> delayed retry.
        async with self._sf() as session:
            await repo.record_sent(
                session, job.review_id, msg.message_id, clip_attached=bool(job.clip_key)
            )

    async def _send_record(self, job: OutboxJob) -> None:
        """/record delivery: a text message with a button linking to the MinIO
        clip (or 'unavailable' when Frigate never produced it). No photo — the
        current frame would not match the requested past timecode. Idempotent
        via ``sent_messages`` like the other actions."""
        async with self._sf() as session:
            if await repo.get_sent(session, job.review_id) is not None:
                return

        caption = record_caption(job.camera, job.ts, job.clip_seconds or 0, self._s.app.tzinfo)
        if job.clip_key:
            url = await self._storage.presigned_get(job.clip_key)
            kb = clip_keyboard(url)
        else:
            kb = clip_unavailable_keyboard()

        try:
            msg = await self._bot.send_message(
                self._s.telegram.chat_id, caption, reply_markup=kb
            )
        except _PERMANENT as e:
            log.warning("record for review %s skipped permanently: %s", job.review_id, e)
            return
        # network/proxy/5xx propagates -> delayed retry.
        async with self._sf() as session:
            await repo.record_sent(
                session, job.review_id, msg.message_id, clip_attached=bool(job.clip_key)
            )

    async def _attach_clip(self, job: OutboxJob) -> None:
        async with self._sf() as session:
            sent = await repo.get_sent(session, job.review_id)
        if sent is None or sent.clip_attached:
            return

        if job.clip_key:
            url = await self._storage.presigned_get(job.clip_key)
            kb = clip_keyboard(url)
        else:
            kb = clip_unavailable_keyboard()

        try:
            await self._bot.edit_message_reply_markup(
                chat_id=self._s.telegram.chat_id,
                message_id=sent.message_id,
                reply_markup=kb,
            )
        except _PERMANENT as e:
            log.warning("attach_clip for review %s skipped: %s", job.review_id, e)
        # network errors propagate -> retry the job
        async with self._sf() as session:
            await repo.mark_clip_attached(session, job.review_id)
