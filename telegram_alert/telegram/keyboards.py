"""Inline keyboards for control commands."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from telegram_alert.db.models import ScheduleEntry
from telegram_alert.modes import MODE_LABELS, AlertMode
from telegram_alert.services.schedule import fmt_minutes
from telegram_alert.telegram.callbacks import Cb

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

_MODE_ACTION = {
    AlertMode.OFF: "mode_off",
    AlertMode.ALWAYS: "mode_always",
    AlertMode.SCHEDULE: "mode_schedule",
}


def approval_keyboard(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Авторизовать", callback_data=Cb(a="grant", uid=uid).pack()),
                InlineKeyboardButton(text="🚫 Отклонить", callback_data=Cb(a="deny", uid=uid).pack()),
            ]
        ]
    )


def users_keyboard(users, su_ids: set[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for u in users:
        if u.tg_id in su_ids:
            name = f"@{u.username}" if u.username else str(u.tg_id)
            rows.append(
                [InlineKeyboardButton(text=f"👑 {name} ({u.tg_id})", callback_data=Cb(a="noop").pack())]
            )
            continue
        mark = "✅" if u.authorized else "🚫"
        name = f"@{u.username}" if u.username else str(u.tg_id)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {name} ({u.tg_id})",
                    callback_data=Cb(a="utoggle", uid=u.tg_id).pack(),
                )
            ]
        )
    if not rows:
        rows.append([InlineKeyboardButton(text="— нет известных пользователей —", callback_data=Cb(a="noop").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mode_keyboard(current: AlertMode) -> InlineKeyboardMarkup:
    rows = []
    for mode in (AlertMode.OFF, AlertMode.ALWAYS, AlertMode.SCHEDULE):
        mark = " ✅" if mode == current else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{MODE_LABELS[mode]}{mark}",
                    callback_data=Cb(a=_MODE_ACTION[mode]).pack(),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def week_keyboard() -> InlineKeyboardMarkup:
    rows = []
    row: list[InlineKeyboardButton] = []
    for wd, name in enumerate(WEEKDAYS):
        row.append(InlineKeyboardButton(text=name, callback_data=Cb(a="sched_day", wd=wd).pack()))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def day_keyboard(weekday: int, entries: list[ScheduleEntry]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for e in entries:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🗑 {fmt_minutes(e.start_min)}–{fmt_minutes(e.end_min)}",
                    callback_data=Cb(a="sched_del", wd=weekday, eid=e.id).pack(),
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ Добавить интервал", callback_data=Cb(a="sched_add", wd=weekday).pack())])
    rows.append(
        [
            InlineKeyboardButton(text="📋 На будни", callback_data=Cb(a="sched_copy_week", wd=weekday).pack()),
            InlineKeyboardButton(text="📋 На выходные", callback_data=Cb(a="sched_copy_wend", wd=weekday).pack()),
        ]
    )
    rows.append([InlineKeyboardButton(text="« К неделе", callback_data=Cb(a="sched_back").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)
