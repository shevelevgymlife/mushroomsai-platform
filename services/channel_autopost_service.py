"""
Привязка личного Telegram-канала (автопост в ленту + кнопка «в соцсеть» под постами).
Общая логика для бота и веб-кабинета /account/channel-autopost.
"""
from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from telegram import Bot
from telegram.constants import ChatMemberStatus

from config import settings
from db.database import database
from db.models import channel_autopost_link_pending, users

logger = logging.getLogger(__name__)


async def user_can_use_channel_partner_social_button(user_id: int) -> bool:
    """Кнопка с персональной реф-ссылкой — для партнёра магазина и/или платной партнёрки приложения."""
    uid = int(user_id)
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row:
        return False
    if (row.get("referral_shop_url") or "").strip():
        return True
    try:
        from services.subscription_service import paid_subscription_for_referral_program
        from services.shop_referral_hub import maxi_marketplace_can_bind_any_shop_url

        if await paid_subscription_for_referral_program(uid):
            return True
        if await maxi_marketplace_can_bind_any_shop_url(uid):
            return True
    except Exception:
        logger.debug("user_can_use_channel_partner_social_button failed uid=%s", uid, exc_info=True)
    return False


async def upsert_link_pending(
    telegram_user_id: int,
    channel_chat_id: int,
    channel_title: str | None,
    channel_username: str | None,
) -> None:
    tid = int(telegram_user_id)
    try:
        await database.execute(
            sa.text(
                """
                INSERT INTO channel_autopost_link_pending
                    (telegram_user_id, channel_chat_id, channel_title, channel_username, updated_at)
                VALUES (:tid, :ccid, :tit, :un, NOW())
                ON CONFLICT (telegram_user_id) DO UPDATE SET
                    channel_chat_id = EXCLUDED.channel_chat_id,
                    channel_title = EXCLUDED.channel_title,
                    channel_username = EXCLUDED.channel_username,
                    updated_at = NOW()
                """
            ),
            {
                "tid": tid,
                "ccid": int(channel_chat_id),
                "tit": channel_title,
                "un": (channel_username or "").strip() or None,
            },
        )
    except Exception as e:
        logger.warning("upsert_link_pending: %s", e)


async def get_link_pending(telegram_user_id: int) -> dict[str, Any] | None:
    r = await database.fetch_one(
        channel_autopost_link_pending.select().where(
            channel_autopost_link_pending.c.telegram_user_id == int(telegram_user_id)
        )
    )
    return dict(r) if r else None


async def delete_link_pending(telegram_user_id: int) -> None:
    try:
        await database.execute(
            channel_autopost_link_pending.delete().where(
                channel_autopost_link_pending.c.telegram_user_id == int(telegram_user_id)
            )
        )
    except Exception as e:
        logger.debug("delete_link_pending: %s", e)


async def _bot() -> Bot | None:
    token = (getattr(settings, "TELEGRAM_TOKEN", None) or "").strip()
    if not token:
        return None
    try:
        return Bot(token=token)
    except Exception:
        return None


async def verify_bot_can_post_channel(channel_chat_id: int) -> bool:
    bot = await _bot()
    if not bot:
        return False
    try:
        me = await bot.get_me()
        m = await bot.get_chat_member(chat_id=int(channel_chat_id), user_id=me.id)
    except Exception as e:
        logger.debug("verify_bot_can_post_channel: %s", e)
        return False
    st = m.status
    if st == ChatMemberStatus.OWNER:
        return True
    if st == ChatMemberStatus.ADMINISTRATOR:
        return bool(getattr(m, "can_post_messages", True))
    return False


async def verify_bot_can_edit_channel_messages(channel_chat_id: int) -> bool:
    bot = await _bot()
    if not bot:
        return False
    try:
        me = await bot.get_me()
        m = await bot.get_chat_member(chat_id=int(channel_chat_id), user_id=me.id)
    except Exception as e:
        logger.debug("verify_bot_can_edit_channel_messages: %s", e)
        return False
    if m.status == ChatMemberStatus.ADMINISTRATOR:
        return bool(getattr(m, "can_edit_messages", False))
    return False


