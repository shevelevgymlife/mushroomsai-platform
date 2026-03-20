"""Migration v10: Add custom_title, blur_for_guests, blur_text to homepage_blocks."""
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

    await db.execute("ALTER TABLE homepage_blocks ADD COLUMN IF NOT EXISTS custom_title TEXT")
    print("Added column: custom_title")

    await db.execute("ALTER TABLE homepage_blocks ADD COLUMN IF NOT EXISTS blur_for_guests BOOLEAN DEFAULT false")
    print("Added column: blur_for_guests")

    await db.execute("ALTER TABLE homepage_blocks ADD COLUMN IF NOT EXISTS blur_text TEXT DEFAULT 'Войти для просмотра'")
    print("Added column: blur_text")

    await db.disconnect()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
