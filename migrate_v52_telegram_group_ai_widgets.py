"""Виджет NeuroFungi AI в Telegram-группах: закреплённое сообщение с кнопками (вкл. только из админки)."""

STEPS = [
    """
    CREATE TABLE IF NOT EXISTS telegram_group_ai_widgets (
        id SERIAL PRIMARY KEY,
        chat_id BIGINT NOT NULL UNIQUE,
        chat_type VARCHAR(32) NOT NULL DEFAULT 'supergroup',
        chat_title TEXT,
        enabled BOOLEAN NOT NULL DEFAULT false,
        pinned_message_id INTEGER,
        referral_attribution_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        last_error TEXT,
        last_pin_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_tg_group_ai_widgets_enabled ON telegram_group_ai_widgets (enabled)",
]
