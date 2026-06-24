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

        # /start (with or without secret) is always allowed through.
        if isinstance(event, Message) and (event.text or "").startswith("/start"):
            return await handler(event, data)

        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        async with sf() as session:
            authorized = await repo.is_authorized(session, user.id)

        if not authorized:
            if isinstance(event, Message):
                await event.answer("⛔ Не авторизован. Используй /start <секрет>.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Не авторизован.", show_alert=True)
            return None

        return await handler(event, data)
