"""
migrate_v16 — users.profile_public_cards_json (свайп-блоки профиля: крипто, соцсети).

Запуск: python migrate_v16_profile_public_cards.py (нужен DATABASE_URL в .env)
"""
import asyncio

from sqlalchemy import text

from db.database import database

STEPS = [
    """
    DO $$ BEGIN
        ALTER TABLE users ADD COLUMN profile_public_cards_json TEXT;
    EXCEPTION
        WHEN duplicate_column THEN NULL;
    END $$
    """,
]


async def main():
    await database.connect()
    try:
        for s in STEPS:
            await database.execute(text(s))
            print("OK:", s.strip()[:80])
    finally:
        await database.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
