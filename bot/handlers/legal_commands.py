"""Команды /terms и /privacy ведут на единый блок: документы, концепция сервиса, реквизиты, кнопки оплаты."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers.legal_bundle import send_legal_bundle


async def terms_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_legal_bundle(update, context)


async def privacy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_legal_bundle(update, context)
