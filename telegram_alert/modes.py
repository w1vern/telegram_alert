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
