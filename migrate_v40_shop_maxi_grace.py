"""migrate_v40 — Грейс-окно витрины/ссылок магазина Макси после окончания подписки."""

STEPS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS maxi_perks_grace_until TIMESTAMP NULL",
]
