"""Helper to send Telegram notifications from web routes."""
import httpx
from config import settings


async def notify_user(tg_id: int, text: str) -> bool:
    """Send a Telegram message to a user by tg_id. Returns True on success."""
    if not settings.TELEGRAM_ENABLED:
        return False
    if not tg_id or not settings.TELEGRAM_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(url, json={"chat_id": tg_id, "text": text, "parse_mode": "HTML"})
            return r.status_code == 200
    except Exception:
        return False
