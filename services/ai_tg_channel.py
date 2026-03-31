"""Дублирование постов и «мыслей» NeuroFungi AI в Telegram-канал (основной бот TELEGRAM_TOKEN)."""
from __future__ import annotations

import html
import logging
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

_MAX = 3900  # запас под подпись


def neurofungi_telegram_footer_html() -> str:
    site = (settings.SITE_URL or "https://mushroomsai.ru").rstrip("/")
    return (
        "\n\n—\n🍄 <b>NeuroFungi AI</b> · внедрена Евгением Шевелёвым\n"
        f'<a href="{html.escape(site, quote=True)}">Соцсеть NEUROFUNGI</a>'
    )


def _channel_chat_id() -> str:
    return (settings.NEUROFUNGI_AI_TG_CHANNEL or "").strip()


def telegram_channel_configured() -> bool:
    return bool(_channel_chat_id() and (settings.TELEGRAM_TOKEN or "").strip())


async def send_neurofungi_post_to_telegram_channel(
    *,
    title: Optional[str],
    body_plain: str,
) -> bool:
    """
    Отправка в канал от имени бота (бот должен быть админом канала).
    chat_id: @username или -100… из NEUROFUNGI_AI_TG_CHANNEL.
    """
    if not telegram_channel_configured():
        return False
    ch = _channel_chat_id()
    token = (settings.TELEGRAM_TOKEN or "").strip()
    raw = (body_plain or "").strip()
    if len(raw) < 2:
        return False
    tit = (title or "").strip()
    body_h = html.escape(raw[:_MAX])
    if tit:
        msg = f"<b>{html.escape(tit[:200])}</b>\n\n{body_h}"
    else:
        msg = body_h
    msg += neurofungi_telegram_footer_html()
    if len(msg) > 4096:
        msg = msg[:4090] + "…"
    payload = {
        "chat_id": ch if ch.startswith("@") or ch.startswith("-") else f"@{ch}",
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=payload,
            )
            if r.status_code == 200:
                return True
            logger.warning(
                "ai_tg_channel sendMessage: %s %s %s",
                r.status_code,
                r.text[:400],
                ch,
            )
    except Exception as e:
        logger.warning("ai_tg_channel sendMessage exception: %s", e)
    return False


async def send_neurofungi_thought_to_telegram_channel(thought_plain: str) -> bool:
    """Короткое сообщение «мысль» в канал."""
    if not telegram_channel_configured():
        return False
    t = (thought_plain or "").strip()
    if len(t) < 3:
        return False
    msg = "💭 <b>Мысль NeuroFungi AI</b>\n\n" + html.escape(t[:3500])
    msg += neurofungi_telegram_footer_html()
    if len(msg) > 4096:
        msg = msg[:4090] + "…"
    token = (settings.TELEGRAM_TOKEN or "").strip()
    ch = _channel_chat_id()
    payload = {
        "chat_id": ch if ch.startswith("@") or ch.startswith("-") else f"@{ch}",
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=payload,
            )
            if r.status_code == 200:
                return True
            logger.warning("ai_tg_channel thought: %s %s", r.status_code, r.text[:300])
    except Exception as e:
        logger.warning("ai_tg_channel thought exception: %s", e)
    return False
