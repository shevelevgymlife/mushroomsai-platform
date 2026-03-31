"""Реферальный внешний магазин: у приглашённого пользователя (referred_by → обычный user) подмена ссылки «Купить» и скрытие акцента «маркетплейс» в навигации."""
from __future__ import annotations

from typing import Any, Optional


def normalize_referral_shop_url(raw: Optional[str]) -> Optional[str]:
    """Пустая строка → None; иначе только http(s), разумная длина (как в админке)."""
    s = (raw or "").strip()
    if not s:
        return None
    if len(s) > 2048:
        raise ValueError("Слишком длинная ссылка")
    low = s.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        raise ValueError("Укажите ссылку с http:// или https://")
    return s

from db.database import database
from db.models import users

# Кнопки главного бота (должны совпадать с обработчиками в main_bot.py)
# Одна подпись для всех: прямой вход, реферал админа, реферал участника.
TG_BTN_SHOP_MARKETPLACE = "🛍 Магазин"
TG_BTN_SHOP_SIMPLE = "🛍 Магазин"

SHOP_RUS_URL = "https://t.me/neurotrops_rus_bot?start=rHQemtw"
SHOP_EU_US_URL = "https://grimmurk.com/?aff=Shevelev"

# Inline-кнопки под сообщением «Магазин» (лимит Telegram 64 символа)
TG_BTN_SHOP_RU = "Магазин РФ и РБ СДЭК / ПОЧТА РФ"
TG_BTN_SHOP_EU = "Магазин Европа / Америка"

# Текст под кнопками (без ссылок — они в кнопках выше)
SHOP_FOOTER_HTML = (
    "🛍 Отзывы, рейтинги и комментарии о товарах в магазине также доступны внутри приложения "
    "после регистрации и подписки <b>Старт</b>.\n\n"
    "Если будут вопросы по выбору или приёму — нажмите кнопку «Задать вопрос AI» ниже."
)


def _staff_role(role: str | None) -> bool:
    return (role or "user").lower() in ("admin", "moderator")


async def shop_urls_for_user(internal_user_id: int) -> tuple[str, str]:
    """
    Ссылки магазинов для пользователя: (РФ/РБ Telegram, Европа/Америка Grimmurk).
    Реферал от обычного пользователя с referral_shop_url → РФ — URL амбассадора; иначе стандартные.
    Учитывается primary_user_id (как в веб-сессии).
    Если у пользователя сохранена своя партнёрская ссылка (referral_shop_partner_self), но нет Старт+ —
    платформенные URL до возобновления подписки. Приглашённые без своей ссылки смотрят referred_by.
    """
    eu = SHOP_EU_US_URL
    row = await database.fetch_one(users.select().where(users.c.id == int(internal_user_id)))
    if not row:
        return SHOP_RUS_URL, eu
    uid = int(row.get("primary_user_id") or row["id"])
    if uid != int(internal_user_id):
        row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row:
        return SHOP_RUS_URL, eu
    # Только партнёры с сохранённой своей ссылкой; иначе смотрим referred_by (приглашённый по рефке амбассадора).
    if (row.get("referral_shop_url") or "").strip() and bool(row.get("referral_shop_partner_self")):
        from services.subscription_service import paid_subscription_for_referral_program

        if not await paid_subscription_for_referral_program(uid):
            return SHOP_RUS_URL, eu
    rb = row.get("referred_by")
    if not rb:
        return SHOP_RUS_URL, eu
    ref = await database.fetch_one(users.select().where(users.c.id == int(rb)))
    if not ref or _staff_role(ref.get("role")):
        return SHOP_RUS_URL, eu
    u = (ref.get("referral_shop_url") or "").strip()
    if u:
        from services.subscription_service import paid_subscription_for_referral_program

        if await paid_subscription_for_referral_program(int(ref["id"])):
            return u, eu
    return SHOP_RUS_URL, eu


