"""Обработчик подтверждения привязки Telegram-аккаунта через deeplink."""
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from db.database import database
from db.models import users

logger = logging.getLogger(__name__)


async def link_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data  # "link_confirm:<token>" or "link_cancel:<token>"

    try:
        action, token = data.split(":", 1)
    except ValueError:
        await query.edit_message_text("Неверный формат запроса.")
        return

    if action == "link_cancel":
        await query.edit_message_text("Привязка отменена.")
        return

    if action != "link_confirm":
        return

    tg_id = query.from_user.id

    row = await database.fetch_one(
        users.select().where(users.c.link_token == token)
    )
    if not row:
        await query.edit_message_text("Ссылка недействительна или уже использована.")
        return

    expires = row["link_token_expires"]
    if expires and datetime.utcnow() > expires:
        await query.edit_message_text("Срок действия ссылки истёк. Сгенерируйте новую на сайте.")
        return

    # Check if this tg_id already belongs to another account
    existing = await database.fetch_one(
        users.select().where(users.c.tg_id == tg_id)
    )

    if existing and existing["id"] != row["id"]:
        # Two accounts exist — ask which to keep
        keyboard = [
            [InlineKeyboardButton(
                "Оставить этот (Telegram) → удалить Web-аккаунт",
                callback_data=f"link_merge_keep_tg:{token}:{existing['id']}:{row['id']}"
            )],
            [InlineKeyboardButton(
                "Оставить Web-аккаунт → удалить этот Telegram",
                callback_data=f"link_merge_keep_web:{token}:{existing['id']}:{row['id']}"
            )],
            [InlineKeyboardButton("Отмена", callback_data=f"link_cancel:{token}")],
        ]
        await query.edit_message_text(
            "⚠️ Этот Telegram уже зарегистрирован в системе как отдельный аккаунт.\n\n"
            "Выберите, какой аккаунт оставить основным (второй будет удалён, "
            "но способ входа через него сохранится):",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    # No conflict — simply link
    await _do_link(query, tg_id, row["id"], token)


async def link_merge_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data  # "link_merge_keep_tg:<token>:<tg_user_id>:<web_user_id>"
                       # "link_merge_keep_web:<token>:<tg_user_id>:<web_user_id>"
    try:
        action, token, tg_user_id_str, web_user_id_str = data.split(":", 3)
        tg_user_id = int(tg_user_id_str)
        web_user_id = int(web_user_id_str)
    except (ValueError, TypeError):
        await query.edit_message_text("Ошибка обработки запроса.")
        return

    tg_id = query.from_user.id

    if action == "link_merge_keep_tg":
        # Keep tg_user_id as primary, merge web_user_id into it
        from web.routes.account import merge_accounts
        await merge_accounts(primary_id=tg_user_id, secondary_id=web_user_id)
        await database.execute(
            users.update().where(users.c.id == tg_user_id).values(
                link_token=None, link_token_expires=None
            )
        )
        await query.edit_message_text(
            "✅ Аккаунты объединены! Основным стал ваш Telegram-аккаунт.\n"
            "Войти через любой привязанный способ → попадёте в один профиль."
        )

    elif action == "link_merge_keep_web":
        # Keep web_user_id as primary, merge tg_user_id into it, set tg_id on web account
        from web.routes.account import merge_accounts
        await merge_accounts(primary_id=web_user_id, secondary_id=tg_user_id)
        await database.execute(
            users.update().where(users.c.id == web_user_id).values(
                tg_id=tg_id, linked_tg_id=tg_id,
                link_token=None, link_token_expires=None
            )
        )
        await query.edit_message_text(
            "✅ Аккаунты объединены! Основным остался ваш Web-аккаунт.\n"
            "Войти через любой привязанный способ → попадёте в один профиль."
        )


async def _do_link(query, tg_id: int, user_id: int, token: str) -> None:
    await database.execute(
        users.update().where(users.c.id == user_id).values(
            tg_id=tg_id,
            linked_tg_id=tg_id,
            link_token=None,
            link_token_expires=None,
        )
    )
    await query.edit_message_text(
        "✅ Telegram успешно привязан к вашему аккаунту!\n\n"
        "Теперь вы можете входить на сайт через Telegram."
    )
