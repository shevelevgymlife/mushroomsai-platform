"""migrate_v27 — самоприсвоение реферальной ссылки магазина пользователем (/referral)."""

STEPS = [
    """
    ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_shop_partner_self BOOLEAN NOT NULL DEFAULT false
    """,
]
