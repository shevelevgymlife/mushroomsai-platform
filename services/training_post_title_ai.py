"""Короткий заголовок обучающего поста по смыслу текста (OpenAI)."""
from __future__ import annotations

import logging
import re

from openai import AsyncOpenAI

from config import settings

logger = logging.getLogger(__name__)


def _fallback_title(text: str, max_len: int) -> str:
    t = (text or "").strip()
    if not t:
        return "Пост из Telegram"
    line = (t.split("\n", 1)[0] or "").strip() or t[:80]
    return line[:max_len] if len(line) > max_len else line


async def suggest_training_post_title(body: str, *, max_len: int = 72) -> str:
    text = (body or "").strip()
    if len(text) < 4:
        return _fallback_title(text, max_len)
    key = (settings.OPENAI_API_KEY or "").strip()
    if not key:
        return _fallback_title(text, max_len)
    try:
        client = AsyncOpenAI(api_key=key)
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Сформулируй один короткий заголовок на русском для базы знаний. "
                        "Без кавычек и без двоеточия в конце. Не более 8 слов. "
                        "Только заголовок, без пояснений."
                    ),
                },
                {"role": "user", "content": text[:12000]},
            ],
            max_tokens=80,
            temperature=0.35,
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^[\"«»']+|[\"«»']+$", "", raw)
        raw = raw.replace("\n", " ").strip()
        if not raw:
            return _fallback_title(text, max_len)
        return raw[:max_len]
    except Exception as e:
        logger.warning("suggest_training_post_title: %s", e)
        return _fallback_title(text, max_len)
