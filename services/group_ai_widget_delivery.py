"""Отправка и закрепление виджета в Telegram-чате (веб-админка, при выходе бота из группы)."""
from __future__ import annotations

import logging

from telegram import Bot
from telegram.error import TelegramError

from config import settings
from services.group_ai_widget_service import (
    build_widget_reply_markup,
    get_widget,
    save_pin_result,
    set_enabled,
    widget_public_message_html,
)

logger = logging.getLogger(__name__)


async def disable_group_ai_widget(chat_id: int) -> tuple[bool, str]:
    """Снять закрепление (если было), выключить виджет в БД, очистить id сообщения."""
    token = (settings.TELEGRAM_TOKEN or "").strip()
    w = await get_widget(int(chat_id))
    if not w:
        return True, "нет записи"
    mid = w.get("pinned_message_id")
    if token and mid:
        bot = Bot(token=token)
        try:
            await bot.unpin_chat_message(chat_id=int(chat_id), message_id=int(mid))
        except TelegramError as e:
            logger.debug("unpin_group_ai_widget chat=%s: %s", chat_id, e)
        except Exception as e:
            logger.debug("unpin_group_ai_widget chat=%s: %s", chat_id, e)
    await set_enabled(int(chat_id), False)
    await save_pin_result(int(chat_id), None, None, clear_pinned=True)
    return True, "ok"


async def deliver_group_ai_widget(
    chat_id: int,
    referral_attribution_user_id: int | None,
) -> tuple[bool, str]:
    token = (settings.TELEGRAM_TOKEN or "").strip()
    if not token:
        return False, "TELEGRAM_TOKEN не задан."

    text = widget_public_message_html()
    try:
        kb = await build_widget_reply_markup(referral_attribution_user_id)
    except Exception as e:
        logger.exception("build_widget_reply_markup")
        return False, f"Кнопки: {e!s}"

    w = await get_widget(int(chat_id))
    old_mid = (w or {}).get("pinned_message_id")

    bot = Bot(token=token)
    try:
        if old_mid:
            try:
                await bot.unpin_chat_message(chat_id=int(chat_id), message_id=int(old_mid))
            except TelegramError:
                pass
        msg = await bot.send_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=kb,
        )
        await bot.pin_chat_message(
            chat_id=int(chat_id),
            message_id=int(msg.message_id),
            disable_notification=True,
        )
        await save_pin_result(int(chat_id), int(msg.message_id), None)
        return True, "ok"
    except TelegramError as e:
        err = str(e)[:2000]
        await save_pin_result(int(chat_id), None, err)
        logger.warning("deliver_group_ai_widget chat=%s: %s", chat_id, e)
        return False, err
    except Exception as e:
        err = str(e)[:2000]
        await save_pin_result(int(chat_id), None, err)
        logger.exception("deliver_group_ai_widget chat=%s", chat_id)
        return False, err
