"""
Подключение личного Telegram-канала к аккаунту: автопост в ленту сообщества (текст и/или фото, без видео).
"""
from __future__ import annotations

import html
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
BTN_CH_SOC_ON = "🔔 Показывать кнопку «В соцсеть» под постами канала"
BTN_CH_SOC_OFF = "🔕 Убрать кнопку «В соцсеть» с постов канала"

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
        "Включать и выключать автопост можно кнопкой в этом чате.\n\n"
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
        "перешлите сюда <b>любое сообщение из канала</b> (из того канала, который подключаете).\n"
        "После успешной привязки бот спросит, нужна ли <b>кнопка «Войти в социальную сеть»</b> "
        "под каждым новым постом в канале (можно включить или отказаться).\n\n"
        f'<b>Соцсеть в браузере</b>: <a href="{site}">{site}</a>\n\n'
        "<b>Что попадает в ленту сейчас</b>\n"
        "Только <b>текст</b> и <b>одно фото</b> (в т.ч. с подписью). "
        "Видео, файлы, опросы, стикеры и альбомы из нескольких фото <b>не</b> публикуются."
    )


def _social_button_choice_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Да, добавлять кнопку", callback_data="ch_soc_btn:1"),
                InlineKeyboardButton("⏭ Нет", callback_data="ch_soc_btn:0"),
            ]
        ]
    )


async def _attach_social_button_to_post(bot, channel_chat_id: int, message_id: int) -> None:
    url = _social_network_entry_url()
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


async def _prompt_social_button_choice(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    main_kb: object,
) -> None:
    await message.reply_html(
        "<b>Кнопка под постами в канале</b>\n\n"
        "Добавлять под <b>каждым новым</b> постом в канале кнопку "
        "«<b>Войти в социальную сеть</b>» (ссылка на бота или приложение)?\n\n"
        "<i>Нужно право бота <b>изменять сообщения</b> в канале. "
        "Без него Telegram не даст дописать кнопку к посту.</i>\n\n"
        "Вы всегда можете сменить это решение: кнопки внизу экрана или снова «Подключить свой канал».",
        reply_markup=_social_button_choice_markup(),
    )
    await message.reply_text("⌨️", reply_markup=main_kb)


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


def _social_network_entry_url() -> str:
    bot_u = (settings.TELEGRAM_BOT_USERNAME or "").strip().lstrip("@")
    site = (settings.SITE_URL or "https://mushroomsai.ru").rstrip("/")
    if bot_u:
        return f"https://t.me/{bot_u}"
    return f"{site}/app"


async def _verify_bot_can_edit_channel_messages(bot, channel_chat_id: int) -> bool:
    me = await bot.get_me()
    try:
        m = await bot.get_chat_member(chat_id=channel_chat_id, user_id=me.id)
    except Exception as e:
        logger.debug("channel_autopost verify can_edit: %s", e)
        return False
    if m.status == ChatMemberStatus.ADMINISTRATOR:
        return bool(getattr(m, "can_edit_messages", False))
    return False


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
            "SELECT user_id, channel_chat_id, channel_title, channel_username, autopost_enabled, "
            "channel_social_button_enabled "
            "FROM user_channel_autopost WHERE user_id = :u"
        ),
        {"u": int(internal_user_id)},
    )
    return dict(r) if r else None


async def autopost_extra_rows(internal_user_id: int) -> list[list[KeyboardButton]] | None:
    row = await _row_for_user(internal_user_id)
    if not row:
        return None
    ap = [[KeyboardButton(BTN_AUTOPOST_DISABLE if row.get("autopost_enabled") else BTN_AUTOPOST_ENABLE)]]
    soc = [
        [
            KeyboardButton(
                BTN_CH_SOC_OFF if row.get("channel_social_button_enabled") else BTN_CH_SOC_ON
            )
        ]
    ]
    return ap + soc


async def main_keyboard_with_autopost(site_url: str, ai_active: bool, internal_user_id: int):
    from services.referral_shop_prefs import tg_shop_button_label

    extras = await autopost_extra_rows(internal_user_id)
    shop_btn = await tg_shop_button_label(internal_user_id)
    return main_keyboard(site_url, ai_active, extra_rows=extras, shop_button=shop_btn)


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


async def ch_soc_btn_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data:
        return
    m = re.match(r"^ch_soc_btn:([01])$", q.data)
    if not m:
        return
    await q.answer()
    user = await ensure_user_or_blocked_reply(update)
    if not user:
        return
    val = m.group(1) == "1"
    row = await _row_for_user(int(user["id"]))
    if not row:
        await q.message.reply_text("Сначала подключите канал.")
        return
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
        ok_edit = await _verify_bot_can_edit_channel_messages(context.bot, int(row["channel_chat_id"]))
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
        f"Сохранено: кнопка «Войти в социальную сеть» под новыми постами в канале <b>{state}</b>.{warn}",
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


