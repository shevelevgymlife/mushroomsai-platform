"""Создание поста сообщества + уведомления подписчикам и @упоминаниям (веб и бот)."""
from __future__ import annotations

import json

import sqlalchemy as sa

from db.database import database
from db.models import community_follows, community_posts, users
from services.event_notify import extract_mentioned_numeric_ids, send_event_telegram_html, user_exists
from services.in_app_notifications import create_notification


async def publish_community_post(
    *,
    user_id: int,
    author_name: str,
    content: str,
    title: str | None = None,
    image_url: str | None = None,
    images_json: str | None = None,
    folder_id: int | None = None,
    from_telegram: bool = False,
) -> int | None:
    """
    Вставляет пост и рассылает уведомления. Возвращает id поста или None при ошибке вставки.
    """
    body_text = (content or "").strip()
    if len(body_text) < 2:
        return None

    tit = (title or "").strip()[:200] or None
    uid = int(user_id)

    if images_json is None and image_url:
        images_json = json.dumps([image_url])
    img_url = image_url

    try:
        ins_row = await database.fetch_one_write(
            community_posts.insert()
            .values(
                user_id=uid,
                title=tit,
                content=body_text,
                image_url=img_url,
                images_json=images_json,
                folder_id=folder_id,
                approved=True,
                from_telegram=from_telegram,
            )
            .returning(community_posts.c.id)
        )
    except Exception:
        return None

    post_id = int(ins_row["id"]) if ins_row else None
    if not post_id:
        return None

    preview = (tit or body_text[:120] or "Новый пост").strip()
    aname = (author_name or "").strip() or "Участник"

    try:
        followers = await database.fetch_all(
            community_follows.select()
            .where(community_follows.c.following_id == uid)
            .where(community_follows.c.follower_id != uid)
        )
        for fr in followers:
            rid = int(fr["follower_id"])
            if rid <= 0:
                continue
            await create_notification(
                recipient_id=rid,
                actor_id=uid,
                ntype="subscription_post",
                title="Новый пост подписки",
                body=f"{aname}: {preview[:400]}",
                link_url=f"/community/post/{post_id}",
                source_kind="subscription_post",
                source_id=int(post_id),
            )
            await send_event_telegram_html(
                rid,
                "subscription_post",
                "Новый пост подписки",
                f"{aname}: {preview[:350]}",
                f"/community/post/{post_id}",
            )
        combined = f"{tit or ''}\n{body_text}"
        for mid in extract_mentioned_numeric_ids(combined):
            if mid == uid:
                continue
            if not await user_exists(mid):
                continue
            await create_notification(
                recipient_id=mid,
                actor_id=uid,
                ntype="mention",
                title="Вас упомянули в посте",
                body=f"{aname} в посте: {preview[:380]}",
                link_url=f"/community/post/{post_id}",
                source_kind="mention_post",
                source_id=int(post_id),
            )
            await send_event_telegram_html(
                mid,
                "mention",
                "Вас упомянули в посте",
                f"{aname}: {preview[:350]}",
                f"/community/post/{post_id}",
            )
    except Exception:
        pass

    return post_id
