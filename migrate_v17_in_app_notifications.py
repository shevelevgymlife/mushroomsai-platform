"""
migrate_v17 — in_app_notifications + users.notification_prefs_json.

Запуск: python migrate_v17_in_app_notifications.py
"""
import asyncio

from sqlalchemy import text

from db.database import database

STEPS = [
    """
    DO $$ BEGIN
        ALTER TABLE users ADD COLUMN notification_prefs_json TEXT;
    EXCEPTION WHEN duplicate_column THEN NULL;
    END $$
    """,
    """
    CREATE TABLE IF NOT EXISTS in_app_notifications (
        id SERIAL PRIMARY KEY,
        recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        ntype VARCHAR(40) NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL DEFAULT '',
        link_url TEXT,
        source_kind VARCHAR(32),
        source_id INTEGER,
        read_at TIMESTAMP,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        meta_json TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_in_app_notifications_recipient_created ON in_app_notifications (recipient_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_in_app_notifications_recipient_unread ON in_app_notifications (recipient_id) WHERE read_at IS NULL",
    """
    INSERT INTO in_app_notifications (recipient_id, actor_id, ntype, title, body, link_url, source_kind, source_id, read_at, created_at)
    SELECT p.user_id, l.user_id, 'post_like', 'Лайк поста',
      COALESCE(u.name, 'Участник') || ' оценил(а) ваш пост',
      '/community/post/' || l.post_id::text,
      'community_like', l.id,
      CASE WHEN l.seen_by_post_owner THEN NOW() ELSE NULL END,
      NOW()
    FROM community_likes l
    INNER JOIN community_posts p ON p.id = l.post_id
    INNER JOIN users u ON u.id = l.user_id
    WHERE p.user_id IS NOT NULL AND l.user_id <> p.user_id
      AND NOT EXISTS (
        SELECT 1 FROM in_app_notifications x WHERE x.source_kind = 'community_like' AND x.source_id = l.id
      )
    """,
    """
    INSERT INTO in_app_notifications (recipient_id, actor_id, ntype, title, body, link_url, source_kind, source_id, read_at, created_at)
    SELECT p.user_id, c.user_id, 'comment', 'Комментарий',
      COALESCE(u.name, 'Участник') || ': ' || LEFT(TRIM(c.content), 200),
      '/community/post/' || c.post_id::text,
      'community_comment', c.id,
      CASE WHEN c.seen_by_post_owner THEN NOW() ELSE NULL END,
      COALESCE(c.created_at, NOW())
    FROM community_comments c
    INNER JOIN community_posts p ON p.id = c.post_id
    INNER JOIN users u ON u.id = c.user_id
    WHERE p.user_id IS NOT NULL AND c.user_id IS NOT NULL AND c.user_id <> p.user_id
      AND NOT EXISTS (
        SELECT 1 FROM in_app_notifications x WHERE x.source_kind = 'community_comment' AND x.source_id = c.id
      )
    """,
    """
    INSERT INTO in_app_notifications (recipient_id, actor_id, ntype, title, body, link_url, source_kind, source_id, read_at, created_at)
    SELECT pl.liked_user_id, pl.user_id, 'profile_like', 'Лайк профиля',
      COALESCE(u.name, 'Участник') || ' оценил(а) ваш профиль',
      '/community/profile/' || pl.liked_user_id::text,
      'profile_like', pl.id,
      CASE WHEN pl.seen_by_owner THEN NOW() ELSE NULL END,
      COALESCE(pl.created_at, NOW())
    FROM profile_likes pl
    INNER JOIN users u ON u.id = pl.user_id
    WHERE pl.user_id <> pl.liked_user_id
      AND NOT EXISTS (
        SELECT 1 FROM in_app_notifications x WHERE x.source_kind = 'profile_like' AND x.source_id = pl.id
      )
    """,
    """
    INSERT INTO in_app_notifications (recipient_id, actor_id, ntype, title, body, link_url, source_kind, source_id, read_at, created_at)
    SELECT dm.recipient_id, dm.sender_id, 'message', 'Личное сообщение',
      COALESCE(u.name, 'Участник') || ': ' || LEFT(TRIM(dm.text), 200),
      '/chats?open_user=' || dm.sender_id::text,
      'direct_message', dm.id,
      CASE WHEN dm.is_read THEN NOW() ELSE NULL END,
      COALESCE(dm.created_at, NOW())
    FROM direct_messages dm
    INNER JOIN users u ON u.id = dm.sender_id
    WHERE dm.is_system = false AND dm.sender_id <> dm.recipient_id
      AND NOT EXISTS (
        SELECT 1 FROM in_app_notifications x WHERE x.source_kind = 'direct_message' AND x.source_id = dm.id
      )
    """,
    """
    INSERT INTO in_app_notifications (recipient_id, actor_id, ntype, title, body, link_url, source_kind, source_id, read_at, created_at)
    SELECT f.following_id, f.follower_id, 'follower', 'Новый подписчик',
      COALESCE(u.name, 'Участник') || ' подписался(ась) на вас',
      '/community/profile/' || f.follower_id::text,
      'community_follow', f.id,
      NULL,
      COALESCE(f.created_at, NOW())
    FROM community_follows f
    INNER JOIN users u ON u.id = f.follower_id
    WHERE f.follower_id <> f.following_id
      AND NOT EXISTS (
        SELECT 1 FROM in_app_notifications x WHERE x.source_kind = 'community_follow' AND x.source_id = f.id
      )
    """,
]


async def main():
    await database.connect()
    try:
        for s in STEPS:
            await database.execute(text(s))
            print("OK:", s.strip()[:70])
    finally:
        await database.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
