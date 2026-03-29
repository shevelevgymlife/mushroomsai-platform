"""
Подключение личного Telegram-канала к аккаунту: автопост в ленту сообщества (текст и/или фото, без видео).
"""
from __future__ import annotations

import logging
import re

import sqlalchemy as sa
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, MessageOriginChannel, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import ApplicationHandlerStop, ContextTypes, MessageHandler, filters

from bot.handlers.start import ensure_user_or_blocked_reply, main_keyboard
from config import settings
from db.database import database
from db.models import users
from services.channel_ingest_save_image import save_channel_ingest_image
from services.community_post_publish import publish_community_post
from services.telegram_file_download import download_telegram_file_bytes

logger = logging.getLogger(__name__)

BTN_AUTOPOST_DISABLE = "🔕 Выключить автопост из канала в ленту"
BTN_AUTOPOST_ENABLE = "🔔 Включить автопост из канала в ленту"

LINK_INSTRUCTIONS_HTML = (
    "📢 <b>Подключение канала к ленте сообщества</b>\n\n"
    "1. Откройте ваш канал → меню (⋮) → <b>Управление каналом</b> → <b>Администраторы</b>.\n"
    "2. Нажмите <b>Добавить администратора</b> и выберите <b>этого бота</b>.\n"
    "3. Включите право <b>Публиковать сообщения</b> (остальное по желанию; "
    "добавлять других администраторов боту не нужно).\n"
    "4. Сохраните изменения.\n\n"
    "После этого нажмите кнопку ниже. Если бот не увидит канал — перешлите "
    "<b>любое сообщение из канала</b> сюда в чат.\n\n"
    "В ленту попадают только <b>текст</b> и <b>фото</b> (в т.ч. с подписью). "
    "Видео, файлы, опросы и альбомы из нескольких фото пока не публикуются."
)


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


async def _verify_bot_can_post(bot, channel_chat_id: int) -> bool:
    me = await bot.get_me()
    try:
        m = await bot.get_chat_member(chat_id=channel_chat_id, user_id=me.id)
    except Exception as e:
        logger.debug("channel_autopost verify member: %s", e)
        return False
    st = m.status
    if st == ChatMemberStatus.OWNER:
        return True
    if st == ChatMemberStatus.ADMINISTRATOR:
        return bool(getattr(m, "can_post_messages", True))
    return False


async def _row_for_user(internal_user_id: int) -> dict | None:
    r = await database.fetch_one(
        sa.text(
            "SELECT user_id, channel_chat_id, channel_title, channel_username, autopost_enabled "
            "FROM user_channel_autopost WHERE user_id = :u"
        ),
        {"u": int(internal_user_id)},
    )
    return dict(r) if r else None


async def autopost_extra_rows(internal_user_id: int) -> list[list[KeyboardButton]] | None:
    row = await _row_for_user(internal_user_id)
    if not row:
        return None
    if row.get("autopost_enabled"):
        return [[KeyboardButton(BTN_AUTOPOST_DISABLE)]]
    return [[KeyboardButton(BTN_AUTOPOST_ENABLE)]]


async def main_keyboard_with_autopost(site_url: str, ai_active: bool, internal_user_id: int):
    extras = await autopost_extra_rows(internal_user_id)
    return main_keyboard(site_url, ai_active, extra_rows=extras)


async def _save_link(
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
                    (user_id, channel_chat_id, channel_title, channel_username, autopost_enabled, linked_at, updated_at)
                VALUES (:uid, :ccid, :tit, :un, true, NOW(), NOW())
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
        logger.warning("user_channel_autopost upsert: %s", e)
        return False, "Не удалось сохранить привязку в базе."
    return True, None


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
) -> tuple[bool, str | object]:
    if channel_chat_id is None:
        return False, (
            "Не удалось определить канал. Добавьте бота администратором с правом "
            "<b>публиковать сообщения</b> и снова нажмите «Я подвязал», либо перешлите сообщение из канала."
        )
    if not await _verify_bot_can_post(context.bot, channel_chat_id):
        return False, (
            "Бот не является администратором канала или нет права <b>публиковать сообщения</b>. "
            "Проверьте настройки и попробуйте снова."
        )
    tit = channel_title or (pending.get("channel_title") if pending else None)
    un = channel_username or (pending.get("channel_username") if pending else None)
    ok, err = await _save_link(
        int(user_row["id"]),
        channel_chat_id,
        tit,
        un,
    )
    if not ok:
        return False, err or "Ошибка при сохранении."
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    kb = await main_keyboard_with_autopost(site, context.user_data.get("tg_ai_mode"), int(user_row["id"]))
    return True, kb


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


