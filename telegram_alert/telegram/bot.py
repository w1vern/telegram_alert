"""Bot/Dispatcher construction.

All Telegram traffic goes through the configured SOCKS/HTTP proxy; without it
Telegram is unreachable.  When the proxy is down, sends raise a network error
which the outbox consumer turns into a delayed retry — so notifications wait in
RabbitMQ and flush once the proxy is back.
"""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from telegram_alert.config import TelegramSettings
from telegram_alert.telegram.handlers import router
from telegram_alert.telegram.middleware import AuthMiddleware


def build_bot(cfg: TelegramSettings) -> Bot:
    session = AiohttpSession(proxy=cfg.proxy_url) if cfg.proxy_url else AiohttpSession()
    return Bot(
        token=cfg.token.get_secret_value(),
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    dp.include_router(router)
    return dp
