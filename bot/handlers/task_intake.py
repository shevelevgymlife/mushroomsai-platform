from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, CallbackQueryHandler, CommandHandler, filters

from config import settings
from db.database import database
from services.task_notify import notify_task_accepted, notify_task_done, notify_deploy_sent


ASK_TASK_TEXT, ASK_PHOTO_CHOICE, WAIT_PHOTO = range(3)


def _task_chat_id() -> int:
    raw = (
        (settings.DEPLOY_NOTIFY_TASK_CHAT_ID or "").strip()
        or (settings.DEPLOY_NOTIFY_TG_CHAT_ID or "").strip()
        or str(int(getattr(settings, "ADMIN_TG_ID", 0) or 0))
    )
    try:
        return int(raw or 0)
    except Exception:
        return 0


def _is_owner(uid: int) -> bool:
    if not uid:
        return False
    # In dedicated ops-bot mode, allow any sender in this bot.
    if (
        (getattr(settings, "TASK_APPROVAL_BOT_TOKEN", "") or "").strip()
        or (getattr(settings, "DEPLOY_NOTIFY_TG_BOT_TOKEN", "") or "").strip()
    ):
        return True
    if int(uid) == int(getattr(settings, "ADMIN_TG_ID", 0) or 0):
        return True
    extras = str(getattr(settings, "TASK_APPROVAL_ALLOWED_TG_IDS", "") or "")
    allowed = {s.strip() for s in extras.split(",") if s.strip()}
    return str(int(uid)) in allowed


async def _ensure_task_inbox_table() -> None:
    await database.execute(
        sa.text(
            """CREATE TABLE IF NOT EXISTS bot_task_requests (
                id SERIAL PRIMARY KEY,
                tg_user_id BIGINT NOT NULL,
                username TEXT,
                full_name TEXT,
                task_text TEXT NOT NULL,
                needs_photo BOOLEAN NOT NULL DEFAULT false,
                photo_file_id TEXT,
                status VARCHAR(24) NOT NULL DEFAULT 'new',
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )"""
        )
    )
    await database.execute(
        sa.text(
            "ALTER TABLE bot_task_requests ADD COLUMN IF NOT EXISTS auto_requested BOOLEAN NOT NULL DEFAULT false"
        )
    )


async def _create_task(tg_user_id: int, text: str, username: str = "", full_name: str = "") -> int:
    row = await database.fetch_one_write(
        sa.text(
            "INSERT INTO bot_task_requests (tg_user_id, username, full_name, task_text, status, updated_at) "
            "VALUES (:uid, :username, :full_name, :txt, 'new', NOW()) "
            "RETURNING id"
        ).bindparams(
            uid=int(tg_user_id),
            username=(username or "").strip()[:255],
            full_name=(full_name or "").strip()[:255],
            txt=(text or "").strip()[:6000],
        )
    )
    return int((row or {}).get("id") or 0)


async def _update_task(task_id: int, **kwargs: Any) -> None:
    if not task_id:
        return
    sets = []
    params: dict[str, Any] = {"id": int(task_id)}
    for k, v in kwargs.items():
        sets.append(f"{k} = :{k}")
        params[k] = v
    sets.append("updated_at = NOW()")
    await database.execute(
        sa.text(f"UPDATE bot_task_requests SET {', '.join(sets)} WHERE id = :id").bindparams(**params)
    )


async def _run_task_auto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    task_id = int(context.user_data.get("task_intake_id") or 0)
    task_text = str(context.user_data.get("task_intake_text") or "").strip()
    if not task_text:
        if update.message:
            await update.message.reply_text("Не вижу активной задачи. Сначала отправьте /task.")
        return
    if task_id:
        await _update_task(task_id, auto_requested=True, status="queued")
    try:
        from services.task_autorun import trigger_task_autorun

        ok = await trigger_task_autorun(task_text=task_text, task_id=task_id)
    except Exception:
        ok = False
    if update.message:
        if ok:
            await update.message.reply_text("Авто-запуск отправлен. Начал выполнение задачи.")
        else:
            await update.message.reply_text("Не удалось отправить в авто-запуск. Продолжаю в ручном режиме.")
    await notify_task_accepted(task_text=f"Принял задачу: {task_text}")


async def task_give_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END
    uid = int(update.effective_user.id) if update.effective_user else 0
    if not _is_owner(uid):
        await update.message.reply_text("Эта функция доступна только владельцу.")
        return ConversationHandler.END
    try:
        await _ensure_task_inbox_table()
    except Exception:
        await update.message.reply_text("Не удалось подготовить прием задач. Повторите через минуту.")
        return ConversationHandler.END
    context.user_data["task_intake_stage"] = "wait_text"
    context.user_data.pop("task_intake_id", None)
    context.user_data.pop("task_intake_text", None)
    await update.message.reply_text("Евгений Алексеевич, что бы вы хотели добавить/изменить?")
    return ASK_TASK_TEXT


