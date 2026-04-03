"""v49: внутренняя биржа бонус↔токен, пул ликвидности."""

STEPS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_balance NUMERIC(18,8) NOT NULL DEFAULT 0",
    """CREATE TABLE IF NOT EXISTS liquidity_pool (
      id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
      token_reserve NUMERIC(18,8) NOT NULL DEFAULT 0,
      bonus_reserve NUMERIC(18,8) NOT NULL DEFAULT 0,
      updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
    )""",
    """INSERT INTO liquidity_pool (id, token_reserve, bonus_reserve) VALUES (1, 100000, 25000)
       ON CONFLICT (id) DO NOTHING""",
    """CREATE TABLE IF NOT EXISTS exchange_trades (
      id SERIAL PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      type VARCHAR(20) NOT NULL,
      amount_bonus NUMERIC(18,8) NOT NULL,
      amount_token NUMERIC(18,8) NOT NULL,
      price NUMERIC(36,18) NOT NULL,
      created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_exchange_trades_user_created ON exchange_trades(user_id, created_at DESC)",
    """INSERT INTO dashboard_blocks (block_key, block_name, position, is_visible, access_level)
       VALUES ('internal_exchange', 'Биржа', 75, true, 'start')
       ON CONFLICT (block_key) DO NOTHING""",
]
