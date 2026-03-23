"""Уведомления о деплое (email + Telegram)."""
from __future__ import annotations

import asyncio
import logging
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from socket import gethostname

from config import settings
from services.task_notify import notify_deploy_finished

logger = logging.getLogger(__name__)


def _smtp_is_configured() -> bool:
    return bool(
        (settings.DEPLOY_NOTIFY_EMAIL_TO or "").strip()
        and (settings.SMTP_HOST or "").strip()
        and int(getattr(settings, "SMTP_PORT", 0) or 0) > 0
        and (settings.SMTP_USER or "").strip()
        and (settings.SMTP_PASS or "").strip()
    )


def _send_deploy_email_sync() -> None:
    to_email = (settings.DEPLOY_NOTIFY_EMAIL_TO or "").strip()
    from_email = (settings.DEPLOY_NOTIFY_EMAIL_FROM or settings.SMTP_USER or "").strip()
    if not to_email or not from_email:
        return

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    host = gethostname()
    site = (settings.SITE_URL or "").strip() or "unknown-site"
    service = os.getenv("RENDER_SERVICE_NAME", "mushroomsai")
    commit = os.getenv("RENDER_GIT_COMMIT", "")[:12] or "unknown"
    subject = f"[MushroomsAI] Deploy started ({service}) {commit}"
    body = (
        "Новый инстанс MushroomsAI запущен.\n\n"
        f"Service: {service}\n"
        f"Commit: {commit}\n"
        f"Site: {site}\n"
        f"Host: {host}\n"
        f"Time: {ts}\n"
    )

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


async def send_deploy_email() -> None:
    """Best-effort email о startup (на Render это обычно новый деплой)."""
    if not _smtp_is_configured():
        return
    try:
        await asyncio.to_thread(_send_deploy_email_sync)
        logger.info("Deploy notification email sent")
    except Exception as e:
        logger.warning(f"Deploy notification email failed: {e}")


async def send_deploy_notifications() -> None:
    """Best-effort: email старт деплоя + Telegram/email о завершении запуска."""
    await send_deploy_email()
    await notify_deploy_finished("Приложение успешно запущено на Render.")
