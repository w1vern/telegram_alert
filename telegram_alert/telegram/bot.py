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
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    TelegramObject,
    Update,
)

from telegram_alert.config import TelegramSettings
from telegram_alert.telegram.handlers import router
from telegram_alert.telegram.middleware import AuthMiddleware

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
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    dp.include_router(router)
    return dp


# Shown in the Telegram "menu" button (default scope, everyone).
BASE_COMMANDS = [
    BotCommand(command="start", description="Начало работы"),
    BotCommand(command="status", description="Текущее состояние"),
    BotCommand(command="mode", description="Режим алертов"),
    BotCommand(command="schedule", description="Окна присутствия"),
    BotCommand(command="clip", description="Запись последних действий со всех камер"),
    BotCommand(command="help", description="Список команд"),
]

# Extra commands shown only to superusers.
ADMIN_COMMANDS = BASE_COMMANDS + [
    BotCommand(command="users", description="Пользователи и доступ"),
    BotCommand(command="grant", description="Выдать доступ по ID"),
    BotCommand(command="revoke", description="Забрать доступ по ID"),
]


async def setup_commands(bot: Bot, superuser_ids: list[int]) -> None:
    await bot.set_my_commands(BASE_COMMANDS, scope=BotCommandScopeDefault())
    for uid in superuser_ids:
        try:
            await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=uid))
        except Exception:  # noqa: BLE001 - superuser may not have started the bot yet
            log.warning("could not set admin commands for %s", uid)
