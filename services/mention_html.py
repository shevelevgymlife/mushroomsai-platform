"""Отображение упоминаний @id и URL в HTML (безопасно, с escaping)."""
from __future__ import annotations

import re

import markupsafe
from jinja2 import pass_context

# Синхронно с services/event_notify._MENTION_IDS
_MENTION_IDS = re.compile(r"(?<![\w/])@(\d{1,12})\b")
_URL_RE = re.compile(
    r"((?:https?://|www\.)[^\s<]+|(?<!@)\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}(?:/[^\s<]*)?)",
    re.IGNORECASE,
)


def _linkify_mentions_escaped(escaped_text: str) -> str:
    out: list[str] = []
    last = 0
    for m in _MENTION_IDS.finditer(escaped_text):
        out.append(escaped_text[last : m.start()])
        uid = m.group(1)
        out.append(
            f'<a class="nf-mention" href="/community/profile/{uid}" title="Профиль участника">@{uid}</a>'
        )
        last = m.end()
    out.append(escaped_text[last:])
    return "".join(out)


def _linkify_urls_and_mentions_html(escaped_text: str, links_enabled: bool) -> str:
    if not links_enabled:
        # Полностью отключаем кликабельность: ни URL, ни @mentions.
        return str(escaped_text)

    out: list[str] = []
    last = 0
    for m in _URL_RE.finditer(escaped_text):
        out.append(_linkify_mentions_escaped(escaped_text[last : m.start()]))
        url = m.group(1)
        href = url if re.match(r"^https?://", url, re.IGNORECASE) else f"https://{url}"
        out.append(
            '<a class="nf-chat-url" href="'
            + href
            + '" target="_blank" rel="noopener noreferrer">'
            + url
            + "</a>"
        )
        last = m.end()
    out.append(_linkify_mentions_escaped(escaped_text[last:]))
    return "".join(out)


def linkify_mentions_html(
    text: str | None, max_chars: int | None = None, links_enabled: bool = True
) -> markupsafe.Markup:
    """Экранирует текст + @123; при links_enabled также URL/домены делаються кликабельными."""
    if text is None:
        return markupsafe.Markup("")
    s = str(text)
    if max_chars is not None and max_chars > 0 and len(s) > max_chars:
        s = s[:max_chars] + "…"
    esc = str(markupsafe.escape(s))
    return markupsafe.Markup(_linkify_urls_and_mentions_html(esc, links_enabled))


@pass_context
def jinja_linkify_mentions(context, value, max_chars=None):
    """Фильтр Jinja: {{ text | linkify_mentions }} или {{ text | linkify_mentions(500) }}."""
    try:
        mc = int(max_chars) if max_chars is not None and str(max_chars).strip() != "" else None
    except (TypeError, ValueError):
        mc = None
    links_enabled = bool(context.get("links_clickable_enabled", True))
    return linkify_mentions_html(value, mc, links_enabled=links_enabled)
