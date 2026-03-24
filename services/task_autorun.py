"""Task auto-run dispatcher (best-effort webhook trigger)."""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)


async def trigger_task_autorun(task_payload: dict[str, Any]) -> tuple[bool, str]:
    """Send accepted task to external executor webhook if configured."""
    url = (getattr(settings, "TASK_AUTORUN_WEBHOOK_URL", "") or "").strip()
    if not url:
        return False, "TASK_AUTORUN_WEBHOOK_URL не задан — автозапуск не выполнен."
    body = {
        "source": "ops_bot",
        "task": task_payload or {},
    }
    secret = (getattr(settings, "TASK_AUTORUN_SECRET", "") or "").strip()
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Task-Autorun-Secret"] = secret
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, headers=headers, json=body)
        if 200 <= resp.status_code < 300:
            return True, f"Автозапуск отправлен ({resp.status_code})."
        return False, f"Webhook вернул {resp.status_code}: {resp.text[:300]}"
    except Exception as e:
        logger.warning("task autorun trigger failed: %s", e)
        return False, f"Ошибка автозапуска: {e}"

