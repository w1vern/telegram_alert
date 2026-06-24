"""Alert message formatting and the clip button keyboard."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def alert_caption(camera: str, ts: float, tz: ZoneInfo) -> str:
    when = datetime.fromtimestamp(ts, tz).strftime("%H:%M:%S %d.%m.%Y") if ts else "—"
    return f"🚨 {camera} · человек · {when}"


def clip_caption(camera: str, ts: float, tz: ZoneInfo) -> str:
    when = datetime.fromtimestamp(ts, tz).strftime("%H:%M:%S %d.%m.%Y") if ts else "—"
    return f"🎥 {camera} · запись · {when}"


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
