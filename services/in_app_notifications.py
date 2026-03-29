"""Внутриигровые уведомления (/notifications) — записи не удаляются."""
from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from db.database import database
from db.models import in_app_notifications, users

NTYPE_PREF_KEY: dict[str, str] = {
    "follower": "followers",
    "post_like": "likes",
    "comment": "comments",
    "profile_like": "likes",
    "message": "dm",
    "group_post": "group_posts",
    "mention": "mentions",
    "subscription_post": "subscription_posts",
    "comment_reply": "comment_replies",
    "subscription_gift": "subscription_posts",
}

DEFAULT_PREFS: dict[str, bool] = {
    "followers": True,
    "likes": True,
    "comments": True,
    "dm": True,
    "comment_replies": True,
    "group_posts": True,
    "mentions": True,
    "subscription_posts": True,
    "telegram_bot": True,
    "radio_player": True,
}

TYPE_META: dict[str, tuple[str, str]] = {
    "follower": ("👤", "Новый подписчик"),
    "post_like": ("⭐", "Лайк поста"),
    "comment": ("💬", "Комментарий"),
    "profile_like": ("❤️", "Лайк профиля"),
    "message": ("✉️", "Личное сообщение"),
    "group_post": ("👥", "Публикация в группе"),
    "mention": ("@", "Упоминание"),
    "subscription_post": ("📰", "Новый пост подписки"),
    "comment_reply": ("↩️", "Ответ на комментарий"),
    "subscription_gift": ("🎁", "Подарок подписки"),
}


def merge_prefs(raw: str | None) -> dict[str, bool]:
    out = dict(DEFAULT_PREFS)
    if not raw:
        return out
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            for k, v in data.items():
                if k in out:
                    out[k] = bool(v)
    except Exception:
        pass
    return out


async def load_prefs_for_user(user_id: int) -> dict[str, bool]:
    row = await database.fetch_one(
        sa.select(users.c.notification_prefs_json).where(users.c.id == user_id)
    )
    return merge_prefs(row["notification_prefs_json"] if row else None)


def prefs_allow_ntype(prefs: dict[str, bool], ntype: str) -> bool:
    key = NTYPE_PREF_KEY.get(ntype, "likes")
    if ntype == "comment":
        return bool(prefs.get("comments", True) or prefs.get("comment_replies", True))
    return bool(prefs.get(key, True))


async def should_send_telegram(recipient_user_id: int) -> bool:
    p = await load_prefs_for_user(recipient_user_id)
    return bool(p.get("telegram_bot", True))


async def should_send_telegram_for_event(recipient_user_id: int, ntype: str) -> bool:
    """Push в Telegram только если включён бот и соответствующий тип события (как для «Событий»)."""
    p = await load_prefs_for_user(recipient_user_id)
    if not p.get("telegram_bot", True):
        return False
    return prefs_allow_ntype(p, ntype)


async def _dedup_exists(
    recipient_id: int, source_kind: str | None, source_id: int | None
) -> bool:
    if source_kind is None or source_id is None:
        return False
    row = await database.fetch_one(
        sa.select(in_app_notifications.c.id)
        .where(in_app_notifications.c.recipient_id == recipient_id)
        .where(in_app_notifications.c.source_kind == source_kind)
        .where(in_app_notifications.c.source_id == source_id)
        .limit(1)
    )
    return row is not None


async def create_notification(
    *,
    recipient_id: int,
    actor_id: int | None,
    ntype: str,
    title: str,
    body: str,
    link_url: str | None = None,
    source_kind: str | None = None,
    source_id: int | None = None,
    read_at=None,
    meta: dict[str, Any] | None = None,
    skip_prefs: bool = False,
) -> int | None:
    if recipient_id <= 0:
        return None
    if actor_id is not None and actor_id == recipient_id:
        return None
    if not skip_prefs:
        prefs = await load_prefs_for_user(recipient_id)
        if not prefs_allow_ntype(prefs, ntype):
            return None
    if await _dedup_exists(recipient_id, source_kind, source_id):
        return None
    meta_json = json.dumps(meta, ensure_ascii=False) if meta else None
    row = await database.fetch_one_write(
        in_app_notifications.insert()
        .values(
            recipient_id=recipient_id,
            actor_id=actor_id,
            ntype=ntype,
            title=title or "",
            body=body or "",
            link_url=link_url,
            source_kind=source_kind,
            source_id=source_id,
            read_at=read_at,
            meta_json=meta_json,
        )
        .returning(in_app_notifications.c.id)
    )
    return int(row["id"]) if row else None


async def count_unread_events(recipient_id: int) -> int:
    q = await database.fetch_val(
        sa.select(sa.func.count())
        .select_from(in_app_notifications)
        .where(in_app_notifications.c.recipient_id == recipient_id)
        .where(in_app_notifications.c.read_at.is_(None))
        .where(in_app_notifications.c.ntype != "message")
    )
    return int(q or 0)


async def mark_events_notifications_read(recipient_id: int) -> None:
    await database.execute(
        in_app_notifications.update()
        .where(in_app_notifications.c.recipient_id == recipient_id)
        .where(in_app_notifications.c.read_at.is_(None))
        .where(in_app_notifications.c.ntype != "message")
        .values(read_at=sa.func.now())
    )


async def mark_one_read(notification_id: int, recipient_id: int) -> None:
    await database.execute(
        in_app_notifications.update()
        .where(in_app_notifications.c.id == notification_id)
        .where(in_app_notifications.c.recipient_id == recipient_id)
        .values(read_at=sa.func.now())
    )


def type_display(ntype: str) -> tuple[str, str]:
    return TYPE_META.get(ntype, ("🔔", "Событие"))
