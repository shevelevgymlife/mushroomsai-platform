"""
migrate_v11.py — Tariff system: verify schema supports all plans (free/start/pro/maxi).
The users table already has the required columns:
  subscription_plan  → stores plan value (free/start/pro/maxi)
  subscription_end   → stores expiry datetime (plan_expires_at)
  daily_questions    → stores AI questions used today (ai_questions_today)
  last_reset         → stores the date questions were last reset (ai_questions_reset_date)
No schema changes are required; this script verifies and prints current state.
"""
import asyncio
from db.database import database


async def main():
    await database.connect()

    try:
        from sqlalchemy import text
        row = await database.fetch_one(
            text("SELECT subscription_plan, subscription_end, daily_questions, last_reset "
                 "FROM users LIMIT 1")
        )
        print("OK subscription_plan (plan)")
        print("OK subscription_end (plan_expires_at)")
        print("OK daily_questions (ai_questions_today)")
        print("OK last_reset (ai_questions_reset_date)")
    except Exception as e:
        print(f"Schema check failed: {e}")

    # Count users per plan
    try:
        from sqlalchemy import text as _text
        for plan in ("free", "start", "pro", "maxi"):
            cnt = await database.fetch_val(
                _text(f"SELECT COUNT(*) FROM users WHERE subscription_plan = '{plan}'")
            )
            print(f"  Plan '{plan}': {cnt} users")
    except Exception as e:
        print(f"Stats failed: {e}")

    print("Done.")
    await database.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
