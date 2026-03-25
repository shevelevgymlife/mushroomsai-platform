"""Уведомления пользователю в Telegram через Bot API (если задан TELEGRAM_TOKEN / NOTIFY_BOT_TOKEN)."""


async def notify_user(tg_id: int, text: str) -> bool:
    try:
        from services.tg_notify import notify_user_telegram

        return await notify_user_telegram(int(tg_id), text)
    except Exception:
        return False
