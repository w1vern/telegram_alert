"""Callback-data factory shared by keyboards and handlers."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class Cb(CallbackData, prefix="al"):
    a: str  # action
    wd: int = -1  # weekday (0=Mon..6=Sun)
    eid: int = -1  # schedule entry id
    idx: int = -1  # generic index (e.g. camera position in the /record flow)
