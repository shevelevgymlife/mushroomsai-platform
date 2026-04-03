"""Автоматическая активность NeuroFungi AI в ленте сообщества: посты, комментарии, подписки, статусы."""
from __future__ import annotations

import json
import logging
import random
import re
import secrets
import string
from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa

from auth.email_auth import hash_password
from config import settings
from db.database import database
from db.models import (
    ai_community_bot_settings,
    community_comments,
    community_follows,
    community_posts,
    users,
)
from services.ai_tg_channel import (
    send_neurofungi_post_to_telegram_channel,
    send_neurofungi_thought_to_telegram_channel,
    telegram_channel_configured,
)
from services.community_post_publish import publish_community_post
from services.event_notify import extract_mentioned_numeric_ids, send_event_telegram_html, user_exists
from services.in_app_notifications import create_notification
from services.ai_multichannel_settings import get_ai_multichannel_settings

logger = logging.getLogger(__name__)

_BOT_EMAIL = "neurofungi.ai.community@system.invalid"


def _utc_day_start() -> datetime:
    now = datetime.utcnow()
    return datetime(now.year, now.month, now.day)


async def apply_ai_community_bot_schema_if_needed() -> None:
    """Идемпотентно применяет DDL v25/v26 (если heavy_startup не успел или в БД не хватает колонок)."""
    try:
        import migrate_v25_ai_community_bot as m25

        for step in m25.STEPS:
            await database.execute(sa.text(step))
    except Exception as e:
        logger.warning("apply_ai_community_bot_schema v25: %s", e)
    try:
        import migrate_v26_ai_telegram_channel as m26

        for step in m26.STEPS:
            await database.execute(sa.text(step))
    except Exception as e:
        logger.warning("apply_ai_community_bot_schema v26: %s", e)


async def load_bot_settings_row() -> Optional[dict[str, Any]]:
    """SELECT * — не ломается, если в БД ещё нет колонок из новых миграций (ORM перечислил бы все поля модели)."""
    try:
        return await database.fetch_one(
            sa.text("SELECT * FROM ai_community_bot_settings WHERE id = 1")
        )
    except Exception as e:
        logger.warning("load_bot_settings_row: %s", e)
        return None


async def count_today(uid: int, table: str, col_user: str = "user_id") -> int:
    start = _utc_day_start()
    q = sa.text(
        f"""
        SELECT COUNT(*) FROM {table}
        WHERE {col_user} = :uid AND created_at >= :start
        """
    )
    return int(await database.fetch_val(q, {"uid": uid, "start": start}) or 0)


async def _ensure_bot_settings_row_exists() -> None:
    """Если миграция не отработала, строка id=1 может отсутствовать — создаём."""
    try:
        await database.execute(
            sa.text(
                "INSERT INTO ai_community_bot_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING"
            )
        )
    except Exception as e:
        logger.warning("ai_community_bot: cannot ensure settings row: %s", e)


async def _persist_ai_community_bot_user_id(uid: int) -> None:
    """Надёжно пишет user_id в строку настроек (UPSERT), чтобы не зависеть от пустой строки после INSERT."""
    await apply_ai_community_bot_schema_if_needed()
    await _ensure_bot_settings_row_exists()
    await database.execute(
        sa.text(
            """
            INSERT INTO ai_community_bot_settings (id, user_id, updated_at)
            VALUES (1, :uid, NOW())
            ON CONFLICT (id) DO UPDATE SET user_id = EXCLUDED.user_id, updated_at = NOW()
            """
        ),
        {"uid": uid},
    )


async def bind_ai_community_bot_to_user_id(uid: int) -> tuple[bool, str]:
    """
    Привязка существующего users.id к NeuroFungi AI в сообществе (из админки).
    Возвращает (успех, текст ошибки для отображения или пустая строка).
    """
    if uid < 1:
        return False, "Укажите положительный числовой id пользователя."
    u = await database.fetch_one(users.select().where(users.c.id == uid))
    if not u:
        return False, f"Пользователь с id={uid} не найден в базе."
    try:
        await _persist_ai_community_bot_user_id(uid)
    except Exception as e:
        logger.warning("bind_ai_community_bot_to_user_id: %s", e, exc_info=True)
        return False, "Не удалось записать настройки. См. логи сервера."
    return True, ""


