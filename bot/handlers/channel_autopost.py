"""
Подключение личного Telegram-канала к аккаунту: автопост в ленту сообщества (текст и/или фото, без видео).
"""
from __future__ import annotations

import html
import logging
import re

import sqlalchemy as sa
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, MessageOriginChannel, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import ApplicationHandlerStop, ContextTypes, MessageHandler, filters

from bot.handlers.start import ensure_user_or_blocked_reply, main_keyboard
from config import settings
from db.database import database
from db.models import users
from services.channel_ingest_save_image import save_channel_ingest_image
from services.channel_autopost_service import (
    get_link_pending,
    try_finalize_link_from_pending,
    upsert_link_pending,
    user_can_use_channel_partner_social_button,
    verify_bot_can_edit_channel_messages as _svc_verify_bot_can_edit_channel_messages,
)
from services.community_post_publish import publish_community_post
from services.referral_service import social_app_entry_url_for_channel_owner
from services.telegram_file_download import download_telegram_file_bytes

logger = logging.getLogger(__name__)

def build_link_instructions_html() -> str:
    """Текст перед назначением бота админом канала: что делает функция и какие права выдать."""
    site = (settings.SITE_URL or "https://mushroomsai.ru").rstrip("/")
    bot_u = (settings.TELEGRAM_BOT_USERNAME or "").strip().lstrip("@")
    bot_ref = ""
    if bot_u:
        bot_ref = (
            f"Вход и регистрация в соцсети — через того же бота: "
            f'<a href="https://t.me/{bot_u}">@{bot_u}</a>.\n\n'
        )
    return (
        "📢 <b>Подключение канала к NEUROFUNGI</b>\n\n"
        "<b>Что будет после подключения</b>\n"
        "Новые посты вашего канала (в рамках правил ниже) могут "
        "<b>дублироваться в ленту сообщества</b> на сайте / в приложении — от вашего профиля. "
        "Включать и выключать автопост и кнопку «В соцсеть» под постами настраивайте в "
        "<b>приложении</b>: меню → «Мой канал в ленту».\n\n"
        f"{bot_ref}"
        "<b>Как правильно добавить этого бота администратором канала</b>\n"
        "1. Откройте <b>ваш канал</b> в Telegram.\n"
        "2. Меню канала (⋮ или название сверху) → <b>Управление каналом</b> → "
        "<b>Администраторы</b>.\n"
        "3. <b>Добавить администратора</b> → в поиске выберите <b>этого бота</b> "
        "(того, с кем вы сейчас переписываетесь).\n"
        "4. В списке прав отметьте как минимум:\n"
        "   • <b>Публиковать сообщения</b> — <i>обязательно</i>, иначе бот не сможет "
        "работать с каналом и копировать посты в ленту.\n"
        "   • <b>Изменять сообщения других пользователей</b> (или аналог в вашей версии Telegram) — "
        "<i>желательно</i>: тогда в будущем (или при включённой опции) под постами в канале можно "
        "будет добавлять кнопку «наша соцсеть» со ссылкой на бота / регистрацию. "
        "<b>Без этого права</b> Telegram не даёт боту дописывать кнопки к уже опубликованным постам.\n"
        "5. Право <b>добавлять новых администраторов</b> боту <b>не нужно</b> — можно отключить.\n"
        "6. Сохраните изменения (Готово / Сохранить).\n\n"
        "<b>Дальше в этом чате</b>\n"
        "Нажмите кнопку <b>«Я подвязал»</b> ниже. Если бот напишет, что канал не найден — "
        "перешлите сюда <b>любое сообщение из канала</b> (из того канала, который подключаете).\n\n"
        f'<b>Соцсеть в браузере</b>: <a href="{site}">{site}</a>\n\n'
        "<b>Что попадает в ленту сейчас</b>\n"
        "Только <b>текст</b> и <b>одно фото</b> (в т.ч. с подписью). "
        "Видео, файлы, опросы, стикеры и альбомы из нескольких фото <b>не</b> публикуются."
    )


async def _attach_social_button_to_post(bot, channel_chat_id: int, message_id: int, channel_owner_id: int) -> None:
    url = await social_app_entry_url_for_channel_owner(int(channel_owner_id))
    try:
        await bot.edit_message_reply_markup(
            chat_id=channel_chat_id,
            message_id=message_id,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Войти в социальную сеть", url=url)]]
            ),
        )
    except Exception as e:
        logger.debug("channel social button markup: %s", e)


