import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, WebAppInfo
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from config import settings
from bot.handlers.legal_bundle import BTN_LEGAL_BUNDLE, send_legal_bundle
from bot.handlers.start import BTN_AI, BTN_AI_EXIT, BTN_REFRESH_BOT, main_keyboard, start, start_handler
from bot.handlers.link import link_confirm_callback, link_merge_callback
from bot.handlers.support import get_support_conversation
from bot.handlers.community_post_wizard import get_community_post_conversation
from bot.handlers.partner_wizard import (
    get_partner_conversation,
    ref_copy_help_callback,
    ref_copy_site_callback,
    ref_copy_tg_callback,
)
from bot.handlers.chat import (
    handle_chat_message,
    tg_ai_continue_callback,
    tg_ai_exit_callback,
)
from bot.handlers.yookassa_subscribe import (
    SUCCESSFUL_PAYMENT as TG_PAYMENT_FILTER,
    SUBSCRIBE_BUTTON_TEXT,
    pre_checkout_handler,
    subscribe_command,
    subscribe_menu_handler,
    successful_payment_handler,
    tgpay_plan_callback,
    tgstars_plan_callback,
)
from bot.handlers.legal_commands import privacy_command, terms_command
from bot.handlers.closed_telegram import (
    closed_telegram_back_handler,
    closed_telegram_hub_handler,
    closed_telegram_message_handler,
    on_chat_join_request,
    post_subscribe_closed_tg_hint_callback,
)
from services.closed_telegram_access import TG_BTN_CLOSED_BACK, TG_BTN_CLOSED_HUB

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

# Тексты кнопок клавиатуры (два варианта — см. services/referral_shop_prefs)
from services.referral_shop_prefs import TG_BTN_SHOP_MARKETPLACE, TG_BTN_SHOP_SIMPLE

BTN_SHOP = TG_BTN_SHOP_MARKETPLACE  # обратная совместимость имён
_SHOP_BUTTONS_RX = "^(" + re.escape(TG_BTN_SHOP_MARKETPLACE) + "|" + re.escape(TG_BTN_SHOP_SIMPLE) + ")$"
BTN_WEB = "🌍 Веб версия"
BTN_SECURITY = "🔒 Безопасность"
BTN_SUPPORT = "🆘 Тех. поддержка"


