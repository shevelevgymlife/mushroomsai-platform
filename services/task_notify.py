"""Unified task/deploy notifications (Telegram + email)."""
from __future__ import annotations

import asyncio
import logging
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

import httpx

from config import settings

logger = logging.getLogger(__name__)


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _site_url() -> str:
    return (settings.SITE_URL or "").strip() or "https://mushroomsai.ru"


def _render_service() -> str:
    import os

    return os.getenv("RENDER_SERVICE_NAME", "mushroomsai")


def _render_commit() -> str:
    import os

    return (os.getenv("RENDER_GIT_COMMIT", "") or "")[:12] or "unknown"


def _render_deploy_id() -> str:
    import os

    return os.getenv("RENDER_DEPLOY_ID", "") or "unknown"


def _smtp_is_configured() -> bool:
    return bool(
        (settings.DEPLOY_NOTIFY_EMAIL_TO or "").strip()
        and (settings.SMTP_HOST or "").strip()
        and int(getattr(settings, "SMTP_PORT", 0) or 0) > 0
        and (settings.SMTP_USER or "").strip()
        and (settings.SMTP_PASS or "").strip()
    )


def _send_email_sync(subject: str, body: str) -> None:
    to_email = (settings.DEPLOY_NOTIFY_EMAIL_TO or "").strip()
    from_email = (settings.DEPLOY_NOTIFY_EMAIL_FROM or settings.SMTP_USER or "").strip()
    if not to_email or not from_email:
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(body)

    with smtplib.SMTP((settings.SMTP_HOST or "").strip(), int(settings.SMTP_PORT), timeout=12) as server:
        if bool(settings.SMTP_USE_TLS):
            server.starttls()
        server.login((settings.SMTP_USER or "").strip(), settings.SMTP_PASS)
        server.send_message(msg)


async def _notify_telegram(text: str) -> None:
    if not text:
        return
    token = (settings.TELEGRAM_NOTIFY_BOT_TOKEN or settings.TELEGRAM_TOKEN or "").strip()
    chat_id = int(getattr(settings, "TELEGRAM_NOTIFY_CHAT_ID", 0) or 0)
    if not token or not chat_id:
        return
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text[:3900]},
            )
    except Exception as e:
        logger.warning(f"Telegram notify failed: {e}")


async def _notify_email(subject: str, body: str) -> None:
    if not _smtp_is_configured():
        return
    try:
        await asyncio.to_thread(_send_email_sync, subject, body)
    except Exception as e:
        logger.warning(f"Email notify failed: {e}")


async def notify_task_accepted(task_text: str) -> None:
    text = (task_text or "").strip() or "Задача принята в обработку."
    service = _render_service()
    commit = _render_commit()
    site = _site_url()
    ts = _now_utc()
    msg = (
        "🟡 MushroomsAI Agent\n"
        "Статус: задача принята в работу\n\n"
        f"Задача: {text}\n"
        f"Service: {service}\n"
        f"Commit: {commit}\n"
        f"Site: {site}\n"
        f"Time: {ts}"
    )
    subject = f"[MushroomsAI] Task accepted ({service}) {commit}"
    await asyncio.gather(_notify_telegram(msg), _notify_email(subject, msg), return_exceptions=True)


async def notify_deploy_sent(task_text: str = "") -> None:
    service = _render_service()
    commit = _render_commit()
    site = _site_url()
    ts = _now_utc()
    task_line = f"Задача: {task_text.strip()}\n" if (task_text or "").strip() else ""
    msg = (
        "🔵 MushroomsAI Agent\n"
        "Статус: отправлено в Render на деплой\n\n"
        f"{task_line}"
        f"Service: {service}\n"
        f"Commit: {commit}\n"
        f"Deploy ID: {_render_deploy_id()}\n"
        f"Site: {site}\n"
        f"Time: {ts}"
    )
    subject = f"[MushroomsAI] Deploy sent ({service}) {commit}"
    await asyncio.gather(_notify_telegram(msg), _notify_email(subject, msg), return_exceptions=True)


async def notify_deploy_finished(short_result: str = "Деплой завершён успешно.") -> None:
    service = _render_service()
    commit = _render_commit()
    site = _site_url()
    ts = _now_utc()
    result = (short_result or "").strip() or "Деплой завершён успешно."
    msg = (
        "🟢 MushroomsAI Agent\n"
        "Статус: деплой завершён\n\n"
        f"Итог: {result}\n"
        f"Service: {service}\n"
        f"Commit: {commit}\n"
        f"Deploy ID: {_render_deploy_id()}\n"
        f"Проверка: {site}\n"
        f"Time: {ts}"
    )
    subject = f"[MushroomsAI] Deploy finished ({service}) {commit}"
    await asyncio.gather(_notify_telegram(msg), _notify_email(subject, msg), return_exceptions=True)
