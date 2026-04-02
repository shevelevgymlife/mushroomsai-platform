"""migrate_v39 — Токен в URL возврата с ЮKassa (Mini App / WebView без cookie)."""

STEPS = [
    """CREATE TABLE IF NOT EXISTS yookassa_return_tokens (
        token VARCHAR(128) PRIMARY KEY,
        payment_id VARCHAR(128) NOT NULL,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMP DEFAULT NOW(),
        expires_at TIMESTAMP NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ix_yookassa_return_tokens_expires ON yookassa_return_tokens (expires_at)",
]
