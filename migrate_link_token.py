"""Migration: add link_token and link_token_expires to users table."""
import asyncio
from db.database import database


async def main():
    await database.connect()
    try:
        await database.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS link_token VARCHAR(64),
            ADD COLUMN IF NOT EXISTS link_token_expires TIMESTAMP
        """)
        print("OK: link_token columns added")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await database.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
