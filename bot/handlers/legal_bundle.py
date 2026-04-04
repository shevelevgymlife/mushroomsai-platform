"""
Один сценарий «документы + концепция сервиса + подписка»: текст в чат, ссылки на все legal-страницы, кнопки оплаты.
"""
from __future__ import annotations

import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import ContextTypes

from config import settings

# Синхронизировано с bot.handlers.yookassa_subscribe.TG_SUBSCRIPTION_PAYMENT_NOTICE
TG_PAYMENT_NOTICE_SHORT = (
    "Оплачивая подписку, вы соглашаетесь с условиями. Возврат средств за уже оплаченный период не предусмотрен."
)


BTN_LEGAL_BUNDLE = "📋 Условия и подписка"


def _site() -> str:
    return (settings.SITE_URL or "https://mushroomsai.ru").strip().rstrip("/")


def legal_bundle_inline_keyboard() -> InlineKeyboardMarkup:
    site = _site()
    if not site.startswith("http"):
        site = "https://" + site.lstrip("/")
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("📄 Оферта", url=f"{site}/legal/offer"),
            InlineKeyboardButton("📜 Соглашение", url=f"{site}/legal/terms"),
        ],
        [
            InlineKeyboardButton("🔒 Конфиденциальность", url=f"{site}/legal/privacy"),
            InlineKeyboardButton("💼 Выплаты партнёрам", url=f"{site}/legal/referral-payouts"),
        ],
        [
            InlineKeyboardButton(
                "💳 Купить подписку (сайт)",
                web_app=WebAppInfo(url=f"{site}/subscriptions"),
            ),
        ],
    ]
    bot_u = (settings.TELEGRAM_BOT_USERNAME or "").strip().lstrip("@")
    if bot_u:
        rows.append(
            [InlineKeyboardButton("💳 Тарифы в боте", url=f"https://t.me/{bot_u}?start=subscribe")]
        )
    return InlineKeyboardMarkup(rows)


def _requisites_block() -> str:
    name = html.escape((settings.REFERRAL_CLIENT_NAME_LEGAL or "—").strip())
    inn = html.escape((settings.REFERRAL_CLIENT_INN or "—").strip())
    ogrnip = html.escape((getattr(settings, "LEGAL_OGRNIP", None) or "—").strip())
    return (
        f"<b>Исполнитель (реквизиты)</b>\n"
        f"{name}\n"
        f"ИНН: <code>{inn}</code>\n"
        f"ОГРНИП: <code>{ogrnip}</code>"
    )


def _concept_block() -> str:
    return (
        "<b>NEUROFUNGI AI — что даёт подписка</b>\n"
        "Один платный тариф открывает доступ к экосистеме (конкретные лимиты и цены — в приложении и при оплате):\n\n"
        "• <b>Соцсеть</b> — лента, профили, публикации, связи.\n"
        "• <b>Закрытый Telegram</b> — канал, группа, чат консультаций (по уровню тарифа).\n"
        "• <b>Магазин</b> — карточки, отзывы, рейтинги.\n"
        "• <b>AI</b> — консультации и сценарии (объём зависит от плана; на бесплатном — лимиты).\n"
        "• <b>Партнёрка магазина</b> — реферальные ссылки на каталог.\n"
        "• <b>Партнёрка соцсети</b> — приглашения и линии (условия на сайте /referral).\n"
        "• <b>Маркетплейс</b> — кабинет продавца на старших тарифах (если доступен по плану).\n"
        "• <b>Кошелёк, подписка, настройки</b> — в веб-приложении и мини-приложении Telegram.\n\n"
        "<b>Бесплатный план</b> — базовый вход; расширение функций — после оформления платной подписки или пробного периода, если он доступен."
    )


def _legal_notice_block(site: str) -> str:
    esc = html.escape
    return (
        "<b>Юридические документы</b>\n"
        "Ниже кнопки ведут на актуальные тексты на сайте:\n"
        f"• {esc(site + '/legal/offer')} — публичная оферта (оплата и доступ).\n"
        f"• {esc(site + '/legal/terms')} — пользовательское соглашение.\n"
        f"• {esc(site + '/legal/privacy')} — политика конфиденциальности.\n"
        f"• {esc(site + '/legal/referral-payouts')} — правила выплат партнёрам (самозанятые / ИП).\n\n"
        f"<b>Оплата</b>\n"
        f"<i>{html.escape(TG_PAYMENT_NOTICE_SHORT)}</i>\n\n"
        "Оформите подписку кнопками ниже — полный доступ к сервису в рамках выбранного тарифа."
    )


async def send_legal_bundle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет один или два сообщения (лимит Telegram 4096) + inline-клавиатуру на последнем."""
    site = _site()
    msg = update.message or (update.callback_query and update.callback_query.message)
    if not msg:
        return

    part_a = _requisites_block() + "\n\n" + _concept_block()
    part_b = _legal_notice_block(site)
    kb = legal_bundle_inline_keyboard()

    if len(part_a) + len(part_b) + 80 > 4096:
        await msg.reply_html(part_a, disable_web_page_preview=True)
        await msg.reply_html(part_b, reply_markup=kb, disable_web_page_preview=True)
    else:
        await msg.reply_html(part_a + "\n\n" + part_b, reply_markup=kb, disable_web_page_preview=True)


async def legal_bundle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_legal_bundle(update, context)
