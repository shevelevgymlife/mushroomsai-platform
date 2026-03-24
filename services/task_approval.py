"""Подтверждение задач (раньше через Telegram; интеграция отключена, остаётся БД и ожидание)."""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from typing import Any

import sqlalchemy as sa

from config import settings
from db.database import database

def _chat_id() -> int:
    try:
        return int(getattr(settings, "ADMIN_TG_ID", 0) or 0)
    except Exception:
        return 0


def _new_request_id() -> str:
    return secrets.token_urlsafe(16)


def _deadline_ts(timeout_sec: int = 1800) -> int:
    return int(time.time()) + max(30, int(timeout_sec or 1800))


async def _has_json_schema() -> bool:
    try:
        v = await database.fetch_val(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name='task_confirmations' AND column_name='request_id' LIMIT 1"
            )
        )
        return bool(v)
    except Exception:
        return False


async def _send_approval_prompt(request_id: str, question: str, details: str = "") -> None:
    return


async def _answer_callback(callback_query_id: str, text: str = "") -> None:
    return


async def _fetch_callback_updates() -> list[dict[str, Any]]:
    return []


async def _consume_callback_for_request(request_id: str) -> bool | None:
    return None


async def _load_request(request_id: str) -> dict[str, Any] | None:
    if await _has_json_schema():
        row = await database.fetch_one(
            sa.text("SELECT payload_json FROM task_confirmations WHERE request_id = :rid").bindparams(rid=request_id)
        )
        if not row or not row.get("payload_json"):
            return None
        try:
            return json.loads(row["payload_json"])
        except Exception:
            return None
    row = await database.fetch_one(
        sa.text(
            "SELECT token, question, status, "
            "COALESCE(answer_text,'') AS answer_text, answer_by, "
            "EXTRACT(EPOCH FROM created_at)::BIGINT AS created_at_ts, "
            "COALESCE(EXTRACT(EPOCH FROM answered_at)::BIGINT,0) AS decided_at_ts "
            "FROM task_confirmations WHERE token=:rid"
        ).bindparams(rid=request_id)
    )
    if not row:
        return None
    return {
        "request_id": row.get("token"),
        "question": row.get("question") or "",
        "details": "",
        "status": row.get("status") or "pending",
        "decision": "yes" if str(row.get("answer_text") or "").lower() in ("yes", "approved") else ("no" if str(row.get("answer_text") or "").lower() in ("no", "rejected") else ""),
        "created_at_ts": int(row.get("created_at_ts") or 0),
        "expires_at_ts": 0,
        "decided_at_ts": int(row.get("decided_at_ts") or 0),
        "decided_by_chat_id": int(row.get("answer_by") or 0),
    }


async def _save_request(request_id: str, payload: dict[str, Any]) -> None:
    if await _has_json_schema():
        await database.execute(
            sa.text("UPDATE task_confirmations SET payload_json = :payload WHERE request_id = :rid").bindparams(
                rid=request_id, payload=json.dumps(payload, ensure_ascii=False)
            )
        )
        return
    status = str(payload.get("status") or "pending")
    decision = str(payload.get("decision") or "")
    answer_text = "yes" if decision == "yes" else ("no" if decision == "no" else None)
    answer_by = int(payload.get("decided_by_chat_id") or 0) or None
    await database.execute(
        sa.text(
            "UPDATE task_confirmations SET "
            "question=:q, status=:st, answer_text=:at, answer_by=:ab, "
            "answered_at=CASE WHEN :st='pending' THEN NULL ELSE NOW() END "
            "WHERE token=:rid"
        ).bindparams(
            rid=request_id,
            q=str(payload.get("question") or ""),
            st=status,
            at=answer_text,
            ab=answer_by,
        )
    )


