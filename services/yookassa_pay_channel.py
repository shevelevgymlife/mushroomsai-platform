"""С какого канала пришёл редирект на оплату ЮKassa: браузер (веб-магазин) или Telegram / Mini App (второй магазин)."""
from __future__ import annotations

from fastapi import Request


def detect_yookassa_pay_channel(request: Request) -> str:
    """
    browser — оплата через магазин «веб» (payment_provider:yookassa).
    telegram_embedded — бот или Mini App (payment_provider:yookassa_bot, shopId+секрет).
    """
    qp = (request.query_params.get("pay_ctx") or "").strip().lower()
    if qp in ("tg", "telegram", "miniapp", "app", "tma", "bot"):
        return "telegram_embedded"
    if qp in ("web", "browser", "site"):
        return "browser"
    ref = (request.headers.get("referer") or "").lower()
    if "telegram.org" in ref or "t.me/" in ref:
        return "telegram_embedded"
    return "browser"
