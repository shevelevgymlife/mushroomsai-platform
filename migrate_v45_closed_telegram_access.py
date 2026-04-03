"""v45: блок кабинета «Закрытый канал и чаты» для тарифов Про+."""

STEPS = [
    """
    INSERT INTO dashboard_blocks (block_key, block_name, position, is_visible, access_level)
    VALUES ('closed_telegram_access', 'Закрытый канал и чаты (Telegram)', 89, true, 'pro')
    ON CONFLICT (block_key) DO NOTHING
    """,
]
