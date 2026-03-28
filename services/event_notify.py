"""Telegram-уведомления о событиях ленты /notifications (с учётом настроек и типа события)."""
from __future__ import annotations

import html
import re
import sqlalchemy as sa

from config import settings
from db.database import database
from db.models import users

# Упоминание по числовому id: @12345 (не цепляет e-mail)
_MENTION_IDS = re.compile(r"(?<![\w/])@(\d{1,12})\b")


def extract_mentioned_numeric_ids(text: str) -> list[int]:
    if not text or not text.strip():
        return []
    seen: set[int] = set()
    out: list[int] = []
    for m in _MENTION_IDS.finditer(text):
        try:
            uid = int(m.group(1))
        except ValueError:
            continue
        if uid <= 0 or uid in seen:
            continue
        seen.add(uid)
        out.append(uid)
    return out


async def user_exists(user_id: int) -> bool:
    row = await database.fetch_one(
        sa.select(users.c.id).where(users.c.id == user_id).limit(1)
    )
    return row is not None


async def send_event_telegram_html(
    recipient_user_id: int,
    ntype: str,
    title: str,
    body_plain: str,
    link_path: str | None = None,
) -> bool:
    from services.in_app_notifications import should_send_telegram_for_event
    from services.notify_user_stub import notify_user

    if not await should_send_telegram_for_event(recipient_user_id, ntype):
        return False
    row = await database.fetch_one(users.select().where(users.c.id == recipient_user_id))
    if not row:
        return False
    tg_id = row.get("tg_id") or row.get("linked_tg_id")
    if not tg_id:
        return False
    base = (getattr(settings, "SITE_URL", None) or "").rstrip("/")
    full_link = None
    if link_path and link_path.startswith("/") and base:
        full_link = f"{base}{link_path}"
    t = html.escape((title or "").strip())
    b = html.escape((body_plain or "").strip())
    parts = []
    if t:
        parts.append(f"<b>{t}</b>")
    if b:
        parts.append(b)
    msg = "\n".join(parts) if parts else "NEUROFUNGI AI"
    if full_link:
        msg += f'\n\n<a href="{html.escape(full_link)}">Открыть</a>'
    return await notify_user(int(tg_id), msg)
