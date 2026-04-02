STEPS = [
    """
    CREATE TABLE IF NOT EXISTS referral_bonus_events (
      id SERIAL PRIMARY KEY,
      referral_id INTEGER NULL REFERENCES referrals(id) ON DELETE SET NULL,
      referrer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      referred_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      subscription_id INTEGER NULL REFERENCES subscriptions(id) ON DELETE SET NULL,
      plan_key VARCHAR(20) NOT NULL,
      paid_amount_rub NUMERIC(12,2) NOT NULL,
      bonus_rub NUMERIC(12,2) NOT NULL,
      payment_source VARCHAR(32) NULL,
      credited_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_ref_bonus_events_referrer_credited ON referral_bonus_events (referrer_id, credited_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_ref_bonus_events_referred_credited ON referral_bonus_events (referred_id, credited_at DESC)",
]

