"""Alert message formatting and the clip button keyboard."""

from __future__ import annotations

from datetime import datetime, tzinfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from telegram_alert.services.timecode import fmt_length


def alert_caption(camera: str, ts: float, tz: tzinfo) -> str:
    when = datetime.fromtimestamp(ts, tz).strftime("%H:%M:%S %d.%m.%Y") if ts else "—"
    return f"🚨 {camera} · человек · {when}"


def clip_caption(camera: str, ts: float, tz: tzinfo) -> str:
    when = datetime.fromtimestamp(ts, tz).strftime("%H:%M:%S %d.%m.%Y") if ts else "—"
    return f"🎥 {camera} · запись · {when}"


def record_caption(camera: str, start_ts: float, length_s: int, tz: tzinfo) -> str:
    when = datetime.fromtimestamp(start_ts, tz).strftime("%H:%M:%S %d.%m.%Y") if start_ts else "—"
    return f"🎬 {camera} · запись с {when} · {fmt_length(length_s)}"


def clip_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🎬 Видео", url=url)]]
    )


def clip_unavailable_keyboard() -> InlineKeyboardMarkup:
    from telegram_alert.telegram.callbacks import Cb

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎬 видео недоступно", callback_data=Cb(a="noop").pack())]
        ]
    )
