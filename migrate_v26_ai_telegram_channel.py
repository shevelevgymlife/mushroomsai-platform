"""migrate_v26 — дублирование постов NeuroFungi AI в Telegram-канал (переключатель в админке)."""

STEPS = [
    """
    ALTER TABLE ai_community_bot_settings
    ADD COLUMN IF NOT EXISTS allow_telegram_channel BOOLEAN NOT NULL DEFAULT true
    """,
]