async def connect_channel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await ensure_user_or_blocked_reply(update)
    if not user or not update.message:
        return
    context.user_data["tg_ai_mode"] = False
    context.user_data["channel_link_awaiting"] = True
    context.user_data.pop("channel_link_need_forward", None)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Я подвязал", callback_data="ch_link_done")]])
    await update.message.reply_html(LINK_INSTRUCTIONS_HTML, reply_markup=kb, disable_web_page_preview=True)
    raise ApplicationHandlerStop


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
    channel_chat_id = pending["channel_chat_id"] if pending else None
    title = pending.get("channel_title") if pending else None
    username = pending.get("channel_username") if pending else None

    ok, payload = await _try_finalize_link(update, context, user, pending, channel_chat_id, title, username)
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    if ok:
        _pending_map(context).pop(tg_uid, None)
        context.user_data.pop("channel_link_awaiting", None)
        context.user_data.pop("channel_link_need_forward", None)
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await q.message.reply_html(
            "✅ <b>Канал успешно подключён.</b>\n\n"
            "Новые посты канала (текст и фото) будут появляться в вашей ленте сообщества, "
            "пока включён автопост. Кнопка внизу переключает публикацию.",
            reply_markup=payload,
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
        update, context, user, pending, channel_chat_id, ch.title, ch.username
    )
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    if ok:
        context.user_data.pop("channel_link_awaiting", None)
        context.user_data.pop("channel_link_need_forward", None)
        await msg.reply_html(
            "✅ <b>Канал успешно подключён</b> (по пересланному сообщению).",
            reply_markup=payload,
        )
    else:
        await msg.reply_html(
            f"❌ {payload}",
            reply_markup=await main_keyboard_with_autopost(site, False, int(user["id"])),
        )
    raise ApplicationHandlerStop


async def toggle_autopost_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    context.user_data["tg_ai_mode"] = False
    user = await ensure_user_or_blocked_reply(update)
    if not user:
        return
    row = await _row_for_user(int(user["id"]))
    if not row:
        site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
        await update.message.reply_text(
            "Сначала подключите канал — кнопка «📢 Подключить свой канал».",
            reply_markup=main_keyboard(site, context.user_data.get("tg_ai_mode")),
        )
        raise ApplicationHandlerStop
    new_val = not bool(row.get("autopost_enabled"))
    try:
        await database.execute(
            sa.text(
                "UPDATE user_channel_autopost SET autopost_enabled = :v, updated_at = NOW() WHERE user_id = :u"
            ),
            {"v": new_val, "u": int(user["id"])},
        )
    except Exception as e:
        logger.warning("autopost toggle: %s", e)
        await update.message.reply_text("Не удалось переключить. Попробуйте позже.")
        raise ApplicationHandlerStop
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    kb = await main_keyboard_with_autopost(site, context.user_data.get("tg_ai_mode"), int(user["id"]))
    state = "включён" if new_val else "выключен"
    await update.message.reply_text(
        f"Автопост из канала в ленту сообщества <b>{state}</b>.",
        parse_mode="HTML",
        reply_markup=kb,
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
            "SELECT user_id FROM user_channel_autopost WHERE channel_chat_id = :c AND autopost_enabled = true"
        ),
        {"c": chat_id},
    )
    if not link:
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


_TOGGLE_PATTERN = re.compile(
    "^(" + re.escape(BTN_AUTOPOST_DISABLE) + "|" + re.escape(BTN_AUTOPOST_ENABLE) + ")$"
)


def get_toggle_autopost_handler() -> MessageHandler:
    return MessageHandler(filters.Regex(_TOGGLE_PATTERN) & filters.ChatType.PRIVATE, toggle_autopost_handler)
