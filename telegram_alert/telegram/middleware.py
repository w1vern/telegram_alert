"""Authorization gate.  Unauthorized users see only an auth prompt."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_alert.db import repo


class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        sf: async_sessionmaker[AsyncSession] = data["session_factory"]
        settings = data["settings"]

        # Public commands available to anyone (needed to begin using the bot).
        if isinstance(event, Message):
            text = event.text or ""
            if text.startswith(("/start", "/help")):
                return await handler(event, data)

        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        if user.id in settings.telegram.superuser_ids:
            return await handler(event, data)

        async with sf() as session:
            authorized = await repo.is_authorized(session, user.id)

        if not authorized:
            msg = (
                "⛔ Нет доступа к этой команде.\n"
                "Отправь /start, чтобы запросить доступ у администратора."
            )
            if isinstance(event, Message):
                await event.answer(msg)
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Нет доступа.", show_alert=True)
            return None

        return await handler(event, data)
