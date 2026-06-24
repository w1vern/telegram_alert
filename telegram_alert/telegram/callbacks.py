"""Callback-data factory shared by keyboards and handlers."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class Cb(CallbackData, prefix="al"):
    a: str  # action
    wd: int = -1  # weekday (0=Mon..6=Sun)
    eid: int = -1  # schedule entry id
    uid: int = 0  # target user tg_id (admin actions)