async def ensure_ai_community_bot_user() -> Optional[int]:
    """Создаёт или привязывает пользователя NeuroFungi AI для сообщества."""
    await apply_ai_community_bot_schema_if_needed()
    await _ensure_bot_settings_row_exists()
    row = await load_bot_settings_row()
    if row and row.get("user_id"):
        u = await database.fetch_one(users.select().where(users.c.id == int(row["user_id"])))
        if u:
            return int(u["id"])
        try:
            await database.execute(
                sa.text(
                    "UPDATE ai_community_bot_settings SET user_id = NULL, updated_at = NOW() WHERE id = 1"
                )
            )
        except Exception as e:
            logger.warning("ai_community_bot: clear stale user_id: %s", e)

    nid = int(getattr(settings, "NEUROFUNGI_AI_USER_ID", 0) or 0)
    if nid > 0:
        u = await database.fetch_one(users.select().where(users.c.id == nid))
        if u:
            await _persist_ai_community_bot_user_id(int(nid))
            return int(nid)

    existing = await database.fetch_one(users.select().where(users.c.email == _BOT_EMAIL))
    if existing:
        uid = int(existing["id"])
        await _persist_ai_community_bot_user_id(uid)
        return uid

    uid: Optional[int] = None
    for attempt in range(8):
        referral_code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        pw = secrets.token_urlsafe(24)
        try:
            await database.execute(
                users.insert().values(
                    email=_BOT_EMAIL,
                    password_hash=hash_password(pw),
                    name="NeuroFungi AI",
                    bio="Официальный AI-профиль NEUROFUNGI: фунготерапия, психология, КПТ и живая лента сообщества.",
                    referral_code=referral_code,
                    role="user",
                    subscription_plan="free",
                    needs_tariff_choice=False,
                )
            )
            row_ins = await database.fetch_one(users.select().where(users.c.email == _BOT_EMAIL))
            if row_ins:
                uid = int(row_ins["id"])
                break
        except Exception as e:
            err = str(e).lower()
            logger.warning("ai_community_bot: INSERT users attempt %s: %s", attempt, e)
            if "unique" in err or "duplicate" in err:
                row_ins = await database.fetch_one(users.select().where(users.c.email == _BOT_EMAIL))
                if row_ins:
                    uid = int(row_ins["id"])
                    break
                continue
            logger.error("ai_community_bot: INSERT users failed: %s", e)
            return None
    if not uid:
        logger.error("ai_community_bot: INSERT users could not get new id after retries")
        return None
    await _persist_ai_community_bot_user_id(uid)
    logger.info("ai_community_bot: created system user id=%s", uid)
    return uid


async def _platform_stats() -> dict[str, Any]:
    nu = await database.fetch_val(
        sa.select(sa.func.count()).select_from(users).where(users.c.primary_user_id.is_(None))
    )
    np = await database.fetch_val(sa.select(sa.func.count()).select_from(community_posts))
    nc = await database.fetch_val(sa.select(sa.func.count()).select_from(community_comments))
    return {
        "users_total": int(nu or 0),
        "posts_total": int(np or 0),
        "comments_total": int(nc or 0),
    }


