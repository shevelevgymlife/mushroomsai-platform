from datetime import datetime, timedelta, date

from config import settings
from db.database import database
from db.models import users, subscriptions

PLANS = {
    "free":  {"name": "Бесплатный", "price": 0,    "questions_per_day": 5,  "recipes_per_day": 1},
    "start": {"name": "Старт",      "price": 990,  "questions_per_day": -1, "recipes_per_day": -1},
    "pro":   {"name": "Про",        "price": 1990, "questions_per_day": -1, "recipes_per_day": -1},
    "maxi":  {"name": "Макси",      "price": 4999, "questions_per_day": -1, "recipes_per_day": -1},
}

START_TRIAL_DAYS = 3


async def activate_subscription(user_id: int, plan: str, months: int = 1):
    if plan not in PLANS:
        return False

    end_date = datetime.utcnow() + timedelta(days=30 * months)
    price = PLANS[plan]["price"] * months

    await database.execute(
        subscriptions.insert().values(
            user_id=user_id,
            plan=plan,
            price=price,
            end_date=end_date,
            active=True,
        )
    )
    await database.execute(
        users.update()
        .where(users.c.id == user_id)
        .values(subscription_plan=plan, subscription_end=end_date)
    )
    return True


async def _notify_trial_started(user_id: int) -> None:
    from services.tg_notify import notify_user_telegram

    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not row:
        return
    tg = row.get("tg_id") or row.get("linked_tg_id")
    if not tg:
        return
    site = (settings.SITE_URL or "").rstrip("/")
    sub_url = f"{site}/subscriptions" if site else "/subscriptions"
    text = (
        "🎁 <b>Пробный доступ «Старт» на 3 дня</b>\n"
        "Открыты лента, магазин, сообщения и остальные возможности тарифа Старт.\n"
        f"<a href=\"{sub_url}\">Оформить подписку после окончания пробного периода</a>"
    )
    await notify_user_telegram(int(tg), text)


async def _notify_trial_ended(user_id: int) -> None:
    from services.tg_notify import notify_user_telegram

    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not row:
        return
    tg = row.get("tg_id") or row.get("linked_tg_id")
    if not tg:
        return
    site = (settings.SITE_URL or "").rstrip("/")
    sub_url = f"{site}/subscriptions" if site else "/subscriptions"
    text = (
        "⏳ <b>Пробный период «Старт» завершён</b>\n"
        "Доступ к ленте и функциям тарифа Старт приостановлен.\n"
        f"<a href=\"{sub_url}\">Выбрать подписку Старт, Про или Макси</a>"
    )
    await notify_user_telegram(int(tg), text)


async def claim_start_trial(user_id: int) -> dict:
    """Одноразовая пробная подписка «как Старт» на START_TRIAL_DAYS дней."""
    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not row:
        return {"ok": False, "error": "not_found"}
    role = (row.get("role") or "user").lower()
    if role in ("admin", "moderator"):
        return {"ok": False, "error": "staff"}
    if row.get("start_trial_claimed_at"):
        return {"ok": False, "error": "already_used"}
    now = datetime.utcnow()
    if row.get("subscription_end") and row["subscription_end"] > now:
        p = (row.get("subscription_plan") or "free").lower()
        if p in ("start", "pro", "maxi"):
            return {"ok": False, "error": "has_paid"}
    until = now + timedelta(days=START_TRIAL_DAYS)
    await database.execute(
        users.update()
        .where(users.c.id == user_id)
        .values(
            start_trial_claimed_at=now,
            start_trial_until=until,
            start_trial_end_notified=False,
        )
    )
    await _notify_trial_started(user_id)
    return {"ok": True, "until": until.isoformat() + "Z"}


async def check_subscription(user_id: int) -> str:
    """
    Эффективный тариф для доступа: оплаченный активный, иначе активный пробный «Старт»,
    иначе free. При истечении оплаченного — сброс в free; при истечении пробного — уведомление в Telegram.
    """
    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not row:
        return "free"

    now = datetime.utcnow()
    sub_end = row.get("subscription_end")
    stored_plan = (row.get("subscription_plan") or "free").lower()

    # Активная оплата
    if sub_end and sub_end > now and stored_plan in ("start", "pro", "maxi"):
        return stored_plan

    # Просроченная оплата → free в БД
    if stored_plan != "free" and (not sub_end or sub_end <= now):
        await database.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(subscription_plan="free", subscription_end=None)
        )
        row = await database.fetch_one(users.select().where(users.c.id == user_id)) or row

    # Пробный «Старт»
    trial_until = row.get("start_trial_until")
    if trial_until and trial_until > now:
        return "start"

    # Пробный истёк — одноразовое уведомление
    if (
        row.get("start_trial_claimed_at")
        and trial_until
        and trial_until <= now
        and not row.get("start_trial_end_notified")
    ):
        await database.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(start_trial_end_notified=True)
        )
        await _notify_trial_ended(user_id)

    return "free"


async def web_default_home_path(user_id: int) -> str:
    """
    Куда вести с главной / после входа, если нет явного next:
    без доступа к ленте (free без пробного) → страница подписок;
    с доступом (оплата или активный пробный «Старт») → профиль в сообществе.
    """
    uid = int(user_id)
    plan = await check_subscription(uid)
    if plan == "free":
        return "/subscriptions"
    return f"/community/profile/{uid}"


async def can_ask_question(user_id: int) -> bool:
    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not row:
        return False

    role = (row.get("role") or "user").lower()
    if role in ("admin", "moderator"):
        return True

    plan = await check_subscription(user_id)
    if plan in ("start", "pro", "maxi"):
        return True

    daily_cap = int(PLANS.get("free", {}).get("questions_per_day") or 5)
    if daily_cap < 0:
        return True

    today = date.today()
    if row["last_reset"] != today:
        await database.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(daily_questions=0, daily_recipes=0, last_reset=today)
        )
        return True

    return (row["daily_questions"] or 0) < daily_cap


async def increment_question_count(user_id: int):
    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    if row and (row.get("role") or "user").lower() in ("admin", "moderator"):
        return
    plan = await check_subscription(user_id)
    if plan in ("start", "pro", "maxi"):
        return
    await database.execute(
        users.update()
        .where(users.c.id == user_id)
        .values(daily_questions=users.c.daily_questions + 1)
    )
