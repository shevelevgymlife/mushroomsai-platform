"""Реферальные выплаты: реквизиты партнёра, месяц заявки, поле для чека в Telegram."""

STEPS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_tax_status VARCHAR(24)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_partner_inn VARCHAR(20)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_payout_bank_note TEXT",
    "ALTER TABLE referral_withdrawals ADD COLUMN IF NOT EXISTS withdraw_calendar_month VARCHAR(7)",
    "ALTER TABLE referral_withdrawals ADD COLUMN IF NOT EXISTS check_telegram_file_id TEXT",
    "ALTER TABLE referral_withdrawals ADD COLUMN IF NOT EXISTS check_received_at TIMESTAMP",
    "ALTER TABLE referral_withdrawals ALTER COLUMN status TYPE VARCHAR(32)",
]
