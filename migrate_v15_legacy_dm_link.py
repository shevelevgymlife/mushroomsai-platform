"""
migrate_v15 — связка старых ЛС (direct_messages) с chat_messages.

Запуск: python migrate_v15_legacy_dm_link.py
"""
import asyncio

from sqlalchemy import text

from db.database import database

STEPS = [
    "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS legacy_direct_message_id INTEGER UNIQUE",
]


async def main():
    await database.connect()
    try:
        for s in STEPS:
            await database.execute(text(s))
            print("OK:", s[:70])
    finally:
        await database.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
