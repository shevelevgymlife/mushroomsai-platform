"""
migrate_v14 — таблицы мессенджера (чаты, участники, сообщения, реакции).

Идемпотентно: CREATE IF NOT EXISTS. Запуск из heavy_startup.
"""

STEPS = [
    """
    CREATE TABLE IF NOT EXISTS chats (
        id SERIAL PRIMARY KEY,
        type VARCHAR(20) NOT NULL,
        name VARCHAR(255),
        avatar_url TEXT,
        created_by INTEGER,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_members (
        chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL,
        role VARCHAR(20) NOT NULL DEFAULT 'member',
        joined_at TIMESTAMP DEFAULT NOW(),
        last_read_message_id INTEGER,
        PRIMARY KEY (chat_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_messages (
        id SERIAL PRIMARY KEY,
        chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL,
        text TEXT,
        media_url TEXT,
        reply_to_id INTEGER,
        is_edited BOOLEAN DEFAULT false,
        is_deleted BOOLEAN DEFAULT false,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_reactions (
        message_id INTEGER NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL,
        emoji VARCHAR(32) NOT NULL,
        PRIMARY KEY (message_id, user_id, emoji)
    )
    """,
]
