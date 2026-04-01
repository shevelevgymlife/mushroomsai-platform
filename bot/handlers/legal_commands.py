"""Команды /terms (оферта) и /privacy — ссылки на страницы сайта."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import settings


def _site_base() -> str:
    return (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")


async def terms_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать ссылку на публичную оферту (/legal/offer)."""
    if not update.message:
        return
    site = _site_base()
    await update.message.reply_text(
        "📄 <b>Публичная оферта</b>\n\n"
        "Условия платной подписки и доступа к сервису — на сайте.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Открыть оферту", url=f"{site}/legal/offer")]]
        ),
        disable_web_page_preview=True,
    )


async def privacy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    site = _site_base()
    await update.message.reply_text(
        "🔒 <b>Политика конфиденциальности</b>\n\n"
        "Обработка данных (Telegram, Google, платежи, cookie) — на сайте.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Открыть политику", url=f"{site}/legal/privacy")]]
        ),
        disable_web_page_preview=True,
    )
