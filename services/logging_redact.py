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
    """Снимает токен из строк лога (в т.ч. %s в message args у httpx)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            new_args: list[object] = []
            changed = False
            for a in record.args:
                if isinstance(a, str):
                    na = redact_telegram_bot_urls(a)
                    if na != a:
                        changed = True
                    new_args.append(na)
                else:
                    new_args.append(a)
            if changed:
                record.args = tuple(new_args)
        elif isinstance(record.msg, str):
            nm = redact_telegram_bot_urls(record.msg)
            if nm != record.msg:
                record.msg = nm
        return True


_installed = False


def install_telegram_token_redact_filter() -> None:
    """Вешает фильтр на логгеры HTTP-клиентов (один раз на процесс)."""
    global _installed
    if _installed:
        return
    _installed = True
    f = RedactTelegramBotTokenFilter()
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).addFilter(f)
