"""migrate_v21 — URL внешнего магазина у амбассадора (реферальная ссылка для приглашённых)."""

STEPS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_shop_url TEXT",
]
