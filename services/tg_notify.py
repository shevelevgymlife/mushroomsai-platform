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
    return (settings.NOTIFY_BOT_TOKEN or settings.TELEGRAM_TOKEN or "").strip()

def _user_tokens() -> list[str]:
    """
    Для уведомлений пользователям сначала пробуем основной TELEGRAM_TOKEN
    (где пользователь уже нажал /start), затем fallback на notify-бота.
    """
    tokens: list[str] = []
    main_token = (settings.TELEGRAM_TOKEN or "").strip()
    notify_token = (settings.NOTIFY_BOT_TOKEN or "").strip()
    if main_token:
        tokens.append(main_token)
    if notify_token and notify_token != main_token:
        tokens.append(notify_token)
    return tokens


def _admin_id() -> int:
    return int(settings.ADMIN_TG_ID or 0)


def _render_service() -> str:
    return os.getenv("RENDER_SERVICE_NAME", "mushroomsai")


def _render_commit() -> str:
    return (os.getenv("RENDER_GIT_COMMIT", "") or "")[:10] or "—"


async def notify_dm_read_button(
    chat_id: int,
    sender_name: str,
    text_preview: str,
    read_url: str,
) -> bool:
    """Личное сообщение в Telegram с кнопкой «Прочитать» (ссылка на чат на сайте / Mini App)."""
    if not chat_id:
        return False
    tokens = _user_tokens()
    if not tokens:
        return False
    url = (read_url or "").strip()
    if not url.startswith("http"):
        base = (getattr(settings, "SITE_URL", None) or "").rstrip("/")
        url = f"{base}{url}" if url.startswith("/") else f"{base}/{url}"
    name = (sender_name or "Участник").replace("<", "")
    prev = (text_preview or "").strip()[:180]
    msg_text = (
        f"💬 <b>Вам пришло сообщение в личку</b>\n"
        f"От: {name}\n"
        f"<i>{prev}</i>"
    )
    payload = {
        "chat_id": int(chat_id),
        "text": msg_text[:_MAX_LEN],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {
            "inline_keyboard": [[{"text": "📩 Прочитать", "url": url}]]
        },
    }
    for token in tokens:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json=payload,
                )
                if r.status_code == 200:
                    return True
                logger.warning("notify_dm_read_button failed: %s %s", r.status_code, r.text[:200])
        except Exception as e:
            logger.warning("notify_dm_read_button exception: %s", e)
    return False


async def notify_group_chat_button(
    tg_chat_id: int,
    *,
    chat_title: str,
    open_path: str,
    is_mention: bool,
    is_reply: bool,
) -> bool:
    """Упоминание или ответ в групповом чате — кнопка «Открыть чат» (часто при «без звука» в группе)."""
    if not tg_chat_id:
        return False
    tokens = _user_tokens()
    if not tokens:
        return False
    url = (open_path or "").strip()
    if not url.startswith("http"):
        base = (getattr(settings, "SITE_URL", None) or "").rstrip("/")
        url = f"{base}{url}" if url.startswith("/") else f"{base}/{url}"
    title = (chat_title or "Чат").replace("<", "").replace("&", "")[:120]
    if is_mention and is_reply:
        head = "Вас упомянули и ответили вам"
    elif is_mention:
        head = "Вас упомянули"
    else:
        head = "Вам ответили"
    msg_text = (
        f"👥 <b>{head}</b> в групповом чате «{title}».\n"
        f"Откройте переписку на сайте."
    )
    payload = {
        "chat_id": int(tg_chat_id),
        "text": msg_text[:_MAX_LEN],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {
            "inline_keyboard": [[{"text": "💬 Открыть чат", "url": url}]]
        },
    }
    for token in tokens:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json=payload,
                )
                if r.status_code == 200:
                    return True
                logger.warning("notify_group_chat_button failed: %s %s", r.status_code, r.text[:200])
        except Exception as e:
            logger.warning("notify_group_chat_button exception: %s", e)
    return False


async def notify_user_telegram(chat_id: int, text: str, parse_mode: str = "HTML") -> bool:
    """Сообщение пользователю по chat_id (тот же бот, что и для админа)."""
    if not chat_id:
        return False
    text = (text or "").strip()[:_MAX_LEN]
    if not text:
        return False
    tokens = _user_tokens()
    if not tokens:
        return False
    for token in tokens:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={
                        "chat_id": int(chat_id),
                        "text": text,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": True,
                    },
                )
                if r.status_code == 200:
                    return True
                logger.warning("notify_user_telegram failed: %s %s", r.status_code, r.text[:200])
        except Exception as e:
            logger.warning("notify_user_telegram exception: %s", e)
    return False


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


async def notify_new_feedback_with_reply(
    feedback_id: int,
    text: str,
    user_label: str = "Гость",
    user_tg_id: int | None = None,
) -> None:
    """Уведомление о новом обращении в поддержку с inline-кнопкой «Ответить»."""
    token = _token()
    chat_id = _admin_id()
    if not token or not chat_id:
        return

    msg_text = (
        f"📬 <b>Обращение в поддержку #{feedback_id}</b>\n"
        f"От: {user_label}"
        + (f" (<code>tg:{user_tg_id}</code>)" if user_tg_id else "") + "\n\n"
        f"<i>{(text or '').strip()[:600]}</i>\n\n"
        f"<a href='{settings.SITE_URL}/admin/feedback'>Открыть в админке</a>"
    )

    payload: dict = {
        "chat_id": chat_id,
        "text": msg_text[:_MAX_LEN],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if user_tg_id:
        payload["reply_markup"] = {
            "inline_keyboard": [[
                {"text": "💬 Ответить", "callback_data": f"reply_fb:{feedback_id}:{user_tg_id}"}
            ]]
        }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=payload,
            )
            if r.status_code != 200:
                logger.warning("notify_new_feedback_with_reply failed: %s %s", r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("notify_new_feedback_with_reply exception: %s", e)


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
    # Отправляем только финальные статусы (не промежуточные building/deploying)
    final_statuses = {"live", "failed", "canceled", "deactivated"}
    if status not in final_statuses:
        return
    icons = {"live": "🟢", "failed": "🔴", "canceled": "⚫", "deactivated": "⚫"}
    icon = icons.get(status, "ℹ️")
    await tg_send(
        f"{icon} <b>Render: {status}</b>\n"
        f"Сервис: <code>{service}</code>\n"
        f"Deploy: <code>{deploy_id[:12] if deploy_id else '—'}</code>\n"
        f"Коммит: <code>{commit[:10] if commit else '—'}</code>"
    )
