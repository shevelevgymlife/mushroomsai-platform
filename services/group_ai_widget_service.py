"""
Виджет «NeuroFungi AI» в Telegram-группах/супергруппах: учёт чатов, мастер-выключатель в platform_settings.
Реферальные ссылки — та же логика, что для кнопки «в соцсеть» у владельца канала (social_app_entry_url_for_channel_owner + магазин).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import sqlalchemy as sa

from config import settings
from db.database import database
from db.models import platform_settings, telegram_group_ai_widgets, users

logger = logging.getLogger(__name__)

MASTER_KEY = "group_ai_widget_master_enabled"


async def get_master_enabled() -> bool:
    row = await database.fetch_one(
        platform_settings.select().where(platform_settings.c.key == MASTER_KEY)
    )
    if not row:
        return False
    raw = (row.get("value") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


async def set_master_enabled(on: bool) -> None:
    val = "true" if on else "false"
    row = await database.fetch_one(
        platform_settings.select().where(platform_settings.c.key == MASTER_KEY)
    )
    if row:
        await database.execute(
            platform_settings.update()
            .where(platform_settings.c.key == MASTER_KEY)
            .values(value=val)
        )
    else:
        await database.execute(
            platform_settings.insert().values(key=MASTER_KEY, value=val)
        )


async def upsert_chat_discovered(chat_id: int, chat_type: str, title: str | None) -> None:
    row = await database.fetch_one(
        telegram_group_ai_widgets.select().where(telegram_group_ai_widgets.c.chat_id == int(chat_id))
    )
    if row:
        await database.execute(
            telegram_group_ai_widgets.update()
            .where(telegram_group_ai_widgets.c.chat_id == int(chat_id))
            .values(
                chat_type=(chat_type or "supergroup")[:32],
                chat_title=(title or "")[:500] or None,
                updated_at=datetime.utcnow(),
            )
        )
        return
    await database.execute(
        telegram_group_ai_widgets.insert().values(
            chat_id=int(chat_id),
            chat_type=(chat_type or "supergroup")[:32],
            chat_title=(title or "")[:500] or None,
            enabled=False,
        )
    )


async def list_widgets() -> list[dict[str, Any]]:
    rows = await database.fetch_all(
        telegram_group_ai_widgets.select().order_by(telegram_group_ai_widgets.c.chat_id)
    )
    return [dict(r) for r in rows]


async def get_widget(chat_id: int) -> dict[str, Any] | None:
    r = await database.fetch_one(
        telegram_group_ai_widgets.select().where(telegram_group_ai_widgets.c.chat_id == int(chat_id))
    )
    return dict(r) if r else None


async def set_enabled(chat_id: int, enabled: bool, *, clear_error: bool = True) -> None:
    vals: dict = {"enabled": bool(enabled), "updated_at": datetime.utcnow()}
    if clear_error:
        vals["last_error"] = None
    await database.execute(
        telegram_group_ai_widgets.update()
        .where(telegram_group_ai_widgets.c.chat_id == int(chat_id))
        .values(**vals)
    )


async def set_attribution_user_id(chat_id: int, user_id: int | None) -> None:
    await database.execute(
        telegram_group_ai_widgets.update()
        .where(telegram_group_ai_widgets.c.chat_id == int(chat_id))
        .values(
            referral_attribution_user_id=int(user_id) if user_id else None,
            updated_at=datetime.utcnow(),
        )
    )


async def save_pin_result(
    chat_id: int,
    message_id: int | None,
    err: str | None,
    *,
    clear_pinned: bool = False,
) -> None:
    vals: dict = {"updated_at": datetime.utcnow(), "last_error": (err or None)[:2000] if err else None}
    if clear_pinned:
        vals["pinned_message_id"] = None
        vals["last_pin_at"] = None
    elif message_id is not None:
        vals["pinned_message_id"] = int(message_id)
        vals["last_pin_at"] = datetime.utcnow()
    await database.execute(
        telegram_group_ai_widgets.update()
        .where(telegram_group_ai_widgets.c.chat_id == int(chat_id))
        .values(**vals)
    )


async def manual_add_chat(chat_id: int) -> tuple[bool, str]:
    """Добавить чат вручную (ещё не было события от бота)."""
    if int(chat_id) >= 0:
        return False, "Ожидается отрицательный chat_id супергруппы (например -100…)."
    exists = await get_widget(chat_id)
    if exists:
        return True, "Уже в списке."
    await database.execute(
        telegram_group_ai_widgets.insert().values(
            chat_id=int(chat_id),
            chat_type="supergroup",
            chat_title=None,
            enabled=False,
        )
    )
    return True, "Добавлено."


def widget_public_message_html() -> str:
    """Текст для участников группы (HTML)."""
    site = (settings.SITE_URL or "https://mushroomsai.ru").rstrip("/")
    return (
        "🍄 <b>NeuroFungi AI — консультант по функциональным грибам</b>\n\n"
        "<b>Как это работает</b>\n"
        "Нажмите кнопку <b>«Задать вопрос NeuroFungi AI»</b> ниже — откроется бот в личных сообщениях. "
        "Там включите режим вопроса к AI и напишите запрос: ответ формируется с учётом базы знаний проекта.\n\n"
        "<b>Когда уместно включать этот блок</b>\n"
        "• Сообщество или команда хотят быстрый доступ к AI из группы.\n"
        "• Нужна одна закреплённая «точка входа», чтобы не терять ссылку в ленте сообщений.\n\n"
        "<b>Важно</b>\n"
        "• Сам ИИ отвечает в <b>личке с ботом</b>, а не прямо в группе (меньше шума и нагрузки).\n"
        "• Если администраторы закрепят другое сообщение, этот блок может сместиться — "
        "владелец площадки может снова закрепить его из админ-панели NEUROFUNGI.\n\n"
        "<b>Реферальные ссылки</b>\n"
        "Если для этого чата задан <b>владелец атрибуции</b> в админке, кнопки ведут в бот и магазин "
        "с теми же правилами, что и у партнёра: персональный код в ссылке на бот при активной партнёрке "
        "(магазин и/или платная партнёрка приложения); иначе — общий вход. "
        f"Сайт сервиса: {site}\n\n"
        "<i>Функцию включает только администратор платформы в админке «Группы / Чаты».</i>"
    )


async def build_widget_reply_markup(referral_attribution_user_id: int | None):
    """Inline-кнопки: соц/бот (логика как у владельца канала) + опционально магазин партнёра."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    from services.referral_service import (
        default_social_app_entry_url,
        invite_referral_code_for_sharing,
        social_app_entry_url_for_channel_owner,
    )

    rows: list[list[InlineKeyboardButton]] = []
    site = (settings.SITE_URL or "https://mushroomsai.ru").rstrip("/")
    if not site.startswith("http"):
        site = "https://" + site.lstrip("/")

    if referral_attribution_user_id:
        bot_url = await social_app_entry_url_for_channel_owner(int(referral_attribution_user_id))
        urow = await database.fetch_one(
            users.select().where(users.c.id == int(referral_attribution_user_id))
        )
        shop_url = ((urow or {}).get("referral_shop_url") or "").strip()
        code = await invite_referral_code_for_sharing(int(referral_attribution_user_id))
    else:
        bot_url = default_social_app_entry_url()
        shop_url = ""
        code = ""

    rows.append(
        [InlineKeyboardButton("🤖 Задать вопрос NeuroFungi AI", url=bot_url)]
    )
    if referral_attribution_user_id:
        if shop_url:
            rows.append([InlineKeyboardButton("🛍 Каталог партнёра (магазин)", url=shop_url)])
        elif code:
            rows.append(
                [
                    InlineKeyboardButton(
                        "🛍 Магазин и кабинет (регистрация по коду)",
                        url=f"{site}/login?ref={code}",
                    )
                ]
            )
    return InlineKeyboardMarkup(rows)
