"""Chat gate.  The bot is bound to a single group: updates from any other chat
(including private messages) are ignored.  Group membership is the only trust
boundary — there is no per-user authorization."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject


def _chat_id(event: TelegramObject) -> int | None:
    if isinstance(event, Message):
        return event.chat.id
    if isinstance(event, CallbackQuery):
        return event.message.chat.id if event.message else None
    return None


class ChatFilterMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        settings = data["settings"]
        if _chat_id(event) != settings.telegram.chat_id:
            return None  # not our group — silently ignore
        return await handler(event, data)
