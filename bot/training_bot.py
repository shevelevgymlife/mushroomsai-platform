"""
Отдельный Telegram-бот для добавления папок и обучающих постов (та же БД, что и админка).
Токен: settings.TRAINING_BOT_TOKEN. Доступ: админ + can_training_bot или владелец платформы.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import settings
from db.database import database
from db.models import ai_training_folders, ai_training_posts
from services.training_bot_access import resolve_user_for_training_bot

logger = logging.getLogger(__name__)

BTN_NEW_FOLDER = "📁 Создать папку"
BTN_NEW_POST = "📝 Создать пост"
BTN_BROWSE = "📚 Папки и посты"
BTN_QUICK_ON = "📥 Принимаю в базу"
BTN_QUICK_OFF = "⏸ Стоп приёма"
QUICK_INGEST_FOLDER = "Из Telegram"
PER_PAGE = 10


def main_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [BTN_NEW_FOLDER, BTN_NEW_POST],
            [BTN_BROWSE],
            [BTN_QUICK_ON, BTN_QUICK_OFF],
        ],
        resize_keyboard=True,
        input_field_placeholder="Меню внизу…",
    )


def _title_from_quick_message(text: str) -> str:
    first = (text.strip().split("\n", 1)[0] or "").strip() or "Сообщение из Telegram"
    ts = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    base = f"{first[:180]} · {ts}"
    return base[:500]


async def folder_names_ordered() -> list[str]:
    posts_list = await database.fetch_all(ai_training_posts.select())
    seen: dict[str, bool] = {}
    for p in posts_list:
        fn = (p.get("folder") or "").strip() or "Без папки"
        seen[fn] = True
    try:
        extras = await database.fetch_all(ai_training_folders.select())
        for r in extras:
            seen[r["name"]] = True
    except Exception:
        pass
    keys = list(seen.keys())
    return sorted(keys, key=lambda x: (0 if x == "Без папки" else 1, x.lower()))


async def posts_for_folder(folder_name: str) -> list[dict]:
    rows = await database.fetch_all(
        ai_training_posts.select().order_by(ai_training_posts.c.title)
    )
    out: list[dict] = []
    for p in rows:
        fn = (p.get("folder") or "").strip() or "Без папки"
        if fn == folder_name:
            out.append(dict(p))
    return out


def _trunc(s: str, n: int = 28) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def folder_keyboard_page(names: list[str], page: int, mode: str = "browse") -> InlineKeyboardMarkup:
    """mode: 'browse' | 'create' — откуда выбрали список папок."""
    start = page * PER_PAGE
    chunk = names[start : start + PER_PAGE]
    prefix = "tp:cs" if mode == "create" else "tp:fs"
    rows = []
    for i, name in enumerate(chunk):
        idx = start + i
        rows.append(
            [InlineKeyboardButton(_trunc(name, 60), callback_data=f"{prefix}:{idx}")]
        )
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("« Пред.", callback_data=f"tp:fp:{page - 1}"))
    if start + PER_PAGE < len(names):
        nav.append(InlineKeyboardButton("След. »", callback_data=f"tp:fp:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🏠 В меню", callback_data="tp:hm")])
    return InlineKeyboardMarkup(rows)


def post_keyboard_page(folder_idx: int, page: int, posts: list[dict]) -> InlineKeyboardMarkup:
    start = page * PER_PAGE
    chunk = posts[start : start + PER_PAGE]
    rows = []
    for p in chunk:
        pid = int(p["id"])
        rows.append(
            [
                InlineKeyboardButton(
                    _trunc(p.get("title") or f"#{pid}", 58),
                    callback_data=f"tp:pv:{pid}",
                )
            ]
        )
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("« Пред.", callback_data=f"tp:pp:{folder_idx}:{page - 1}"))
    if start + PER_PAGE < len(posts):
        nav.append(InlineKeyboardButton("След. »", callback_data=f"tp:pp:{folder_idx}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton("➕ Пост сюда", callback_data=f"tp:np:{folder_idx}"),
            InlineKeyboardButton("📂 Все папки", callback_data="tp:fp:0"),
        ]
    )
    rows.append([InlineKeyboardButton("🏠 В меню", callback_data="tp:hm")])
    return InlineKeyboardMarkup(rows)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return
    tg_id = update.effective_user.id
    user, err = await resolve_user_for_training_bot(tg_id)
    if not user:
        await update.message.reply_text(err or "Доступ запрещён.")
        return
    context.user_data.clear()
    await update.message.reply_text(
        "Привет! Здесь вы наполняете <b>обучающие посты</b> — их подмешивает AI на сайте в ответы (как и материалы из админки).\n\n"
        "• <b>«Принимаю в базу»</b> — бот пишет, что готов; дальше <b>каждое</b> ваше текстовое сообщение сохраняется отдельным постом "
        f"в папку «{html.escape(QUICK_INGEST_FOLDER)}».\n"
        "• «Стоп приёма» — выключить этот режим (кнопки меню в посты не пишутся).\n"
        "• «Создать папку» / «Создать пост» / «Папки и посты» — как раньше.",
        parse_mode="HTML",
        reply_markup=main_reply_kb(),
    )


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("Состояние сброшено.", reply_markup=main_reply_kb())


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message or not update.message.text:
        return
    tg_id = update.effective_user.id
    user, err = await resolve_user_for_training_bot(tg_id)
    if not user:
        await update.message.reply_text(err or "Доступ запрещён.")
        return

    text = (update.message.text or "").strip()
    state = context.user_data.get("state")

    if state == "folder_name":
        if len(text) < 1 or len(text) > 200:
            await update.message.reply_text("Имя папки: от 1 до 200 символов. Пришлите ещё раз или /cancel")
            return
        try:
            await database.execute(ai_training_folders.insert().values(name=text))
        except Exception as e:
            logger.warning("training_bot folder insert: %s", e)
            await update.message.reply_text("Не удалось создать (возможно, папка уже есть).")
            context.user_data.pop("state", None)
            return
        context.user_data.pop("state", None)
        await update.message.reply_text(f"Папка «{html.escape(text)}» создана.", parse_mode="HTML", reply_markup=main_reply_kb())
        return

    if state == "post_title":
        if len(text) < 1 or len(text) > 500:
            await update.message.reply_text("Заголовок: 1–500 символов.")
            return
        context.user_data["post_title"] = text
        context.user_data["state"] = "post_body"
        await update.message.reply_text("Теперь пришлите <b>текст поста</b> одним сообщением.", parse_mode="HTML")
        return

    if state == "post_body":
        folder_name = context.user_data.get("post_folder") or "Без папки"
        title = (context.user_data.get("post_title") or "").strip()
        if not title:
            context.user_data.clear()
            await update.message.reply_text("Сброс: начните снова.", reply_markup=main_reply_kb())
            return
        fn = None if folder_name == "Без папки" else folder_name
        try:
            await database.execute(
                ai_training_posts.insert().values(
                    title=title[:500],
                    content=text[:100000],
                    category=None,
                    folder=fn,
                )
            )
            if fn:
                try:
                    await database.execute(ai_training_folders.insert().values(name=fn))
                except Exception:
                    pass
        except Exception as e:
            logger.exception("training_bot post insert: %s", e)
            await update.message.reply_text("Ошибка сохранения поста.")
            context.user_data.clear()
            return
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Пост сохранён в папку «{html.escape(folder_name)}».", parse_mode="HTML", reply_markup=main_reply_kb()
        )
        return

    if text == BTN_NEW_FOLDER:
        context.user_data["state"] = "folder_name"
        await update.message.reply_text("Пришлите <b>название новой папки</b> текстом.", parse_mode="HTML")
        return

    if text == BTN_NEW_POST:
        names = await folder_names_ordered()
        if not names:
            names = ["Без папки"]
        context.user_data["folder_names_cache"] = names
        context.user_data["pick_mode"] = "create"
        await update.message.reply_text(
            "Выберите папку для нового поста:",
            reply_markup=folder_keyboard_page(names, 0, "create"),
        )
        return

    if text == BTN_BROWSE:
        names = await folder_names_ordered()
        if not names:
            names = ["Без папки"]
        context.user_data["folder_names_cache"] = names
        context.user_data["pick_mode"] = "browse"
        await update.message.reply_text(
            "Папки (по 10 на странице). Нажмите папку, чтобы открыть посты:",
            reply_markup=folder_keyboard_page(names, 0, "browse"),
        )
        return

    if text == BTN_QUICK_ON:
        context.user_data.pop("state", None)
        context.user_data["quick_capture"] = True
        await update.message.reply_text(
            "✅ <b>Готов принимать сообщения.</b> Всё, что вы пришлёте текстом сейчас, "
            f"запишу в обучающие посты (папка «{html.escape(QUICK_INGEST_FOLDER)}»). "
            "AI на сайте использует их так же, как остальные посты.\n\n"
            "Кнопки меню снизу не сохраняются. «Стоп приёма» — выключить режим.",
            parse_mode="HTML",
            reply_markup=main_reply_kb(),
        )
        return

    if text == BTN_QUICK_OFF:
        context.user_data["quick_capture"] = False
        await update.message.reply_text(
            "Приём в базу выключен. Снова включить — «Принимаю в базу».",
            reply_markup=main_reply_kb(),
        )
        return

    if context.user_data.get("quick_capture"):
        if len(text) < 1:
            return
        title = _title_from_quick_message(text)
        try:
            await database.execute(
                ai_training_posts.insert().values(
                    title=title,
                    content=text[:100000],
                    category=None,
                    folder=QUICK_INGEST_FOLDER,
                )
            )
            try:
                await database.execute(ai_training_folders.insert().values(name=QUICK_INGEST_FOLDER))
            except Exception:
                pass
        except Exception as e:
            logger.exception("training_bot quick ingest: %s", e)
            await update.message.reply_text("Не удалось сохранить пост.")
            return
        await update.message.reply_text("✅ Записано в обучающие посты.", reply_markup=main_reply_kb())
        return

    await update.message.reply_text("Используйте кнопки меню внизу или /start.", reply_markup=main_reply_kb())


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return
    try:
        await query.answer()
    except Exception:
        pass

    tg_id = update.effective_user.id
    user, err = await resolve_user_for_training_bot(tg_id)
    if not user:
        try:
            await query.edit_message_text(err or "Доступ запрещён.")
        except Exception:
            pass
        return

    data = query.data
    if not data.startswith("tp:"):
        return
    parts = data.split(":")
    if len(parts) < 2:
        return
    action = parts[1]

    if action == "hm":
        context.user_data.clear()
        try:
            await query.edit_message_text("Меню: используйте кнопки внизу экрана.")
        except Exception:
            pass
        return

    names = context.user_data.get("folder_names_cache")
    if not names:
        names = await folder_names_ordered()
        if not names:
            names = ["Без папки"]
        context.user_data["folder_names_cache"] = names

    if action == "fp":
        page = int(parts[2]) if len(parts) > 2 else 0
        page = max(0, page)
        mode = context.user_data.get("pick_mode") or "browse"
        if mode not in ("browse", "create"):
            mode = "browse"
        try:
            await query.edit_message_reply_markup(reply_markup=folder_keyboard_page(names, page, mode))
        except Exception:
            try:
                await query.message.reply_text(
                    "Папки:",
                    reply_markup=folder_keyboard_page(names, page, mode),
                )
            except Exception:
                pass
        return

    if action == "cs":
        idx = int(parts[2]) if len(parts) > 2 else 0
        if idx < 0 or idx >= len(names):
            await query.answer("Неверный выбор", show_alert=True)
            return
        folder_name = names[idx]
        context.user_data.clear()
        context.user_data["post_folder"] = folder_name
        context.user_data["state"] = "post_title"
        try:
            await query.edit_message_text(
                f"Новый пост в «<b>{html.escape(folder_name)}</b>».\nПришлите <b>заголовок</b> следующим сообщением.",
                parse_mode="HTML",
            )
        except Exception:
            await query.message.reply_text(f"Новый пост в «{folder_name}». Пришлите заголовок.")
        return

    if action == "fs":
        idx = int(parts[2]) if len(parts) > 2 else 0
        if idx < 0 or idx >= len(names):
            await query.answer("Неверный выбор", show_alert=True)
            return
        folder_name = names[idx]
        posts = await posts_for_folder(folder_name)
        await query.edit_message_text(
            f"📁 <b>{html.escape(folder_name)}</b>\nПостов: {len(posts)}. Стр. 1.",
            parse_mode="HTML",
            reply_markup=post_keyboard_page(idx, 0, posts),
        )
        return

    if action == "pp":
        folder_idx = int(parts[2]) if len(parts) > 2 else 0
        page = int(parts[3]) if len(parts) > 3 else 0
        if folder_idx < 0 or folder_idx >= len(names):
            await query.answer("Ошибка", show_alert=True)
            return
        folder_name = names[folder_idx]
        posts = await posts_for_folder(folder_name)
        page = max(0, page)
        try:
            await query.edit_message_text(
                f"📁 <b>{html.escape(folder_name)}</b>\nПостов: {len(posts)}. Стр. {page + 1}.",
                parse_mode="HTML",
                reply_markup=post_keyboard_page(folder_idx, page, posts),
            )
        except Exception:
            pass
        return

    if action == "pv":
        pid = int(parts[2]) if len(parts) > 2 else 0
        row = await database.fetch_one(ai_training_posts.select().where(ai_training_posts.c.id == pid))
        if not row:
            await query.answer("Пост не найден", show_alert=True)
            return
        p = dict(row)
        title = html.escape(p.get("title") or "")
        body = html.escape((p.get("content") or "")[:3500])
        fn = (p.get("folder") or "").strip() or "Без папки"
        await query.message.reply_text(
            f"<b>{title}</b>\n<i>{html.escape(fn)}</i>\n\n{body}",
            parse_mode="HTML",
        )
        return

    if action == "np":
        folder_idx = int(parts[2]) if len(parts) > 2 else 0
        if folder_idx < 0 or folder_idx >= len(names):
            await query.answer("Ошибка", show_alert=True)
            return
        folder_name = names[folder_idx]
        context.user_data.clear()
        context.user_data["post_folder"] = folder_name
        context.user_data["state"] = "post_title"
        try:
            await query.edit_message_text(
                f"Новый пост в «<b>{html.escape(folder_name)}</b>».\nПришлите <b>заголовок</b> следующим сообщением.",
                parse_mode="HTML",
            )
        except Exception:
            await query.message.reply_text(
                f"Новый пост в «{folder_name}». Пришлите заголовок.",
            )
        return


def create_training_bot() -> Application:
    token = (settings.TRAINING_BOT_TOKEN or "").strip()
    if not token:
        raise RuntimeError("TRAINING_BOT_TOKEN пуст")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^tp:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app