async def _enable_ai_mode_and_notify(update, context) -> None:
    """Включает режим AI и отправляет то же приветствие, что и по кнопке клавиатуры."""
    context.user_data["tg_ai_mode"] = True
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    text = (
        "🤖 <b>Режим AI включён</b>\n\n"
        "Напишите вопрос ниже — ответит консультант.\n\n"
        "После ответа вы сможете выбрать: продолжить с AI или выйти.\n"
        "Либо нажмите «❌ Выйти из режима AI» или любую кнопку меню (Магазин и др.), чтобы выйти."
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
    from services.referral_shop_prefs import tg_shop_message_and_buttons

    context.user_data["tg_ai_mode"] = False
    site = (settings.SITE_URL or "https://mushroomsai.onrender.com").rstrip("/")
    from bot.handlers.start import ensure_user

    u = await ensure_user(update.effective_user) if update.effective_user else None
    uid = int(u["id"]) if u else 0
    text, rows = await tg_shop_message_and_buttons(uid, site)
    rows = list(rows) + [[InlineKeyboardButton(BTN_AI, callback_data="tg_shop_ask_ai")]]
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="HTML",
        disable_web_page_preview=True,
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


async def _refresh_bot_handler(update, context):
    """Только повторяет /start без аргументов (тот же текст и клавиатура)."""
    await start_handler(update, context, _argv=[])
    raise ApplicationHandlerStop


async def _legal_bundle_handler(update, context):
    await send_legal_bundle(update, context)
    raise ApplicationHandlerStop


async def _referral_withdraw_handler(update, context):
    """Кнопка «💸 Вывести N ₽» — та же заявка, что на сайте /referral/withdraw."""
    from bot.handlers.start import ensure_user
    from services.referral_service import telegram_referral_withdraw_reply_html

    context.user_data["tg_ai_mode"] = False
    if not update.message or not update.effective_user:
        return
    u = await ensure_user(update.effective_user)
    if not u:
        return
    uid = int(u.get("primary_user_id") or u["id"])
    _ok, html_body = await telegram_referral_withdraw_reply_html(uid)
    await update.message.reply_html(html_body, disable_web_page_preview=True)
    await update.message.reply_text("⌨️", reply_markup=await _reply_kb(update, context, ai_active=False))


def create_bot() -> Application:
    application = (
        Application.builder()
        .token(settings.TELEGRAM_TOKEN)
        .build()
    )
    application.bot_data.setdefault("channel_autopost_pending", {})

    from bot.handlers.channel_autopost import (
        ch_link_done_callback,
        ch_soc_btn_callback,
        get_channel_forward_handler,
        on_channel_post,
        on_my_chat_member,
    )

    application.add_handler(CommandHandler("start", start))
    legal_group = -4
    application.add_handler(CommandHandler("terms", terms_command), group=legal_group)
    application.add_handler(CommandHandler("privacy", privacy_command), group=legal_group)
    # Подписка ЮKassa в Telegram (до общих текстовых хендлеров)
    pay_group = -3
    application.add_handler(CommandHandler("subscribe", subscribe_command), group=pay_group)
    application.add_handler(PreCheckoutQueryHandler(pre_checkout_handler), group=pay_group)
    application.add_handler(
        CallbackQueryHandler(tgpay_plan_callback, pattern=r"^tgpay_[a-zA-Z0-9_]+$"),
        group=pay_group,
    )
    application.add_handler(
        CallbackQueryHandler(tgstars_plan_callback, pattern=r"^tgstars_[a-zA-Z0-9_]+$"),
        group=pay_group,
    )
    application.add_handler(MessageHandler(TG_PAYMENT_FILTER, successful_payment_handler), group=pay_group)
    application.add_handler(
        MessageHandler(SUBSCRIBE_BUTTON_TEXT, subscribe_menu_handler),
        group=pay_group,
    )
    application.add_handler(get_partner_conversation(), group=-1)
    application.add_handler(get_community_post_conversation(), group=-1)
    application.add_handler(get_support_conversation())

    application.add_handler(
        ChatMemberHandler(on_my_chat_member, chat_member_types=ChatMemberHandler.MY_CHAT_MEMBER)
    )
    application.add_handler(ChatJoinRequestHandler(on_chat_join_request))
    application.add_handler(CallbackQueryHandler(ch_link_done_callback, pattern=r"^ch_link_done$"))
    application.add_handler(CallbackQueryHandler(ch_soc_btn_callback, pattern=r"^ch_soc_btn:[01]$"))

    ch_group = -2
    application.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, on_channel_post), group=ch_group)
    application.add_handler(get_channel_forward_handler(), group=ch_group)
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & filters.Regex(r"^💸 Вывести\s"),
            _referral_withdraw_handler,
        ),
        group=ch_group,
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.Regex(f"^{re.escape(BTN_REFRESH_BOT)}$"),
            _refresh_bot_handler,
        ),
        group=ch_group,
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.Regex(f"^{re.escape(BTN_LEGAL_BUNDLE)}$"),
            _legal_bundle_handler,
        ),
        group=ch_group,
    )

    # Режим AI: вход / выход (до общего текстового хендлера)
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_AI_EXIT}$"), _ai_exit_handler))
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_AI}$"), _ai_enter_handler))

    # Кнопки клавиатуры (сбрасывают режим AI)
    application.add_handler(MessageHandler(filters.Regex(_SHOP_BUTTONS_RX), _shop_handler))
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.Regex(f"^{re.escape(TG_BTN_CLOSED_HUB)}$"),
            closed_telegram_hub_handler,
        ),
        group=-2,
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.Regex(f"^{re.escape(TG_BTN_CLOSED_BACK)}$"),
            closed_telegram_back_handler,
        ),
        group=-2,
    )
    application.add_handler(closed_telegram_message_handler(), group=-2)
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_WEB}$"), _web_handler))
    application.add_handler(MessageHandler(filters.Regex(f"^{BTN_SECURITY}$"), _security_handler))

    # Callback кнопки
    application.add_handler(CallbackQueryHandler(link_confirm_callback, pattern=r"^link_confirm:"))
    application.add_handler(CallbackQueryHandler(link_confirm_callback, pattern=r"^link_cancel:"))
    application.add_handler(CallbackQueryHandler(link_merge_callback, pattern=r"^link_merge_ok:"))
    application.add_handler(CallbackQueryHandler(tg_ai_continue_callback, pattern=r"^tg_ai_continue$"))
    application.add_handler(CallbackQueryHandler(tg_ai_exit_callback, pattern=r"^tg_ai_exit$"))
    application.add_handler(CallbackQueryHandler(_shop_ask_ai_callback, pattern=r"^tg_shop_ask_ai$"))
    application.add_handler(CallbackQueryHandler(ref_copy_tg_callback, pattern=r"^ref_copy_tg$"))
    application.add_handler(CallbackQueryHandler(ref_copy_site_callback, pattern=r"^ref_copy_site$"))
    application.add_handler(CallbackQueryHandler(ref_copy_help_callback, pattern=r"^ref_copy_help$"))
    application.add_handler(
        CallbackQueryHandler(post_subscribe_closed_tg_hint_callback, pattern=r"^ctas:[auox]:[cgq]$")
    )

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
                text="Приложение",
                web_app=WebAppInfo(url=site + "/app"),
            )
        )
        logger.info("Bot menu button «Приложение» WebApp: %s", site)
    except Exception as e:
        logger.warning("Could not set bot menu button: %s", e)
