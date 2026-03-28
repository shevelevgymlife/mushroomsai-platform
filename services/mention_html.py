"""Отображение упоминаний @<числовой user id> в HTML (как на сервере парсит event_notify)."""
from __future__ import annotations

import re

import markupsafe

# Синхронно с services/event_notify._MENTION_IDS
_MENTION_IDS = re.compile(r"(?<![\w/])@(\d{1,12})\b")


def linkify_mentions_html(text: str | None, max_chars: int | None = None) -> markupsafe.Markup:
    """Экранирует текст и превращает @123 в ссылку на профиль. При max_chars — усечь до превью (с …)."""
    if text is None:
        return markupsafe.Markup("")
    s = str(text)
    if max_chars is not None and max_chars > 0 and len(s) > max_chars:
        s = s[:max_chars] + "…"
    esc = str(markupsafe.escape(s))
    out: list[str] = []
    last = 0
    for m in _MENTION_IDS.finditer(esc):
        out.append(esc[last : m.start()])
        uid = m.group(1)
        out.append(
            f'<a class="nf-mention" href="/community/profile/{uid}" title="Профиль участника">@{uid}</a>'
        )
        last = m.end()
    out.append(esc[last:])
    return markupsafe.Markup("".join(out))


def jinja_linkify_mentions(value, max_chars=None):
    """Фильтр Jinja: {{ text | linkify_mentions }} или {{ text | linkify_mentions(500) }}."""
    try:
        mc = int(max_chars) if max_chars is not None and str(max_chars).strip() != "" else None
    except (TypeError, ValueError):
        mc = None
    return linkify_mentions_html(value, mc)
