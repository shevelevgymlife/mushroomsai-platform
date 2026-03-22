"""
migrate_v12 — platform_settings (политика создания групп) и поля групп (slow mode, история).
Запуск: python migrate_v12_platform_settings.py (нужен DATABASE_URL в .env)
"""
import asyncio

from sqlalchemy import text

from db.database import database

STEPS = [
    """
    CREATE TABLE IF NOT EXISTS platform_settings (
        key VARCHAR(128) PRIMARY KEY,
        value TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    ALTER TABLE community_groups ADD COLUMN IF NOT EXISTS slow_mode_seconds INTEGER
    """,
    """
    ALTER TABLE community_groups ADD COLUMN IF NOT EXISTS show_history_to_new_members BOOLEAN DEFAULT TRUE
    """,
]


async def main():
    await database.connect()
    try:
        for s in STEPS:
            await database.execute(text(s))
            print("OK:", s.strip().split("\n")[1].strip()[:70])
    finally:
        await database.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
