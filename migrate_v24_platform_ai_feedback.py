"""
migrate_v24 — пожелания NeuroFungi AI от пользователей (вопросы к платформе, ответ админа).
"""

STEPS = [
    """
    CREATE TABLE IF NOT EXISTS platform_ai_feedback (
      id SERIAL PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      user_role VARCHAR(20) NOT NULL DEFAULT 'user',
      raw_text TEXT NOT NULL,
      source VARCHAR(48),
      admin_reply TEXT,
      admin_reply_at TIMESTAMPTZ,
      created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_platform_ai_feedback_created
    ON platform_ai_feedback (created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_platform_ai_feedback_user
    ON platform_ai_feedback (user_id)
    """,
]
