"""Скачивание файлов Telegram Bot API (фото из канала → bytes)."""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


async def download_telegram_file_bytes(bot_token: str, file_id: str) -> bytes | None:
    token = (bot_token or "").strip()
    fid = (file_id or "").strip()
    if not token or not fid:
        return None
    url_base = f"https://api.telegram.org/bot{token}"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            gr = await client.get(f"{url_base}/getFile", params={"file_id": fid})
            if gr.status_code != 200:
                return None
            data = gr.json()
            if not data.get("ok"):
                return None
            fp = (data.get("result") or {}).get("file_path")
            if not fp:
                return None
            fr = await client.get(f"https://api.telegram.org/file/bot{token}/{fp}")
            if fr.status_code != 200:
                return None
            return fr.content
    except Exception as e:
        logger.warning("download_telegram_file_bytes: %s", e)
        return None
