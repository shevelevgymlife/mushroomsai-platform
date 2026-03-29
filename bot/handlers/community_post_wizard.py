"""Мастер публикации поста в ленту сообщества из главного Telegram-бота."""
from __future__ import annotations

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.handlers.start import BTN_COMMUNITY_POST, BTN_CONNECT_CHANNEL, ensure_user, main_keyboard
from bot.handlers.channel_autopost import (
    BTN_AUTOPOST_DISABLE,
    BTN_AUTOPOST_ENABLE,
    BTN_CH_SOC_OFF,
    BTN_CH_SOC_ON,
)
from config import settings
from services.channel_ingest_save_image import save_channel_ingest_image
from services.community_post_publish import publish_community_post
from services.telegram_file_download import download_telegram_file_bytes

logger = logging.getLogger(__name__)

CP_TITLE = 1
CP_BODY = 2
CP_PHOTO = 3
CP_CONFIRM = 4

# Тексты кнопок нижней клавиатуры — в мастере поста не считаем их вводом шага
_WIZARD_BLOCK_TEXTS = frozenset(
    {
        "🤖 Задать вопрос AI",
        "❌ Выйти из режима AI",
        "📤 Пост в сообщество",
        BTN_CONNECT_CHANNEL,
        BTN_AUTOPOST_DISABLE,
        BTN_AUTOPOST_ENABLE,
        BTN_CH_SOC_ON,
        BTN_CH_SOC_OFF,
        "🛍 Маркет плейс",
        "🌐 Сообщество",
        "🌍 Веб версия",
        "🔒 Безопасность",
        "🆘 Тех. поддержка",
    }
)


def _esc(s: str) -> str:
    return html.escape((s or "").strip(), quote=False)


def _clear_draft(context: ContextTypes.DEFAULT_TYPE) -> None:
    for k in (
        "cp_title",
        "cp_body",
        "cp_image_url",
        "cp_author_id",
        "cp_author_name",
        "cp_post_wizard",
    ):
        context.user_data.pop(k, None)


def _wizard_exit_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🏠 Выйти в общий режим", callback_data="cp_exit_main")]]
    )


def _kb_photo_step() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🚫 Без фото", callback_data="cp_skip_photo")],
            [InlineKeyboardButton("🏠 Выйти в общий режим", callback_data="cp_exit_main")],
        ]
    )


def _kb_confirm_step() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Отправить", callback_data="cp_send")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cp_cancel")],
            [InlineKeyboardButton("🏠 Выйти в общий режим", callback_data="cp_exit_main")],
        ]
    )


async def _reject_menu_button_during_wizard(
    update: Update,
    reply_markup: InlineKeyboardMarkup,
) -> bool:
    if not update.message:
        return False
    t = (update.message.text or "").strip()
    if t not in _WIZARD_BLOCK_TEXTS:
        return False
    await update.message.reply_text(
        "Сейчас активен только режим «Пост в сообщество». Завершите шаги, нажмите «🏠 Выйти в общий режим» или /cancel.",
        reply_markup=reply_markup,
    )
    return True


def _preview_caption(title: str, body: str) -> str:
    """Текст предпросмотра; обрезка под лимит подписи к фото (1024) в Telegram."""
    t = (title or "").strip()
    if len(t) > 160:
        t = t[:159] + "…"
    head = f"👁 <b>Предпросмотр</b>\n\n<b>{_esc(t)}</b>\n\n"
    foot = "\n\nОтправить пост в ленту сообщества?"
    raw = (body or "").strip()
    max_body = min(650, 1024 - len(head) - len(foot) - 80)
    if max_body < 120:
        max_body = 120
    if len(raw) > max_body:
        raw = raw[: max_body - 1] + "…"
    return head + _esc(raw) + foot


