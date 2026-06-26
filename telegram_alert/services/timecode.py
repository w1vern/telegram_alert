"""Parsing for the /record command: an absolute timecode and a clip length.

Times are interpreted in the dacha timezone (``AppSettings.tzinfo``); a missing
date defaults to today (or yesterday if that would land in the future), and a
missing year defaults to the current one.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, tzinfo

# Full date+time layouts, tried in order. Year/seconds are optional.
_DT_FORMATS = (
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m %H:%M:%S",
    "%d.%m %H:%M",
)
# Time-only layouts (date is inferred from ``now``).
_TIME_FORMATS = ("%H:%M:%S", "%H:%M")

_DURATION_RE = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


def parse_when(text: str, tz: tzinfo, now: datetime) -> float:
    """Absolute local timecode -> epoch seconds.

    ``now`` (timezone-aware, in ``tz``) is the reference for defaulting the
    missing date/year. Raises ``ValueError`` on an unrecognized format.
    """
    text = text.strip()
    # Time-only: assume today, or yesterday if that puts it in the future.
    for fmt in _TIME_FORMATS:
        try:
            t = datetime.strptime(text, fmt)
        except ValueError:
            continue
        dt = now.replace(hour=t.hour, minute=t.minute, second=t.second, microsecond=0)
        if dt > now:
            dt -= timedelta(days=1)
        return dt.timestamp()
    for fmt in _DT_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if "%Y" not in fmt:
            dt = dt.replace(year=now.year)
        return dt.replace(tzinfo=tz).timestamp()
    raise ValueError(
        "Не понял время. Примеры: «14:30», «26.06 14:30», «26.06.2026 14:30:00»."
    )


def parse_duration(text: str) -> int:
    """Clip length in seconds.

    Accepts a plain number of seconds (``30``), suffixed parts (``90s``, ``5m``,
    ``1h30m``) or a colon form (``MM:SS``, ``HH:MM:SS``). Raises ``ValueError``;
    the result is always > 0.
    """
    text = text.strip().lower().replace(" ", "")
    if not text:
        raise ValueError("Пустая длина")
    if ":" in text:
        parts = text.split(":")
        if len(parts) > 3 or not all(p.isdigit() for p in parts):
            raise ValueError("Неверная длина. Примеры: «5:00», «1:30:00».")
        nums = [int(p) for p in parts]
        while len(nums) < 3:
            nums.insert(0, 0)
        total = nums[0] * 3600 + nums[1] * 60 + nums[2]
    elif text.isdigit():
        total = int(text)
    else:
        m = _DURATION_RE.fullmatch(text)
        if m is None or not any(m.groups()):
            raise ValueError("Неверная длина. Примеры: «30», «90s», «5m», «1h30m».")
        total = int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)
    if total <= 0:
        raise ValueError("Длина должна быть больше нуля")
    return total


def fmt_length(seconds: int) -> str:
    """Compact human length like '90с' -> '1м 30с', '3700' -> '1ч 1м 40с'."""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    parts: list[str] = []
    if h:
        parts.append(f"{h}ч")
    if m:
        parts.append(f"{m}м")
    if s or not parts:
        parts.append(f"{s}с")
    return " ".join(parts)
