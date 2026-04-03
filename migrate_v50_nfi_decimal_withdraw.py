"""v50: адрес Decimal Wallet для вывода NFI и заявки token_withdraw_requests."""

STEPS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS decimal_nfi_wallet_address TEXT",
    """CREATE TABLE IF NOT EXISTS token_withdraw_requests (
      id SERIAL PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      amount_token NUMERIC(18,8) NOT NULL,
      to_address TEXT NOT NULL,
      status VARCHAR(20) NOT NULL DEFAULT 'pending',
      tx_hash TEXT,
      admin_note TEXT,
      created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
      processed_at TIMESTAMP WITHOUT TIME ZONE,
      processed_by_admin_id INTEGER REFERENCES users(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_token_withdraw_user_status ON token_withdraw_requests(user_id, status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_token_withdraw_pending ON token_withdraw_requests(status, created_at) WHERE status = 'pending'",
]
