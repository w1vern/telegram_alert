"""Bot/Dispatcher construction.

All Telegram traffic goes through the configured SOCKS/HTTP proxy; without it
Telegram is unreachable.  When the proxy is down, sends raise a network error
which the outbox consumer turns into a delayed retry — so notifications wait in
RabbitMQ and flush once the proxy is back.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import TelegramObject, Update

from telegram_alert.config import TelegramSettings
from telegram_alert.telegram.handlers import router
from telegram_alert.telegram.middleware import ChatFilterMiddleware

log = logging.getLogger(__name__)


class UpdateLogMiddleware(BaseMiddleware):
    """Logs every incoming update so we can see whether polling delivers them."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update):
            kind = event.event_type
            user = event.event.from_user if getattr(event.event, "from_user", None) else None
            text = getattr(event.event, "text", None)
            log.info(
                "update #%s %s from %s: %r",
                event.update_id,
                kind,
                (user.id if user else "?"),
                text,
            )
        return await handler(event, data)


def build_bot(cfg: TelegramSettings) -> Bot:
    session = AiohttpSession(proxy=cfg.proxy_url) if cfg.proxy_url else AiohttpSession()
    return Bot(
        token=cfg.token.get_secret_value(),
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(UpdateLogMiddleware())
    dp.message.middleware(ChatFilterMiddleware())
    dp.callback_query.middleware(ChatFilterMiddleware())
    dp.include_router(router)
    return dp
