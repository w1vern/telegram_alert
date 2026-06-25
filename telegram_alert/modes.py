"""Alert delivery mode — a single global tri-state for the whole object."""

from __future__ import annotations

from enum import Enum


class AlertMode(str, Enum):
    OFF = "off"  # никогда не слать (приехали на дачу, движение ожидаемо)
    ALWAYS = "always"  # слать всегда, игнорируя расписание
    SCHEDULE = "schedule"  # слать, кроме заданных окон присутствия


MODE_LABELS: dict[AlertMode, str] = {
    AlertMode.OFF: "🔕 Отключены",
    AlertMode.ALWAYS: "🔔 Включены всегда",
    AlertMode.SCHEDULE: "📅 По расписанию",
}

MODE_HINTS: dict[AlertMode, str] = {
    AlertMode.OFF: "приехали на дачу — алерты не шлём",
    AlertMode.ALWAYS: "шлём всегда, расписание игнорируется",
    AlertMode.SCHEDULE: "шлём, кроме окон присутствия",
}


def parse_mode(value: str) -> AlertMode:
    try:
        return AlertMode(value)
    except ValueError:
        return AlertMode.SCHEDULE


class ScheduleOverride(str, Enum):
    """Temporary manual deviation on top of SCHEDULE mode.

    Only consulted while mode == SCHEDULE; ignored in OFF/ALWAYS.  Stays until a
    new override replaces it or ``/auto`` (NONE) clears it.
    """

    NONE = "none"  # follow the schedule windows
    MUTE = "mute"  # force alerts off regardless of the schedule
    UNMUTE = "unmute"  # force alerts on regardless of the schedule


OVERRIDE_LABELS: dict[ScheduleOverride, str] = {
    ScheduleOverride.NONE: "по расписанию",
    ScheduleOverride.MUTE: "🔕 временно заглушено",
    ScheduleOverride.UNMUTE: "🔔 временно включено",
}


def parse_override(value: str) -> ScheduleOverride:
    try:
        return ScheduleOverride(value)
    except ValueError:
        return ScheduleOverride.NONE


def resolve_override(value: str, until: float | None, now_ts: float) -> ScheduleOverride:
    """Effective override at ``now_ts``: NONE if unset or already expired."""
    ov = parse_override(value)
    if ov == ScheduleOverride.NONE:
        return ScheduleOverride.NONE
    if until is not None and now_ts >= until:
        return ScheduleOverride.NONE
    return ov
