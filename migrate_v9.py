"""Migration v9: Create dashboard_blocks and user_block_overrides tables."""
import asyncio
import os
import databases

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://mushroomsai_db_user:vg9FRq2ix6FJwJnVPhApc9lTxVbRnk1r@dpg-d6t8dti4d50c73c54ceg-a.oregon-postgres.render.com/mushroomsai_db"
)


async def main():
    db = databases.Database(DATABASE_URL)
    await db.connect()

    await db.execute("""
        CREATE TABLE IF NOT EXISTS dashboard_blocks (
            id SERIAL PRIMARY KEY,
            block_key TEXT UNIQUE NOT NULL,
            block_name TEXT NOT NULL,
            is_visible BOOLEAN DEFAULT true,
            position INTEGER DEFAULT 0,
            access_level TEXT DEFAULT 'all',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    print("Created table: dashboard_blocks")

    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_block_overrides (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            block_key TEXT NOT NULL,
            is_visible BOOLEAN,
            custom_name TEXT,
            UNIQUE(user_id, block_key)
        )
    """)
    print("Created table: user_block_overrides")

    blocks = [
        ("ai_chat",       "AI Консультант",           0),
        ("messages",      "Сообщения",                1),
        ("community",     "Сообщество",               2),
        ("shop",          "Магазин",                  3),
        ("profile_photo", "Фото профиля",             4),
        ("posts",         "Посты",                    5),
        ("tariffs",       "Тарифы и подписка",        6),
        ("referral",      "Реферальная программа",    7),
        ("knowledge_base","База знаний",              8),
    ]
    for block_key, block_name, position in blocks:
        await db.execute(
            "INSERT INTO dashboard_blocks (block_key, block_name, position) "
            "VALUES (:k, :n, :p) ON CONFLICT (block_key) DO NOTHING",
            {"k": block_key, "n": block_name, "p": position}
        )
    print("Inserted 9 default dashboard_blocks")

    await db.disconnect()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
