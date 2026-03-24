from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime

import sqlalchemy as sa

from db.database import database
from db.models import followups, users
from config import settings
from services.ops_alerts import maybe_notify_billing, send_daily_summary

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


async def purge_expired_group_messages():
    """Удаляет сообщения групп старше message_retention_days (если задано у группы)."""
    try:
        await database.execute(
            sa.text("""
                DELETE FROM community_group_messages cgm
                USING community_groups cg
                WHERE cgm.group_id = cg.id
                  AND cg.message_retention_days IS NOT NULL
                  AND cgm.created_at < NOW() - (cg.message_retention_days * INTERVAL '1 day')
            """)
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
    scheduler.add_job(
        purge_expired_group_messages,
        "interval",
        hours=1,
    )
    scheduler.add_job(
        maybe_notify_billing,
        "interval",
        hours=12,
    )
    scheduler.add_job(
        send_daily_summary,
        "cron",
        hour=int(getattr(settings, "OPS_NOTIFY_DAILY_SUMMARY_HOUR_UTC", 9) or 9),
        minute=0,
    )
    scheduler.start()
