"""migrate_v22 — дневник фунготерапии: настройки у users, записи wellness_journal_entries, флаг платформы."""

STEPS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS wellness_journal_interval_days INTEGER DEFAULT 1",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS wellness_journal_opt_out BOOLEAN DEFAULT false",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS wellness_journal_admin_paused BOOLEAN DEFAULT false",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS wellness_last_prompt_at TIMESTAMPTZ",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS wellness_next_prompt_at TIMESTAMPTZ",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS wellness_baseline_json TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS wellness_weekly_digest_last_at TIMESTAMPTZ",
    """
    CREATE TABLE IF NOT EXISTS wellness_journal_entries (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role VARCHAR(24) NOT NULL,
        raw_text TEXT NOT NULL,
        extracted_json TEXT,
        direct_message_id INTEGER REFERENCES direct_messages(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_wellness_journal_user_created ON wellness_journal_entries (user_id, created_at DESC)",
    """
    INSERT INTO platform_settings (key, value) VALUES ('wellness_journal_globally_enabled', 'true')
    ON CONFLICT (key) DO NOTHING
    """,
]
