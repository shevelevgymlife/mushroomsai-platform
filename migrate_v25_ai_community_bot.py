"""migrate_v25 — настройки AI-аккаунта в сообществе (посты, комментарии, подписки, лимиты)."""

STEPS = [
    """
    CREATE TABLE IF NOT EXISTS ai_community_bot_settings (
      id INTEGER PRIMARY KEY CHECK (id = 1),
      user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
      master_enabled BOOLEAN NOT NULL DEFAULT true,
      allow_posts BOOLEAN NOT NULL DEFAULT true,
      allow_comments BOOLEAN NOT NULL DEFAULT true,
      allow_follow BOOLEAN NOT NULL DEFAULT true,
      allow_unfollow BOOLEAN NOT NULL DEFAULT true,
      allow_reply_to_comments BOOLEAN NOT NULL DEFAULT true,
      allow_profile_thoughts BOOLEAN NOT NULL DEFAULT true,
      allow_photos BOOLEAN NOT NULL DEFAULT false,
      allow_story_posts BOOLEAN NOT NULL DEFAULT true,
      allow_bug_reports BOOLEAN NOT NULL DEFAULT true,
      limit_posts_per_day INTEGER NOT NULL DEFAULT 5,
      limit_comments_per_day INTEGER NOT NULL DEFAULT 30,
      limit_follows_per_day INTEGER NOT NULL DEFAULT 15,
      limit_unfollows_per_day INTEGER NOT NULL DEFAULT 10,
      limit_thoughts_per_day INTEGER NOT NULL DEFAULT 15,
      limit_reply_comments_per_day INTEGER NOT NULL DEFAULT 25,
      last_tick_at TIMESTAMPTZ,
      updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    INSERT INTO ai_community_bot_settings (id) VALUES (1)
    ON CONFLICT (id) DO NOTHING
    """,
    """
    ALTER TABLE ai_community_bot_settings ADD COLUMN IF NOT EXISTS thoughts_count_date DATE
    """,
    """
    ALTER TABLE ai_community_bot_settings ADD COLUMN IF NOT EXISTS thoughts_count_today INTEGER DEFAULT 0
    """,
]
