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

    # Проверить: есть ли в системе другой аккаунт с этим tg_id
    existing = await database.fetch_one(
        users.select().where(users.c.tg_id == tg_id)
    )

    if existing and existing["id"] != row["id"]:
        # Конфликт: этот Telegram уже зарегистрирован отдельно.
        # Всегда оставляем web/Google аккаунт (row), удаляем TG-аккаунт (existing).
        created_str = ""
        if existing.get("created_at"):
            try:
                created_str = f", создан {existing['created_at'].strftime('%d.%m.%Y')}"
            except Exception:
                pass

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "✅ Да, привязать и удалить TG-аккаунт",
                callback_data=f"link_merge_keep_web:{token}:{existing['id']}:{row['id']}"
            )],
            [InlineKeyboardButton("❌ Отмена", callback_data=f"link_cancel:{token}")],
        ])
        await query.edit_message_text(
            f"⚠️ <b>Этот Telegram уже зарегистрирован в системе</b> как отдельный аккаунт"
            f"{created_str}.\n\n"
            "После привязки тот аккаунт будет <b>удалён</b>, данные и история перенесутся "
            "в ваш основной аккаунт. Войдя через Telegram в будущем, вы попадёте в этот профиль.\n\n"
            "Подтвердите действие:",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    # Нет конфликта — просто привязываем
    await _do_link(query, tg_id, row["id"], token)


async def link_merge_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data  # "link_merge_keep_web:<token>:<tg_user_id>:<web_user_id>"

    try:
        action, token, tg_user_id_str, web_user_id_str = data.split(":", 3)
        tg_user_id = int(tg_user_id_str)
        web_user_id = int(web_user_id_str)
    except (ValueError, TypeError):
        await query.edit_message_text("Ошибка обработки запроса.")
        return

    tg_id = query.from_user.id

    if action == "link_merge_keep_web":
        # Оставляем web-аккаунт (web_user_id) как основной.
        # Переносим данные из TG-аккаунта (tg_user_id), затем УДАЛЯЕМ его.
        from web.routes.account import merge_accounts
        from services.user_permanent_delete import permanently_delete_user

        await merge_accounts(primary_id=web_user_id, secondary_id=tg_user_id)
        await database.execute(
            users.update().where(users.c.id == web_user_id).values(
                tg_id=tg_id,
                linked_tg_id=tg_id,
                link_token=None,
                link_token_expires=None,
            )
        )
        # Удаляем вторичный TG-аккаунт полностью
        ok, err = await permanently_delete_user(tg_user_id)
        if not ok:
            logger.warning("link_merge: permanently_delete_user(%s) failed: %s", tg_user_id, err)

        await query.edit_message_text(
            "✅ <b>Готово!</b> Аккаунты объединены.\n\n"
            "Прежний Telegram-аккаунт удалён. Теперь при входе через Telegram "
            "вы будете попадать в свой основной профиль.",
            parse_mode="HTML",
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
        "✅ <b>Telegram успешно привязан!</b>\n\n"
        "Теперь вы можете входить на сайт через Telegram.",
        parse_mode="HTML",
    )
