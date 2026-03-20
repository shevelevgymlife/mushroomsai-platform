"""Migration v8: Add position and access_level columns to homepage_blocks."""
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

    await db.execute("ALTER TABLE homepage_blocks ADD COLUMN IF NOT EXISTS position INTEGER DEFAULT 0")
    print("Added column: position")

    await db.execute("ALTER TABLE homepage_blocks ADD COLUMN IF NOT EXISTS access_level TEXT DEFAULT 'all'")
    print("Added column: access_level")

    # Set default positions based on logical display order
    order = ["hero", "features", "pricing", "community", "shop"]
    for i, block_name in enumerate(order):
        await db.execute(
            "UPDATE homepage_blocks SET position = :pos WHERE block_name = :name",
            {"pos": i, "name": block_name}
        )
    print("Set default positions:", order)

    await db.disconnect()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
