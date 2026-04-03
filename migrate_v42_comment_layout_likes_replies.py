"""migrate_v42 — комментарии сообщества: reply_to_id + likes_count + likes table."""

STEPS = [
    "ALTER TABLE community_comments ADD COLUMN IF NOT EXISTS reply_to_id INTEGER REFERENCES community_comments(id) ON DELETE SET NULL",
    "ALTER TABLE community_comments ADD COLUMN IF NOT EXISTS likes_count INTEGER NOT NULL DEFAULT 0",
    """CREATE TABLE IF NOT EXISTS community_comment_likes (
        id SERIAL PRIMARY KEY,
        comment_id INTEGER NOT NULL REFERENCES community_comments(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMP DEFAULT NOW(),
        UNIQUE(comment_id, user_id)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_community_comment_likes_comment ON community_comment_likes (comment_id)",
]
