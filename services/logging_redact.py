"""Маскирование секретов в логах (токены Telegram Bot API в URL httpx/httpcore)."""
from __future__ import annotations

import logging
import re

# https://api.telegram.org/bot<token>/method — токен: цифры:буквы/цифры/_
_TELEGRAM_BOT_API_URL = re.compile(
    r"(https?://api\.telegram\.org/bot)([\d]+:[A-Za-z0-9_-]+)(/.*)?",
    re.IGNORECASE,
)


def redact_telegram_bot_urls(text: str) -> str:
    if not text or "telegram.org" not in text:
        return text

    def _sub(m: re.Match[str]) -> str:
        tail = m.group(3) or ""
        return m.group(1) + "***" + tail

    return _TELEGRAM_BOT_API_URL.sub(_sub, text)


class RedactTelegramBotTokenFilter(logging.Filter):
    """
    Подменяет готовую строку лога после %/format — покрывает httpx и все httpx.* без отдельной регистрации.
    Вешается на root logger; для остальных имён — no-op.
    """

    _PREFIXES = ("httpx", "httpcore")

    @classmethod
    def _is_http_client_log(cls, name: str) -> bool:
        n = (name or "").lower()
        return any(n == p or n.startswith(p + ".") for p in cls._PREFIXES)

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._is_http_client_log(record.name):
            return True
        try:
            full = record.getMessage()
        except Exception:
            return True
        redacted = redact_telegram_bot_urls(full)
        if redacted == full:
            return True
        record.msg = redacted
        record.args = ()
        if hasattr(record, "message"):
            delattr(record, "message")
        return True


_installed = False


def install_telegram_token_redact_filter() -> None:
    """
    Фильтр на handler'ах root: записи от дочерних логгеров (httpx) не проходят через Logger root,
    но доходят до StreamHandler — там маскируем готовую строку.
    """
    global _installed
    if _installed:
        return
    _installed = True
    f = RedactTelegramBotTokenFilter()
    root = logging.getLogger()
    if root.handlers:
        for h in root.handlers:
            h.addFilter(f)
    else:
        root.addFilter(f)
