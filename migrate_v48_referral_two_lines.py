"""v48: две линии реферальных бонусов за подписки (проценты L1/L2, уровень в событиях)."""

STEPS = [
    "ALTER TABLE referral_bonus_events ADD COLUMN IF NOT EXISTS line_level SMALLINT NOT NULL DEFAULT 1",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_bonus_line1_override NUMERIC(5,2)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_bonus_line2_override NUMERIC(5,2)",
    """UPDATE users SET referral_bonus_line1_override = referral_bonus_percent_override,
         referral_bonus_line2_override = referral_bonus_percent_override
       WHERE referral_bonus_percent_override IS NOT NULL
         AND referral_bonus_line1_override IS NULL
         AND referral_bonus_line2_override IS NULL""",
    """INSERT INTO site_settings (key, value, updated_at)
       VALUES ('referral_bonus_line1_percent', '5', NOW())
       ON CONFLICT (key) DO NOTHING""",
    """INSERT INTO site_settings (key, value, updated_at)
       VALUES ('referral_bonus_line2_percent', '5', NOW())
       ON CONFLICT (key) DO NOTHING""",
]