def _strip_urls_and_md_links(text: str) -> str:
    """Лента: без URL и markdown-ссылок в тексте поста/комментария."""
    t = (text or "").strip()
    if not t:
        return t
    t = re.sub(r"https?://\S+", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"\s+\n", "\n", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


async def _community_system_prompt(
    user_message: str,
    task_suffix: str,
    *,
    channel_kind: str = "comment",
) -> str:
    """Тот же базовый промт + обучающие посты, что и в чате (`get_system_prompt`)."""
    from ai.openai_client import get_system_prompt

    base = await get_system_prompt(user_message=user_message)
    cfg = await get_ai_multichannel_settings()
    post_prompt = (cfg.get("post_prompt") or "").strip()
    comment_prompt = (cfg.get("comment_prompt") or "").strip()
    style_prompt = post_prompt if channel_kind == "post" else comment_prompt
    style_block = (
        "КАНАЛЬНЫЙ СТИЛЬ (строго):\n" + style_prompt
        if style_prompt
        else ""
    )
    return (
        base
        + "\n\n[Режим ленты сообщества NEUROFUNGI] Соблюдай системный промт и блоки знаний выше строго. "
        "Пиши только связным текстом на русском: без URL, без http/https, без markdown-ссылок, без списков ссылок. "
        "Факты о грибах/дозах/связках/рекомендациях бери только из обучающих постов, которые подмешаны в промпт; "
        "если факта нет в обучающих постах, не выдумывай и честно скажи об этом.\n"
        + (style_block + "\n" if style_block else "")
        + task_suffix
    )


async def _openai_text(
    system: str,
    user: str,
    max_tokens: int = 900,
    *,
    temperature: float = 0.85,
    strip_links: bool = False,
) -> Optional[str]:
    if not getattr(settings, "OPENAI_API_KEY", None):
        return None
    try:
        from openai import AsyncOpenAI

        cli = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        resp = await cli.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user[:12000]},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        out = (resp.choices[0].message.content or "").strip()
        if not out:
            return None
        out = out[:12000]
        if strip_links:
            out = _strip_urls_and_md_links(out)
        return out or None
    except Exception:
        logger.exception("ai_community_bot: openai failed")
        return None


async def _notify_admins_bug_report(body: str) -> None:
    rows = await database.fetch_all(sa.select(users.c.id).where(users.c.role == "admin"))
    for r in rows:
        rid = int(r["id"])
        if rid <= 0:
            continue
        try:
            await create_notification(
                recipient_id=rid,
                actor_id=None,
                ntype="system",
                title="NeuroFungi AI: замечание",
                body=(body or "")[:500],
                link_url="/admin/ai-community-bot",
                source_kind="ai_bot_bug",
                source_id=int(datetime.utcnow().timestamp()) % 1_000_000_000,
                skip_prefs=True,
            )
        except Exception:
            logger.debug("ai_community_bot: notify admin failed uid=%s", rid, exc_info=True)


async def bot_add_comment(
    *,
    post_id: int,
    bot_uid: int,
    content: str,
    author_name: str,
) -> Optional[int]:
    post = await database.fetch_one(community_posts.select().where(community_posts.c.id == post_id))
    if not post:
        return None
    text = (content or "").strip()
    if len(text) < 1:
        return None
    c_seen = post["user_id"] is not None and int(post["user_id"]) == int(bot_uid)
    crow = await database.fetch_one_write(
        community_comments.insert()
        .values(
            post_id=post_id,
            user_id=bot_uid,
            content=text[:8000],
            seen_by_post_owner=c_seen,
        )
        .returning(community_comments.c.id)
    )
    comment_id = int(crow["id"]) if crow else None
    if not comment_id:
        return None
    await database.execute(
        community_posts.update()
        .where(community_posts.c.id == post_id)
        .values(comments_count=community_posts.c.comments_count + 1)
    )
    owner_id = post.get("user_id")
    if owner_id and int(owner_id) != int(bot_uid) and not c_seen:
        await create_notification(
            recipient_id=int(owner_id),
            actor_id=int(bot_uid),
            ntype="comment",
            title="Комментарий",
            body=f"{author_name}: {text[:400]}",
            link_url=f"/community/post/{post_id}",
            source_kind="community_comment",
            source_id=comment_id,
        )
        await send_event_telegram_html(
            int(owner_id),
            "comment",
            "Комментарий к посту",
            f"{author_name}: {text[:350]}",
            f"/community/post/{post_id}",
        )
    for mid in extract_mentioned_numeric_ids(text):
        if mid == int(bot_uid):
            continue
        if not await user_exists(mid):
            continue
        await create_notification(
            recipient_id=mid,
            actor_id=int(bot_uid),
            ntype="mention",
            title="Вас упомянули в комментарии",
            body=f"{author_name}: {text[:380]}",
            link_url=f"/community/post/{post_id}",
            source_kind="mention_comment",
            source_id=comment_id,
        )
    return comment_id


