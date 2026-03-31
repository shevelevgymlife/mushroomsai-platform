"""Обработчик подтверждения привязки Telegram-аккаунта через deeplink.

callback_data Telegram ≤ 64 байт: кнопка слияния хранит только link_merge_ok:<link_token>,
а id второго аккаунта — в users.link_merge_secondary_id.
"""
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from db.database import database
from db.models import users
from bot.handlers.channel_autopost import main_keyboard_with_autopost
from config import settings

logger = logging.getLogger(__name__)


def _user_row_matches_telegram_id(row: dict, tg_id: int) -> bool:
    """Как find_user_by_telegram_id: совпадение по tg_id или linked_tg_id."""
    for key in ("tg_id", "linked_tg_id"):
        v = row.get(key)
        if v is not None and int(v) == int(tg_id):
            return True
    return False


async def _send_main_reply_keyboard(context: ContextTypes.DEFAULT_TYPE, chat_id: int, internal_user_id: int) -> None:
    """После правок сообщения с inline-кнопками клиент часто не показывает reply-меню — принудительно открываем."""
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    kb = await main_keyboard_with_autopost(site, False, int(internal_user_id))
    await context.bot.send_message(
        chat_id=chat_id,
        text="⌨️",
        reply_markup=kb,
        disable_notification=True,
    )


