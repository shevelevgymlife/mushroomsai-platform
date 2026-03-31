"""Фоновая обработка истёкших подписок: сброс в free + уведомления (как в check_subscription)."""
from __future__ import annotations

import logging

import sqlalchemy as sa

from db.database import database
from services.subscription_service import check_subscription

logger = logging.getLogger(__name__)


async def run_subscription_expiry_sweep_job() -> None:
    """
    Пользователи с платным планом и прошедшей датой subscription_end не всегда заходят на сайт —
    тогда check_subscription не вызывается. Раз в интервал обходим таких и применяем ту же логику.
    """
    try:
        rows = await database.fetch_all(
            sa.text(
                """
                SELECT id FROM users
                WHERE subscription_plan IN ('start', 'pro', 'maxi')
                  AND subscription_end IS NOT NULL
                  AND subscription_end <= NOW()
                  AND COALESCE(subscription_admin_granted, false) = false
                """
            )
        )
    except Exception:
        logger.exception("subscription_expiry_sweep: select failed")
        return

    for r in rows:
        uid = int(r["id"])
        try:
            await check_subscription(uid)
        except Exception:
            logger.exception("subscription_expiry_sweep: check_subscription uid=%s", uid)