async def bot_follow(bot_uid: int, target_id: int) -> bool:
    if bot_uid == target_id:
        return False
    existing = await database.fetch_one(
        community_follows.select()
        .where(community_follows.c.follower_id == bot_uid)
        .where(community_follows.c.following_id == target_id)
    )
    if existing:
        return False
    try:
        fr = await database.fetch_one_write(
            community_follows.insert()
            .values(follower_id=bot_uid, following_id=target_id)
            .returning(community_follows.c.id)
        )
        fid = int(fr["id"]) if fr else None
        await database.execute(
            users.update().where(users.c.id == bot_uid).values(following_count=users.c.following_count + 1)
        )
        await database.execute(
            users.update().where(users.c.id == target_id).values(followers_count=users.c.followers_count + 1)
        )
        if fid:
            urow = await database.fetch_one(users.select().where(users.c.id == bot_uid))
            actor_name = (urow.get("name") if urow else None) or "NeuroFungi AI"
            await create_notification(
                recipient_id=int(target_id),
                actor_id=int(bot_uid),
                ntype="follower",
                title="Новый подписчик",
                body=f"{actor_name} подписался(ась) на вас",
                link_url=f"/community/profile/{bot_uid}",
                source_kind="community_follow",
                source_id=fid,
            )
            await send_event_telegram_html(
                int(target_id),
                "follower",
                "Новый подписчик",
                f"{actor_name} подписался(ась) на вас",
                f"/community/profile/{bot_uid}",
            )
        return True
    except Exception:
        logger.debug("bot_follow failed", exc_info=True)
        return False


async def bot_unfollow(bot_uid: int, target_id: int) -> bool:
    existing = await database.fetch_one(
        community_follows.select()
        .where(community_follows.c.follower_id == bot_uid)
        .where(community_follows.c.following_id == target_id)
    )
    if not existing:
        return False
    await database.execute(
        community_follows.delete()
        .where(community_follows.c.follower_id == bot_uid)
        .where(community_follows.c.following_id == target_id)
    )
    await database.execute(
        users.update()
        .where(users.c.id == bot_uid)
        .values(
            following_count=sa.case((users.c.following_count > 0, users.c.following_count - 1), else_=0)
        )
    )
    await database.execute(
        users.update()
        .where(users.c.id == target_id)
        .values(
            followers_count=sa.case((users.c.followers_count > 0, users.c.followers_count - 1), else_=0)
        )
    )
    return True


