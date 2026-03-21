import secrets
import string
import sqlalchemy as sa
from db.database import database
from db.models import users, referrals
from services.subscription_service import PLANS


def referral_bonus_per_invite_rub() -> int:
    """10% от месячной цены тарифа Старт (баллы на продление)."""
    return max(1, int(PLANS["start"]["price"] * 0.1))


async def generate_referral_code() -> str:
    while True:
        code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        existing = await database.fetch_one(users.select().where(users.c.referral_code == code))
        if not existing:
            return code


async def process_referral(new_user_id: int, referral_code: str) -> bool:
    """
    Привязать приглашённого к рефереру. Один раз на пользователя.
    Начислить рефереру referral_balance (+10% от цены Старт).
    """
    ref = (referral_code or "").strip().upper()
    if not ref or len(ref) > 20:
        return False

    new_u = await database.fetch_one(users.select().where(users.c.id == new_user_id))
    if not new_u or new_u.get("referred_by"):
        return False

    referrer = await database.fetch_one(users.select().where(users.c.referral_code == ref))
    if not referrer or referrer["id"] == new_user_id:
        return False

    dup = await database.fetch_one(
        referrals.select().where(referrals.c.referred_id == new_user_id)
    )
    if dup:
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
            bonus_applied=True,
        )
    )

    bonus = referral_bonus_per_invite_rub()
    await database.execute(
        sa.text(
            "UPDATE users SET referral_balance = COALESCE(referral_balance, 0) + :b "
            "WHERE id = :rid"
        ),
        {"b": bonus, "rid": referrer["id"]},
    )
    return True


async def apply_pending_web_invite(request, new_user_id: int) -> None:
    """После веб-регистрации: cookie invite_ref."""
    code = (request.cookies.get("invite_ref") or "").strip().upper()
    if code:
        await process_referral(new_user_id, code)


def clear_invite_cookie(response) -> None:
    response.delete_cookie("invite_ref", path="/")


def attach_invite_ref_from_query(request, response) -> None:
    ref = (request.query_params.get("ref") or "").strip().upper()
    if ref and 2 <= len(ref) <= 20 and all(c.isalnum() for c in ref):
        response.set_cookie(
            "invite_ref",
            ref,
            max_age=90 * 24 * 3600,
            path="/",
            httponly=True,
            samesite="lax",
        )


async def finalize_web_referral(request, response, user_id: int) -> None:
    await apply_pending_web_invite(request, user_id)
    clear_invite_cookie(response)


async def apply_referral_bonus(referral_id: int):
    """Устарело: бонус начисляется в process_referral."""
    pass


async def get_referral_stats(user_id: int) -> dict:
    refs = await database.fetch_all(
        referrals.select().where(referrals.c.referrer_id == user_id)
    )
    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    bal = row.get("referral_balance") if row else 0
    try:
        bal_f = float(bal or 0)
    except (TypeError, ValueError):
        bal_f = 0.0
    return {
        "total": len(refs),
        "bonus_applied": sum(1 for r in refs if r["bonus_applied"]),
        "balance_rub": round(bal_f, 2),
    }
