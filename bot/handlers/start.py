import secrets
import string
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from telegram.ext import ContextTypes
from db.database import database
from db.models import users
from config import settings


async def ensure_user(tg_user) -> dict:
    row = await database.fetch_one(users.select().where(users.c.tg_id == tg_user.id))
    if not row:
        referral_code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        user_id = await database.execute(
            users.insert().values(
                tg_id=tg_user.id,
                name=tg_user.full_name,
                avatar=tg_user.username,
                referral_code=referral_code,
                role="user",
            )
        )
        row = await database.fetch_one(users.select().where(users.c.id == user_id))
    return dict(row)


def main_keyboard(site_url: str):
    keyboard = [
        [KeyboardButton("Консультация"), KeyboardButton("Рецепты")],
        [KeyboardButton("Магазин"), KeyboardButton("О грибах")],
        [KeyboardButton("Тарифы и подписки"), KeyboardButton("Написать нам")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def main_inline_keyboard(site_url: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📱 Открыть приложение", web_app=WebAppInfo(url="https://mushroomsai.onrender.com")),
        ],
        [
            InlineKeyboardButton("Личный кабинет", url=f"{site_url}/dashboard"),
            InlineKeyboardButton("Открыть сайт", url=site_url),
        ],
    ])


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    user = await ensure_user(tg_user)

    # Check for referral
    if context.args:
        ref_code = context.args[0]
        if ref_code != user.get("referral_code"):
            referrer = await database.fetch_one(
                users.select().where(users.c.referral_code == ref_code)
            )
            if referrer and not user.get("referred_by"):
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
