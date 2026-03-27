"""Migration v14: Add music_tracks table and user music player columns."""
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
        CREATE TABLE IF NOT EXISTS music_tracks (
            id SERIAL PRIMARY KEY,
            title VARCHAR(255),
            gdrive_file_id VARCHAR(255),
            gdrive_url TEXT,
            is_active BOOLEAN DEFAULT true,
            position INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    print("Table music_tracks: OK")

    await db.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS music_player_enabled BOOLEAN DEFAULT false")
    print("Column music_player_enabled: OK")

    await db.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS music_player_position VARCHAR(50) DEFAULT 'bottom-right'")
    print("Column music_player_position: OK")

    await db.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS music_player_volume FLOAT DEFAULT 0.5")
    print("Column music_player_volume: OK")

    await db.disconnect()
    print("Migration v14 done.")


if __name__ == "__main__":
    asyncio.run(main())
