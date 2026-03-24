import secrets
import string
import sqlalchemy as sa
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from telegram.ext import ContextTypes
from db.database import database
from db.models import users
from config import settings

BLOCKED_BOT_MSG = (
    "🚫 Доступ к аккаунту ограничен администратором. "
    "Если это ошибка, напишите в поддержку проекта."
)


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
        user_id = await database.execute(
            users.insert().values(
                tg_id=tg_user.id,
                linked_tg_id=tg_user.id,
                name=tg_user.full_name,
                avatar=tg_user.username,
                referral_code=referral_code,
                role="user",
            )
        )
        row = await database.fetch_one(users.select().where(users.c.id == user_id))
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


def main_keyboard(site_url: str):
    keyboard = [
        [KeyboardButton("Консультация"), KeyboardButton("Рецепты")],
        [KeyboardButton("Магазин"), KeyboardButton("О грибах")],
        [KeyboardButton("Сообщество"), KeyboardButton("Тарифы и подписки")],
        [KeyboardButton("Написать нам")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def main_inline_keyboard(site_url: str):
    app_url = site_url.strip().rstrip("/")
    if not app_url.startswith("http"):
        app_url = "https://" + app_url
    app_url = app_url + "/"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("MushroomsAI — приложение", web_app=WebAppInfo(url=app_url)),
        ],
        [
            InlineKeyboardButton("Личный кабинет", url=f"{site_url}/dashboard"),
            InlineKeyboardButton("Открыть сайт", url=site_url),
        ],
    ])


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    user = await ensure_user_or_blocked_reply(update)
    if not user:
        return

    if context.args:
        ref_code = context.args[0]
        if ref_code and ref_code != user.get("referral_code"):
            from services.referral_service import process_referral
            await process_referral(user["id"], ref_code)

    site_url = settings.SITE_URL

    welcome_text = (
        f"Добрый день, {tg_user.first_name}.\n\n"
        "Я — AI-консультант Евгения Шевелева, эксперта по фунготерапии и психолога.\n\n"
        "Помогу вам подобрать функциональные грибы для улучшения здоровья, "
        "составить персональный протокол и ответить на вопросы.\n\n"
        "Выберите раздел:"
    )

    await update.message.reply_text(
        welcome_text,
        reply_markup=main_keyboard(site_url),
    )
    await update.message.reply_text(
        "Также доступны:",
        reply_markup=main_inline_keyboard(site_url),
        parse_mode="HTML",
    )
