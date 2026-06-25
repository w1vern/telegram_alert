"""Per-user Telegram command menus.

Telegram lets us set the command list per chat at any time (not only at
startup), so each user sees only what they may actually run.  Three tiers,
refreshed whenever a user's rights change (/start, grant, revoke).

Kept separate from :mod:`telegram_alert.telegram.bot` so handlers can import the
runtime updater without a circular import (bot imports the handlers router).
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

log = logging.getLogger(__name__)


# Anyone who hasn't been granted access — only enough to ask for it.
PUBLIC_COMMANDS = [
    BotCommand(command="start", description="Начало работы / запросить доступ"),
    BotCommand(command="help", description="Справка"),
]

# Authorized users.
USER_COMMANDS = [
    BotCommand(command="start", description="Начало работы"),
    BotCommand(command="status", description="Текущее состояние"),
    BotCommand(command="mode", description="Режим алертов"),
    BotCommand(command="schedule", description="Окна присутствия"),
    BotCommand(command="clip", description="Запись со всех камер: /clip 30"),
    BotCommand(command="mute", description="Заглушить на N часов: /mute 2"),
    BotCommand(command="unmute", description="Включить на N часов: /unmute 2"),
    BotCommand(command="auto", description="Сбросить override, вернуть расписание"),
    BotCommand(command="help", description="Список команд"),
]

# Superusers: everything above plus access management.
ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand(command="users", description="Пользователи и доступ"),
    BotCommand(command="grant", description="Выдать доступ по ID"),
    BotCommand(command="revoke", description="Забрать доступ по ID"),
]


def _commands_for(*, authorized: bool, superuser: bool) -> list[BotCommand]:
    if superuser:
        return ADMIN_COMMANDS
    if authorized:
        return USER_COMMANDS
    return PUBLIC_COMMANDS


async def apply_user_commands(
    bot: Bot, uid: int, *, authorized: bool, superuser: bool
) -> None:
    """Refresh one user's menu to match their current rights.  Safe to call on
    every /start, grant and revoke."""
    cmds = _commands_for(authorized=authorized, superuser=superuser)
    try:
        await bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id=uid))
    except Exception:  # noqa: BLE001 - user may not have an open chat yet
        log.warning("could not set commands for %s", uid)


async def setup_commands(
    bot: Bot, superuser_ids: list[int], authorized_ids: list[int]
) -> None:
    """Startup: default menu is the public one; known authorized users and
    superusers get their fuller chat-scoped menus."""
    await bot.set_my_commands(PUBLIC_COMMANDS, scope=BotCommandScopeDefault())
    su = set(superuser_ids)
    for uid in set(authorized_ids) | su:
        await apply_user_commands(bot, uid, authorized=True, superuser=uid in su)
