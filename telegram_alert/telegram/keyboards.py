"""Inline keyboards for control commands."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from telegram_alert.db.models import ScheduleEntry
from telegram_alert.services.schedule import fmt_minutes
from telegram_alert.telegram.callbacks import Cb

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def notifications_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=("🔔 Включены ✅" if enabled else "🔔 Включить"),
                    callback_data=Cb(a="notif_on").pack(),
                ),
                InlineKeyboardButton(
                    text=("🔕 Выключить" if enabled else "🔕 Выключены ✅"),
                    callback_data=Cb(a="notif_off").pack(),
                ),
            ]
        ]
    )


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