async def start_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["tg_ai_mode"] = False
    tg_user = update.effective_user
    if not tg_user or not update.message:
        return ConversationHandler.END

    row = await ensure_user(tg_user)
    if not row:
        await update.message.reply_text(
            "🚫 Не удалось продолжить. Откройте приложение через /start или обратитесь в поддержку."
        )
        return ConversationHandler.END

    _clear_draft(context)
    context.user_data["cp_author_id"] = int(row.get("primary_user_id") or row["id"])
    context.user_data["cp_author_name"] = (row.get("name") or "").strip() or "Участник"
    context.user_data["cp_post_wizard"] = True

    await update.message.reply_text(
        "📝 <b>Публикация в сообщество</b>\n\n"
        "Сейчас активен <b>только этот режим</b>: сообщения не уходят в нейросеть и не обрабатываются как обычный чат. "
        "Вопросы AI — после выхода и кнопки «🤖 Задать вопрос AI».\n\n"
        "Шаг 1 из 3: пришлите <b>название</b> поста одним сообщением.\n\n"
        "/cancel — отменить публикацию.",
        parse_mode="HTML",
        reply_markup=_wizard_exit_markup(),
    )
    return CP_TITLE


async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return CP_TITLE
    if await _reject_menu_button_during_wizard(update, _wizard_exit_markup()):
        return CP_TITLE
    t = (update.message.text or "").strip()
    if len(t) < 1:
        await update.message.reply_text(
            "Нужно непустое название. Пришлите текст или /cancel.",
            reply_markup=_wizard_exit_markup(),
        )
        return CP_TITLE
    if len(t) > 200:
        t = t[:200]
    context.user_data["cp_title"] = t
    await update.message.reply_text(
        "Шаг 2 из 3: пришлите <b>текст поста</b> (описание).\n\n"
        "Минимум 2 символа.\n/cancel — отменить.",
        parse_mode="HTML",
        reply_markup=_wizard_exit_markup(),
    )
    return CP_BODY