async def attach_referral_shop_context(u: dict) -> None:
    """Дополняет dict пользователя для шаблонов (после загрузки из БД)."""
    uid = int(u.get("primary_user_id") or u["id"])
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row:
        u["show_marketplace_nav"] = True
        u["referrer_external_shop_url"] = None
        return
    if (row.get("referral_shop_url") or "").strip() and bool(row.get("referral_shop_partner_self")):
        from services.subscription_service import paid_subscription_for_referral_program

        if not await paid_subscription_for_referral_program(uid):
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
    url = (ref.get("referral_shop_url") or "").strip()
    if url:
        from services.subscription_service import paid_subscription_for_referral_program

        if await paid_subscription_for_referral_program(int(ref["id"])):
            u["show_marketplace_nav"] = False
            u["referrer_external_shop_url"] = url
            return
    u["show_marketplace_nav"] = True
    u["referrer_external_shop_url"] = None


async def external_buy_url_for_user(user: dict | None) -> Optional[str]:
    """Ссылка «Купить» для карточки товара: URL реферера-амбассадора или None."""
    if not user:
        return None
    uid = int(user.get("primary_user_id") or user["id"])
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row:
        return None
    if (row.get("referral_shop_url") or "").strip() and bool(row.get("referral_shop_partner_self")):
        from services.subscription_service import paid_subscription_for_referral_program

        if not await paid_subscription_for_referral_program(uid):
            return None
    rb = row.get("referred_by")
    if not rb:
        return None
    ref = await database.fetch_one(users.select().where(users.c.id == int(rb)))
    if not ref or _staff_role(ref.get("role")):
        return None
    url = (ref.get("referral_shop_url") or "").strip()
    if not url:
        return None
    from services.subscription_service import paid_subscription_for_referral_program

    if not await paid_subscription_for_referral_program(int(ref["id"])):
        return None
    return url


async def tg_shop_button_label(internal_user_id: int) -> str:
    """Подпись клавиатуры «Магазин» (для всех сценариев входа)."""
    row = await database.fetch_one(users.select().where(users.c.id == int(internal_user_id)))
    if not row:
        return TG_BTN_SHOP_MARKETPLACE
    uid = int(row.get("primary_user_id") or row["id"])
    if uid != int(internal_user_id):
        row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row:
        return TG_BTN_SHOP_MARKETPLACE
    if (row.get("referral_shop_url") or "").strip() and bool(row.get("referral_shop_partner_self")):
        from services.subscription_service import paid_subscription_for_referral_program

        if not await paid_subscription_for_referral_program(uid):
            return TG_BTN_SHOP_MARKETPLACE
    rb = row.get("referred_by")
    if not rb:
        return TG_BTN_SHOP_MARKETPLACE
    ref = await database.fetch_one(users.select().where(users.c.id == int(rb)))
    if not ref or _staff_role(ref.get("role")):
        return TG_BTN_SHOP_MARKETPLACE
    url = (ref.get("referral_shop_url") or "").strip()
    if not url:
        return TG_BTN_SHOP_MARKETPLACE
    from services.subscription_service import paid_subscription_for_referral_program

    if not await paid_subscription_for_referral_program(int(ref["id"])):
        return TG_BTN_SHOP_MARKETPLACE
    return TG_BTN_SHOP_SIMPLE


async def tg_shop_message_and_buttons(internal_user_id: int, site: str) -> tuple[str, list[list[Any]]]:
    """
    Текст и строки inline-кнопок для ответа на кнопку «Магазин».
    Сначала две ссылки (РФ/РБ и Европа/Америка), затем Web App; логика URL — как в shop_urls_for_user.
    """
    from telegram import InlineKeyboardButton, WebAppInfo

    ru, eu = await shop_urls_for_user(internal_user_id)

    app_url = site.rstrip("/") + "/app"
    rows: list[list[Any]] = [
        [InlineKeyboardButton(TG_BTN_SHOP_RU, url=ru)],
        [InlineKeyboardButton(TG_BTN_SHOP_EU, url=eu)],
        [
            InlineKeyboardButton(
                "🍄 Приложение соц сети",
                web_app=WebAppInfo(url=app_url),
            )
        ],
    ]
    return SHOP_FOOTER_HTML, rows
