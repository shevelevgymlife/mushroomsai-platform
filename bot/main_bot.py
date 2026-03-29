import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, WebAppInfo
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import settings
from bot.handlers.start import BTN_AI, BTN_AI_EXIT, BTN_CONNECT_CHANNEL, main_keyboard, start
from bot.handlers.link import link_confirm_callback, link_merge_callback
from bot.handlers.support import get_support_conversation
from bot.handlers.community_post_wizard import get_community_post_conversation
from bot.handlers.chat import (
    handle_chat_message,
    tg_ai_continue_callback,
    tg_ai_exit_callback,
)

logger = logging.getLogger(__name__)

SECURITY_URL = "https://t.me/VPN_POLETELI_bot?start=742166400"


async def _reply_kb(update, context, ai_active: bool):
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    from bot.handlers.start import ensure_user
    from bot.handlers.channel_autopost import main_keyboard_with_autopost

    if not update.effective_user:
        return main_keyboard(site, ai_active)
    u = await ensure_user(update.effective_user)
    if not u:
        return main_keyboard(site, ai_active)
    return await main_keyboard_with_autopost(site, ai_active, int(u["id"]))

# Тексты кнопок клавиатуры
BTN_SHOP = "🛍 Маркет плейс"
BTN_COMMUNITY = "🌐 Сообщество"
BTN_WEB = "🌍 Веб версия"
BTN_SECURITY = "🔒 Безопасность"
BTN_SUPPORT = "🆘 Тех. поддержка"


SHOP_RUS_URL = "https://t.me/neurotrops_rus_bot?start=rHQemtw"
SHOP_EU_US_URL = "https://grimmurk.com/?aff=Shevelev"

SHOP_MESSAGE_HTML = (
    "Вот ссылки на магазины, где можно заказать нужные грибы:\n\n"
    "• Для России и Белоруссии: магазин Сдэк/Почта РФ — "
    f'<a href="{SHOP_RUS_URL}">открыть в Telegram</a>\n\n'
    "• Для Европы и Америки: магазин — "
    f'<a href="{SHOP_EU_US_URL}">Grimmurk</a>\n\n'
    "🛍 <b>Маркет плейс NEUROFUNGI</b>\n\n"
    "Доступен только внутри приложения после регистрации и подписки <b>Старт</b>.\n\n"
    "В маркет плейсе у каждого товара есть описание, комментарии, отзывы и рейтинг.\n\n"
    "Если будут вопросы по выбору или приёму — нажмите кнопку «Задать вопрос AI» ниже."
)


async def _enable_ai_mode_and_notify(update, context) -> None:
    """Включает режим AI и отправляет то же приветствие, что и по кнопке клавиатуры."""
    context.user_data["tg_ai_mode"] = True
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    text = (
        "🤖 <b>Режим AI включён</b>\n\n"
        "Напишите вопрос ниже — ответит консультант.\n\n"
        "После ответа вы сможете выбрать: продолжить с AI или выйти.\n"
        "Либо нажмите «❌ Выйти из режима AI» или любую кнопку меню (Магазин, Сообщество…), чтобы выйти."
    )
    kb = await _reply_kb(update, context, ai_active=True)
    if update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def _shop_ask_ai_callback(update, context):
    await update.callback_query.answer()
    await _enable_ai_mode_and_notify(update, context)


async def _shop_handler(update, context):
    context.user_data["tg_ai_mode"] = False
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    app_url = site.rstrip("/") + "/app"
    await update.message.reply_text(
        SHOP_MESSAGE_HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🍄 Приложение: регистрация и маркетплейс",
                        web_app=WebAppInfo(url=app_url),
                    )
                ],
                [InlineKeyboardButton(BTN_AI, callback_data="tg_shop_ask_ai")],
            ]
        ),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await update.message.reply_text("⌨️", reply_markup=await _reply_kb(update, context, ai_active=False))


async def _community_handler(update, context):
    context.user_data["tg_ai_mode"] = False
    site = (settings.SITE_URL or "").rstrip("/")
    await update.message.reply_text(
        "🌐 <b>Сообщество NEUROFUNGI AI</b>\n\nОткрыть приложение:",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🍄 Открыть сообщество",
                        web_app=WebAppInfo(url=site + "/app"),
                    )
                ],
            ]
        ),
        parse_mode="HTML",
    )
    await update.message.reply_text("⌨️", reply_markup=await _reply_kb(update, context, ai_active=False))


