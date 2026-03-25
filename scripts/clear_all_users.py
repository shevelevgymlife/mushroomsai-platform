"""
One-time script: delete ALL users and related data.
Run in Render Shell:
    python scripts/clear_all_users.py
"""
import asyncio
from db.database import database


async def main():
    await database.connect()
    try:
        # Delete in order to respect FK constraints
        for table in [
            "order_items", "orders", "product_reviews",
            "follows", "post_likes", "post_comments", "posts",
            "group_members", "group_messages",
            "direct_messages", "notifications",
            "leads", "messages", "sessions",
        ]:
            try:
                await database.execute(f"DELETE FROM {table}")
                print(f"  cleared {table}")
            except Exception as e:
                print(f"  skip {table}: {e}")

        await database.execute("DELETE FROM users")
        print("OK: all users deleted")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await database.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
