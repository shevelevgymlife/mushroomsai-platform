"""Task confirmation via Telegram inline buttons (Yes/No)."""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from typing import Any

import httpx
import sqlalchemy as sa

from config import settings
from db.database import database

_UPDATES_OFFSET = 0


def _chat_id() -> int:
    raw = (
        (getattr(settings, "TASK_APPROVAL_CHAT_ID", "") or "").strip()
        or (settings.DEPLOY_NOTIFY_TASK_CHAT_ID or "").strip()
        or (settings.DEPLOY_NOTIFY_TG_CHAT_ID or "").strip()
        or str(int(getattr(settings, "ADMIN_TG_ID", 0) or 0))
    )
    try:
        return int(raw or 0)
    except Exception:
        return 0


def _token() -> str:
    return (
        (getattr(settings, "TASK_APPROVAL_BOT_TOKEN", "") or "").strip()
        or (settings.DEPLOY_NOTIFY_TG_BOT_TOKEN or "").strip()
        or (settings.TELEGRAM_TOKEN or "").strip()
    )


def _is_allowed_approver(user_id: int) -> bool:
    if not user_id:
        return False
    allowed: set[int] = set()
    try:
        aid = int(getattr(settings, "ADMIN_TG_ID", 0) or 0)
        if aid:
            allowed.add(aid)
    except Exception:
        pass
    try:
        cid = _chat_id()
        if cid:
            allowed.add(cid)
    except Exception:
        pass
    extra = str(getattr(settings, "TASK_APPROVAL_ALLOWED_TG_IDS", "") or "")
    for raw in extra.split(","):
        s = raw.strip()
        if not s:
            continue
        try:
            allowed.add(int(s))
        except Exception:
            continue
    return int(user_id) in allowed


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
    bot_token = _token()
    chat_id = _chat_id()
    if not bot_token or not chat_id:
        return
    text = (
        f"❓ {question.strip()}\n"
        f"{(details or '').strip() + chr(10) if (details or '').strip() else ''}"
        "Подтверждаете?"
    )
    reply_markup = {
        "inline_keyboard": [[
            {"text": "✅ Да", "callback_data": f"confirm:yes:{request_id}"},
            {"text": "❌ Нет", "callback_data": f"confirm:no:{request_id}"},
        ]]
    }
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text[:3800], "reply_markup": reply_markup},
            )
    except Exception:
        # best effort
        return


async def _answer_callback(callback_query_id: str, text: str = "") -> None:
    bot_token = _token()
    if not bot_token or not callback_query_id:
        return
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            await client.post(
                f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
                json={"callback_query_id": callback_query_id, "text": (text or "")[:180]},
            )
    except Exception:
        return


async def _fetch_callback_updates() -> list[dict[str, Any]]:
    global _UPDATES_OFFSET
    bot_token = _token()
    if not bot_token:
        return []
    payload: dict[str, Any] = {"timeout": 0, "allowed_updates": ["callback_query"]}
    if _UPDATES_OFFSET:
        payload["offset"] = _UPDATES_OFFSET
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"https://api.telegram.org/bot{bot_token}/getUpdates", json=payload)
            data = r.json() if r is not None else {}
        items = data.get("result") if isinstance(data, dict) else []
        if not isinstance(items, list):
            return []
        for upd in items:
            try:
                uid = int(upd.get("update_id") or 0)
                if uid >= _UPDATES_OFFSET:
                    _UPDATES_OFFSET = uid + 1
            except Exception:
                continue
        return items
    except Exception:
        return []


async def _consume_callback_for_request(request_id: str) -> bool | None:
    updates = await _fetch_callback_updates()
    if not updates:
        return None
    for upd in updates:
        cq = upd.get("callback_query") if isinstance(upd, dict) else None
        if not isinstance(cq, dict):
            continue
        data = str(cq.get("data") or "")
        parts = data.split(":")
        if len(parts) != 3 or parts[0] != "confirm":
            continue
        yn = parts[1]
        rid = parts[2]
        cb_id = str(cq.get("id") or "")
        from_id = int((cq.get("from") or {}).get("id") or 0)
        if rid != request_id:
            await _answer_callback(cb_id, "Это другой запрос подтверждения.")
            continue
        if not _is_allowed_approver(from_id):
            await _answer_callback(cb_id, "Нет прав для подтверждения.")
            continue
        approve = yn == "yes"
        _, msg = await process_confirmation_decision(request_id=request_id, approve=approve, chat_id=from_id)
        await _answer_callback(cb_id, msg)
        return approve
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


# Backward-compatible aliases
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

