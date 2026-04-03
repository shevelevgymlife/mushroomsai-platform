"""Настройки публичных свайп-карточек профиля (крипто + соцсети)."""
from __future__ import annotations

import json
from typing import Any

SOCIAL_KEYS = ("telegram", "youtube", "instagram", "vk", "tiktok", "website")

# Порядок свайп-слайдов в карусели профиля (ключи)
SLIDE_KEYS: tuple[str, ...] = ("about", "crypto", "social")

DEFAULT_VALIDATOR_IMAGES = (
    "/static/img/validators/nf-val-1.svg",
    "/static/img/validators/nf-val-2.svg",
)


def default_profile_public_cards() -> dict[str, Any]:
    return {
        "show_crypto_slide": True,
        "show_social_slide": True,
        "slide_order": list(SLIDE_KEYS),
        "social": {k: "" for k in SOCIAL_KEYS},
        "validators": [
            {"image": DEFAULT_VALIDATOR_IMAGES[0], "label": "Валидатор 1", "url": ""},
            {"image": DEFAULT_VALIDATOR_IMAGES[1], "label": "Валидатор 2", "url": ""},
        ],
    }


def normalize_slide_order(raw: Any) -> list[str]:
    """Строгий порядок: каждый из about/crypto/social ровно один раз."""
    default = list(SLIDE_KEYS)
    if raw is None:
        return default
    if isinstance(raw, str):
        parts = [p.strip().lower() for p in raw.replace(" ", "").split(",") if p.strip()]
    elif isinstance(raw, list):
        parts = [str(p).strip().lower() for p in raw if str(p).strip()]
    else:
        return default
    allowed = set(SLIDE_KEYS)
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p in allowed and p not in seen:
            out.append(p)
            seen.add(p)
    for k in SLIDE_KEYS:
        if k not in seen:
            out.append(k)
            seen.add(k)
    return out


def merge_profile_public_cards(raw: str | None) -> dict[str, Any]:
    base = default_profile_public_cards()
    if not raw or not str(raw).strip():
        return base
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return base
    if not isinstance(data, dict):
        return base
    if "show_crypto_slide" in data:
        base["show_crypto_slide"] = bool(data["show_crypto_slide"])
    if "show_social_slide" in data:
        base["show_social_slide"] = bool(data["show_social_slide"])
    if "slide_order" in data:
        base["slide_order"] = normalize_slide_order(data.get("slide_order"))
    soc = data.get("social")
    if isinstance(soc, dict):
        for k in SOCIAL_KEYS:
            v = soc.get(k)
            base["social"][k] = (str(v).strip()[:2000] if v is not None else "") or ""
    vals = data.get("validators")
    if isinstance(vals, list):
        merged = []
        for i, item in enumerate(vals[:2]):
            if not isinstance(item, dict):
                continue
            img = (str(item.get("image") or "").strip()[:2000] or DEFAULT_VALIDATOR_IMAGES[i])
            merged.append(
                {
                    "image": img,
                    "label": (str(item.get("label") or f"Валидатор {i + 1}").strip()[:120]),
                    "url": (str(item.get("url") or "").strip()[:2000]),
                }
            )
        while len(merged) < 2:
            merged.append(
                {
                    "image": DEFAULT_VALIDATOR_IMAGES[len(merged)],
                    "label": f"Валидатор {len(merged) + 1}",
                    "url": "",
                }
            )
        base["validators"] = merged[:2]
    return base


def dumps_profile_public_cards(data: dict[str, Any]) -> str:
    clean = default_profile_public_cards()
    clean["show_crypto_slide"] = bool(data.get("show_crypto_slide"))
    clean["show_social_slide"] = bool(data.get("show_social_slide"))
    clean["slide_order"] = normalize_slide_order(data.get("slide_order"))
    for k in SOCIAL_KEYS:
        clean["social"][k] = (str(data.get("social", {}).get(k, "") or "").strip()[:2000]) or ""
    vals = data.get("validators") or []
    out_v = []
    for i in range(2):
        item = vals[i] if i < len(vals) and isinstance(vals[i], dict) else {}
        img = (str(item.get("image") or "").strip()[:2000] or DEFAULT_VALIDATOR_IMAGES[i])
        out_v.append(
            {
                "image": img,
                "label": (str(item.get("label") or f"Валидатор {i + 1}").strip()[:120]),
                "url": (str(item.get("url") or "").strip()[:2000]),
            }
        )
    clean["validators"] = out_v
    return json.dumps(clean, ensure_ascii=False)


def profile_public_cards_from_form(
    show_crypto: bool,
    show_social: bool,
    social_raw: dict[str, str],
    v0_img: str,
    v0_label: str,
    v0_url: str,
    v1_img: str,
    v1_label: str,
    v1_url: str,
    slide_order_csv: str = "",
) -> str:
    d = default_profile_public_cards()
    d["show_crypto_slide"] = show_crypto
    d["show_social_slide"] = show_social
    d["slide_order"] = normalize_slide_order((slide_order_csv or "").strip() or None)
    for k in SOCIAL_KEYS:
        raw = (social_raw.get(k, "") or "").strip()
        d["social"][k] = normalize_social_url(k, raw) if raw else ""
    d["validators"] = [
        {
            "image": (v0_img.strip()[:2000] if v0_img.strip() else DEFAULT_VALIDATOR_IMAGES[0]),
            "label": (v0_label.strip()[:120] if v0_label.strip() else "Валидатор 1"),
            "url": v0_url.strip()[:2000],
        },
        {
            "image": (v1_img.strip()[:2000] if v1_img.strip() else DEFAULT_VALIDATOR_IMAGES[1]),
            "label": (v1_label.strip()[:120] if v1_label.strip() else "Валидатор 2"),
            "url": v1_url.strip()[:2000],
        },
    ]
    return json.dumps(d, ensure_ascii=False)


def normalize_social_url(key: str, raw: str) -> str:
    u = (raw or "").strip()
    if not u:
        return ""
    if u.startswith(("http://", "https://", "tg://", "mailto:")):
        return u[:2000]
    if key == "telegram":
        if u.startswith("@"):
            u = u[1:]
        return f"https://t.me/{u}"[:2000]
    return f"https://{u.lstrip('/')}"[:2000]
