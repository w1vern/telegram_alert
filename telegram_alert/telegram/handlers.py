"""Command and callback handlers.

Dependencies (``session_factory``, ``settings``) are injected by aiogram from
the values passed to ``dp.start_polling(...)``.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_alert.broker.amqp import Broker
from telegram_alert.broker.jobs import MediaJob
from telegram_alert.config import Settings
from telegram_alert.db import repo
from telegram_alert.modes import (
    MODE_HINTS,
    MODE_LABELS,
    OVERRIDE_LABELS,
    AlertMode,
    ScheduleOverride,
    parse_mode,
    parse_override,
)
from telegram_alert.services.frigate import FrigateClient
from telegram_alert.services.schedule import (
    Window,
    fmt_minutes,
    parse_interval,
    should_notify,
    windows_for_day,
)
from telegram_alert.telegram.callbacks import Cb
from telegram_alert.telegram.keyboards import (
    WEEKDAYS,
    approval_keyboard,
    day_keyboard,
    mode_keyboard,
    users_keyboard,
    week_keyboard,
)

log = logging.getLogger(__name__)
router = Router()


class SchedFSM(StatesGroup):
    waiting_interval = State()


def _is_superuser(settings: Settings, uid: int) -> bool:
    return uid in settings.telegram.superuser_ids


# --- helpers -------------------------------------------------------------

async def _load(
    session: AsyncSession,
) -> tuple[AlertMode, list[Window], ScheduleOverride]:
    row = await repo.get_settings_row(session)
    entries = await repo.list_schedule(session)
    windows = [Window(e.weekday, e.start_min, e.end_min) for e in entries]
    return parse_mode(row.mode), windows, parse_override(row.override)


def _day_text(weekday: int, windows: list[Window]) -> str:
    day_windows = windows_for_day(windows, weekday)
    if not day_windows:
        body = "— нет интервалов присутствия —"
    else:
        body = "\n".join(
            f"• {fmt_minutes(w.start_min)}–{fmt_minutes(w.end_min)}" for w in day_windows
        )
    return f"📅 <b>{WEEKDAYS[weekday]}</b> — окна присутствия (алерты подавляются):\n{body}"


def _mode_text(mode: AlertMode) -> str:
    lines = ["Режим алертов (глобально для всех):", ""]
    for m in (AlertMode.OFF, AlertMode.ALWAYS, AlertMode.SCHEDULE):
        prefix = "▶️" if m == mode else "▫️"
        lines.append(f"{prefix} {MODE_LABELS[m]} — {MODE_HINTS[m]}")
    return "\n".join(lines)


# --- public (no auth) ----------------------------------------------------

@router.message(Command("start"))
async def cmd_start(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    uid = message.from_user.id
    username = message.from_user.username
    async with session_factory() as session:
        await repo.upsert_user(session, uid, username)
        authorized = await repo.is_authorized(session, uid)

    if authorized or _is_superuser(settings, uid):
        await message.answer("👋 Привет! Бот активен. /help — список команд.")
        return

    await message.answer(
        "👋 Это алерт-бот дачи.\n"
        "Заявка на доступ отправлена администратору — ожидай подтверждения."
    )
    # Ping every superuser with an approve/deny inline keyboard.
    handle = f"@{username}" if username else "—"
    for su in settings.telegram.superuser_ids:
        try:
            await message.bot.send_message(
                su,
                f"🆕 Запрос доступа: {handle} (<code>{uid}</code>)",
                reply_markup=approval_keyboard(uid),
            )
        except Exception:  # noqa: BLE001 - superuser may not have opened the bot
            log.warning("could not notify superuser %s about new user %s", su, uid)


@router.message(Command("help"))
async def cmd_help(message: Message, settings: Settings) -> None:
    base = (
        "Команды:\n"
        "/status — текущее состояние\n"
        "/mode — режим алертов: отключены / всегда / по расписанию\n"
        "/schedule — окна присутствия (для режима «по расписанию»)\n"
        "/clip — запись последних действий со всех камер\n"
        "\nВ режиме «по расписанию» (поверх расписания, до /auto):\n"
        "/mute — временно заглушить уведомления\n"
        "/unmute — временно включить уведомления\n"
        "/auto — сбросить, вернуться к расписанию"
    )
    if _is_superuser(settings, message.from_user.id):
        base += (
            "\n\nАдмин:\n"
            "/users — список пользователей, выдать/забрать доступ\n"
            "/grant &lt;id&gt; — выдать доступ по ID\n"
            "/revoke &lt;id&gt; — забрать доступ по ID"
        )
    await message.answer(base)


# --- admin: authorization management ------------------------------------

@router.message(Command("users"))
async def cmd_users(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    if not _is_superuser(settings, message.from_user.id):
        await message.answer("⛔ Только для администратора.")
        return
    async with session_factory() as session:
        users = await repo.list_users(session)
    su = set(settings.telegram.superuser_ids)
    await message.answer(
        "👥 Пользователи (нажми, чтобы выдать/забрать доступ):",
        reply_markup=users_keyboard(users, su),
    )


@router.message(Command("grant"))
async def cmd_grant(
    message: Message,
    command: CommandObject,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    await _grant_revoke_cmd(message, command, session_factory, settings, value=True)


@router.message(Command("revoke"))
async def cmd_revoke(
    message: Message,
    command: CommandObject,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    await _grant_revoke_cmd(message, command, session_factory, settings, value=False)


async def _grant_revoke_cmd(message, command, session_factory, settings, value: bool) -> None:
    if not _is_superuser(settings, message.from_user.id):
        await message.answer("⛔ Только для администратора.")
        return
    arg = (command.args or "").strip()
    if not arg.lstrip("-").isdigit():
        await message.answer("Формат: <code>/grant 123456789</code>")
        return
    target = int(arg)
    async with session_factory() as session:
        await repo.set_authorized(session, target, value)
    word = "выдан" if value else "забран"
    await message.answer(f"✅ Доступ {word} для <code>{target}</code>.")
    if value:
        try:
            await message.bot.send_message(target, "✅ Тебе выдан доступ к алертам дачи.")
        except Exception:  # noqa: BLE001
            pass


@router.callback_query(Cb.filter(F.a == "grant"))
async def cb_grant(query: CallbackQuery, callback_data: Cb, session_factory, settings: Settings) -> None:
    if not _is_superuser(settings, query.from_user.id):
        await query.answer("⛔ Только для администратора.", show_alert=True)
        return
    async with session_factory() as session:
        await repo.set_authorized(session, callback_data.uid, True)
    await query.message.edit_text(f"✅ Авторизован <code>{callback_data.uid}</code>")
    try:
        await query.bot.send_message(callback_data.uid, "✅ Тебе выдан доступ к алертам дачи.")
    except Exception:  # noqa: BLE001
        pass
    await query.answer("Готово")


@router.callback_query(Cb.filter(F.a == "deny"))
async def cb_deny(query: CallbackQuery, callback_data: Cb, settings: Settings) -> None:
    if not _is_superuser(settings, query.from_user.id):
        await query.answer("⛔ Только для администратора.", show_alert=True)
        return
    await query.message.edit_text(f"🚫 Отклонён <code>{callback_data.uid}</code>")
    await query.answer()


@router.callback_query(Cb.filter(F.a == "utoggle"))
async def cb_utoggle(query: CallbackQuery, callback_data: Cb, session_factory, settings: Settings) -> None:
    if not _is_superuser(settings, query.from_user.id):
        await query.answer("⛔ Только для администратора.", show_alert=True)
        return
    async with session_factory() as session:
        user = await repo.get_user(session, callback_data.uid)
        new_value = not (user.authorized if user else False)
        await repo.set_authorized(session, callback_data.uid, new_value)
        users = await repo.list_users(session)
    su = set(settings.telegram.superuser_ids)
    await query.message.edit_reply_markup(reply_markup=users_keyboard(users, su))
    await query.answer("Доступ выдан" if new_value else "Доступ забран")


# --- /status -------------------------------------------------------------

@router.message(Command("status"))
async def cmd_status(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    async with session_factory() as session:
        mode, windows, override = await _load(session)
    now = datetime.now(tz=settings.app.tzinfo)
    active = should_notify(now, mode, windows, override)
    lines = [
        f"Режим: <b>{MODE_LABELS[mode]}</b> ({MODE_HINTS[mode]})",
        f"➡️ Сейчас: <b>{'АКТИВНО — шлём' if active else 'подавлено'}</b>",
    ]
    if mode == AlertMode.SCHEDULE:
        if override != ScheduleOverride.NONE:
            lines.append(f"⏸ Override: <b>{OVERRIDE_LABELS[override]}</b> (/auto — сбросить)")
        today = now.weekday()
        day_windows = windows_for_day(windows, today)
        if day_windows:
            wins = ", ".join(
                f"{fmt_minutes(w.start_min)}–{fmt_minutes(w.end_min)}" for w in day_windows
            )
            lines.append(f"📅 Сегодня ({WEEKDAYS[today]}): {wins}")
        else:
            lines.append(f"📅 Сегодня ({WEEKDAYS[today]}): окон нет")
    await message.answer("\n".join(lines))


# --- /mode ---------------------------------------------------------------

@router.message(Command("mode"))
async def cmd_mode(
    message: Message, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        mode, _, _ = await _load(session)
    await message.answer(_mode_text(mode), reply_markup=mode_keyboard(mode))


async def _set_mode(query: CallbackQuery, session_factory, mode: AlertMode) -> None:
    async with session_factory() as session:
        await repo.set_mode(session, mode.value)
    await query.message.edit_text(_mode_text(mode), reply_markup=mode_keyboard(mode))
    await query.answer(f"Режим: {MODE_LABELS[mode]}")


@router.callback_query(Cb.filter(F.a == "mode_off"))
async def cb_mode_off(query: CallbackQuery, session_factory) -> None:
    await _set_mode(query, session_factory, AlertMode.OFF)


@router.callback_query(Cb.filter(F.a == "mode_always"))
async def cb_mode_always(query: CallbackQuery, session_factory) -> None:
    await _set_mode(query, session_factory, AlertMode.ALWAYS)


@router.callback_query(Cb.filter(F.a == "mode_schedule"))
async def cb_mode_schedule(query: CallbackQuery, session_factory) -> None:
    await _set_mode(query, session_factory, AlertMode.SCHEDULE)


@router.callback_query(Cb.filter(F.a == "noop"))
async def cb_noop(query: CallbackQuery) -> None:
    await query.answer("Видео для этого события недоступно")


# --- temporary override (only in SCHEDULE mode) -------------------------

async def _set_override(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    value: ScheduleOverride,
    ok_text: str,
) -> None:
    """Apply a temporary override, but only while in SCHEDULE mode.  Any new
    override replaces the previous one (single global value)."""
    async with session_factory() as session:
        row = await repo.get_settings_row(session)
        mode = parse_mode(row.mode)
        if mode != AlertMode.SCHEDULE:
            await message.answer(
                f"⚠️ Работает только в режиме «{MODE_LABELS[AlertMode.SCHEDULE]}».\n"
                f"Сейчас: <b>{MODE_LABELS[mode]}</b>. Сменить — /mode."
            )
            return
        await repo.set_override(session, value.value)
    await message.answer(ok_text)


@router.message(Command("mute"))
async def cmd_mute(
    message: Message, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _set_override(
        message,
        session_factory,
        ScheduleOverride.MUTE,
        "🔕 Уведомления временно заглушены поверх расписания.\n/auto — вернуть расписание.",
    )


@router.message(Command("unmute"))
async def cmd_unmute(
    message: Message, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _set_override(
        message,
        session_factory,
        ScheduleOverride.UNMUTE,
        "🔔 Уведомления временно включены поверх расписания.\n/auto — вернуть расписание.",
    )


@router.message(Command("auto"))
async def cmd_auto(
    message: Message, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        await repo.set_override(session, ScheduleOverride.NONE.value)
    await message.answer("🔄 Сброшено — уведомления снова по расписанию.")


# --- /test ---------------------------------------------------------------

MAX_CLIP_SECONDS = 120


@router.message(Command("clip"))
async def cmd_clip(
    message: Message,
    command: CommandObject,
    broker: Broker,
    frigate: FrigateClient,
    settings: Settings,
) -> None:
    seconds = settings.frigate.clip_seconds
    arg = (command.args or "").strip().lower()
    if arg.endswith("s"):
        arg = arg[:-1].strip()
    if arg:
        if not arg.isdigit() or int(arg) == 0:
            await message.answer(
                "Формат: <code>/clip 30</code> — длина в секундах (без аргумента — по умолчанию)."
            )
            return
        seconds = min(int(arg), MAX_CLIP_SECONDS)

    try:
        cameras = await frigate.list_cameras()
    except Exception:  # noqa: BLE001
        log.exception("/clip: failed to read Frigate config")
        await message.answer("⚠️ Не удалось обратиться к Frigate.")
        return
    if not cameras:
        await message.answer("⚠️ В конфиге Frigate нет камер.")
        return

    ts = time.time()
    for camera in cameras:
        job = MediaJob(
            type="new",
            on_demand=True,
            review_id=f"clip-{int(ts)}-{camera}",
            camera=camera,
            ts=ts,
            clip_seconds=seconds,
        )
        await broker.publish_job(job.model_dump_json().encode())

    capped = " (макс)" if arg and int(arg) > MAX_CLIP_SECONDS else ""
    await message.answer(
        f"🎥 Беру {seconds}-сек{capped} запись с камер: "
        f"<b>{', '.join(cameras)}</b> — разошлю всем авторизованным."
    )


# --- /schedule -----------------------------------------------------------

@router.message(Command("schedule"))
async def cmd_schedule(message: Message) -> None:
    await message.answer(
        "📅 Выбери день для редактирования окон присутствия:", reply_markup=week_keyboard()
    )


@router.callback_query(Cb.filter(F.a == "sched_back"))
async def cb_sched_back(query: CallbackQuery) -> None:
    await query.message.edit_text(
        "📅 Выбери день для редактирования окон присутствия:", reply_markup=week_keyboard()
    )
    await query.answer()


@router.callback_query(Cb.filter(F.a == "sched_day"))
async def cb_sched_day(
    query: CallbackQuery, callback_data: Cb, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        entries = await repo.list_schedule(session, callback_data.wd)
    windows = [Window(e.weekday, e.start_min, e.end_min) for e in entries]
    await query.message.edit_text(
        _day_text(callback_data.wd, windows), reply_markup=day_keyboard(callback_data.wd, entries)
    )
    await query.answer()


@router.callback_query(Cb.filter(F.a == "sched_del"))
async def cb_sched_del(
    query: CallbackQuery, callback_data: Cb, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        await repo.delete_schedule(session, callback_data.eid)
        entries = await repo.list_schedule(session, callback_data.wd)
    windows = [Window(e.weekday, e.start_min, e.end_min) for e in entries]
    await query.message.edit_text(
        _day_text(callback_data.wd, windows), reply_markup=day_keyboard(callback_data.wd, entries)
    )
    await query.answer("Удалено")


@router.callback_query(Cb.filter(F.a == "sched_add"))
async def cb_sched_add(query: CallbackQuery, callback_data: Cb, state: FSMContext) -> None:
    await state.set_state(SchedFSM.waiting_interval)
    await state.update_data(weekday=callback_data.wd)
    await query.message.answer(
        f"Введи интервал для <b>{WEEKDAYS[callback_data.wd]}</b> в формате <code>18:00-23:00</code>:"
    )
    await query.answer()


@router.message(SchedFSM.waiting_interval)
async def on_interval_input(
    message: Message, state: FSMContext, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    data = await state.get_data()
    weekday = int(data["weekday"])
    try:
        start_min, end_min = parse_interval(message.text or "")
    except ValueError as e:
        await message.answer(f"⚠️ {e}. Попробуй ещё раз, например <code>18:00-23:00</code>:")
        return
    async with session_factory() as session:
        await repo.add_schedule(session, weekday, start_min, end_min)
        entries = await repo.list_schedule(session, weekday)
    await state.clear()
    windows = [Window(e.weekday, e.start_min, e.end_min) for e in entries]
    await message.answer(_day_text(weekday, windows), reply_markup=day_keyboard(weekday, entries))


@router.callback_query(Cb.filter(F.a == "sched_copy_week"))
async def cb_copy_week(
    query: CallbackQuery, callback_data: Cb, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        await repo.copy_weekday(session, callback_data.wd, [0, 1, 2, 3, 4])
    await query.answer("Скопировано на будни (Пн–Пт)", show_alert=True)


@router.callback_query(Cb.filter(F.a == "sched_copy_wend"))
async def cb_copy_wend(
    query: CallbackQuery, callback_data: Cb, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        await repo.copy_weekday(session, callback_data.wd, [5, 6])
    await query.answer("Скопировано на выходные (Сб–Вс)", show_alert=True)
