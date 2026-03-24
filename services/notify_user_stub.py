"""Заглушка: внешние push (Telegram) отключены."""


async def notify_user(tg_id: int, text: str) -> bool:
    return False
