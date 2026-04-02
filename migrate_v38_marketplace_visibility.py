"""
v38: visibility controls for seller marketplace products.
"""

STEPS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS marketplace_visibility_scope VARCHAR(20) NOT NULL DEFAULT 'all'",
    "ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS visibility_scope VARCHAR(20) NOT NULL DEFAULT 'all'",
    "UPDATE users SET marketplace_seller = true WHERE COALESCE(subscription_plan, 'free') = 'maxi'",
]

