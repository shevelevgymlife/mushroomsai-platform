"""
migrate_v19 — блокировки ЛС, автоудаление (видимость сообщений для участника).
"""

STEPS = [
    "ALTER TABLE chat_members ADD COLUMN IF NOT EXISTS auto_delete_ttl_seconds INTEGER",
    """
    CREATE TABLE IF NOT EXISTS dm_user_blocks (
        blocker_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        blocked_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMP DEFAULT NOW(),
        PRIMARY KEY (blocker_id, blocked_id),
        CHECK (blocker_id <> blocked_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_dm_user_blocks_blocked ON dm_user_blocks (blocked_id)",
]