async def _pick_comment_to_reply(bot_uid: int) -> Optional[dict[str, Any]]:
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT c.id AS cid, c.post_id, c.user_id AS author_id, c.content AS ctext, c.created_at AS cat,
                   p.user_id AS post_owner
            FROM community_comments c
            JOIN community_posts p ON p.id = c.post_id
            WHERE p.user_id = :uid AND c.user_id IS NOT NULL AND c.user_id <> :uid
            ORDER BY c.created_at DESC
            LIMIT 80
            """
        ),
        {"uid": bot_uid},
    )
    for r in rows:
        post_id = int(r["post_id"])
        cat = r["cat"]
        later = await database.fetch_one(
            sa.text(
                """
                SELECT 1 FROM community_comments x
                WHERE x.post_id = :pid AND x.user_id = :bot AND x.created_at > :cat
                LIMIT 1
                """
            ),
            {"pid": post_id, "bot": bot_uid, "cat": cat},
        )
        if later:
            continue
        return dict(r)
    return None


async def run_ai_community_bot_job() -> None:
    await apply_ai_community_bot_schema_if_needed()
    st_row = await load_bot_settings_row()
    if not st_row:
        return
    st = dict(st_row)
    if not st.get("master_enabled"):
        return
    bot_uid = st.get("user_id")
    if not bot_uid:
        bot_uid = await ensure_ai_community_bot_user()
    if not bot_uid:
        logger.warning("ai_community_bot: no user id")
        return
    bot_uid = int(bot_uid)
    urow = await database.fetch_one(users.select().where(users.c.id == bot_uid))
    author_name = (urow.get("name") if urow else None) or "NeuroFungi AI"

    limits = {
        "posts": int(st.get("limit_posts_per_day") or 5),
        "comments": int(st.get("limit_comments_per_day") or 30),
        "follows": int(st.get("limit_follows_per_day") or 15),
        "thoughts": int(st.get("limit_thoughts_per_day") or 15),
        "replies": int(st.get("limit_reply_comments_per_day") or 25),
    }

    async def used(kind: str) -> int:
        if kind == "posts":
            return await count_today(bot_uid, "community_posts")
        if kind == "comments":
            return await count_today(bot_uid, "community_comments")
        if kind == "follows":
            return await count_today(bot_uid, "community_follows", "follower_id")
        return 0

    stats = await _platform_stats()

    # 1) Reply to comments on our posts
    if st.get("allow_reply_to_comments"):
        rep_today = await database.fetch_val(
            sa.text(
                """
                SELECT COUNT(*) FROM community_comments c
                JOIN community_posts p ON p.id = c.post_id
                WHERE c.user_id = :bot AND p.user_id = :bot AND c.created_at >= :start
                """
            ),
            {"bot": bot_uid, "start": _utc_day_start()},
        ) or 0
        if int(rep_today) < limits["replies"]:
            pick = await _pick_comment_to_reply(bot_uid)
            if pick:
                um = (
                    "Задача: короткий уважительный ответ на комментарий к твоему посту в ленте.\n"
                    f"Комментарий пользователя:\n{pick['ctext'][:2000]}"
                )
                sys = await _community_system_prompt(
                    um,
                    "Без медицинских назначений; можно КПТ/психологию/образование про грибы. До 900 символов, по-русски.",
                    channel_kind="comment",
                )
                reply = await _openai_text(
                    sys,
                    "Сгенерируй ответ.",
                    max_tokens=500,
                    temperature=0.45,
                    strip_links=True,
                )
                if reply:
                    await bot_add_comment(
                        post_id=int(pick["post_id"]),
                        bot_uid=bot_uid,
                        content=reply,
                        author_name=author_name,
                    )
                    await database.execute(
                        ai_community_bot_settings.update()
                        .where(ai_community_bot_settings.c.id == 1)
                        .values(last_tick_at=datetime.utcnow())
                    )
                    return

    # 2) New post (системный промт и обучающие посты — как в чате, через get_system_prompt)
    if st.get("allow_posts") and await used("posts") < limits["posts"]:
        um = (
            "Задача: написать один пост в ленту сообщества NEUROFUNGI от имени NeuroFungi AI. "
            "Темы: грибы (образовательно), психология, КПТ, провокативная терапия, биохимия грибов только в общенаучном ключе (не назначения). "
            "Включи 1–2 предложения с мягкой статистикой о платформе, используя только эти числа (не выдумывай другие): "
            + json.dumps(stats, ensure_ascii=False)
        )
        task_suffix = "Объём до 2200 символов. Без хэштегов-спама."
        if st.get("allow_telegram_channel", True) and telegram_channel_configured():
            task_suffix += (
                " Пост может дублироваться в Telegram-канал: мотивируй присоединиться к соцсети NEUROFUNGI; "
                "не указывай цены и тарифы; без финансовых обещаний."
            )
        sys = await _community_system_prompt(um, task_suffix, channel_kind="post")
        body = await _openai_text(
            sys,
            "Сгенерируй текст поста для публикации в ленте.",
            max_tokens=1200,
            temperature=0.45,
            strip_links=True,
        )
        if body:
            title = None
            if st.get("allow_story_posts") and random.random() < 0.35:
                title = "Сторис"
            pid = await publish_community_post(
                user_id=bot_uid,
                author_name=author_name,
                content=body,
                title=title,
                image_url=None,
                images_json=None,
                folder_id=None,
                from_telegram=False,
            )
            if pid:
                if st.get("allow_telegram_channel", True) and telegram_channel_configured():
                    await send_neurofungi_post_to_telegram_channel(title=title, body_plain=body)
                if st.get("allow_bug_reports") and random.random() < 0.08:
                    await _notify_admins_bug_report(
                        "Плановая отметка AI: проверьте раздел «Управление AI в сообществе», если заметите аномалии в ленте."
                    )
                await database.execute(
                    ai_community_bot_settings.update()
                    .where(ai_community_bot_settings.c.id == 1)
                    .values(last_tick_at=datetime.utcnow())
                )
                return

    # 3) Comment on someone else's post
    if st.get("allow_comments") and await used("comments") < limits["comments"]:
        prow = await database.fetch_one(
            sa.text(
                """
                SELECT id, user_id, content, title FROM community_posts
                WHERE approved = true AND (user_id IS NULL OR user_id <> :uid)
                ORDER BY random() LIMIT 1
                """
            ),
            {"uid": bot_uid},
        )
        if prow:
            excerpt = ((prow.get("title") or "") + "\n" + (prow.get("content") or ""))[:3000]
            um = "Задача: один дружелюбный комментарий к чужому посту в сообществе.\n" + excerpt
            sys = await _community_system_prompt(
                um, "По-русски, до 500 символов.", channel_kind="comment"
            )
            ctext = await _openai_text(
                sys,
                "Сгенерируй комментарий.",
                max_tokens=400,
                temperature=0.45,
                strip_links=True,
            )
            if ctext:
                await bot_add_comment(
                    post_id=int(prow["id"]),
                    bot_uid=bot_uid,
                    content=ctext,
                    author_name=author_name,
                )
                await database.execute(
                    ai_community_bot_settings.update()
                    .where(ai_community_bot_settings.c.id == 1)
                    .values(last_tick_at=datetime.utcnow())
                )
                return

    # 4) Follow
    if st.get("allow_follow") and await count_today(bot_uid, "community_follows", "follower_id") < limits["follows"]:
        urow2 = await database.fetch_one(
            sa.text(
                """
                SELECT u.id FROM users u
                WHERE u.id <> :bot AND u.primary_user_id IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM community_follows f
                    WHERE f.follower_id = :bot AND f.following_id = u.id
                  )
                ORDER BY random() LIMIT 1
                """
            ),
            {"bot": bot_uid},
        )
        if urow2:
            await bot_follow(bot_uid, int(urow2["id"]))
            await database.execute(
                ai_community_bot_settings.update()
                .where(ai_community_bot_settings.c.id == 1)
                .values(last_tick_at=datetime.utcnow())
            )
            return

    # 5) Unfollow (random from current follows)
    if st.get("allow_unfollow"):
        fol = await database.fetch_one(
            sa.text(
                """
                SELECT following_id FROM community_follows
                WHERE follower_id = :bot ORDER BY random() LIMIT 1
                """
            ),
            {"bot": bot_uid},
        )
        if fol and random.random() < 0.12:
            await bot_unfollow(bot_uid, int(fol["following_id"]))
            await database.execute(
                ai_community_bot_settings.update()
                .where(ai_community_bot_settings.c.id == 1)
                .values(last_tick_at=datetime.utcnow())
            )
            return

    # 6) Profile thought (status) — лимит через thoughts_count_today
    if st.get("allow_profile_thoughts"):
        today_d = datetime.utcnow().date()
        tdate = st.get("thoughts_count_date")
        if tdate is not None and hasattr(tdate, "date"):
            tdate = tdate.date()
        elif tdate is not None and not isinstance(tdate, type(today_d)):
            try:
                from datetime import date as date_cls

                if isinstance(tdate, str):
                    tdate = date_cls.fromisoformat(tdate[:10])
            except Exception:
                tdate = None
        tcount = int(st.get("thoughts_count_today") or 0)
        if tdate != today_d:
            tcount = 0
        if tcount < limits["thoughts"]:
            um = (
                "Задача: одна короткая строка для блока «мысли» в профиле — настроение и работа с сообществом."
            )
            sys = await _community_system_prompt(
                um, "До 200 символов, русский.", channel_kind="comment"
            )
            thought = await _openai_text(
                sys,
                "Сгенерируй статус.",
                max_tokens=120,
                temperature=0.45,
                strip_links=True,
            )
            if thought:
                await database.execute(
                    users.update()
                    .where(users.c.id == bot_uid)
                    .values(profile_thoughts=thought[:1200])
                )
                if st.get("allow_telegram_channel", True) and telegram_channel_configured():
                    await send_neurofungi_thought_to_telegram_channel(thought)
                await database.execute(
                    ai_community_bot_settings.update()
                    .where(ai_community_bot_settings.c.id == 1)
                    .values(
                        thoughts_count_date=today_d,
                        thoughts_count_today=tcount + 1,
                        last_tick_at=datetime.utcnow(),
                    )
                )
                return

    await database.execute(
        ai_community_bot_settings.update()
        .where(ai_community_bot_settings.c.id == 1)
        .values(last_tick_at=datetime.utcnow())
    )
