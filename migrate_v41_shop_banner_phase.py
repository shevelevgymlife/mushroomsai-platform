"""migrate_v41 — Конец грейса Макси: отдельная фаза баннера до возврата стандартной витрины."""

STEPS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS maxi_shop_banner_until TIMESTAMP NULL",
]
