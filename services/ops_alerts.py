"""Ops notifications hub for system bot.

Covers 6 blocks:
1) infra/deploy
2) billing
3) payments/orders
4) operations (feedback/questions/support)
5) security
6) daily summary
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa

from config import settings
from db.database import database
from db.models import (
    feedback,
    product_questions,
    shop_market_orders,
    users,
)
from services.task_notify import notify_status


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _safe_name(row: dict | None) -> str:
    if not row:
        return "Пользователь"
    return str(row.get("name") or "").strip() or "Пользователь"


def _site() -> str:
    return (settings.SITE_URL or "https://mushroomsai.ru").strip()


def _route() -> str:
    # Web links for quick action in alerts.
    return f"{_site()}/admin"


async def _notify(stage: str, summary: str, details: str = "") -> None:
    from services.tg_notify import tg_send
    await notify_status(stage=stage, summary=summary, details=details, include_email=False)
    # Telegram дублируется через notify_status → tg_send, но для кастомных событий вызываем явно


# ──────────────────────────────────────────────────────────────────────────────
# 1) INFRA / DEPLOY
# ──────────────────────────────────────────────────────────────────────────────
async def notify_infra_event(kind: str, text: str, details: str = "") -> None:
    icon = "🖥️"
    if kind == "down":
        icon = "🔴"
    elif kind == "warning":
        icon = "🟠"
    await _notify(
        stage="task_done",
        summary=f"{icon} Infra: {text}",
        details=details,
    )


# ──────────────────────────────────────────────────────────────────────────────
# 2) BILLING
# ──────────────────────────────────────────────────────────────────────────────
async def maybe_notify_billing() -> None:
    due_raw = (settings.OPS_NOTIFY_BILLING_DUE_AT or "").strip()
    cur = float(getattr(settings, "OPS_NOTIFY_BILLING_CURRENT_USD", 0.0) or 0.0)
    lim = float(getattr(settings, "OPS_NOTIFY_BILLING_LIMIT_USD", 0.0) or 0.0)
    warn_pct = int(getattr(settings, "OPS_NOTIFY_BILLING_WARN_PERCENT", 90) or 90)

    if due_raw:
        try:
            due = datetime.strptime(due_raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days_left = (due.date() - _now_utc().date()).days
            if 0 <= days_left <= 3:
                await _notify(
                    stage="task_done",
                    summary=f"💳 Платеж по инфраструктуре через {days_left} дн.",
                    details=f"Дата платежа: {due_raw}\nПроверьте Render/карты.\n{_route()}",
                )
        except Exception:
            pass

    if lim > 0:
        pct = (cur / lim) * 100.0
        if pct >= max(1, warn_pct):
            await _notify(
                stage="task_done",
                summary=f"💸 Расходы {pct:.1f}% от лимита",
                details=f"Текущие: ${cur:.2f}\nЛимит: ${lim:.2f}\n{_route()}",
            )


# ──────────────────────────────────────────────────────────────────────────────
# 3) PAYMENTS / ORDERS
# ──────────────────────────────────────────────────────────────────────────────
async def notify_new_order(order_id: int, user_id: int, total_amount: int) -> None:
    u = await database.fetch_one(users.select().where(users.c.id == user_id))
    await _notify(
        stage="task_done",
        summary=f"🧾 Новый заказ #{order_id}",
        details=(
            f"Покупатель: {_safe_name(dict(u) if u else None)} (id {user_id})\n"
            f"Сумма: {int(total_amount or 0)} ₽\n"
            f"Проверьте заказы: {_site()}/admin/shop"
        ),
    )


# ──────────────────────────────────────────────────────────────────────────────
# 4) OPERATIONS
# ──────────────────────────────────────────────────────────────────────────────
async def notify_new_feedback(text: str, user_label: str = "Гость") -> None:
    await _notify(
        stage="task_done",
        summary="📬 Новая обратная связь",
        details=f"От: {user_label}\nТекст: {(text or '').strip()[:700]}\n{_site()}/admin/feedback",
    )


async def notify_plan_upgrade_request(user_id: int, requested_plan: str, note: str = "") -> None:
    u = await database.fetch_one(users.select().where(users.c.id == user_id))
    await _notify(
        stage="task_done",
        summary=f"📋 Запрос смены тарифа → {requested_plan}",
        details=(
            f"Пользователь: {_safe_name(dict(u) if u else None)} (id {user_id})\n"
            f"Комментарий: {(note or '—')[:700]}\n"
            f"{_route()}"
        ),
    )


async def notify_product_question(product_id: int, question_text: str, user_id: int | None = None) -> None:
    who = f"id {user_id}" if user_id else "неизвестный пользователь"
    await _notify(
        stage="task_done",
        summary=f"❓ Новый вопрос по товару #{product_id}",
        details=f"Кто: {who}\nВопрос: {(question_text or '').strip()[:700]}\n{_site()}/admin/shop",
    )


# ──────────────────────────────────────────────────────────────────────────────
# 5) SECURITY
# ──────────────────────────────────────────────────────────────────────────────
async def notify_security_event(event: str, details: str = "") -> None:
    await _notify(
        stage="task_done",
        summary=f"🔐 Security: {event}",
        details=details[:1200],
    )


# ──────────────────────────────────────────────────────────────────────────────
# 6) DAILY SUMMARY
# ──────────────────────────────────────────────────────────────────────────────
async def send_daily_summary() -> None:
    now = _now_utc()
    day_ago = now - timedelta(days=1)
    hour_ago = now - timedelta(hours=1)

    users_new = (
        await database.fetch_val(
            sa.text("SELECT COUNT(*) FROM users WHERE created_at >= :ts").bindparams(ts=day_ago)
        )
        or 0
    )
    orders_new = (
        await database.fetch_val(
            sa.text("SELECT COUNT(*) FROM shop_market_orders WHERE created_at >= :ts").bindparams(ts=day_ago)
        )
        or 0
    )
    orders_amount = (
        await database.fetch_val(
            sa.text(
                "SELECT COALESCE(SUM(total_amount),0) FROM shop_market_orders WHERE created_at >= :ts"
            ).bindparams(ts=day_ago)
        )
        or 0
    )
    feedback_new = (
        await database.fetch_val(
            sa.text("SELECT COUNT(*) FROM feedback WHERE created_at >= :ts").bindparams(ts=day_ago)
        )
        or 0
    )
    questions_new = (
        await database.fetch_val(
            sa.text("SELECT COUNT(*) FROM product_questions WHERE created_at >= :ts").bindparams(ts=day_ago)
        )
        or 0
    )
    banned_count = (
        await database.fetch_val(
            sa.text("SELECT COUNT(*) FROM users WHERE is_banned = true")
        )
        or 0
    )
    recent_errors = (
        await database.fetch_val(
            sa.text("SELECT COUNT(*) FROM feedback WHERE status='new' AND created_at >= :ts").bindparams(ts=hour_ago)
        )
        or 0
    )

    await _notify(
        stage="task_done",
        summary="📊 Daily summary (24h)",
        details=(
            f"Новые пользователи: {int(users_new)}\n"
            f"Новые заказы: {int(orders_new)}\n"
            f"Сумма заказов: {int(orders_amount)} ₽\n"
            f"Новые feedback: {int(feedback_new)}\n"
            f"Новые вопросы по товарам: {int(questions_new)}\n"
            f"Заблокированных аккаунтов: {int(banned_count)}\n"
            f"Новых нерешенных feedback за 1ч: {int(recent_errors)}\n"
            f"Сформировано: {_fmt_dt(now)}"
        ),
    )

