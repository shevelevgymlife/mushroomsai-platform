"""Каталог магазина: галерея фото, доп. URL из текстового поля формы."""
from __future__ import annotations

import json
from typing import Any


def product_gallery_urls(row: dict[str, Any]) -> list[str]:
    """Порядок: главное image_url, затем URL из image_urls_json (без дублей)."""
    out: list[str] = []
    main = (row.get("image_url") or "").strip()
    if main:
        out.append(main)
    raw = row.get("image_urls_json") or ""
    if not raw:
        return out
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(data, list):
            for u in data:
                s = str(u).strip()
                if s and s not in out:
                    out.append(s)
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return out


def extra_image_urls_from_text(text: str) -> str | None:
    """Строки текста → JSON-массив для image_urls_json; None если пусто."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return None
    return json.dumps(lines, ensure_ascii=False)


def extra_image_lines_from_json(raw: str | None) -> str:
    """Для textarea в админке: JSON-массив → строки."""
    if not raw:
        return ""
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return "\n".join(str(x).strip() for x in data if x and str(x).strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return ""
