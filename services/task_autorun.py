"""Task auto-run dispatcher (best-effort webhook trigger)."""
from __future__ import annotations

import logging
from typing import Any

import httpx
import sqlalchemy as sa

from config import settings
from db.database import database

logger = logging.getLogger(__name__)


def _task_payload_from_args(
    task_payload: dict[str, Any] | None = None,
    task_text: str = "",
    task_id: int = 0,
    tg_user_id: int = 0,
) -> dict[str, Any]:
    if isinstance(task_payload, dict) and task_payload:
        return dict(task_payload)
    return {
        "id": int(task_id or 0),
        "task_text": (task_text or "").strip(),
        "tg_user_id": int(tg_user_id or 0),
    }


async def trigger_task_autorun(
    task_payload: dict[str, Any] | None = None,
    task_text: str = "",
    task_id: int = 0,
    tg_user_id: int = 0,
) -> tuple[bool, str]:
    """Send accepted task to external executor webhook if configured."""
    url = (getattr(settings, "TASK_AUTORUN_WEBHOOK_URL", "") or "").strip()
    if not url:
        return False, "TASK_AUTORUN_WEBHOOK_URL не задан — автозапуск не выполнен."
    payload = _task_payload_from_args(
        task_payload=task_payload,
        task_text=task_text,
        task_id=task_id,
        tg_user_id=tg_user_id,
    )
    body = {
        "source": "ops_bot",
        "task": payload,
    }
    secret = (
        (getattr(settings, "TASK_AUTORUN_SECRET", "") or "").strip()
        or (getattr(settings, "TASK_AUTORUN_WEBHOOK_TOKEN", "") or "").strip()
    )
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Task-Autorun-Secret"] = secret
        headers["Authorization"] = f"Bearer {secret}"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, headers=headers, json=body)
        if 200 <= resp.status_code < 300:
            return True, f"Автозапуск отправлен ({resp.status_code})."
        return False, f"Webhook вернул {resp.status_code}: {resp.text[:300]}"
    except Exception as e:
        logger.warning("task autorun trigger failed: %s", e)
        return False, f"Ошибка автозапуска: {e}"


async def run_latest_task_from_telegram(tg_user_id: int) -> bool:
    """Load latest task for user and send it to autorun webhook."""
    uid = int(tg_user_id or 0)
    if not uid:
        return False
    row = await database.fetch_one(
        sa.text(
            "SELECT id, task_text FROM bot_task_requests "
            "WHERE tg_user_id = :uid "
            "ORDER BY id DESC LIMIT 1"
        ).bindparams(uid=uid)
    )
    if not row:
        return False
    task_id = int((row or {}).get("id") or 0)
    task_text = str((row or {}).get("task_text") or "").strip()
    if not task_text:
        return False
    ok, msg = await trigger_task_autorun(task_text=task_text, task_id=task_id, tg_user_id=uid)
    try:
        if task_id:
            # Compatibility: different deployments may have different columns.
            await database.execute(
                sa.text(
                    "UPDATE bot_task_requests SET "
                    "status = :status, "
                    "updated_at = NOW(), "
                    "autorun_result = :result "
                    "WHERE id = :id"
                ).bindparams(
                    id=task_id,
                    status=("queued" if ok else "new"),
                    result=(msg[:2000] if msg else ""),
                )
            )
    except Exception:
        pass
    try:
        if task_id and ok:
            await database.execute(
                sa.text(
                    "UPDATE bot_task_requests SET "
                    "autorun_requested = true, "
                    "autorun_started_at = NOW() "
                    "WHERE id = :id"
                ).bindparams(id=task_id)
            )
    except Exception:
        pass
    try:
        if task_id and ok:
            await database.execute(
                sa.text(
                    "UPDATE bot_task_requests SET "
                    "auto_requested = true "
                    "WHERE id = :id"
                ).bindparams(id=task_id)
            )
    except Exception:
        pass
    return bool(ok)

