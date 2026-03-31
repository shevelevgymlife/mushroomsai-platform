"""migrate_v28 — Оплата в админке: can_payment, payment_webhook_dedup."""

STEPS = [
    "ALTER TABLE admin_permissions ADD COLUMN IF NOT EXISTS can_payment BOOLEAN NOT NULL DEFAULT false",
    """CREATE TABLE IF NOT EXISTS payment_webhook_dedup (
        id SERIAL PRIMARY KEY,
        provider VARCHAR(32) NOT NULL,
        external_id VARCHAR(128) NOT NULL,
        created_at TIMESTAMP DEFAULT NOW(),
        CONSTRAINT uq_payment_webhook_dedup UNIQUE (provider, external_id)
    )""",
]
