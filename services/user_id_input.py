"""Нормализация user id из форм: префикс @, полноширинная @."""
from __future__ import annotations

import re


def normalize_form_user_id(raw: str | None) -> str:
    """Вернуть только цифры id или пустую строку."""
    s = (raw or "").strip().replace("\ufeff", "").replace("\uff20", "@")
    while s.startswith("@"):
        s = s[1:].strip()
    return s if s.isdigit() else ""


def parse_form_user_id_int(raw: str | None) -> int | None:
    s = normalize_form_user_id(raw)
    return int(s) if s else None


def parse_user_ids_bulk(text: str | None) -> list[int]:
    """Список id из строки: запятые, пробелы, префикс @ у каждого числа."""
    out: list[int] = []
    if not text:
        return out
    for part in re.split(r"[\s,;]+", (text or "").strip()):
        p = part.strip().replace("\uff20", "@")
        while p.startswith("@"):
            p = p[1:].strip()
        if p.isdigit():
            out.append(int(p))
    return list(dict.fromkeys(out))[:500]
