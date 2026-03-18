import secrets
import string
from db.database import database
from db.models import users, referrals


async def generate_referral_code() -> str:
    while True:
        code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        existing = await database.fetch_one(users.select().where(users.c.referral_code == code))
        if not existing:
            return code


async def process_referral(new_user_id: int, referral_code: str) -> bool:
    referrer = await database.fetch_one(users.select().where(users.c.referral_code == referral_code))
    if not referrer or referrer["id"] == new_user_id:
        return False

    await database.execute(
        users.update()
        .where(users.c.id == new_user_id)
        .values(referred_by=referrer["id"])
    )
    await database.execute(
        referrals.insert().values(
            referrer_id=referrer["id"],
            referred_id=new_user_id,
        )
    )
    return True


async def apply_referral_bonus(referral_id: int):
    """Apply 50% discount bonus when referral subscribes."""
    referral = await database.fetch_one(referrals.select().where(referrals.c.id == referral_id))
    if not referral or referral["bonus_applied"]:
        return

    await database.execute(
        referrals.update()
        .where(referrals.c.id == referral_id)
        .values(bonus_applied=True)
    )


async def get_referral_stats(user_id: int) -> dict:
    refs = await database.fetch_all(
        referrals.select().where(referrals.c.referrer_id == user_id)
    )
    return {
        "total": len(refs),
        "bonus_applied": sum(1 for r in refs if r["bonus_applied"]),
    }