async def task_text_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END
    uid = int(update.effective_user.id) if update.effective_user else 0
    if not _is_owner(uid):
        return ConversationHandler.END
    txt = (update.message.text or "").strip()
    if not txt:
        await update.message.reply_text("Пожалуйста, отправьте текст задачи.")
        return ASK_TASK_TEXT
    task_id = 0
    try:
        user = update.effective_user
        task_id = await _create_task(
            uid,
            txt,
            username=(getattr(user, "username", "") or ""),
            full_name=(getattr(user, "full_name", "") or ""),
        )
    except Exception:
        # Do not block the flow if DB write fails; still ask photo choice.
        pass
    context.user_data["task_intake_id"] = task_id
    context.user_data["task_intake_text"] = txt
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да", callback_data="task_photo:yes"),
            InlineKeyboardButton("❌ Нет", callback_data="task_photo:no"),
        ]
    ])
    await update.message.reply_text(
        "Фото прилагаться будут к задаче?",
        reply_markup=kb,
    )
    context.user_data["task_intake_stage"] = "wait_photo_choice"
    return ASK_PHOTO_CHOICE


async def task_photo_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    uid = int(update.effective_user.id) if update.effective_user else 0
    if not _is_owner(uid):
        await q.answer("Нет прав", show_alert=True)
        return ConversationHandler.END
    if str(context.user_data.get("task_intake_stage") or "") != "wait_photo_choice":
        # Soft fallback: if text already present in session, still allow choice.
        if not str(context.user_data.get("task_intake_text") or "").strip():
            await q.answer("Сначала отправьте задачу через /task", show_alert=True)
            return ConversationHandler.END
    await q.answer()
    decision = str(q.data or "").split(":")[-1]
    task_id = int(context.user_data.get("task_intake_id") or 0)
    task_text = str(context.user_data.get("task_intake_text") or "")
    if not task_text:
        await q.edit_message_text("Не вижу текста задачи. Отправьте /task ещё раз.")
        context.user_data["task_intake_stage"] = ""
        return ConversationHandler.END
    if decision == "yes":
        if task_id:
            await _update_task(task_id, needs_photo=True, status="wait_photo")
        await q.edit_message_text("Жду фото.")
        context.user_data["task_intake_stage"] = "wait_photo"
        return WAIT_PHOTO

    if task_id:
        await _update_task(task_id, needs_photo=False, status="in_progress")
    await q.edit_message_text("Принял. Начал выполнять задачу без фото.")
    await notify_task_accepted(task_text=f"Принял задачу: {task_text}")
    context.user_data["task_intake_stage"] = ""
    return ConversationHandler.END


async def task_photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.photo:
        await update.message.reply_text("Пожалуйста, отправьте фото одним сообщением.")
        return WAIT_PHOTO
    uid = int(update.effective_user.id) if update.effective_user else 0
    if not _is_owner(uid):
        return ConversationHandler.END
    if str(context.user_data.get("task_intake_stage") or "") != "wait_photo":
        await update.message.reply_text("Сначала отправьте /task и текст задачи.")
        return ConversationHandler.END
    task_id = int(context.user_data.get("task_intake_id") or 0)
    task_text = str(context.user_data.get("task_intake_text") or "")
    if not task_id or not task_text:
        await update.message.reply_text("Не вижу активной задачи. Отправьте /task ещё раз.")
        context.user_data["task_intake_stage"] = ""
        return ConversationHandler.END
    file_id = update.message.photo[-1].file_id
    await _update_task(task_id, photo_file_id=file_id, status="in_progress")
    await update.message.reply_text("Фото к заданию принял.")
    await notify_task_accepted(task_text=f"Принял задачу: {task_text} (с фото)")
    context.user_data["task_intake_stage"] = ""
    return ConversationHandler.END


async def task_run_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    await _run_task_auto(update, context)


async def task_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("Отменил ввод задачи.")
    context.user_data["task_intake_stage"] = ""
    return ConversationHandler.END


def get_task_intake_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^Дать задачу$"), task_give_entry),
            CommandHandler("task", task_give_entry),
        ],
        states={
            ASK_TASK_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_text_received)],
            ASK_PHOTO_CHOICE: [CallbackQueryHandler(task_photo_choice, pattern=r"^task_photo:(yes|no)$")],
            WAIT_PHOTO: [MessageHandler(filters.PHOTO, task_photo_received)],
        },
        fallbacks=[CommandHandler("cancel", task_cancel)],
        per_user=True,
        per_chat=True,
        per_message=False,
    )
