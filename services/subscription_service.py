from datetime import datetime, timedelta, date
from db.database import database
from db.models import users, subscriptions

PLANS = {
    "free":  {"name": "Бесплатный", "price": 0,    "questions_per_day": 5,  "recipes_per_day": 1},
    "start": {"name": "Старт",      "price": 990,  "questions_per_day": -1, "recipes_per_day": -1},
    "pro":   {"name": "Про",        "price": 1990, "questions_per_day": -1, "recipes_per_day": -1},
    "maxi":  {"name": "Макси",      "price": 4999, "questions_per_day": -1, "recipes_per_day": -1},
}


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


async def check_subscription(user_id: int) -> str:
    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not row:
        return "free"

    if row["subscription_end"] and row["subscription_end"] > datetime.utcnow():
        return row["subscription_plan"]

    if row["subscription_plan"] != "free":
        await database.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(subscription_plan="free")
        )
    return "free"


async def can_ask_question(user_id: int) -> bool:
    row = await database.fetch_one(users.select().where(users.c.id == user_id))
    if not row:
        return False

    plan = await check_subscription(user_id)
    if plan in ("start", "pro", "maxi"):
        return True

    today = date.today()
    if row["last_reset"] != today:
        await database.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(daily_questions=0, daily_recipes=0, last_reset=today)
        )
        return True

    return row["daily_questions"] < 5


async def increment_question_count(user_id: int):
    await database.execute(
        users.update()
        .where(users.c.id == user_id)
        .values(daily_questions=users.c.daily_questions + 1)
    )
