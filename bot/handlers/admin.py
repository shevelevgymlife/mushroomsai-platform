"""Команды администратора в боте: /status, /users."""
import os
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from config import settings
from db.database import database


def _is_admin(user_id: int) -> bool:
    return user_id == int(settings.ADMIN_TG_ID or 0)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        return

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
        db_status = f"❌ {str(e)[:80]}"

    await update.message.reply_text(
        f"📊 <b>Статус системы</b>\n\n"
        f"🖥 Сервис: <code>{svc}</code>\n"
        f"📦 Коммит: <code>{commit}</code>\n"
        f"🗄 БД: {db_status}\n\n"
        f"👤 Пользователей всего: {users_total}\n"
        f"👤 Новых за 24ч: {users_today}\n"
        f"🧾 Заказов за 24ч: {orders_today}\n\n"
        f"🕐 {ts}",
        parse_mode="HTML",
    )


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        return

    try:
        rows = await database.fetch_all(
            "SELECT id, name, tg_id, google_id, email, created_at "
            "FROM users ORDER BY created_at DESC LIMIT 10"
        )
        if not rows:
            await update.message.reply_text("Пользователей нет.")
            return
        lines = ["<b>Последние 10 пользователей:</b>\n"]
        for r in rows:
            via = "TG" if r["tg_id"] else ("G" if r["google_id"] else "email")
            lines.append(f"#{r['id']} {r['name'] or '—'} [{via}]")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")