async def connect_channel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await ensure_user_or_blocked_reply(update)
    if not user or not update.message:
        return
    context.user_data["tg_ai_mode"] = False
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    existing = await _row_for_user(int(user["id"]))
    if existing:
        ch = existing.get("channel_title") or existing.get("channel_username") or "канал"
        soc = "включена" if existing.get("channel_social_button_enabled") else "выключена"
        ap = "включён" if existing.get("autopost_enabled") else "выключен"
        main_kb = await main_keyboard_with_autopost(site, False, int(user["id"]))
        await update.message.reply_html(
            f"Канал уже подключён: <b>{html.escape(str(ch))}</b>.\n"
            f"Автопост в ленту сообщества: <b>{ap}</b>.\n"
            f"Кнопка «Войти в социальную сеть» под новыми постами: <b>{soc}</b>.\n\n"
            "Нужно изменить кнопку под постами? Выберите:",
            reply_markup=_social_button_choice_markup(),
        )
        await update.message.reply_text("⌨️", reply_markup=main_kb)
        raise ApplicationHandlerStop

    context.user_data["channel_link_awaiting"] = True
    context.user_data.pop("channel_link_need_forward", None)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Я подвязал", callback_data="ch_link_done")]])
    await update.message.reply_html(
        build_link_instructions_html(),
        reply_markup=kb,
        disable_web_page_preview=True,
    )
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
            "При включённом автопосте новые посты канала (текст и фото) дублируются в ленту сообщества. "
            "Переключатель автопоста — внизу экрана.",
        )
        await _prompt_social_button_choice(q.message, context, payload)
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
            "✅ <b>Канал успешно подключён</b> (по пересланному сообщению).\n\n"
            "При включённом автопосте посты дублируются в ленту сообщества.",
        )
        await _prompt_social_button_choice(msg, context, payload)
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


async def toggle_channel_social_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    context.user_data["tg_ai_mode"] = False
    user = await ensure_user_or_blocked_reply(update)
    if not user:
        return
    row = await _row_for_user(int(user["id"]))
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    if not row:
        await update.message.reply_text(
            "Сначала подключите канал — кнопка «📢 Подключить свой канал».",
            reply_markup=main_keyboard(site, context.user_data.get("tg_ai_mode")),
        )
        raise ApplicationHandlerStop
    t = (update.message.text or "").strip()
    if t == BTN_CH_SOC_ON:
        new_val = True
    elif t == BTN_CH_SOC_OFF:
        new_val = False
    else:
        return
    try:
        await database.execute(
            sa.text(
                "UPDATE user_channel_autopost SET channel_social_button_enabled = :v, updated_at = NOW() "
                "WHERE user_id = :u"
            ),
            {"v": new_val, "u": int(user["id"])},
        )
    except Exception as e:
        logger.warning("toggle channel social btn: %s", e)
        await update.message.reply_text("Не удалось сохранить. Попробуйте позже.")
        raise ApplicationHandlerStop
    kb = await main_keyboard_with_autopost(site, context.user_data.get("tg_ai_mode"), int(user["id"]))
    warn = ""
    if new_val and not await _verify_bot_can_edit_channel_messages(
        context.bot, int(row["channel_chat_id"])
    ):
        warn = (
            "\n\n⚠️ Нужно право бота <b>изменять сообщения</b> в канале — иначе кнопка не появится под постами."
        )
    state = "включена" if new_val else "выключена"
    await update.message.reply_html(
        f"Кнопка «Войти в социальную сеть» под новыми постами в канале <b>{state}</b>.{warn}",
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
            "SELECT user_id, autopost_enabled, channel_social_button_enabled "
            "FROM user_channel_autopost WHERE channel_chat_id = :c LIMIT 1"
        ),
        {"c": chat_id},
    )
    if not link:
        return

    if link.get("channel_social_button_enabled"):
        await _attach_social_button_to_post(context.bot, chat_id, int(post.message_id))

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


_TOGGLE_PATTERN = re.compile(
    "^(" + re.escape(BTN_AUTOPOST_DISABLE) + "|" + re.escape(BTN_AUTOPOST_ENABLE) + ")$"
)
_CH_SOC_TOGGLE_PATTERN = re.compile(
    "^(" + re.escape(BTN_CH_SOC_ON) + "|" + re.escape(BTN_CH_SOC_OFF) + ")$"
)


def get_toggle_autopost_handler() -> MessageHandler:
    return MessageHandler(filters.Regex(_TOGGLE_PATTERN) & filters.ChatType.PRIVATE, toggle_autopost_handler)


def get_toggle_channel_social_button_handler() -> MessageHandler:
    return MessageHandler(
        filters.Regex(_CH_SOC_TOGGLE_PATTERN) & filters.ChatType.PRIVATE,
        toggle_channel_social_button_handler,
    )