async def _web_handler(update, context):
    context.user_data["tg_ai_mode"] = False
    site = (settings.SITE_URL or "https://mushroomsai.ru").rstrip("/")
    await update.message.reply_text(
        "🌍 <b>Веб версия NEUROFUNGI AI</b>\n\nОткрыть сайт в браузере:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🌍 Открыть сайт", url=site)]]),
        parse_mode="HTML",
    )
    await update.message.reply_text("⌨️", reply_markup=await _reply_kb(update, context, ai_active=False))


async def _security_handler(update, context):
    context.user_data["tg_ai_mode"] = False
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    await update.message.reply_text(
        "🔒 <b>Безопасность</b>\n\nЗащитите свои данные и соединение:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔒 Открыть", url=SECURITY_URL)]]),
        parse_mode="HTML",
    )
    await update.message.reply_text("⌨️", reply_markup=await _reply_kb(update, context, ai_active=False))


async def _ai_enter_handler(update, context):
    await _enable_ai_mode_and_notify(update, context)


async def _ai_exit_handler(update, context):
    context.user_data["tg_ai_mode"] = False
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    await update.message.reply_text(
        "Вы вышли из режима AI. Кнопки бота снова в обычном режиме.",
        reply_markup=await _reply_kb(update, context, ai_active=False),
    )


def create_bot() -> Application:
    application = (
        Application.builder()
        .token(settings.TELEGRAM_TOKEN)
        .build()
    )
    application.bot_data.setdefault("channel_autopost_pending", {})

    from bot.handlers.channel_autopost import (
        ch_link_done_callback,
        connect_channel_handler,
        get_channel_forward_handler,
        get_toggle_autopost_handler,
        on_channel_post,
        on_my_chat_member,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(get_community_post_conversation(), group=-1)
    application.add_handler(get_support_conversation())

    application.add_handler(
        ChatMemberHandler(on_my_chat_member, chat_member_types=ChatMemberHandler.MY_CHAT_MEMBER)
    )
    application.add_handler(CallbackQueryHandler(ch_link_done_callback, pattern=r"^ch_link_done$"))

    ch_group = -2
    application.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, on_channel_post), group=ch_group)
    application.add_handler(get_channel_forward_handler(), group=ch_group)
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.Regex(f"^{re.escape(BTN_CONNECT_CHANNEL)}$"),
            connect_channel_handler,
        ),
        group=ch_group,
    )
    application.add_handler(get_toggle_autopost_handler(), group=ch_group)

    # Режим AI: вход / выход (до общего текстового хендлера)
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_AI_EXIT}$"), _ai_exit_handler))
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_AI}$"), _ai_enter_handler))

    # Кнопки клавиатуры (сбрасывают режим AI)
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_SHOP}$"), _shop_handler))
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_COMMUNITY}$"), _community_handler))
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_WEB}$"), _web_handler))
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_SECURITY}$"), _security_handler))

    # Callback кнопки
    application.add_handler(CallbackQueryHandler(link_confirm_callback, pattern=r"^link_confirm:"))
    application.add_handler(CallbackQueryHandler(link_confirm_callback, pattern=r"^link_cancel:"))
    application.add_handler(CallbackQueryHandler(link_merge_callback, pattern=r"^link_merge_ok:"))
    application.add_handler(CallbackQueryHandler(tg_ai_continue_callback, pattern=r"^tg_ai_continue$"))
    application.add_handler(CallbackQueryHandler(tg_ai_exit_callback, pattern=r"^tg_ai_exit$"))
    application.add_handler(CallbackQueryHandler(_shop_ask_ai_callback, pattern=r"^tg_shop_ask_ai$"))

    # Текст: в AI только если включён режим (см. handle_chat_message)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_chat_message))

    return application


async def send_telegram_notification(tg_id: int, text: str) -> None:
    """Send a text notification to a Telegram user via the main bot."""
    try:
        from telegram import Bot
        bot = Bot(token=settings.TELEGRAM_TOKEN)
        await bot.send_message(chat_id=tg_id, text=text, parse_mode="HTML")
    except Exception as e:
        logger.warning("send_telegram_notification failed for tg_id=%s: %s", tg_id, e)


async def setup_bot_menu(application: Application) -> None:
    """Устанавливает кнопку меню бота как WebApp (открывает сайт)."""
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    try:
        await application.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Вход",
                web_app=WebAppInfo(url=site + "/app"),
            )
        )
        logger.info("Bot menu button set to WebApp: %s", site)
    except Exception as e:
        logger.warning("Could not set bot menu button: %s", e)
