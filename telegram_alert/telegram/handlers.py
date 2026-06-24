"""Command and callback handlers.

Dependencies (``session_factory``, ``settings``) are injected by aiogram from
the values passed to ``dp.start_polling(...)``.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_alert.config import Settings
from telegram_alert.db import repo
from telegram_alert.services.schedule import (
    SuppressionState,
    Window,
    fmt_minutes,
    parse_interval,
    should_notify,
    windows_for_day,
)
from telegram_alert.telegram.callbacks import Cb
from telegram_alert.telegram.keyboards import (
    WEEKDAYS,
    day_keyboard,
    notifications_keyboard,
    week_keyboard,
)

log = logging.getLogger(__name__)
router = Router()


class SchedFSM(StatesGroup):
    waiting_interval = State()


# --- helpers -------------------------------------------------------------

async def _load_state(session: AsyncSession, settings: Settings) -> tuple[SuppressionState, datetime]:
    row = await repo.get_settings_row(session)
    entries = await repo.list_schedule(session)
    state = SuppressionState(
        notifications_enabled=row.notifications_enabled,
        away_mode=row.away_mode,
        snooze_until=row.snooze_until,
        windows=[Window(e.weekday, e.start_min, e.end_min) for e in entries],
    )
    now = datetime.now(tz=settings.app.tzinfo)
    return state, now


def _day_text(weekday: int, windows: list[Window]) -> str:
    day_windows = windows_for_day(windows, weekday)
    if not day_windows:
        body = "— нет интервалов присутствия —"
    else:
        body = "\n".join(f"• {fmt_minutes(w.start_min)}–{fmt_minutes(w.end_min)}" for w in day_windows)
    return f"📅 <b>{WEEKDAYS[weekday]}</b> — окна присутствия (алерты подавляются):\n{body}"


# --- /start --------------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(
    message: Message,
    command: CommandObject,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    secret = (command.args or "").strip()
    if not secret:
        await message.answer("👋 Для доступа отправь: <code>/start &lt;секрет&gt;</code>")
        return
    if secret != settings.telegram.auth_secret.get_secret_value():
        await message.answer("⛔ Неверный секрет.")
        return
    async with session_factory() as session:
        await repo.upsert_user(session, message.from_user.id, message.from_user.username)
    await message.answer("✅ Готово, ты авторизован. /status — состояние, /help — команды.")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "Команды:\n"
        "/status — состояние\n"
        "/notifications — вкл/выкл уведомлений (глобально)\n"
        "/schedule — окна присутствия (подавление)\n"
        "/mute /unmute — быстрый глобальный выкл/вкл\n"
        "/home /away — режим «дома/уехали»\n"
        "/snooze 2h — временно подавить\n"
        "/test — тестовый алерт себе"
    )


# --- /status -------------------------------------------------------------

@router.message(Command("status"))
async def cmd_status(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    async with session_factory() as session:
        state, now = await _load_state(session, settings)
    active = should_notify(now, state)
    lines = [
        f"🔔 Уведомления: <b>{'включены' if state.notifications_enabled else 'выключены'}</b>",
        f"🏠 Режим: <b>{'уехали' if state.away_mode else 'дома-по-расписанию'}</b>",
    ]
    if state.snooze_until and now.timestamp() < state.snooze_until:
        until = datetime.fromtimestamp(state.snooze_until, settings.app.tzinfo)
        lines.append(f"😴 Snooze до {until:%H:%M %d.%m}")
    lines.append(f"➡️ Сейчас: <b>{'АКТИВНО (шлём)' if active else 'подавлено'}</b>")
    today = now.weekday()
    day_windows = windows_for_day(state.windows, today)
    if day_windows:
        wins = ", ".join(f"{fmt_minutes(w.start_min)}–{fmt_minutes(w.end_min)}" for w in day_windows)
        lines.append(f"📅 Сегодня ({WEEKDAYS[today]}): {wins}")
    else:
        lines.append(f"📅 Сегодня ({WEEKDAYS[today]}): окон нет")
    await message.answer("\n".join(lines))


# --- /notifications ------------------------------------------------------

@router.message(Command("notifications"))
async def cmd_notifications(
    message: Message, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        row = await repo.get_settings_row(session)
    await message.answer(
        "Управление уведомлениями (глобально для всех):",
        reply_markup=notifications_keyboard(row.notifications_enabled),
    )


@router.callback_query(Cb.filter(F.a == "notif_on"))
async def cb_notif_on(
    query: CallbackQuery, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        await repo.set_notifications_enabled(session, True)
    await query.message.edit_text("🔔 Уведомления включены.", reply_markup=notifications_keyboard(True))
    await query.answer()


@router.callback_query(Cb.filter(F.a == "notif_off"))
async def cb_notif_off(
    query: CallbackQuery, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        await repo.set_notifications_enabled(session, False)
    await query.message.edit_text("🔕 Уведомления выключены.", reply_markup=notifications_keyboard(False))
    await query.answer()


@router.callback_query(Cb.filter(F.a == "noop"))
async def cb_noop(query: CallbackQuery) -> None:
    await query.answer("Видео для этого события недоступно")


# --- /mute /unmute (global shortcuts) -----------------------------------

@router.message(Command("mute"))
async def cmd_mute(message: Message, session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await repo.set_notifications_enabled(session, False)
    await message.answer("🔕 Уведомления выключены (глобально).")


@router.message(Command("unmute"))
async def cmd_unmute(message: Message, session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await repo.set_notifications_enabled(session, True)
    await message.answer("🔔 Уведомления включены (глобально).")


# --- /home /away ---------------------------------------------------------

@router.message(Command("away"))
async def cmd_away(message: Message, session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await repo.set_away_mode(session, True)
    await message.answer("🚗 Режим «уехали»: алерты шлём всегда (расписание игнорируется).")


@router.message(Command("home"))
async def cmd_home(message: Message, session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await repo.set_away_mode(session, False)
    await message.answer("🏠 Режим «дома»: действует расписание присутствия.")


# --- /snooze -------------------------------------------------------------

@router.message(Command("snooze"))
async def cmd_snooze(
    message: Message,
    command: CommandObject,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    arg = (command.args or "").strip().lower()
    if not arg:
        await message.answer("Формат: <code>/snooze 2h</code> или <code>/snooze 30m</code>")
        return
    try:
        seconds = _parse_duration(arg)
    except ValueError:
        await message.answer("Не понял длительность. Примеры: 2h, 30m, 90m")
        return
    until = int(time.time()) + seconds
    async with session_factory() as session:
        await repo.set_snooze_until(session, until)
    until_dt = datetime.fromtimestamp(until, settings.app.tzinfo)
    await message.answer(f"😴 Подавление до {until_dt:%H:%M %d.%m}.")


def _parse_duration(s: str) -> int:
    unit = 60
    if s.endswith("h"):
        unit, s = 3600, s[:-1]
    elif s.endswith("m"):
        unit, s = 60, s[:-1]
    value = int(s)
    if value <= 0:
        raise ValueError
    return value * unit


# --- /test ---------------------------------------------------------------

@router.message(Command("test"))
async def cmd_test(message: Message) -> None:
    await message.answer("🚨 Тестовый алерт · сквозной путь до Telegram работает ✅")


# --- /schedule -----------------------------------------------------------

@router.message(Command("schedule"))
async def cmd_schedule(message: Message) -> None:
    await message.answer("📅 Выбери день для редактирования окон присутствия:", reply_markup=week_keyboard())


@router.callback_query(Cb.filter(F.a == "sched_back"))
async def cb_sched_back(query: CallbackQuery) -> None:
    await query.message.edit_text(
        "📅 Выбери день для редактирования окон присутствия:", reply_markup=week_keyboard()
    )
    await query.answer()


@router.callback_query(Cb.filter(F.a == "sched_day"))
async def cb_sched_day(
    query: CallbackQuery, callback_data: Cb, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        entries = await repo.list_schedule(session, callback_data.wd)
    windows = [Window(e.weekday, e.start_min, e.end_min) for e in entries]
    await query.message.edit_text(_day_text(callback_data.wd, windows), reply_markup=day_keyboard(callback_data.wd, entries))
    await query.answer()


@router.callback_query(Cb.filter(F.a == "sched_del"))
async def cb_sched_del(
    query: CallbackQuery, callback_data: Cb, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        await repo.delete_schedule(session, callback_data.eid)
        entries = await repo.list_schedule(session, callback_data.wd)
    windows = [Window(e.weekday, e.start_min, e.end_min) for e in entries]
    await query.message.edit_text(_day_text(callback_data.wd, windows), reply_markup=day_keyboard(callback_data.wd, entries))
    await query.answer("Удалено")


@router.callback_query(Cb.filter(F.a == "sched_add"))
async def cb_sched_add(query: CallbackQuery, callback_data: Cb, state: FSMContext) -> None:
    await state.set_state(SchedFSM.waiting_interval)
    await state.update_data(weekday=callback_data.wd)
    await query.message.answer(
        f"Введи интервал для <b>{WEEKDAYS[callback_data.wd]}</b> в формате <code>18:00-23:00</code>:"
    )
    await query.answer()


@router.message(SchedFSM.waiting_interval)
async def on_interval_input(
    message: Message, state: FSMContext, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    data = await state.get_data()
    weekday = int(data["weekday"])
    try:
        start_min, end_min = parse_interval(message.text or "")
    except ValueError as e:
        await message.answer(f"⚠️ {e}. Попробуй ещё раз, например <code>18:00-23:00</code>:")
        return
    async with session_factory() as session:
        await repo.add_schedule(session, weekday, start_min, end_min)
        entries = await repo.list_schedule(session, weekday)
    await state.clear()
    windows = [Window(e.weekday, e.start_min, e.end_min) for e in entries]
    await message.answer(_day_text(weekday, windows), reply_markup=day_keyboard(weekday, entries))


@router.callback_query(Cb.filter(F.a == "sched_copy_week"))
async def cb_copy_week(
    query: CallbackQuery, callback_data: Cb, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        await repo.copy_weekday(session, callback_data.wd, [0, 1, 2, 3, 4])
    await query.answer("Скопировано на будни (Пн–Пт)", show_alert=True)


@router.callback_query(Cb.filter(F.a == "sched_copy_wend"))
async def cb_copy_wend(
    query: CallbackQuery, callback_data: Cb, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        await repo.copy_weekday(session, callback_data.wd, [5, 6])
    await query.answer("Скопировано на выходные (Сб–Вс)", show_alert=True)
