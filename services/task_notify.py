"""Уведомления о задачах и деплое (только email)."""
from __future__ import annotations

import asyncio
import logging
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

from config import settings

logger = logging.getLogger(__name__)


def _first_nonempty(*values: str) -> str:
    for v in values:
        s = str(v or "").strip()
        if s:
            return s
    return ""


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _site_url() -> str:
    return _first_nonempty(settings.SITE_URL, "https://mushroomsai.ru")


def _render_service() -> str:
    return os.getenv("RENDER_SERVICE_NAME", "mushroomsai")


def _render_commit() -> str:
    return (os.getenv("RENDER_GIT_COMMIT", "") or "")[:12] or "unknown"


def _render_deploy_id() -> str:
    return os.getenv("RENDER_DEPLOY_ID", "") or "unknown"


def _email_to_for_stage(stage: str) -> str:
    task_email = _first_nonempty(
        getattr(settings, "TASK_NOTIFY_EMAIL_TO", ""),
        getattr(settings, "DEPLOY_NOTIFY_TASK_EMAIL_TO", ""),
    )
    deploy_email = _first_nonempty(settings.DEPLOY_NOTIFY_EMAIL_TO)
    if stage in ("task_accepted", "deploy_sent"):
        return _first_nonempty(task_email, deploy_email)
    return _first_nonempty(deploy_email, task_email)


def _email_from_for_stage() -> str:
    return _first_nonempty(
        getattr(settings, "TASK_NOTIFY_EMAIL_FROM", ""),
        settings.DEPLOY_NOTIFY_EMAIL_FROM,
        settings.SMTP_USER,
    )


def _smtp_is_configured(to_email: str) -> bool:
    return bool(
        to_email
        and (settings.SMTP_HOST or "").strip()
        and int(getattr(settings, "SMTP_PORT", 0) or 0) > 0
        and (settings.SMTP_USER or "").strip()
        and (settings.SMTP_PASS or "").strip()
    )


def _send_email_sync(subject: str, body: str, to_email: str, from_email: str) -> None:
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


async def _notify_email(subject: str, body: str, stage: str) -> None:
    to_email = _email_to_for_stage(stage)
    from_email = _email_from_for_stage()
    if not _smtp_is_configured(to_email):
        return
    try:
        await asyncio.to_thread(_send_email_sync, subject, body, to_email, from_email)
    except Exception as e:
        logger.warning(f"Email notify failed: {e}")


async def notify_status(
    stage: str,
    summary: str,
    details: str = "",
    site_url: str = "",
    include_email: bool = True,
) -> None:
    from services.tg_notify import tg_send
    stage = (stage or "").strip().lower()
    stages = {
        "task_accepted": ("🟡", "задача принята"),
        "task_done": ("✅", "задача завершена"),
        "deploy_sent": ("🔵", "отправлено на деплой в Render"),
        "deploy_completed": ("🟢", "деплой закончился, можно смотреть сайт"),
    }
    icon, stage_title = stages.get(stage, ("ℹ️", stage or "статус"))
    site = (site_url or "").strip() or _site_url()
    ts = _now_utc()
    details_text = (details or "").strip()
    core = summary.strip() or "Обновление по задаче."
    msg = (
        f"{icon} {core}\n"
        f"{stage_title.capitalize()}.\n"
        f"{details_text + chr(10) if details_text else ''}"
        f"Проверка: {site}\n"
        f"Time: {ts}"
    )
    subject = f"[NEUROFUNGI AI] {stage_title} ({_render_service()}) {_render_commit()}"
    if include_email:
        await _notify_email(subject, msg, stage)
    # Telegram
    await tg_send(msg)


async def notify_task_accepted(task_text: str) -> None:
    text = (task_text or "").strip() or "Задача принята в обработку."
    await notify_status(
        stage="task_accepted",
        summary=f"Задача: {text}",
        details=f"Service: {_render_service()}\nCommit: {_render_commit()}",
        include_email=True,
    )


async def notify_deploy_sent(task_text: str = "") -> None:
    task_line = f"Задача: {task_text.strip()}" if (task_text or "").strip() else "Задача: —"
    await notify_status(
        stage="deploy_sent",
        summary=task_line,
        details=f"Service: {_render_service()}\nCommit: {_render_commit()}\nDeploy ID: {_render_deploy_id()}",
        include_email=True,
    )


async def notify_task_done(done_text: str) -> None:
    text = (done_text or "").strip() or "Изменения по задаче выполнены."
    await notify_status(
        stage="task_done",
        summary=text,
        details=f"Service: {_render_service()}\nCommit: {_render_commit()}",
        include_email=True,
    )


async def notify_deploy_finished(short_result: str = "Деплой завершён успешно.") -> None:
    result = (short_result or "").strip() or "Деплой завершён успешно."
    await notify_status(
        stage="deploy_completed",
        summary=f"Итог: {result}",
        details=f"Service: {_render_service()}\nCommit: {_render_commit()}\nDeploy ID: {_render_deploy_id()}",
        include_email=True,
    )
