"""Notification-suppression logic.

``should_notify(now, mode, windows)`` decides whether an alert is delivered, in
the dacha timezone, based on the global tri-state mode:

* OFF      -> never notify (someone is at the dacha);
* ALWAYS   -> always notify (ignore the schedule);
* SCHEDULE -> notify unless ``now`` falls inside a presence window for today.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from telegram_alert.modes import AlertMode, ScheduleOverride


@dataclass(frozen=True)
class Window:
    weekday: int  # 0=Mon .. 6=Sun
    start_min: int
    end_min: int


def _in_window(weekday: int, minute_of_day: int, w: Window) -> bool:
    if w.start_min < w.end_min:
        # Normal same-day window.
        return w.weekday == weekday and w.start_min <= minute_of_day < w.end_min
    # Wrapping window (end <= start): covers [start, 24:00) on its weekday
    # and [00:00, end) on the next weekday.
    if w.weekday == weekday and minute_of_day >= w.start_min:
        return True
    prev = (weekday - 1) % 7
    if w.weekday == prev and minute_of_day < w.end_min:
        return True
    return False


def is_suppressed_by_schedule(now: datetime, windows: list[Window]) -> bool:
    weekday = now.weekday()
    minute_of_day = now.hour * 60 + now.minute
    return any(_in_window(weekday, minute_of_day, w) for w in windows)


def should_notify(
    now: datetime,
    mode: AlertMode,
    windows: list[Window],
    override: ScheduleOverride = ScheduleOverride.NONE,
) -> bool:
    if mode == AlertMode.OFF:
        return False
    if mode == AlertMode.ALWAYS:
        return True
    # SCHEDULE — a temporary override wins over the windows when set.
    if override == ScheduleOverride.MUTE:
        return False
    if override == ScheduleOverride.UNMUTE:
        return True
    return not is_suppressed_by_schedule(now, windows)


def windows_for_day(windows: list[Window], weekday: int) -> list[Window]:
    return sorted(
        (w for w in windows if w.weekday == weekday),
        key=lambda w: w.start_min,
    )


def fmt_minutes(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def parse_interval(text: str) -> tuple[int, int]:
    """Parse ``HH:MM-HH:MM`` into (start_min, end_min).  Raises ValueError."""
    text = text.strip().replace(" ", "")
    if "-" not in text:
        raise ValueError("Ожидаю формат 18:00-23:00")
    left, right = text.split("-", 1)
    start = _parse_hhmm(left)
    end = _parse_hhmm(right)
    if start == end:
        raise ValueError("Начало и конец совпадают")
    return start, end


def _parse_hhmm(s: str) -> int:
    if ":" not in s:
        raise ValueError(f"Неверное время: {s!r}")
    hh, mm = s.split(":", 1)
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"Неверное время: {s!r}")
    return h * 60 + m
