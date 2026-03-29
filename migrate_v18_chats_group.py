"""
migrate_v18 — группы: описание, настройки JSON, баны, аудит, mute в участниках.
"""

STEPS = [
    "ALTER TABLE chats ADD COLUMN IF NOT EXISTS description TEXT",
    "ALTER TABLE chats ADD COLUMN IF NOT EXISTS group_settings_json TEXT",
    "ALTER TABLE chat_members ADD COLUMN IF NOT EXISTS mute_notifications BOOLEAN DEFAULT false",
    """
    CREATE TABLE IF NOT EXISTS chat_group_bans (
        chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL,
        banned_by INTEGER,
        created_at TIMESTAMP DEFAULT NOW(),
        PRIMARY KEY (chat_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_group_audit (
        id SERIAL PRIMARY KEY,
        chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
        actor_id INTEGER,
        action VARCHAR(64) NOT NULL,
        detail TEXT,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
]
