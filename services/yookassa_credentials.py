"""
Учётные данные API ЮKassa для платежей на сайте и вебхука.

Можно задать тестовый/второй магазин через Environment (не трогая shopId в админке).
"""
from __future__ import annotations

from typing import Any

from config import settings


def override_yookassa_shop_active() -> bool:
    sid = (getattr(settings, "YOOKASSA_OVERRIDE_SHOP_ID", "") or "").strip()
    sec = (getattr(settings, "YOOKASSA_OVERRIDE_SECRET_KEY", "") or "").strip()
    return bool(sid and sec)


def resolve_yookassa_shop_credentials(provider_cfg: dict[str, Any] | None) -> tuple[str, str]:
    """
    (shop_id, secret_key) для create payment / GET payment / проверки вебхука.
    Если заданы оба override в env — они имеют приоритет над полями из БД.
    """
    if override_yookassa_shop_active():
        return (
            (getattr(settings, "YOOKASSA_OVERRIDE_SHOP_ID", "") or "").strip(),
            (getattr(settings, "YOOKASSA_OVERRIDE_SECRET_KEY", "") or "").strip(),
        )
    st = provider_cfg or {}
    return (st.get("shop_id") or "").strip(), (st.get("secret_key") or "").strip()
