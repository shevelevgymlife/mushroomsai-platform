"""v47: бонусы — автопродление, журнал операций (внутренние чеки)."""

STEPS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_bonus_auto_renew BOOLEAN NOT NULL DEFAULT false",
    """CREATE TABLE IF NOT EXISTS referral_balance_ledger (
      id SERIAL PRIMARY KEY,
      created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
      correlation_id VARCHAR(64),
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      counterparty_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
      amount_delta NUMERIC(12,2) NOT NULL,
      balance_after NUMERIC(12,2),
      kind VARCHAR(48) NOT NULL,
      detail_text TEXT,
      admin_actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
      plan_key VARCHAR(24)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_referral_balance_ledger_user ON referral_balance_ledger(user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_referral_balance_ledger_corr ON referral_balance_ledger(correlation_id)",
]
