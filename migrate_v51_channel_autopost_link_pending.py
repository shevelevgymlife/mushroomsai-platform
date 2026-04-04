"""v51: последний канал, куда бота добавили админом — для привязки из веб-кабинета."""

STEPS = [
    """CREATE TABLE IF NOT EXISTS channel_autopost_link_pending (
        telegram_user_id BIGINT PRIMARY KEY,
        channel_chat_id BIGINT NOT NULL,
        channel_title TEXT,
        channel_username VARCHAR(255),
        updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
    )""",
]
