"""Migration v7: Create homepage_blocks table."""
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
        CREATE TABLE IF NOT EXISTS homepage_blocks (
            id SERIAL PRIMARY KEY,
            block_name TEXT UNIQUE NOT NULL,
            title TEXT,
            subtitle TEXT,
            content TEXT,
            is_visible BOOLEAN DEFAULT true,
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    print("Created homepage_blocks table")

    rows = [
        ("hero",      "Функциональные грибы для здоровья", "AI-консультант Евгения Шевелева", ""),
        ("features",  "Что умеет AI",                      "Персональные рекомендации по грибам", ""),
        ("shop",      "Магазин",                           "Качественные экстракты функциональных грибов", ""),
        ("community", "Сообщество",                        "Живые обсуждения участников", ""),
        ("pricing",   "Тарифы",                            "Выберите подходящий план", ""),
    ]
    for block_name, title, subtitle, content in rows:
        await db.execute("""
            INSERT INTO homepage_blocks (block_name, title, subtitle, content)
            VALUES (:n, :t, :s, :c)
            ON CONFLICT (block_name) DO NOTHING
        """, {"n": block_name, "t": title, "s": subtitle, "c": content})
    print("Inserted default homepage blocks")

    await db.disconnect()
    print("Done.")

if __name__ == "__main__":
    asyncio.run(main())