async def save_channel_autopost_link(
    internal_user_id: int,
    channel_chat_id: int,
    channel_title: str | None,
    channel_username: str | None,
) -> tuple[bool, str | None]:
    other = await database.fetch_one(
        sa.text(
            "SELECT user_id FROM user_channel_autopost WHERE channel_chat_id = :c AND user_id <> :u LIMIT 1"
        ),
        {"c": int(channel_chat_id), "u": int(internal_user_id)},
    )
    if other:
        return False, "Этот канал уже подключён к другому аккаунту."
    try:
        await database.execute(
            sa.text(
                """
                INSERT INTO user_channel_autopost
                    (user_id, channel_chat_id, channel_title, channel_username, autopost_enabled,
                     channel_social_button_enabled, linked_at, updated_at)
                VALUES (:uid, :ccid, :tit, :un, true, false, NOW(), NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    channel_chat_id = EXCLUDED.channel_chat_id,
                    channel_title = EXCLUDED.channel_title,
                    channel_username = EXCLUDED.channel_username,
                    autopost_enabled = true,
                    updated_at = NOW()
                """
            ),
            {
                "uid": int(internal_user_id),
                "ccid": int(channel_chat_id),
                "tit": channel_title,
                "un": (channel_username or "").strip() or None,
            },
        )
    except Exception as e:
        logger.warning("save_channel_autopost_link: %s", e)
        return False, "Не удалось сохранить привязку в базе."
    return True, None


async def fetch_autopost_row(internal_user_id: int) -> dict[str, Any] | None:
    r = await database.fetch_one(
        sa.text(
            "SELECT user_id, channel_chat_id, channel_title, channel_username, autopost_enabled, "
            "channel_social_button_enabled FROM user_channel_autopost WHERE user_id = :u"
        ),
        {"u": int(internal_user_id)},
    )
    return dict(r) if r else None


async def try_finalize_link_from_pending(
    internal_user_id: int,
    telegram_user_id: int,
    *,
    pending_override: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """
    pending_override — из памяти бота; иначе читаем channel_autopost_link_pending по telegram_user_id.
    """
    pend = pending_override
    if pend is None:
        pend = await get_link_pending(int(telegram_user_id))
    if not pend:
        return False, (
            "Не найден канал. Добавьте бота администратором канала с правом «публиковать сообщения», "
            "затем снова нажмите «Проверить привязку» или откройте бота командой из кабинета и нажмите «Я подвязал»."
        )
    ccid = pend.get("channel_chat_id")
    if ccid is None:
        return False, "Нет данных о канале."
    ccid = int(ccid)
    if not await verify_bot_can_post_channel(ccid):
        return False, (
            "Бот не администратор канала или нет права публиковать сообщения. Проверьте настройки канала."
        )
    tit = pend.get("channel_title")
    un = pend.get("channel_username")
    ok, err = await save_channel_autopost_link(int(internal_user_id), ccid, tit, un)
    if not ok:
        return False, err or "Ошибка сохранения."
    if int(telegram_user_id) > 0:
        await delete_link_pending(int(telegram_user_id))
    return True, ""


async def set_autopost_enabled(internal_user_id: int, enabled: bool) -> bool:
    try:
        await database.execute(
            sa.text(
                "UPDATE user_channel_autopost SET autopost_enabled = :v, updated_at = NOW() WHERE user_id = :u"
            ),
            {"v": bool(enabled), "u": int(internal_user_id)},
        )
        return True
    except Exception as e:
        logger.warning("set_autopost_enabled: %s", e)
        return False


async def set_channel_social_button_enabled(internal_user_id: int, enabled: bool) -> tuple[bool, str | None]:
    if enabled and not await user_can_use_channel_partner_social_button(int(internal_user_id)):
        return False, "Доступно партнёрам: укажите ссылку магазина в партнёрке и/или активируйте платную партнёрку приложения."
    try:
        await database.execute(
            sa.text(
                "UPDATE user_channel_autopost SET channel_social_button_enabled = :v, updated_at = NOW() "
                "WHERE user_id = :u"
            ),
            {"v": bool(enabled), "u": int(internal_user_id)},
        )
        return True, None
    except Exception as e:
        logger.warning("set_channel_social_button_enabled: %s", e)
        return False, "Не удалось сохранить."
