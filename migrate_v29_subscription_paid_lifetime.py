"""migrate_v29 — Бессрочная оплаченная подписка из каталога (subscription_paid_lifetime)."""

STEPS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_paid_lifetime BOOLEAN NOT NULL DEFAULT false",
]
