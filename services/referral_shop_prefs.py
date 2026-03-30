"""Реферальный внешний магазин: у приглашённого пользователя (referred_by → обычный user) подмена ссылки «Купить» и скрытие акцента «маркетплейс» в навигации."""
from __future__ import annotations

from typing import Any, Optional

from db.database import database
from db.models import users

# Кнопки главного бота (должны совпадать с обработчиками в main_bot.py)
# Одна подпись для всех: прямой вход, реферал админа, реферал участника.
TG_BTN_SHOP_MARKETPLACE = "🛍 Магазин"
TG_BTN_SHOP_SIMPLE = "🛍 Магазин"

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

SHOP_MESSAGE_NO_MP_HTML = (
    "Вот ссылки на магазины, где можно заказать нужные грибы:\n\n"
    "• Для России и Белоруссии: магазин Сдэк/Почта РФ — "
    f'<a href="{SHOP_RUS_URL}">открыть в Telegram</a>\n\n'
    "• Для Европы и Америки: магазин — "
    f'<a href="{SHOP_EU_US_URL}">Grimmurk</a>\n\n'
    "Приложение NEUROFUNGI AI — сообщество и AI-консультации (кнопка ниже).\n\n"
    "Если будут вопросы по выбору или приёму — нажмите «Задать вопрос AI» ниже."
)


def shop_message_referral_html(ambassador_shop_url: str) -> str:
    """РФ/РБ — ссылка амбассадора; Европа и Америка — общая Grimmurk для всех."""
    u = ambassador_shop_url.strip()
    return (
        "🛒 <b>Магазин по вашей реферальной ссылке</b>\n\n"
        "Вот ссылки, где можно заказать нужные грибы:\n\n"
        "• Для России и Белоруссии: магазин партнёра (по вашей реферальной ссылке) — "
        f'<a href="{u}">перейти</a>\n\n'
        "• Для Европы и Америки: магазин — "
        f'<a href="{SHOP_EU_US_URL}">Grimmurk</a>\n\n'
        "В приложении NEUROFUNGI AI — лента сообщества, карточки товаров с отзывами и AI-консультант.\n\n"
        "Если будут вопросы по выбору или приёму — нажмите «Задать вопрос AI» ниже."
    )


def _staff_role(role: str | None) -> bool:
    return (role or "user").lower() in ("admin", "moderator")


async def attach_referral_shop_context(u: dict) -> None:
    """Дополняет dict пользователя для шаблонов (после загрузки из БД)."""
    uid = int(u.get("primary_user_id") or u["id"])
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row:
        u["show_marketplace_nav"] = True
        u["referrer_external_shop_url"] = None
        return
    rb = row.get("referred_by")
    if not rb:
        u["show_marketplace_nav"] = True
        u["referrer_external_shop_url"] = None
        return
    ref = await database.fetch_one(users.select().where(users.c.id == int(rb)))
    if not ref:
        u["show_marketplace_nav"] = True
        u["referrer_external_shop_url"] = None
        return
    if _staff_role(ref.get("role")):
        u["show_marketplace_nav"] = True
        u["referrer_external_shop_url"] = None
        return
    u["show_marketplace_nav"] = False
    url = (ref.get("referral_shop_url") or "").strip()
    u["referrer_external_shop_url"] = url if url else None


async def external_buy_url_for_user(user: dict | None) -> Optional[str]:
    """Ссылка «Купить» для карточки товара: URL реферера-амбассадора или None."""
    if not user:
        return None
    uid = int(user.get("primary_user_id") or user["id"])
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row:
        return None
    rb = row.get("referred_by")
    if not rb:
        return None
    ref = await database.fetch_one(users.select().where(users.c.id == int(rb)))
    if not ref or _staff_role(ref.get("role")):
        return None
    url = (ref.get("referral_shop_url") or "").strip()
    return url if url else None


async def tg_shop_button_label(internal_user_id: int) -> str:
    """Подпись клавиатуры «Магазин» (для всех сценариев входа)."""
    row = await database.fetch_one(users.select().where(users.c.id == int(internal_user_id)))
    if not row:
        return TG_BTN_SHOP_MARKETPLACE
    rb = row.get("referred_by")
    if not rb:
        return TG_BTN_SHOP_MARKETPLACE
    ref = await database.fetch_one(users.select().where(users.c.id == int(rb)))
    if not ref or _staff_role(ref.get("role")):
        return TG_BTN_SHOP_MARKETPLACE
    return TG_BTN_SHOP_SIMPLE


async def tg_shop_message_and_buttons(internal_user_id: int, site: str) -> tuple[str, list[list[Any]]]:
    """
    Текст и строки inline-кнопок для ответа на кнопку «Магазин».
    Для приглашённых по ссылке обычного пользователя — акцент на URL амбассадора, без блока про маркетплейс NF (текст ответа; клавиатура у всех «Магазин»).
    """
    from telegram import InlineKeyboardButton, WebAppInfo

    row = await database.fetch_one(users.select().where(users.c.id == int(internal_user_id)))
    ext: Optional[str] = None
    show_mp = True
    if row and row.get("referred_by"):
        ref = await database.fetch_one(users.select().where(users.c.id == int(row["referred_by"])))
        if ref and not _staff_role(ref.get("role")):
            show_mp = False
            u = (ref.get("referral_shop_url") or "").strip()
            if u:
                ext = u

    app_url = site.rstrip("/") + "/app"
    base_rows = [
        [
            InlineKeyboardButton(
                "🍄 Приложение: регистрация и маркетплейс",
                web_app=WebAppInfo(url=app_url),
            )
        ],
    ]

    if ext:
        rows = [[InlineKeyboardButton("🛒 Открыть магазин амбассадора", url=ext)]] + base_rows
        return shop_message_referral_html(ext), rows

    if not show_mp:
        return SHOP_MESSAGE_NO_MP_HTML, base_rows

    return SHOP_MESSAGE_HTML, base_rows