async def create_confirmation_request(
    question: str,
    details: str = "",
    action_key: str = "",
    timeout_sec: int = 1800,
) -> dict[str, Any] | None:
    q = (question or "").strip()
    if not q:
        return None
    rid = (action_key or "").strip()[:120] or _new_request_id()
    row = {
        "request_id": rid,
        "question": q[:1000],
        "details": (details or "").strip()[:3000],
        "status": "pending",
        "decision": "",
        "created_at_ts": int(time.time()),
        "expires_at_ts": _deadline_ts(timeout_sec),
        "decided_at_ts": 0,
        "decided_by_chat_id": 0,
    }
    if await _has_json_schema():
        await database.execute(
            sa.text(
                "INSERT INTO task_confirmations (request_id, payload_json, created_at) "
                "VALUES (:rid, :payload, NOW()) "
                "ON CONFLICT (request_id) DO UPDATE SET payload_json = :payload"
            ).bindparams(rid=rid, payload=json.dumps(row, ensure_ascii=False))
        )
    else:
        await database.execute(
            sa.text(
                "INSERT INTO task_confirmations "
                "(token, question, status, requester, chat_id, created_at) "
                "VALUES (:rid, :q, 'pending', :req, :cid, NOW()) "
                "ON CONFLICT (token) DO UPDATE SET "
                "question=EXCLUDED.question, status='pending', "
                "requester=EXCLUDED.requester, chat_id=EXCLUDED.chat_id, "
                "answer_by=NULL, answer_text=NULL, answered_at=NULL"
            ).bindparams(
                rid=rid,
                q=row["question"],
                req="agent",
                cid=_chat_id(),
            )
        )
    await _send_approval_prompt(request_id=rid, question=row["question"], details=row["details"])
    return row


async def process_confirmation_decision(
    request_id: str,
    approve: bool,
    chat_id: int,
) -> tuple[bool, str]:
    payload = await _load_request(request_id)
    if not payload:
        return False, "Запрос не найден."
    if payload.get("status") != "pending":
        return False, "Этот запрос уже обработан."
    if int(payload.get("expires_at_ts") or 0) < int(time.time()):
        payload["status"] = "expired"
        await _save_request(request_id, payload)
        return False, "Срок подтверждения истёк."
    payload["status"] = "approved" if approve else "rejected"
    payload["decision"] = "yes" if approve else "no"
    payload["decided_at_ts"] = int(time.time())
    payload["decided_by_chat_id"] = int(chat_id or 0)
    await _save_request(request_id, payload)
    if approve:
        return True, "Подтверждено. Продолжаю задачу."
    return True, "Отклонено. Останавливаю выполнение."


async def get_confirmation_status(request_id: str) -> dict[str, Any] | None:
    return await _load_request(request_id)


async def wait_for_confirmation(request_id: str, timeout_sec: int = 1800) -> bool:
    deadline = time.time() + max(30, int(timeout_sec or 1800))
    while time.time() < deadline:
        decision = await _consume_callback_for_request(request_id)
        if decision is True:
            return True
        if decision is False:
            return False
        row = await get_confirmation_status(request_id)
        if row:
            status = str(row.get("status") or "").strip().lower()
            if status == "approved":
                return True
            if status in ("rejected", "expired"):
                return False
        await asyncio.sleep(1)
    row = await get_confirmation_status(request_id)
    if row and str(row.get("status") or "").strip().lower() == "pending":
        row["status"] = "expired"
        await _save_request(request_id, row)
    return False


async def create_approval_request(
    question: str,
    description: str = "",
    timeout_sec: int = 1800,
    request_id: str = "",
) -> str:
    req = await create_confirmation_request(
        question=question,
        details=description,
        action_key=request_id,
        timeout_sec=timeout_sec,
    )
    return str((req or {}).get("request_id") or "")


async def apply_approval_decision(token: str, approve: bool, actor_chat_id: int) -> tuple[bool, str]:
    return await process_confirmation_decision(request_id=token, approve=approve, chat_id=actor_chat_id)


async def get_approval_status(token: str) -> dict[str, Any] | None:
    return await get_confirmation_status(request_id=token)


async def wait_for_approval(token: str, timeout_sec: int = 1800) -> bool:
    return await wait_for_confirmation(request_id=token, timeout_sec=timeout_sec)