async def link_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        pass

    data = query.data or ""

    try:
        action, token = data.split(":", 1)
    except ValueError:
        try:
            await query.edit_message_text("Неверный формат запроса.")
        except Exception:
            pass
        return

    if action == "link_cancel":
        await database.execute(
            users.update()
            .where(users.c.link_token == token)
            .values(
                link_token=None,
                link_token_expires=None,
                link_merge_secondary_id=None,
            )
        )
        try:
            await query.edit_message_text("Привязка отменена.")
        except Exception as e:
            logger.warning("link_cancel edit_message: %s", e)
        return

    if action != "link_confirm":
        return

    tg_id = query.from_user.id

    try:
        row = await database.fetch_one(users.select().where(users.c.link_token == token))
        if not row:
            await query.edit_message_text("Ссылка недействительна или уже использована.")
            return

        expires = row["link_token_expires"]
        if expires and datetime.utcnow() > expires:
            await query.edit_message_text("Срок действия ссылки истёк. Сгенерируйте новую на сайте.")
            return

        from web.routes.account import _resolve_primary_row, find_user_by_telegram_id_excluding

        row_primary = await _resolve_primary_row(int(row["id"]))
        if not row_primary:
            await query.edit_message_text("Ссылка недействительна или уже использована.")
            return
        row = row_primary

        existing = await find_user_by_telegram_id_excluding(tg_id, int(row["id"]))

        if existing and existing["id"] != row["id"]:
            created_str = ""
            if existing.get("created_at"):
                try:
                    created_str = f", создан {existing['created_at'].strftime('%d.%m.%Y')}"
                except Exception:
                    pass

            await database.execute(
                users.update()
                .where(users.c.id == row["id"])
                .values(link_merge_secondary_id=existing["id"])
            )

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Да, привязать и совместить аккаунт",
                            callback_data=f"link_merge_ok:{token}",
                        )
                    ],
                    [InlineKeyboardButton("❌ Отмена", callback_data=f"link_cancel:{token}")],
                ]
            )
            await query.edit_message_text(
                f"⚠️ <b>Этот Telegram уже зарегистрирован в системе</b> как отдельный аккаунт"
                f"{created_str}.\n\n"
                "После привязки тот аккаунт будет <b>совмещён</b>, данные и история перенесутся "
                "в ваш основной аккаунт. Войдя через Telegram в будущем, вы попадёте в этот профиль.\n\n"
                "Подтвердите действие:",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return

        await _do_link(query, tg_id, row["id"], token, context)
    except Exception as e:
        logger.exception("link_confirm: %s", e)
        try:
            await query.answer("Ошибка сервера. Попробуйте позже.", show_alert=True)
        except Exception:
            pass


async def link_merge_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        pass

    data = query.data or ""
    try:
        action, token = data.split(":", 1)
    except ValueError:
        try:
            await query.edit_message_text("Неверный формат запроса.")
        except Exception:
            pass
        return

    if action != "link_merge_ok":
        return

    tg_id = query.from_user.id

    try:
        row = await database.fetch_one(users.select().where(users.c.link_token == token))
        if not row or not row.get("link_merge_secondary_id"):
            await query.edit_message_text("Ссылка недействительна или устарела. Создайте новую на сайте.")
            return

        from web.routes.account import _resolve_primary_row

        row_primary = await _resolve_primary_row(int(row["id"]))
        if not row_primary:
            await query.edit_message_text("Ссылка недействительна или устарела. Создайте новую на сайте.")
            return
        row = row_primary

        secondary_id = int(row["link_merge_secondary_id"])
        secondary = await database.fetch_one(users.select().where(users.c.id == secondary_id))
        sec = dict(secondary) if secondary is not None else {}
        if not secondary or not _user_row_matches_telegram_id(sec, tg_id):
            await query.edit_message_text(
                "Подтверждение недоступно: откройте бот с того же Telegram-аккаунта, что и раньше."
            )
            return

        web_user_id = int(row["id"])
        from web.routes.account import merge_accounts
        from services.user_permanent_delete import permanently_delete_user

        await merge_accounts(primary_id=web_user_id, secondary_id=secondary_id)
        sec_after = await database.fetch_one(users.select().where(users.c.id == secondary_id))
        if not sec_after:
            logger.error("link_merge_ok: secondary %s missing after merge_accounts pri=%s", secondary_id, web_user_id)
            await query.edit_message_text(
                "Не удалось завершить привязку. Создайте новую ссылку на сайте (аккаунт → привязка входа)."
            )
            return

        pu = sec_after.get("primary_user_id")
        merged_into_us = pu is not None and int(pu) == int(web_user_id)
        still_tg = sec_after.get("tg_id") is not None

        if merged_into_us and still_tg:
            await database.execute(
                users.update()
                .where(users.c.id == secondary_id)
                .values(tg_id=None, linked_tg_id=None)
            )
        elif not merged_into_us and still_tg:
            logger.error(
                "link_merge_ok: merge_accounts no-op pri=%s sec=%s tg=%s pu=%s",
                web_user_id,
                secondary_id,
                sec_after.get("tg_id"),
                pu,
            )
            await query.edit_message_text(
                "Не удалось объединить аккаунты (слияние не применилось). "
                "Откройте на сайте привязку входа и сгенерируйте новую ссылку для Telegram."
            )
            return
        elif not merged_into_us and not still_tg:
            logger.warning(
                "link_merge_ok: unexpected secondary state pri=%s sec=%s pu=%s",
                web_user_id,
                secondary_id,
                pu,
            )
            await query.edit_message_text(
                "Не удалось завершить привязку. Создайте новую ссылку на сайте (аккаунт → привязка входа)."
            )
            return

        await database.execute(
            users.update()
            .where(users.c.id == web_user_id)
            .values(
                tg_id=tg_id,
                linked_tg_id=tg_id,
                link_token=None,
                link_token_expires=None,
                link_merge_secondary_id=None,
            )
        )
        logger.info("link_merge_ok: success pri=%s sec=%s tg=%s", web_user_id, secondary_id, tg_id)

        ok, err = await permanently_delete_user(secondary_id)
        if not ok:
            logger.warning("link_merge_ok: permanently_delete_user(%s) failed: %s", secondary_id, err)

        await query.edit_message_text(
            "✅ <b>Готово!</b> Аккаунты объединены.\n\n"
            "Прежний Telegram-аккаунт совмещён с основным профилем. Теперь при входе через Telegram "
            "вы будете попадать в свой основной профиль.",
            parse_mode="HTML",
        )
        if query.message:
            await _send_main_reply_keyboard(context, query.message.chat_id, web_user_id)
    except Exception as e:
        logger.exception("link_merge_ok: %s", e)
        try:
            await query.answer("Ошибка сервера. Попробуйте позже.", show_alert=True)
        except Exception:
            pass


async def _do_link(query, tg_id: int, user_id: int, token: str, context: ContextTypes.DEFAULT_TYPE | None = None) -> None:
    await database.execute(
        users.update()
        .where(users.c.id == user_id)
        .values(
            tg_id=tg_id,
            linked_tg_id=tg_id,
            link_token=None,
            link_token_expires=None,
            link_merge_secondary_id=None,
        )
    )
    await query.edit_message_text(
        "✅ <b>Telegram успешно привязан!</b>\n\n"
        "Теперь вы можете входить на сайт через Telegram.",
        parse_mode="HTML",
    )
    if context and query.message:
        await _send_main_reply_keyboard(context, query.message.chat_id, int(user_id))
