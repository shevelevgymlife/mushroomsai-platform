"""Команды и кнопки администратора: /status, /users, callback admin:*"""
import os
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import settings
from db.database import database


def _is_admin(user_id: int) -> bool:
    return user_id == int(settings.ADMIN_TG_ID or 0)


def _admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Статус", callback_data="admin:status"),
            InlineKeyboardButton("👤 Пользователи", callback_data="admin:users"),
        ],
        [
            InlineKeyboardButton("💸 Реф. выводы", callback_data="admin:refwithdraw"),
        ],
    ])


async def _get_status_text() -> str:
    commit = (os.getenv("RENDER_GIT_COMMIT", "") or "")[:10] or "local"
    svc = os.getenv("RENDER_SERVICE_NAME", "dev")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    try:
        users_total = await database.fetch_val("SELECT COUNT(*) FROM users") or 0
        users_today = await database.fetch_val(
            "SELECT COUNT(*) FROM users WHERE created_at >= NOW() - INTERVAL '24 hours'"
        ) or 0
        orders_today = 0
        try:
            orders_today = await database.fetch_val(
                "SELECT COUNT(*) FROM shop_market_orders WHERE created_at >= NOW() - INTERVAL '24 hours'"
            ) or 0
        except Exception:
            pass
        db_status = "✅ OK"
    except Exception as e:
        users_total = users_today = orders_today = "—"
        db_status = f"❌ {str(e)[:60]}"

    return (
        f"📊 <b>Статус системы</b>\n\n"
        f"🖥 Сервис: <code>{svc}</code>\n"
        f"📦 Коммит: <code>{commit}</code>\n"
        f"🗄 БД: {db_status}\n\n"
        f"👤 Пользователей всего: {users_total}\n"
        f"👤 Новых за 24ч: {users_today}\n"
        f"🧾 Заказов за 24ч: {orders_today}\n\n"
        f"🕐 {ts}"
    )


async def _get_users_text() -> str:
    try:
        rows = await database.fetch_all(
            "SELECT id, name, tg_id, google_id, email, created_at "
            "FROM users ORDER BY created_at DESC LIMIT 10"
        )
        if not rows:
            return "Пользователей нет."
        lines = ["<b>Последние 10 пользователей:</b>\n"]
        for r in rows:
            via = "TG" if r["tg_id"] else ("G" if r["google_id"] else "email")
            lines.append(f"#{r['id']} {r['name'] or '—'} [{via}]")
        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка: {e}"


# ── Commands ────────────────────────────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        return
    text = await _get_status_text()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=_admin_keyboard())


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        return
    text = await _get_users_text()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=_admin_keyboard())


# ── Callbacks (кнопки) ──────────────────────────────────────────────────────

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not _is_admin(query.from_user.id):
        await query.answer("Нет доступа.", show_alert=True)
        return

    action = (query.data or "").replace("admin:", "")

    if action == "status":
        text = await _get_status_text()
    elif action == "users":
        text = await _get_users_text()
    elif action == "refwithdraw":
        from services.referral_admin import referral_finance_summary_html

        text = await referral_finance_summary_html(22)
        if len(text) > 3800:
            text = text[:3790] + "\n…"
    else:
        return

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=_admin_keyboard())