async def receive_body(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return CP_BODY
    if await _reject_menu_button_during_wizard(update, _wizard_exit_markup()):
        return CP_BODY
    t = (update.message.text or "").strip()
    if len(t) < 2:
        await update.message.reply_text(
            "Текст слишком короткий. Нужно минимум 2 символа.",
            reply_markup=_wizard_exit_markup(),
        )
        return CP_BODY
    context.user_data["cp_body"] = t
    await update.message.reply_text(
        "Шаг 3 из 3: пришлите <b>фото</b> для поста (как фото).\n\n"
        "Или нажмите «Без фото», если картинка не нужна.",
        parse_mode="HTML",
        reply_markup=_kb_photo_step(),
    )
    return CP_PHOTO


async def wrong_in_photo_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        if await _reject_menu_button_during_wizard(update, _kb_photo_step()):
            return CP_PHOTO
        await update.message.reply_text(
            "Ожидается фото. Отправьте изображение как «Фото» или кнопки ниже.",
            reply_markup=_kb_photo_step(),
        )
    return CP_PHOTO


async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return CP_PHOTO
    token = (settings.TELEGRAM_TOKEN or "").strip()
    photos = update.message.photo
    if not photos:
        return await wrong_in_photo_step(update, context)
    best = max(photos, key=lambda p: (p.width or 0) * (p.height or 0))
    raw = await download_telegram_file_bytes(token, best.file_id)
    if not raw:
        await update.message.reply_text(
            "Не удалось скачать фото. Попробуйте другое или «Без фото».",
            reply_markup=_kb_photo_step(),
        )
        return CP_PHOTO
    url = save_channel_ingest_image(raw)
    if not url:
        await update.message.reply_text(
            "Файл слишком большой или формат не подходит (до 8 МБ).",
            reply_markup=_kb_photo_step(),
        )
        return CP_PHOTO
    context.user_data["cp_image_url"] = url
    await _reply_preview(update.message, context)
    return CP_CONFIRM


async def skip_photo_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()
    context.user_data.pop("cp_image_url", None)
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    chat = q.message.chat
    cap = _preview_caption(context.user_data.get("cp_title") or "", context.user_data.get("cp_body") or "")
    await context.bot.send_message(chat_id=chat.id, text=cap, parse_mode="HTML", reply_markup=_kb_confirm_step())
    return CP_CONFIRM


async def _reply_preview(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    title = context.user_data.get("cp_title") or ""
    body = context.user_data.get("cp_body") or ""
    img = context.user_data.get("cp_image_url")
    cap = _preview_caption(title, body)
    kb = _kb_confirm_step()
    site = (settings.SITE_URL or "").rstrip("/")
    if img:
        full = site + img if img.startswith("/") else img
        try:
            await message.reply_photo(photo=full, caption=cap, parse_mode="HTML", reply_markup=kb)
            return
        except Exception as e:
            logger.warning("reply_photo preview failed: %s", e)
    await message.reply_text(cap, parse_mode="HTML", reply_markup=kb)


async def send_post_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q or not q.message:
        return ConversationHandler.END
    await q.answer()
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    kb_reply = main_keyboard(site, ai_active=bool(context.user_data.get("tg_ai_mode")))

    uid = context.user_data.get("cp_author_id")
    name = context.user_data.get("cp_author_name") or "Участник"
    title = context.user_data.get("cp_title")
    body = context.user_data.get("cp_body")
    img = context.user_data.get("cp_image_url")

    if not uid or body is None:
        await q.message.reply_text("Черновик потерян. Начните снова: «📤 Пост в сообщество».", reply_markup=kb_reply)
        _clear_draft(context)
        return ConversationHandler.END

    post_id = await publish_community_post(
        user_id=int(uid),
        author_name=str(name),
        content=str(body),
        title=str(title) if title else None,
        image_url=str(img) if img else None,
        from_telegram=True,
    )

    if not post_id:
        await q.message.reply_text(
            "Не удалось опубликовать (проверьте длину текста или попробуйте через приложение).",
            reply_markup=kb_reply,
        )
    else:
        await q.message.reply_text(
            f"✅ <b>Пост опубликован</b> в вашей ленте сообщества.\n\n"
            f'<a href="{site}/community/post/{post_id}">Открыть пост</a>',
            parse_mode="HTML",
            reply_markup=kb_reply,
        )
    _clear_draft(context)
    return ConversationHandler.END


async def exit_to_main_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer("Выход в общий режим")
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    context.user_data["tg_ai_mode"] = False
    _clear_draft(context)
    msg = q.message
    if msg:
        await msg.reply_text(
            "Вы вышли из режима «Пост в сообщество». Снова работают кнопки главного меню; вопросы в AI — только после «🤖 Задать вопрос AI».",
            reply_markup=main_keyboard(site, ai_active=False),
        )
    return ConversationHandler.END


async def cp_confirm_stray_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return CP_CONFIRM
    if await _reject_menu_button_during_wizard(update, _kb_confirm_step()):
        return CP_CONFIRM
    await update.message.reply_text(
        "Сейчас нужно нажать кнопку под предпросмотром: «✅ Отправить», «❌ Отмена» или «🏠 Выйти в общий режим».",
        reply_markup=_kb_confirm_step(),
    )
    return CP_CONFIRM


async def cancel_post_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q:
        await q.answer()
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        msg = q.message
    else:
        msg = None

    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    context.user_data["tg_ai_mode"] = False
    _clear_draft(context)
    kb = main_keyboard(site, ai_active=False)
    if msg:
        await msg.reply_text("Публикация отменена.", reply_markup=kb)
    return ConversationHandler.END


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    context.user_data["tg_ai_mode"] = False
    _clear_draft(context)
    if update.message:
        await update.message.reply_text("Отменено.", reply_markup=main_keyboard(site, ai_active=False))
    return ConversationHandler.END


def get_community_post_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{BTN_COMMUNITY_POST}$"), start_wizard)],
        states={
            CP_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_title)],
            CP_BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_body)],
            CP_PHOTO: [
                MessageHandler(filters.PHOTO, receive_photo),
                CallbackQueryHandler(skip_photo_cb, pattern=r"^cp_skip_photo$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, wrong_in_photo_step),
            ],
            CP_CONFIRM: [
                CallbackQueryHandler(send_post_cb, pattern=r"^cp_send$"),
                CallbackQueryHandler(cancel_post_cb, pattern=r"^cp_cancel$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cp_confirm_stray_text),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_cmd),
            CallbackQueryHandler(exit_to_main_cb, pattern=r"^cp_exit_main$"),
        ],
        per_user=True,
        per_chat=True,
        allow_reentry=True,
        name="community_post_wizard",
    )
