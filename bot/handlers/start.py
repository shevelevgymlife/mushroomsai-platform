import secrets
import string
from datetime import datetime
import sqlalchemy as sa
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from telegram.ext import ContextTypes
from db.database import database
from db.models import users
from config import settings
from services.referral_shop_prefs import TG_BTN_SHOP_MARKETPLACE

BLOCKED_BOT_MSG = (
    "🚫 Доступ к аккаунту ограничен администратором. "
    "Если это ошибка, напишите в поддержку проекта."
)

# Кнопки режима AI (главный бот): нейросеть только после явного нажатия
BTN_AI = "🤖 Задать вопрос AI"
BTN_AI_EXIT = "❌ Выйти из режима AI"
# Публикация поста в ленту сообщества (ConversationHandler в main_bot)
BTN_COMMUNITY_POST = "📤 Пост в сообщество"
# Автопост из личного Telegram-канала в ленту сообщества
BTN_CONNECT_CHANNEL = "📢 Подключить свой канал"
BTN_PARTNER = "🤝 Стать партнёром"


async def ensure_user(tg_user) -> dict | None:
    from auth.blocked_identities import is_identity_blocked, login_denied_for_user_row

    tid = str(int(tg_user.id))
    if await is_identity_blocked("tg_id", tid):
        return None

    row = await database.fetch_one(
        users.select().where(
            sa.or_(users.c.tg_id == tg_user.id, users.c.linked_tg_id == tg_user.id)
        )
    )
    if not row:
        referral_code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        await database.execute(
            users.insert().values(
                tg_id=tg_user.id,
                linked_tg_id=tg_user.id,
                name=tg_user.full_name,
                avatar=tg_user.username,
                referral_code=referral_code,
                role="user",
            )
        )
        row = await database.fetch_one(
            users.select().where(
                sa.or_(users.c.tg_id == tg_user.id, users.c.linked_tg_id == tg_user.id)
            )
        )
    else:
        # Backfill canonical tg identifiers on resolved/legacy linked accounts.
        base_row = dict(row)
        user_id = int(base_row.get("primary_user_id") or base_row["id"])
        if user_id != int(base_row["id"]):
            primary = await database.fetch_one(users.select().where(users.c.id == user_id))
            if primary:
                row = primary
        await database.execute(
            users.update().where(users.c.id == user_id).values(
                tg_id=tg_user.id,
                linked_tg_id=tg_user.id,
            )
        )
        row = await database.fetch_one(users.select().where(users.c.id == user_id))
    u = dict(row)
    if await login_denied_for_user_row(u):
        return None
    return u


async def ensure_user_or_blocked_reply(update: Update) -> dict | None:
    """Как ensure_user; при блокировке отвечает пользователю и возвращает None."""
    tg = update.effective_user
    if not tg:
        return None
    u = await ensure_user(tg)
    if u:
        return u
    if update.message:
        await update.message.reply_text(BLOCKED_BOT_MSG)
    elif update.callback_query:
        try:
            await update.callback_query.answer(BLOCKED_BOT_MSG, show_alert=True)
        except Exception:
            pass
    return None


def main_keyboard(
    site_url: str,
    ai_active: bool = False,
    extra_rows: list | None = None,
    shop_button: str | None = None,
    show_community_post: bool = False,
):
    """Клавиатура главного бота. Режим AI — отдельная строка: вход или выход.
    «Пост в сообщество» — только при show_community_post (роль admin)."""
    shop_lbl = shop_button or TG_BTN_SHOP_MARKETPLACE
    if ai_active:
        top = [[KeyboardButton(BTN_AI_EXIT)]]
    else:
        top = [[KeyboardButton(BTN_AI)]]
    keyboard = top + [
        [KeyboardButton(shop_lbl), KeyboardButton("🌐 Сообщество")],
    ]
    if show_community_post:
        keyboard.append([KeyboardButton(BTN_COMMUNITY_POST)])
    keyboard += [
        [KeyboardButton(BTN_CONNECT_CHANNEL)],
        [KeyboardButton(BTN_PARTNER)],
        [KeyboardButton("🌍 Веб версия"), KeyboardButton("🔒 Безопасность")],
        [KeyboardButton("🆘 Тех. поддержка")],
    ]
    if extra_rows:
        keyboard = keyboard + list(extra_rows)
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)


def main_inline_keyboard(site_url: str):
    app_url = site_url.strip().rstrip("/")
    if not app_url.startswith("http"):
        app_url = "https://" + app_url
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🍄 Открыть приложение", web_app=WebAppInfo(url=app_url + "/app")),
        ],
    ])


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    user = await ensure_user_or_blocked_reply(update)
    if not user:
        return

    context.user_data["tg_ai_mode"] = False

    if context.args:
        ref_code = context.args[0]
        if ref_code.startswith("link_"):
            token = ref_code[len("link_"):].strip()
            row = await database.fetch_one(users.select().where(users.c.link_token == token))
            if not row:
                await update.message.reply_text("Ссылка недействительна или уже использована.")
                return
            expires = row.get("link_token_expires")
            if expires and datetime.utcnow() > expires:
                await update.message.reply_text("Срок действия ссылки истёк. Сгенерируйте новую на сайте.")
                return
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("✅ Подтвердить привязку", callback_data=f"link_confirm:{token}")],
                    [InlineKeyboardButton("❌ Отмена", callback_data=f"link_cancel:{token}")],
                ]
            )
            await update.message.reply_text(
                "Подтвердите привязку Telegram к вашему аккаунту на сайте.",
                reply_markup=kb,
            )
            return
        if ref_code and ref_code != user.get("referral_code"):
            from services.referral_service import process_referral
            await process_referral(user["id"], ref_code)

    site_url = settings.SITE_URL

    welcome_text = (
        f"👋 Добро пожаловать в комьюнити NEUROFUNGI AI, {tg_user.first_name}!\n\n"
        "Здесь вы найдёте:\n"
        "• Персональные консультации по функциональным грибам (кнопка <b>«🤖 Задать вопрос AI»</b> — только после неё сообщения уходят в нейросеть)\n"
        "• Сообщество единомышленников\n"
        "• Маркет плейс и рецепты\n\n"
        "Нажмите кнопку <b>«Вход»</b> внизу экрана, чтобы открыть приложение.\n\n"
        "⚠️ <i>Если кнопка «Вход» не отображается — обновите Telegram до последней версии "
        "или обратитесь в службу поддержки.</i>"
    )

    from bot.handlers.channel_autopost import main_keyboard_with_autopost

    site = (site_url or settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    await update.message.reply_text(
        welcome_text,
        reply_markup=await main_keyboard_with_autopost(site, False, int(user["id"])),
        parse_mode="HTML",
    )
    await update.message.reply_text(
        "👇 Нажмите, чтобы открыть приложение:",
        reply_markup=main_inline_keyboard(site),
        parse_mode="HTML",
    )
    support_msg = await update.message.reply_text(
        "🆘 <b>Служба поддержки</b>\nЕсли у вас возникли вопросы — напишите нам:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🆘 Написать в поддержку", callback_data="support")],
        ]),
        parse_mode="HTML",
    )
    # Закрепляем сообщение — оно всегда видно вверху чата
    try:
        await context.bot.pin_chat_message(
            chat_id=update.effective_chat.id,
            message_id=support_msg.message_id,
            disable_notification=True,
        )
    except Exception:
        pass  # Нет прав закрепить — не критично


# Алиас для совместимости с импортом в main_bot.py
start = start_handler
