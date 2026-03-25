"""Отправка уведомлений администратору в Telegram через Bot API (httpx, без зависимости от running bot)."""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

_MAX_LEN = 4000


def _token() -> str:
    return (settings.TELEGRAM_TOKEN or "").strip()


def _admin_id() -> int:
    return int(settings.ADMIN_TG_ID or 0)


def _render_service() -> str:
    return os.getenv("RENDER_SERVICE_NAME", "mushroomsai")


def _render_commit() -> str:
    return (os.getenv("RENDER_GIT_COMMIT", "") or "")[:10] or "—"


async def tg_send(text: str, parse_mode: str = "HTML") -> bool:
    """Отправить сообщение администратору. Возвращает True при успехе."""
    token = _token()
    chat_id = _admin_id()
    if not token or not chat_id:
        return False
    text = (text or "").strip()[:_MAX_LEN]
    if not text:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode,
                      "disable_web_page_preview": True},
            )
            if r.status_code != 200:
                logger.warning("tg_send failed: %s %s", r.status_code, r.text[:200])
                return False
        return True
    except Exception as e:
        logger.warning("tg_send exception: %s", e)
        return False


# ── Готовые шаблоны ────────────────────────────────────────────────────────

async def notify_deploy_start() -> None:
    commit = _render_commit()
    svc = _render_service()
    await tg_send(
        f"🔵 <b>Деплой начался</b>\n"
        f"Сервис: <code>{svc}</code>\n"
        f"Коммит: <code>{commit}</code>\n"
        f"Сайт: {settings.SITE_URL}"
    )


async def notify_deploy_ok() -> None:
    commit = _render_commit()
    svc = _render_service()
    await tg_send(
        f"🟢 <b>Деплой успешен</b>\n"
        f"Сервис: <code>{svc}</code>\n"
        f"Коммит: <code>{commit}</code>\n"
        f"Сайт: {settings.SITE_URL}"
    )


async def notify_deploy_fail(reason: str = "") -> None:
    commit = _render_commit()
    svc = _render_service()
    await tg_send(
        f"🔴 <b>Деплой упал!</b>\n"
        f"Сервис: <code>{svc}</code>\n"
        f"Коммит: <code>{commit}</code>\n"
        f"Причина: {(reason or '—')[:400]}"
    )


async def notify_new_user(user_id: int, name: str, method: str = "") -> None:
    via = f" (через {method})" if method else ""
    await tg_send(
        f"👤 <b>Новый пользователь{via}</b>\n"
        f"ID: <code>{user_id}</code>  Имя: {name or '—'}\n"
        f"<a href='{settings.SITE_URL}/admin/users'>Открыть в админке</a>"
    )


async def notify_new_order(order_id: int, user_name: str, amount: int) -> None:
    await tg_send(
        f"🧾 <b>Новый заказ #{order_id}</b>\n"
        f"Покупатель: {user_name or '—'}\n"
        f"Сумма: {amount} ₽\n"
        f"<a href='{settings.SITE_URL}/admin/shop'>Открыть заказы</a>"
    )


async def notify_new_feedback(text: str, user_label: str = "Гость") -> None:
    await tg_send(
        f"📬 <b>Обратная связь</b>\n"
        f"От: {user_label}\n"
        f"<i>{(text or '').strip()[:600]}</i>\n"
        f"<a href='{settings.SITE_URL}/admin/feedback'>Открыть</a>"
    )


async def notify_plan_request(user_id: int, user_name: str, plan: str, note: str = "") -> None:
    await tg_send(
        f"📋 <b>Запрос тарифа → {plan}</b>\n"
        f"Пользователь: {user_name} (id {user_id})\n"
        f"Комментарий: {(note or '—')[:400]}\n"
        f"<a href='{settings.SITE_URL}/admin/users'>Открыть</a>"
    )


async def notify_security(event: str, details: str = "") -> None:
    await tg_send(
        f"🔐 <b>Безопасность: {event}</b>\n"
        f"{(details or '').strip()[:600]}"
    )


async def notify_error(title: str, details: str = "") -> None:
    await tg_send(
        f"❗ <b>Ошибка: {title}</b>\n"
        f"{(details or '').strip()[:600]}"
    )


async def notify_billing_warn(current_usd: float, limit_usd: float, pct: float) -> None:
    await tg_send(
        f"💸 <b>Расходы {pct:.0f}% от лимита</b>\n"
        f"Текущие: ${current_usd:.2f}\n"
        f"Лимит: ${limit_usd:.2f}\n"
        f"Проверьте Render Billing."
    )


async def notify_payment_due(due_date: str, days_left: int) -> None:
    await tg_send(
        f"💳 <b>Платёж через {days_left} дн.</b>\n"
        f"Дата: {due_date}\n"
        f"Проверьте карту и баланс."
    )


async def notify_daily_summary(
    users_new: int, orders_new: int, orders_amount: int,
    feedback_new: int, questions_new: int
) -> None:
    await tg_send(
        f"📊 <b>Сводка за 24ч</b>\n"
        f"👤 Новых пользователей: {users_new}\n"
        f"🧾 Новых заказов: {orders_new} (на {orders_amount} ₽)\n"
        f"📬 Обратная связь: {feedback_new}\n"
        f"❓ Вопросов по товарам: {questions_new}\n"
        f"<a href='{settings.SITE_URL}/admin'>Открыть админку</a>"
    )


async def notify_github_push(repo: str, branch: str, author: str, message: str, url: str = "") -> None:
    await tg_send(
        f"📦 <b>GitHub: новый коммит</b>\n"
        f"Репо: <code>{repo}</code>  Ветка: <code>{branch}</code>\n"
        f"Автор: {author}\n"
        f"<i>{(message or '').strip()[:200]}</i>"
        + (f"\n<a href='{url}'>Открыть коммит</a>" if url else "")
    )


async def notify_github_pr(repo: str, title: str, author: str, action: str, url: str = "") -> None:
    icons = {"opened": "🟡", "closed": "🔴", "merged": "🟣", "reopened": "🟠"}
    icon = icons.get(action, "ℹ️")
    await tg_send(
        f"{icon} <b>GitHub PR: {action}</b>\n"
        f"Репо: <code>{repo}</code>\n"
        f"<i>{(title or '').strip()[:200]}</i>\n"
        f"Автор: {author}"
        + (f"\n<a href='{url}'>Открыть PR</a>" if url else "")
    )


async def notify_render_webhook(service: str, status: str, deploy_id: str = "", commit: str = "") -> None:
    icons = {"live": "🟢", "failed": "🔴", "building": "🔵", "canceled": "⚫"}
    icon = icons.get(status, "ℹ️")
    await tg_send(
        f"{icon} <b>Render: {status}</b>\n"
        f"Сервис: <code>{service}</code>\n"
        f"Deploy: <code>{deploy_id[:12] if deploy_id else '—'}</code>\n"
        f"Коммит: <code>{commit[:10] if commit else '—'}</code>"
    )
