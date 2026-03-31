import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import sqlalchemy as sa

from db.database import database
from config import settings
from services.ops_alerts import maybe_notify_billing, send_daily_summary
from services.ai_community_bot import run_ai_community_bot_job
from services.wellness_journal_service import (
    run_wellness_prompts_due_job,
    run_wellness_weekly_digests_job,
    run_wellness_subscription_renewal_nudges_job,
)

scheduler = AsyncIOScheduler()
_logger = logging.getLogger(__name__)
_scheduler_started: bool = False


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


def start_scheduler() -> None:
    """Планировщик без Telegram: очистка групп, billing, дневная сводка."""
    global _scheduler_started
    if _scheduler_started:
        _logger.warning("start_scheduler: планировщик уже запущен, повторный вызов пропущен")
        return
    _scheduler_started = True
    scheduler.add_job(
        purge_expired_group_messages,
        "interval",
        hours=1,
        id="purge_expired_group_messages",
        replace_existing=True,
    )
    scheduler.add_job(
        maybe_notify_billing,
        "interval",
        hours=12,
        id="maybe_notify_billing",
        replace_existing=True,
    )
    scheduler.add_job(
        send_daily_summary,
        "cron",
        hour=int(getattr(settings, "OPS_NOTIFY_DAILY_SUMMARY_HOUR_UTC", 9) or 9),
        minute=0,
        id="send_daily_summary",
        replace_existing=True,
    )
    scheduler.add_job(
        run_wellness_prompts_due_job,
        "interval",
        hours=6,
        id="wellness_journal_prompts",
        replace_existing=True,
    )
    scheduler.add_job(
        run_wellness_weekly_digests_job,
        "cron",
        hour=10,
        minute=30,
        id="wellness_weekly_digest",
        replace_existing=True,
    )
    scheduler.add_job(
        run_wellness_subscription_renewal_nudges_job,
        "interval",
        hours=12,
        id="wellness_subscription_renewal_nudge",
        replace_existing=True,
    )
    scheduler.add_job(
        run_ai_community_bot_job,
        "interval",
        minutes=15,
        id="ai_community_bot",
        replace_existing=True,
    )
    scheduler.start()
