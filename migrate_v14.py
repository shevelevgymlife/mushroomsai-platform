"""
migrate_v14 — мессенджер: chats, chat_members, chat_messages, chat_reactions.
Плюс last_read_message_id в chat_members для бейджа непрочитанных.

Запуск: python migrate_v14.py (нужен DATABASE_URL в .env)
"""
import asyncio

from sqlalchemy import text

from db.database import database

STEPS = [
    """
    CREATE TABLE IF NOT EXISTS chats (
        id SERIAL PRIMARY KEY,
        type VARCHAR(20) NOT NULL CHECK (type IN ('personal', 'group')),
        name VARCHAR(255),
        avatar_url TEXT,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_members (
        id SERIAL PRIMARY KEY,
        chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role VARCHAR(20) NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member')),
        joined_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
        last_read_message_id INTEGER,
        UNIQUE(chat_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_messages (
        id SERIAL PRIMARY KEY,
        chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        text TEXT,
        media_url TEXT,
        reply_to_id INTEGER REFERENCES chat_messages(id) ON DELETE SET NULL,
        is_edited BOOLEAN DEFAULT FALSE,
        is_deleted BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_reactions (
        id SERIAL PRIMARY KEY,
        message_id INTEGER NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        emoji VARCHAR(32) NOT NULL,
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
        UNIQUE(message_id, user_id, emoji)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_chat_members_user_id ON chat_members (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_chat_members_chat_id ON chat_members (chat_id)",
    "CREATE INDEX IF NOT EXISTS ix_chat_messages_chat_id_id ON chat_messages (chat_id, id)",
    "CREATE INDEX IF NOT EXISTS ix_chat_reactions_message_id ON chat_reactions (message_id)",
]


async def main():
    await database.connect()
    try:
        for s in STEPS:
            await database.execute(text(s))
            print("OK:", s.strip().split("\n")[1].strip()[:72])
    finally:
        await database.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
