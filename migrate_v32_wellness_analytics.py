"""migrate_v32 — снимки метрик по дням, рекомендации AI, агрегаты схем (коллективный интеллект)."""

STEPS = [
    """
    CREATE TABLE IF NOT EXISTS wellness_daily_snapshots (
      id SERIAL PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      snapshot_date DATE NOT NULL,
      metrics_json TEXT NOT NULL DEFAULT '{}',
      source_wellness_entry_id INTEGER REFERENCES wellness_journal_entries(id) ON DELETE SET NULL,
      wellness_segment VARCHAR(80),
      created_at TIMESTAMPTZ DEFAULT NOW(),
      updated_at TIMESTAMPTZ DEFAULT NOW(),
      UNIQUE(user_id, snapshot_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_wellness_snap_user_date ON wellness_daily_snapshots (user_id, snapshot_date DESC)",
    """
    CREATE TABLE IF NOT EXISTS wellness_ai_recommendations (
      id SERIAL PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      rec_date DATE NOT NULL,
      body_text TEXT NOT NULL,
      created_at TIMESTAMPTZ DEFAULT NOW(),
      UNIQUE(user_id, rec_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_wellness_rec_user_date ON wellness_ai_recommendations (user_id, rec_date DESC)",
    """
    CREATE TABLE IF NOT EXISTS wellness_scheme_effect_stats (
      id SERIAL PRIMARY KEY,
      mushroom_key VARCHAR(160) NOT NULL,
      segment VARCHAR(80) NOT NULL DEFAULT '',
      sample_n INTEGER NOT NULL DEFAULT 0,
      avg_progress_score DOUBLE PRECISION,
      updated_at TIMESTAMPTZ DEFAULT NOW(),
      UNIQUE(mushroom_key, segment)
    )
    """,
]