def _pending_map(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.application.bot_data.setdefault("channel_autopost_pending", {})


def _set_pending(
    context: ContextTypes.DEFAULT_TYPE,
    tg_uid: int,
    *,
    channel_chat_id: int,
    channel_title: str | None,
    channel_username: str | None,
) -> None:
    _pending_map(context)[int(tg_uid)] = {
        "channel_chat_id": int(channel_chat_id),
        "channel_title": channel_title,
        "channel_username": channel_username,
    }


def _pop_pending(context: ContextTypes.DEFAULT_TYPE, tg_uid: int) -> dict | None:
    return _pending_map(context).pop(int(tg_uid), None)


async def _row_for_user(internal_user_id: int) -> dict | None:
    r = await database.fetch_one(
        sa.text(
            "SELECT user_id, channel_chat_id, channel_title, channel_username, autopost_enabled, "
            "channel_social_button_enabled "
            "FROM user_channel_autopost WHERE user_id = :u"
        ),
        {"u": int(internal_user_id)},
    )
    return dict(r) if r else None


async def main_keyboard_with_autopost(site_url: str, ai_active: bool, internal_user_id: int):
    from services.referral_shop_prefs import tg_shop_button_label
    from services.referral_service import referral_withdraw_keyboard_row

    ref_wd = await referral_withdraw_keyboard_row(internal_user_id)
    merged: list = []
    if ref_wd:
        merged.extend(ref_wd)
    shop_btn = await tg_shop_button_label(internal_user_id)
    from services.closed_telegram_access import closed_telegram_keyboard_rows

    ct_rows = await closed_telegram_keyboard_rows(internal_user_id)
    return main_keyboard(
        site_url,
        ai_active,
        extra_rows=merged if merged else None,
        shop_button=shop_btn,
        closed_tg_rows=ct_rows if ct_rows else None,
    )


async def _delete_by_channel(channel_chat_id: int) -> None:
    try:
        await database.execute(
            sa.text("DELETE FROM user_channel_autopost WHERE channel_chat_id = :c"),
            {"c": int(channel_chat_id)},
        )
    except Exception as e:
        logger.warning("channel_autopost delete by channel: %s", e)


async def _try_finalize_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_row: dict,
    pending: dict | None,
    channel_chat_id: int | None,
    channel_title: str | None,
    channel_username: str | None,
    *,
    telegram_user_id: int,
) -> tuple[bool, str | object]:
    if channel_chat_id is None:
        return False, (
            "Не удалось определить канал. Добавьте бота администратором с правом "
            "<b>публиковать сообщения</b> и снова нажмите «Я подвязал», либо перешлите сообщение из канала."
        )
    tit = channel_title or (pending.get("channel_title") if pending else None)
    un = channel_username or (pending.get("channel_username") if pending else None)
    pend_ov = {
        "channel_chat_id": int(channel_chat_id),
        "channel_title": tit,
        "channel_username": un,
    }
    ok, err_msg = await try_finalize_link_from_pending(
        int(user_row["id"]),
        int(telegram_user_id),
        pending_override=pend_ov,
    )
    if not ok:
        return False, err_msg
    return True, None


