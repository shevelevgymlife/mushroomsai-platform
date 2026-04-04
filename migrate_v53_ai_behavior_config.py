"""Поведение AI по сценариям: глобальные и пользовательские профили, обратная связь админа."""

STEPS = [
    """
    CREATE TABLE IF NOT EXISTS ai_behavior_global (
        aspect_key VARCHAR(64) PRIMARY KEY,
        config_json TEXT NOT NULL DEFAULT '{}',
        updated_at TIMESTAMP DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_behavior_user_overrides (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        aspect_key VARCHAR(64) NOT NULL,
        config_json TEXT NOT NULL DEFAULT '{}',
        updated_at TIMESTAMP DEFAULT NOW(),
        PRIMARY KEY (user_id, aspect_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_ai_behavior_user_overrides_user ON ai_behavior_user_overrides (user_id)",
    """
    CREATE TABLE IF NOT EXISTS ai_behavior_admin_feedback (
        id SERIAL PRIMARY KEY,
        aspect_key VARCHAR(64) NOT NULL,
        question TEXT NOT NULL,
        answer TEXT NOT NULL,
        liked BOOLEAN NOT NULL,
        admin_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_ai_behavior_feedback_aspect ON ai_behavior_admin_feedback (aspect_key, created_at DESC)",
]
