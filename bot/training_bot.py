"""
Отдельный Telegram-бот для добавления папок и обучающих постов (та же БД, что и админка).
Токен: settings.TRAINING_BOT_TOKEN. Доступ: заявка «Получить разрешение» → подтверждение в боте, training_bot_operators, can_training_bot или владелец.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa

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
from db.models import ai_training_folders, ai_training_posts, training_bot_access_requests, training_bot_operators, users
from services.training_bot_access import (
    resolve_registered_site_user_by_telegram,
    resolve_user_for_training_bot,
    training_bot_access_allowed,
)
from services.training_bot_approvers import (
    is_training_bot_approver_telegram,
    training_bot_notifier_chat_ids,
)

logger = logging.getLogger(__name__)

BTN_NEW_FOLDER = "📁 Создать папку"
BTN_NEW_POST = "📝 Создать пост"
BTN_BROWSE = "📚 Папки и посты"
BTN_QUICK_ON = "📥 Принимаю в базу"
BTN_QUICK_OFF = "⏸ Стоп приёма"
BTN_REQUEST_ACCESS = "📩 Получить разрешение на отправку постов"
BTN_LIST_GRANTED = "👥 Кому выдан доступ"
QUICK_INGEST_FOLDER = "Из Telegram"
PER_PAGE = 10


def _is_main_menu_button(text: str) -> bool:
    return text in (
        BTN_NEW_FOLDER,
        BTN_NEW_POST,
        BTN_BROWSE,
        BTN_QUICK_ON,
        BTN_QUICK_OFF,
        BTN_REQUEST_ACCESS,
        BTN_LIST_GRANTED,
    )


def main_reply_kb(*, is_approver: bool = False) -> ReplyKeyboardMarkup:
    rows: list[list[str]] = [
        [BTN_NEW_FOLDER, BTN_NEW_POST],
        [BTN_BROWSE],
        [BTN_QUICK_ON, BTN_QUICK_OFF],
    ]
    if is_approver:
        rows.append([BTN_LIST_GRANTED])
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        input_field_placeholder="Меню внизу…",
    )


def request_access_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[BTN_REQUEST_ACCESS]],
        resize_keyboard=True,
        input_field_placeholder="Запрос доступа…",
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


async def _site_user_id_from_telegram(tg_id: int) -> int | None:
    row = await database.fetch_one(
        users.select().where(sa.or_(users.c.tg_id == tg_id, users.c.linked_tg_id == tg_id))
    )
    if not row:
        return None
    u = dict(row)
    if u.get("primary_user_id"):
        return int(u["primary_user_id"])
    return int(u["id"])


async def _submit_access_request(update: Update, context: ContextTypes.DEFAULT_TYPE, u_reg: dict, tg_id: int) -> None:
    uid = int(u_reg["id"])
    if await training_bot_access_allowed(u_reg):
        await update.message.reply_text("У вас уже есть доступ. Нажмите /start.", reply_markup=main_reply_kb(is_approver=await is_training_bot_approver_telegram(tg_id)))
        return
    pending = await database.fetch_one(
        training_bot_access_requests.select()
        .where(training_bot_access_requests.c.user_id == uid)
        .where(training_bot_access_requests.c.status == "pending")
    )
    if pending:
        await update.message.reply_text(
            "Заявка уже отправлена администратору. Ожидайте ответа или напишите в поддержку.",
            reply_markup=request_access_reply_kb(),
        )
        return
    try:
        await database.execute(
            training_bot_access_requests.insert().values(user_id=uid, requester_tg_id=int(tg_id), status="pending")
        )
    except Exception as e:
        logger.warning("training_bot: insert access request: %s", e)
        pending2 = await database.fetch_one(
            training_bot_access_requests.select()
            .where(training_bot_access_requests.c.user_id == uid)
            .where(training_bot_access_requests.c.status == "pending")
        )
        if pending2:
            await update.message.reply_text(
                "Заявка уже отправлена администратору. Ожидайте ответа.",
                reply_markup=request_access_reply_kb(),
            )
            return
        await update.message.reply_text(
            "Не удалось создать заявку. Попробуйте позже или напишите администратору.",
            reply_markup=request_access_reply_kb(),
        )
        return
    req = await database.fetch_one(
        training_bot_access_requests.select()
        .where(training_bot_access_requests.c.user_id == uid)
        .where(training_bot_access_requests.c.status == "pending")
        .order_by(training_bot_access_requests.c.id.desc())
    )
    rid = int(req["id"]) if req else 0
    uname = html.escape((u_reg.get("name") or "Без имени")[:120])
    uemail = html.escape((u_reg.get("email") or "")[:120])
    lines = [
        "📩 <b>Запрос доступа к боту обучающих постов</b>",
        "",
        f"Сайт: {uname}",
    ]
    if uemail:
        lines.append(f"Email: {uemail}")
    lines.extend(
        [
            f"<code>user_id={uid}</code>",
            f"Telegram ID: <code>{tg_id}</code>",
            "",
            "Разрешить папки, посты и весь функционал бота?",
        ]
    )
    text = "\n".join(lines)
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Да, подтверждаю", callback_data=f"tba:ok:{rid}")],
            [InlineKeyboardButton("❌ Отклонить", callback_data=f"tba:no:{rid}")],
        ]
    )
    chat_ids = await training_bot_notifier_chat_ids()
    if not chat_ids:
        logger.warning("training_bot: нет получателей заявок (ADMIN_TG_ID / TRAINING_BOT_APPROVER_TG_IDS)")
    sent = 0
    for cid in chat_ids:
        try:
            await context.bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=kb)
            sent += 1
        except Exception as e:
            logger.warning("training_bot: не удалось отправить заявку в chat_id=%s: %s", cid, e)
    if sent == 0:
        await update.message.reply_text(
            "Не удалось доставить заявку администраторам (проверьте ADMIN_TG_ID в настройках сервера). Попробуйте позже.",
            reply_markup=request_access_reply_kb(),
        )
        return
    await update.message.reply_text(
        "✅ Запрос отправлен. После подтверждения администратором нажмите <b>/start</b> снова.",
        parse_mode="HTML",
        reply_markup=request_access_reply_kb(),
    )


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    tg_id = update.effective_user.id
    u_reg = await resolve_registered_site_user_by_telegram(tg_id)
    if not u_reg:
        await update.message.reply_text(
            "Сначала зарегистрируйтесь на сайте и привяжите этот Telegram к аккаунту, затем снова /start.",
        )
        return
    context.user_data.clear()
    is_apr = await is_training_bot_approver_telegram(tg_id)
    if await training_bot_access_allowed(u_reg):
        await update.message.reply_text(
            "Привет! Здесь вы наполняете <b>обучающие посты</b> — их подмешивает AI на сайте в ответы (как и материалы из админки).\n\n"
            "• <b>«Принимаю в базу»</b> — бот пишет, что готов; дальше <b>каждое</b> ваше текстовое сообщение сохраняется отдельным постом "
            f"в папку «{html.escape(QUICK_INGEST_FOLDER)}».\n"
            "• «Стоп приёма» — выключить этот режим (кнопки меню в посты не пишутся).\n"
            "• «Создать папку» / «Создать пост» / «Папки и посты» — как раньше.",
            parse_mode="HTML",
            reply_markup=main_reply_kb(is_approver=is_apr),
        )
        return
    await update.message.reply_text(
        "У вас пока <b>нет доступа</b> к наполнению обучающих постов через этого бота.\n\n"
        "Нажмите кнопку ниже — администратор получит запрос и сможет подтвердить доступ "
        "(папки, посты, весь функционал бота).",
        parse_mode="HTML",
        reply_markup=request_access_reply_kb(),
    )


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    if not update.message or not update.effective_user:
        return
    tg_id = update.effective_user.id
    u_reg = await resolve_registered_site_user_by_telegram(tg_id)
    kb = (
        main_reply_kb(is_approver=await is_training_bot_approver_telegram(tg_id))
        if u_reg and await training_bot_access_allowed(u_reg)
        else request_access_reply_kb()
    )
    await update.message.reply_text("Состояние сброшено.", reply_markup=kb)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message or not update.message.text:
        return
    tg_id = update.effective_user.id
    text = (update.message.text or "").strip()

    u_reg = await resolve_registered_site_user_by_telegram(tg_id)
    if not u_reg:
        await update.message.reply_text("Сначала зарегистрируйтесь на сайте и привяжите Telegram.")
        return

    if not await training_bot_access_allowed(u_reg):
        if text == BTN_REQUEST_ACCESS:
            await _submit_access_request(update, context, u_reg, tg_id)
        else:
            await update.message.reply_text(
                "Нет доступа. Нажмите «Получить разрешение на отправку постов» или команду /start.",
                reply_markup=request_access_reply_kb(),
            )
        return

    user = u_reg
    is_apr = await is_training_bot_approver_telegram(tg_id)
    state = context.user_data.get("state")

    if text == BTN_LIST_GRANTED:
        if not is_apr:
            await update.message.reply_text(
                "Эта кнопка доступна только тем, кто подтверждает заявки (владелец / ADMIN_TG_ID / право «Бот обучающих постов»).",
                reply_markup=main_reply_kb(is_approver=is_apr),
            )
            return
        op_rows = await database.fetch_all(
            sa.select(training_bot_operators.c.user_id, users.c.name)
            .select_from(training_bot_operators.join(users, users.c.id == training_bot_operators.c.user_id))
            .order_by(training_bot_operators.c.created_at.desc())
        )
        if not op_rows:
            await update.message.reply_text(
                "Через бота пока никому не выдавали доступ (список пуст).",
                reply_markup=main_reply_kb(is_approver=True),
            )
            return
        lines = ["<b>Доступ к боту обучающих постов:</b>\n"]
        buttons: list[list[InlineKeyboardButton]] = []
        for r in op_rows[:35]:
            uid = int(r["user_id"])
            nm = html.escape((r.get("name") or f"id {uid}")[:48])
            lines.append(f"• {nm} · <code>{uid}</code>")
            buttons.append(
                [InlineKeyboardButton(f"🚫 Отозвать {nm[:28]}", callback_data=f"tba:rv:{uid}")]
            )
        if len(op_rows) > 35:
            lines.append(f"\n… и ещё {len(op_rows) - 35}")
        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

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
        context.user_data["post_folder"] = text
        context.user_data["state"] = "post_after_folder"
        await update.message.reply_text(
            f"Папка «{html.escape(text)}» создана.\n\n"
            "Пришлите <b>текст поста одним сообщением</b> — он сразу попадёт в обучающие посты для AI "
            "(заголовок = первая строка; если одна строка, весь текст и в заголовке, и в теле).\n"
            "Или нажмите кнопку меню — отменим этот шаг.",
            parse_mode="HTML",
            reply_markup=main_reply_kb(is_approver=is_apr),
        )
        return

    if state == "post_after_folder":
        if _is_main_menu_button(text):
            context.user_data.pop("state", None)
            context.user_data.pop("post_folder", None)
        else:
            if len(text) < 1:
                await update.message.reply_text("Пришлите текст поста или выберите действие в меню.")
                return
            folder_name = context.user_data.get("post_folder") or "Без папки"
            fn = None if folder_name == "Без папки" else folder_name
            first_line = (text.split("\n", 1)[0] or "").strip() or "Пост"
            title = first_line[:500]
            try:
                await database.execute(
                    ai_training_posts.insert().values(
                        title=title,
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
            except Exception:
                logger.exception("training_bot post_after_folder insert")
                await update.message.reply_text("Ошибка сохранения поста.")
                context.user_data.clear()
                return
            context.user_data.clear()
            await update.message.reply_text(
                "✅ <b>Пост записан в базу обучающих материалов.</b>\n"
                f"Папка: «{html.escape(folder_name)}».\n"
                "AI на сайте подхватит его в ответах вместе с остальными постами.",
                parse_mode="HTML",
                reply_markup=main_reply_kb(is_approver=is_apr),
            )
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
            await update.message.reply_text("Сброс: начните снова.", reply_markup=main_reply_kb(is_approver=is_apr))
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
            "✅ <b>Пост записан в базу обучающих материалов.</b>\n"
            f"Папка: «{html.escape(folder_name)}».\n"
            "AI на сайте подхватит его в ответах вместе с остальными постами.",
            parse_mode="HTML",
            reply_markup=main_reply_kb(is_approver=is_apr),
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
            reply_markup=main_reply_kb(is_approver=is_apr),
        )
        return

    if text == BTN_QUICK_OFF:
        context.user_data["quick_capture"] = False
        await update.message.reply_text(
            "Приём в базу выключен. Снова включить — «Принимаю в базу».",
            reply_markup=main_reply_kb(is_approver=is_apr),
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
        await update.message.reply_text(
            "✅ <b>Сообщение записано в базу обучающих постов.</b>\n"
            "AI на сайте будет использовать его в ответах (как и остальные материалы).",
            parse_mode="HTML",
            reply_markup=main_reply_kb(is_approver=is_apr),
        )
        return

    await update.message.reply_text(
        "Используйте кнопки меню внизу или /start.",
        reply_markup=main_reply_kb(is_approver=is_apr),
    )


async def _telegram_chat_id_for_site_user(user_id: int) -> int | None:
    row = await database.fetch_one(users.select().where(users.c.id == int(user_id)))
    if not row:
        return None
    u = dict(row)
    tid = u.get("tg_id") or u.get("linked_tg_id")
    if tid is not None:
        try:
            return int(tid)
        except (TypeError, ValueError):
            pass
    fam = await database.fetch_one(
        users.select()
        .where(users.c.primary_user_id == int(user_id))
        .where(sa.or_(users.c.tg_id.isnot(None), users.c.linked_tg_id.isnot(None)))
        .order_by(users.c.id.asc())
    )
    if fam:
        t2 = fam.get("tg_id") or fam.get("linked_tg_id")
        if t2 is not None:
            try:
                return int(t2)
            except (TypeError, ValueError):
                pass
    return None


async def on_tba_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return
    tg_id = update.effective_user.id
    if not await is_training_bot_approver_telegram(tg_id):
        await query.answer("Нет прав на это действие.", show_alert=True)
        return
    try:
        await query.answer()
    except Exception:
        pass
    data = (query.data or "").strip()
    if not data.startswith("tba:"):
        return
    parts = data.split(":")
    if len(parts) < 3:
        return
    action = parts[1]
    arg = parts[2]

    if action == "ok":
        try:
            rid = int(arg)
        except ValueError:
            return
        req = await database.fetch_one(
            training_bot_access_requests.select().where(training_bot_access_requests.c.id == rid)
        )
        if not req or (req.get("status") or "") != "pending":
            try:
                await query.edit_message_text("Заявка не найдена или уже обработана.")
            except Exception:
                pass
            return
        uid = int(req["user_id"])
        granter = await _site_user_id_from_telegram(tg_id)
        try:
            await database.execute(
                training_bot_operators.insert().values(user_id=uid, granted_by=granter)
            )
        except Exception:
            logger.debug("training_bot: operator insert duplicate or error user_id=%s", uid)
        await database.execute(
            training_bot_access_requests.update()
            .where(training_bot_access_requests.c.id == rid)
            .values(status="approved")
        )
        await database.execute(
            training_bot_access_requests.update()
            .where(training_bot_access_requests.c.user_id == uid)
            .where(training_bot_access_requests.c.status == "pending")
            .where(training_bot_access_requests.c.id != rid)
            .values(status="rejected")
        )
        chat_u = await _telegram_chat_id_for_site_user(uid)
        if chat_u:
            try:
                await context.bot.send_message(
                    chat_id=chat_u,
                    text="✅ Вам подтвердили доступ к боту обучающих постов. Откройте меню и нажмите /start.",
                )
            except Exception as e:
                logger.warning("training_bot: notify granted user: %s", e)
        try:
            await query.edit_message_text("✅ Доступ выдан (папки, посты, весь функционал). Пользователь уведомлён.")
        except Exception:
            pass
        return

    if action == "no":
        try:
            rid = int(arg)
        except ValueError:
            return
        req = await database.fetch_one(
            training_bot_access_requests.select().where(training_bot_access_requests.c.id == rid)
        )
        if not req or (req.get("status") or "") != "pending":
            try:
                await query.edit_message_text("Заявка не найдена или уже обработана.")
            except Exception:
                pass
            return
        await database.execute(
            training_bot_access_requests.update()
            .where(training_bot_access_requests.c.id == rid)
            .values(status="rejected")
        )
        chat_u = await _telegram_chat_id_for_site_user(int(req["user_id"]))
        if chat_u:
            try:
                await context.bot.send_message(
                    chat_id=chat_u,
                    text="❌ В доступе к боту обучающих постов отказано. При необходимости свяжитесь с администратором.",
                )
            except Exception as e:
                logger.warning("training_bot: notify rejected user: %s", e)
        try:
            await query.edit_message_text("Заявка отклонена.")
        except Exception:
            pass
        return

    if action == "rv":
        try:
            uid = int(arg)
        except ValueError:
            return
        await database.execute(training_bot_operators.delete().where(training_bot_operators.c.user_id == uid))
        chat_u = await _telegram_chat_id_for_site_user(uid)
        if chat_u:
            try:
                await context.bot.send_message(
                    chat_id=chat_u,
                    text="❌ Ваш доступ к боту обучающих постов отозван администратором.",
                )
            except Exception as e:
                logger.warning("training_bot: notify revoked user: %s", e)
        try:
            await query.edit_message_text(f"🚫 Доступ отозван (user_id={uid}).")
        except Exception:
            try:
                await query.message.reply_text(f"🚫 Доступ отозван (user_id={uid}).")
            except Exception:
                pass


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
    app.add_handler(CallbackQueryHandler(on_tba_callback, pattern=r"^tba:"))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^tp:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app
