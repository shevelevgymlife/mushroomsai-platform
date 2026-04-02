"""migrate_v35 — состояния по дням, каталог схем, эксперименты, bandit/RL-lite, кластеры, автоматизация, дозы."""

STEPS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS wellness_kmeans_cluster_id INTEGER",
    """
    CREATE TABLE IF NOT EXISTS wellness_scheme_catalog (
      id SERIAL PRIMARY KEY,
      scheme_key VARCHAR(64) NOT NULL UNIQUE,
      title TEXT NOT NULL,
      description TEXT,
      bundle_ids_json TEXT NOT NULL DEFAULT '[]',
      is_active BOOLEAN NOT NULL DEFAULT true,
      created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS wellness_user_state_daily (
      id SERIAL PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      state_date DATE NOT NULL,
      feature_vector_json TEXT NOT NULL DEFAULT '{}',
      discrete_state_label VARCHAR(160),
      kmeans_cluster_id INTEGER,
      source VARCHAR(32) NOT NULL DEFAULT 'snapshot',
      created_at TIMESTAMPTZ DEFAULT NOW(),
      UNIQUE(user_id, state_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_wellness_state_user_date ON wellness_user_state_daily (user_id, state_date DESC)",
    """
    CREATE TABLE IF NOT EXISTS wellness_experiments (
      id SERIAL PRIMARY KEY,
      experiment_key VARCHAR(64) NOT NULL UNIQUE,
      title TEXT NOT NULL,
      scheme_a_key VARCHAR(64) NOT NULL,
      scheme_b_key VARCHAR(64) NOT NULL,
      status VARCHAR(20) NOT NULL DEFAULT 'draft',
      started_at TIMESTAMPTZ,
      ended_at TIMESTAMPTZ,
      config_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS wellness_experiment_assignments (
      id SERIAL PRIMARY KEY,
      experiment_id INTEGER NOT NULL REFERENCES wellness_experiments(id) ON DELETE CASCADE,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      arm CHAR(1) NOT NULL,
      assigned_at TIMESTAMPTZ DEFAULT NOW(),
      UNIQUE(experiment_id, user_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_wellness_exp_assign_user ON wellness_experiment_assignments (user_id)",
    """
    CREATE TABLE IF NOT EXISTS wellness_bundle_feedback (
      id SERIAL PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      bundle_id VARCHAR(64) NOT NULL,
      vote SMALLINT NOT NULL,
      source VARCHAR(32) NOT NULL DEFAULT 'dm_command',
      direct_message_id INTEGER REFERENCES direct_messages(id) ON DELETE SET NULL,
      created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_wellness_bundle_fb_user ON wellness_bundle_feedback (user_id, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS wellness_rec_arm_stats (
      id SERIAL PRIMARY KEY,
      bundle_key VARCHAR(64) NOT NULL,
      segment VARCHAR(80) NOT NULL DEFAULT '',
      successes INTEGER NOT NULL DEFAULT 0,
      trials INTEGER NOT NULL DEFAULT 0,
      updated_at TIMESTAMPTZ DEFAULT NOW(),
      UNIQUE(bundle_key, segment)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS wellness_cluster_models (
      id SERIAL PRIMARY KEY,
      k INTEGER NOT NULL,
      model_version INTEGER NOT NULL DEFAULT 1,
      centroids_json TEXT NOT NULL,
      user_count INTEGER,
      trained_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS wellness_user_automation (
      user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
      active_scheme_key VARCHAR(64),
      auto_switched_at TIMESTAMPTZ,
      early_warning_level INTEGER NOT NULL DEFAULT 0,
      early_warning_signals_json TEXT,
      retention_risk VARCHAR(24) NOT NULL DEFAULT 'unknown',
      updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS wellness_mushroom_dose_rules (
      id SERIAL PRIMARY KEY,
      mushroom_key VARCHAR(64) NOT NULL,
      form VARCHAR(32) NOT NULL DEFAULT 'general',
      dose_text_ru TEXT NOT NULL,
      dose_min_mg DOUBLE PRECISION,
      dose_max_mg DOUBLE PRECISION,
      course_weeks_hint VARCHAR(80),
      cautions_ru TEXT,
      sort_order INTEGER NOT NULL DEFAULT 0,
      UNIQUE(mushroom_key, form)
    )
    """,
]
