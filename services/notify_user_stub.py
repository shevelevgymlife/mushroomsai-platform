"""Уведомления пользователю в Telegram через Bot API (если задан TELEGRAM_TOKEN / NOTIFY_BOT_TOKEN)."""


async def notify_user(tg_id: int, text: str) -> bool:
    try:
        from services.tg_notify import notify_user_telegram

        return await notify_user_telegram(int(tg_id), text)
    except Exception:
        return False


async def notify_user_dm_with_read_button(
    tg_id: int, sender_name: str, text_preview: str, read_path: str
) -> bool:
    try:
        from services.tg_notify import notify_dm_read_button

        return await notify_dm_read_button(int(tg_id), sender_name, text_preview, read_path)
    except Exception:
        return False


async def notify_user_group_chat_button(
    tg_id: int,
    *,
    chat_title: str,
    open_path: str,
    is_mention: bool,
    is_reply: bool,
) -> bool:
    try:
        from services.tg_notify import notify_group_chat_button

        return await notify_group_chat_button(
            int(tg_id),
            chat_title=chat_title,
            open_path=open_path,
            is_mention=is_mention,
            is_reply=is_reply,
        )
    except Exception:
        return False
