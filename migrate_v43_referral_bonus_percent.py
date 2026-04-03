"""Процент реферального бонуса: глобальный (site_settings) и override у пользователя."""

STEPS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_bonus_percent_override NUMERIC(5,2)",
    "INSERT INTO site_settings (key, value, updated_at) VALUES ('referral_bonus_percent_global', '10', NOW()) ON CONFLICT (key) DO NOTHING",
]