async def ch_soc_btn_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data:
        return
    m = re.match(r"^ch_soc_btn:([01])$", q.data)
    if not m:
        return
    user = await ensure_user_or_blocked_reply(update)
    if not user:
        return
    val = m.group(1) == "1"
    row = await _row_for_user(int(user["id"]))
    if not row:
        await q.answer()
        await q.message.reply_text("Сначала подключите канал в приложении: меню → «Мой канал в ленту».")
        return
    if val and not await user_can_use_channel_partner_social_button(int(user["id"])):
        await q.answer(
            "Кнопка с вашей реферальной ссылкой доступна партнёрам: укажите магазин в партнёрке "
            "и/или активируйте платную партнёрку приложения.",
            show_alert=True,
        )
        return
    await q.answer()
    try:
        await database.execute(
            sa.text(
                "UPDATE user_channel_autopost SET channel_social_button_enabled = :v, updated_at = NOW() "
                "WHERE user_id = :u"
            ),
            {"v": val, "u": int(user["id"])},
        )
    except Exception as e:
        logger.warning("ch_soc_btn: %s", e)
        await q.answer("Не удалось сохранить.", show_alert=True)
        return
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    kb = await main_keyboard_with_autopost(site, context.user_data.get("tg_ai_mode"), int(user["id"]))
    warn = ""
    if val:
        ok_edit = await _svc_verify_bot_can_edit_channel_messages(int(row["channel_chat_id"]))
        if not ok_edit:
            warn = (
                "\n\n⚠️ У бота пока <b>нет права изменять сообщения</b> в канале — "
                "включите его у этого бота в списке администраторов канала, иначе кнопка под постами не появится."
            )
    state = "включена" if val else "выключена"
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await q.message.reply_html(
        f"Сохранено: кнопка «Войти в социальную сеть» под новыми постами в канале <b>{state}</b>.{warn}\n\n"
        "Дальше эту настройку удобнее менять в <b>приложении</b>: меню → «Мой канал в ленту».",
        reply_markup=kb,
    )


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    res = update.my_chat_member
    if not res or not res.chat:
        return
    chat = res.chat
    if chat.type != ChatType.CHANNEL:
        return
    new = res.new_chat_member
    if new.user.id != context.bot.id:
        return

    if new.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
        await _delete_by_channel(chat.id)
        return

    if new.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        await _delete_by_channel(chat.id)
        return

    if new.status == ChatMemberStatus.ADMINISTRATOR and not getattr(new, "can_post_messages", True):
        return

    fu = res.from_user
    if fu:
        _set_pending(
            context,
            fu.id,
            channel_chat_id=chat.id,
            channel_title=chat.title,
            channel_username=chat.username,
        )
        try:
            await upsert_link_pending(
                int(fu.id),
                int(chat.id),
                chat.title,
                chat.username,
            )
        except Exception as e:
            logger.debug("upsert_link_pending from my_chat_member: %s", e)


async def ch_link_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()
    user = await ensure_user_or_blocked_reply(update)
    if not user:
        return
    tg_uid = q.from_user.id
    pending = _pending_map(context).get(tg_uid)
    if not pending:
        rowp = await get_link_pending(tg_uid)
        if rowp:
            pending = {
                "channel_chat_id": rowp["channel_chat_id"],
                "channel_title": rowp.get("channel_title"),
                "channel_username": rowp.get("channel_username"),
            }
    channel_chat_id = pending["channel_chat_id"] if pending else None
    title = pending.get("channel_title") if pending else None
    username = pending.get("channel_username") if pending else None

    ok, payload = await _try_finalize_link(
        update,
        context,
        user,
        pending,
        channel_chat_id,
        title,
        username,
        telegram_user_id=tg_uid,
    )
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    if ok:
        _pending_map(context).pop(tg_uid, None)
        context.user_data.pop("channel_link_awaiting", None)
        context.user_data.pop("channel_link_need_forward", None)
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        kb = await main_keyboard_with_autopost(site, context.user_data.get("tg_ai_mode"), int(user["id"]))
        await q.message.reply_html(
            "✅ <b>Канал успешно подключён.</b>\n\n"
            "Автопост в ленту и кнопка «В соцсеть» под постами канала настраиваются в <b>приложении</b>: "
            "меню → «Мой канал в ленту».",
            reply_markup=kb,
        )
        return

    context.user_data["channel_link_need_forward"] = True
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await q.message.reply_html(
        f"❌ {payload}\n\n"
        "Можете <b>переслать сюда любое сообщение из канала</b> — бот попробует привязать канал снова.",
        reply_markup=await main_keyboard_with_autopost(site, False, int(user["id"])),
    )


