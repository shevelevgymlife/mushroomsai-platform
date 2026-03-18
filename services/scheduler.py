from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
from db.database import database
from db.models import followups, users

scheduler = AsyncIOScheduler()


async def send_followup_messages(bot):
    """Send scheduled follow-up messages to users."""
    now = datetime.utcnow()
    pending = await database.fetch_all(
        followups.select()
        .where(followups.c.sent == False)
        .where(followups.c.scheduled_at <= now)
    )

    for followup in pending:
        user = await database.fetch_one(
            users.select().where(users.c.id == followup["user_id"])
        )
        if user and user["tg_id"]:
            try:
                await bot.send_message(
                    chat_id=user["tg_id"],
                    text=followup["message"],
                )
                await database.execute(
                    followups.update()
                    .where(followups.c.id == followup["id"])
                    .values(sent=True)
                )
            except Exception:
                pass


def start_scheduler(bot):
    scheduler.add_job(
        send_followup_messages,
        "interval",
        minutes=30,
        args=[bot],
    )
    scheduler.start()
