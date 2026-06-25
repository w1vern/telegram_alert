"""Bot command menu.

The bot lives in a single group and anyone there may run any command, so there
is just one flat command list, scoped to that chat.

Kept separate from :mod:`telegram_alert.telegram.bot` so handlers can import the
updater without a circular import (bot imports the handlers router).
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat

log = logging.getLogger(__name__)


COMMANDS = [
    BotCommand(command="status", description="Текущее состояние"),
    BotCommand(command="mode", description="Режим алертов"),
    BotCommand(command="schedule", description="Окна присутствия"),
    BotCommand(command="clip", description="Запись со всех камер: /clip 30"),
    BotCommand(command="mute", description="Заглушить на N часов: /mute 2"),
    BotCommand(command="unmute", description="Включить на N часов: /unmute 2"),
    BotCommand(command="auto", description="Сбросить override, вернуть расписание"),
    BotCommand(command="help", description="Список команд"),
]


async def setup_commands(bot: Bot, chat_id: int) -> None:
    """Publish the command menu to the bound group chat."""
    try:
        await bot.set_my_commands(COMMANDS, scope=BotCommandScopeChat(chat_id=chat_id))
    except Exception:  # noqa: BLE001 - bot may not be in the chat yet
        log.warning("could not set commands for chat %s", chat_id)