async def channel_forward_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if context.user_data.get("cp_post_wizard"):
        return
    if not (
        context.user_data.get("channel_link_awaiting")
        or context.user_data.get("channel_link_need_forward")
    ):
        return
    msg = update.message
    origin = msg.forward_origin
    if not isinstance(origin, MessageOriginChannel):
        await msg.reply_text("Перешлите сообщение именно из канала (не из личного чата).")
        raise ApplicationHandlerStop

    user = await ensure_user_or_blocked_reply(update)
    if not user:
        return

    ch = origin.chat
    channel_chat_id = ch.id
    pending = {
        "channel_title": ch.title,
        "channel_username": ch.username,
    }
    ok, payload = await _try_finalize_link(
        update,
        context,
        user,
        pending,
        channel_chat_id,
        ch.title,
        ch.username,
        telegram_user_id=int(update.effective_user.id),
    )
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    if ok:
        context.user_data.pop("channel_link_awaiting", None)
        context.user_data.pop("channel_link_need_forward", None)
        kb = await main_keyboard_with_autopost(site, context.user_data.get("tg_ai_mode"), int(user["id"]))
        await msg.reply_html(
            "✅ <b>Канал успешно подключён</b> (по пересланному сообщению).\n\n"
            "Автопост и кнопку «В соцсеть» настраивайте в <b>приложении</b>: меню → «Мой канал в ленту».",
            reply_markup=kb,
        )
    else:
        await msg.reply_html(
            f"❌ {payload}",
            reply_markup=await main_keyboard_with_autopost(site, False, int(user["id"])),
        )
    raise ApplicationHandlerStop


def _channel_post_publishable(msg: Message) -> bool:
    if msg.video or msg.video_note or msg.animation:
        return False
    if msg.document and not msg.photo:
        return False
    if msg.poll or msg.sticker:
        return False
    if msg.photo:
        return True
    if (msg.text or "").strip():
        return True
    return False


async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    post = update.channel_post
    if not post:
        return
    if post.media_group_id:
        return

    chat_id = int(post.chat_id)
    link = await database.fetch_one(
        sa.text(
            "SELECT user_id, autopost_enabled, channel_social_button_enabled "
            "FROM user_channel_autopost WHERE channel_chat_id = :c LIMIT 1"
        ),
        {"c": chat_id},
    )
    if not link:
        return

    if link.get("channel_social_button_enabled") and await user_can_use_channel_partner_social_button(
        int(link["user_id"])
    ):
        await _attach_social_button_to_post(
            context.bot, chat_id, int(post.message_id), int(link["user_id"])
        )

    if not link.get("autopost_enabled"):
        return

    if not _channel_post_publishable(post):
        return

    uid = int(link["user_id"])
    urow = await database.fetch_one(users.select().where(users.c.id == uid))
    if not urow:
        return
    author_name = (urow.get("name") or "").strip() or "Участник"

    cap = (post.caption or "").strip()
    txt = (post.text or "").strip()
    raw_body = cap or txt
    lines = raw_body.split("\n", 1) if raw_body else []
    title = (lines[0][:200] if lines else None) or None
    body = raw_body if len((raw_body or "").strip()) >= 2 else ""

    token = (settings.TELEGRAM_TOKEN or "").strip()
    image_url = None
    if post.photo and token:
        try:
            best = max(post.photo, key=lambda p: (p.width or 0) * (p.height or 0))
            data = await download_telegram_file_bytes(token, best.file_id)
            if data:
                image_url = save_channel_ingest_image(data)
        except Exception as e:
            logger.warning("channel_autopost photo: %s", e)

    if len(body) < 2:
        if image_url:
            body = "📷 Пост из Telegram-канала"
        else:
            return

    mid = int(post.message_id)
    try:
        ins = await database.fetch_one_write(
            sa.text(
                """
                INSERT INTO channel_autopost_log (channel_chat_id, message_id, created_at)
                VALUES (:c, :m, NOW())
                ON CONFLICT (channel_chat_id, message_id) DO NOTHING
                RETURNING message_id
                """
            ),
            {"c": chat_id, "m": mid},
        )
    except Exception as e:
        logger.warning("channel_autopost_log: %s", e)
        return
    if not ins:
        return

    post_id = await publish_community_post(
        user_id=uid,
        author_name=author_name,
        content=body,
        title=title,
        image_url=image_url,
        from_telegram=True,
    )
    if not post_id:
        try:
            await database.execute(
                sa.text("DELETE FROM channel_autopost_log WHERE channel_chat_id = :c AND message_id = :m"),
                {"c": chat_id, "m": mid},
            )
        except Exception:
            pass


def get_channel_forward_handler() -> MessageHandler:
    return MessageHandler(
        filters.ChatType.PRIVATE & filters.UpdateType.MESSAGES & filters.FORWARDED,
        channel_forward_link_handler,
    )


