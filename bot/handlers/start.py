import logging
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

logger = logging.getLogger(__name__)

BLOCKED_BOT_MSG = (
    "🚫 Доступ к аккаунту ограничен администратором. "
    "Если это ошибка, напишите в поддержку проекта."
)

# Кнопки режима AI (главный бот): нейросеть только после явного нажатия
BTN_AI = "🤖 Задать вопрос AI"
BTN_AI_EXIT = "❌ Выйти из режима AI"
# Публикация поста в ленту (ConversationHandler; кнопка с клавиатуры убрана — вход только если снова добавят кнопку / команду)
BTN_COMMUNITY_POST = "📤 Пост в сообщество"
# Автопост из личного Telegram-канала в ленту сообщества
BTN_PARTNER = "🤝 Стать партнёром"
BTN_SUBSCRIBE = "💳 Подписка"
BTN_REFRESH_BOT = "🔄 Обновить бот"


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
        if row:
            try:
                from services.closed_telegram_access import sync_user_telegram_closed_chats

                await sync_user_telegram_closed_chats(int(dict(row)["id"]), notify_reentry=False)
            except Exception:
                logger.debug("sync closed tg after new tg user insert failed", exc_info=True)
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


async def sync_closed_telegram_after_bot_identity(user: dict) -> None:
    """После /start или привязки TG: ban/unban в закрытых чатах по текущей подписке и настройкам."""
    try:
        from services.closed_telegram_access import sync_user_telegram_closed_chats

        uid = int(user.get("primary_user_id") or user["id"])
        await sync_user_telegram_closed_chats(uid, notify_reentry=False)
    except Exception:
        logger.debug("sync_closed_telegram_after_bot_identity failed", exc_info=True)


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
    closed_tg_rows: list | None = None,
):
    """Клавиатура главного бота. Режим AI — отдельная строка: вход или выход."""
    shop_lbl = shop_button or TG_BTN_SHOP_MARKETPLACE
    ai_btn = BTN_AI_EXIT if ai_active else BTN_AI
    keyboard: list[list[KeyboardButton]] = [[KeyboardButton(ai_btn), KeyboardButton(shop_lbl)]]

    hub_btn: KeyboardButton | None = None
    other_closed: list[list[KeyboardButton]] = []
    if closed_tg_rows:
        for row in closed_tg_rows:
            if hub_btn is None and len(row) == 1:
                hub_btn = row[0]
            else:
                other_closed.append(list(row))
    for row in other_closed:
        keyboard.append(row)

    if hub_btn is not None:
        keyboard.append([hub_btn, KeyboardButton(BTN_PARTNER)])
        keyboard.append([KeyboardButton(BTN_SUBSCRIBE), KeyboardButton("🌍 Веб версия")])
        keyboard.append([KeyboardButton("🔒 Безопасность"), KeyboardButton(BTN_REFRESH_BOT)])
        keyboard.append([KeyboardButton("🆘 Тех. поддержка")])
    else:
        keyboard.append([KeyboardButton(BTN_PARTNER), KeyboardButton(BTN_SUBSCRIBE)])
        keyboard.append([KeyboardButton("🌍 Веб версия"), KeyboardButton("🔒 Безопасность")])
        keyboard.append([KeyboardButton("🆘 Тех. поддержка"), KeyboardButton(BTN_REFRESH_BOT)])

    if extra_rows:
        keyboard.extend(list(extra_rows))
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)


def main_inline_keyboard(site_url: str):
    app_url = site_url.strip().rstrip("/")
    if not app_url.startswith("http"):
        app_url = "https://" + app_url
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📱 Приложение", web_app=WebAppInfo(url=app_url + "/app")),
                InlineKeyboardButton("🌍 Сайт", url=app_url),
            ],
        ]
    )


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    user = await ensure_user_or_blocked_reply(update)
    if not user:
        return

    context.user_data["tg_ai_mode"] = False
    # Однократная подсказка в chat.py при первом тексте без режима AI после /start
    context.user_data["tg_ai_offline_hint_shown"] = False

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
        if ref_code in ("subscribe", "stars", "sub"):
            from bot.handlers.yookassa_subscribe import subscribe_menu_handler

            await sync_closed_telegram_after_bot_identity(user)
            await subscribe_menu_handler(update, context)
            return
        if ref_code.lower() == "chlink":
            from bot.handlers.channel_autopost import build_link_instructions_html

            context.user_data["channel_link_awaiting"] = True
            context.user_data.pop("channel_link_need_forward", None)
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("✅ Я подвязал", callback_data="ch_link_done")]]
            )
            await update.message.reply_html(
                build_link_instructions_html(),
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            return
        from services.closed_telegram_access import CLOSED_HUB_DEEPLINK_PREFIX

        _hp = CLOSED_HUB_DEEPLINK_PREFIX
        if len(ref_code) >= len(_hp) and ref_code[: len(_hp)].lower() == _hp.lower():
            inner = ref_code[len(_hp) :].strip()
            ucode = str(user.get("referral_code") or "").strip().upper()
            if inner and inner.upper() != ucode:
                from services.referral_service import process_referral

                await process_referral(user["id"], inner)
            try:
                from services.referral_service import apply_default_referrer_if_absent

                await apply_default_referrer_if_absent(int(user["id"]))
            except Exception:
                pass
            await sync_closed_telegram_after_bot_identity(user)
            from bot.handlers.closed_telegram import send_closed_telegram_hub_from_start

            await send_closed_telegram_hub_from_start(update, context, user)
            return
        if ref_code and ref_code != user.get("referral_code"):
            from services.referral_service import process_referral
            await process_referral(user["id"], ref_code)

    # Прямой вход t.me/bot без ?start= — закрепление за платформенным аккаунтом (как стандартная реф. ссылка)
    try:
        from services.referral_service import apply_default_referrer_if_absent

        await apply_default_referrer_if_absent(int(user["id"]))
    except Exception:
        pass

    await sync_closed_telegram_after_bot_identity(user)

    site_url = settings.SITE_URL

    welcome_text = (
        f"👋 Добро пожаловать в NEUROFUNGI AI, {tg_user.first_name}.\n\n"
        "Доступ ко всему по одной <b>ПОДПИСКЕ</b>:\n"
        "🌐 <b>Соцсеть</b> — посты, люди, связи.\n"
        "🔐 <b>Закрытый канал / группа / чат</b> — консультация, база знаний.\n"
        "🛍 <b>Магазин</b> — рейтинги, отзывы, карточки.\n"
        "🤖 <b>AI</b> — консультации, анализ, статистика.\n"
        "💼 <b>Партнёрка</b> — магазин от 10%.\n"
        "🎁 <b>Партнёрка соцсети</b> — 5%, 1 и 2 линия приглашённых.\n"
        "🏪 <b>Маркетплейс</b> — для вашего магазина.\n"
        "✨ И многое другое…\n\n"
        "⚡️ Нажмите <b>«Приложение»</b> (кнопка внизу или в меню чата) и заходите в сервис."
    )

    from bot.handlers.channel_autopost import main_keyboard_with_autopost

    site = (site_url or settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    await update.message.reply_text(
        welcome_text,
        reply_markup=await main_keyboard_with_autopost(site, False, int(user["id"])),
        parse_mode="HTML",
    )
    await update.message.reply_text(
        "👇 Приложение:",
        reply_markup=main_inline_keyboard(site),
        parse_mode="HTML",
    )
    support_msg = await update.message.reply_text(
        "🆘 <b>Служба поддержки</b>\nЕсли есть вопрос — напишите нам:",
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
