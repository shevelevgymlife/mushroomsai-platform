from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from services.task_approval import get_confirmation_status, process_confirmation_decision
from config import settings


def _is_allowed(uid: int) -> bool:
    if not uid:
        return False
    if int(uid) == int(getattr(settings, "ADMIN_TG_ID", 0) or 0):
        return True
    extras = str(getattr(settings, "TASK_APPROVAL_ALLOWED_TG_IDS", "") or "")
    allowed = {s.strip() for s in extras.split(",") if s.strip()}
    return str(int(uid)) in allowed


async def task_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    user = update.effective_user
    uid = int(user.id) if user else 0
    if not _is_allowed(uid):
        await q.answer("Нет прав для подтверждения", show_alert=True)
        return
    data = str(q.data or "")
    # Format: confirm:yes:<request_id> | confirm:no:<request_id>
    parts = data.split(":")
    if len(parts) != 3:
        await q.answer()
        return
    _, yn, request_id = parts
    approve = yn == "yes"
    ok, msg = await process_confirmation_decision(request_id=request_id, approve=approve, chat_id=uid)
    await q.answer(msg[:180], show_alert=not ok)
    try:
        payload = await get_confirmation_status(request_id)
        if payload:
            q_text = str(payload.get("question") or "").strip()
            d_text = str(payload.get("details") or "").strip()
            state = "✅ Да" if str(payload.get("decision") or "") == "yes" else "❌ Нет"
            text = (
                f"Вопрос: {q_text}\n"
                f"{d_text + chr(10) if d_text else ''}"
                f"Ответ: {state}"
            )
            await q.edit_message_text(text)
        else:
            await q.edit_message_text(msg)
    except Exception:
        pass


async def approval_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.effective_user
    uid = int(user.id) if user else 0
    if not _is_allowed(uid):
        await update.message.reply_text("Нет прав.")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Использование: /approval_status <request_id>")
        return
    request_id = args[0].strip()
    payload = await get_confirmation_status(request_id)
    if not payload:
        await update.message.reply_text("Запрос не найден.")
        return
    text = (
        f"ID: {payload.get('request_id')}\n"
        f"Status: {payload.get('status')}\n"
        f"Question: {payload.get('question')}\n"
        f"Details: {payload.get('details') or '—'}\n"
        f"Decision: {payload.get('decision') or '—'}"
    )
    await update.message.reply_text(text)
