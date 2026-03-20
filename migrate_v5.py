"""Migration v5: direct_messages, moderation tables, user ban columns"""
import psycopg2

DATABASE_URL = "postgresql://mushroomsai_db_user:vg9FRq2ix6FJwJnVPhApc9lTxVbRnk1r@dpg-d6t8dti4d50c73c54ceg-a.oregon-postgres.render.com/mushroomsai_db"

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

steps = [
    ("direct_messages table", """
        CREATE TABLE IF NOT EXISTS direct_messages (
            id SERIAL PRIMARY KEY,
            sender_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            recipient_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            text TEXT NOT NULL,
            is_read BOOLEAN DEFAULT false,
            is_system BOOLEAN DEFAULT false,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """),
    ("idx_dm_recipient", """
        CREATE INDEX IF NOT EXISTS idx_dm_recipient ON direct_messages(recipient_id, is_read)
    """),
    ("idx_dm_conversation", """
        CREATE INDEX IF NOT EXISTS idx_dm_conversation ON direct_messages(sender_id, recipient_id)
    """),
    ("users.violations_count", """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS violations_count INTEGER DEFAULT 0
    """),
    ("users.is_banned", """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN DEFAULT false
    """),
    ("users.ban_until", """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS ban_until TIMESTAMP NULL
    """),
    ("users.ban_reason", """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS ban_reason TEXT NULL
    """),
    ("moderation_log table", """
        CREATE TABLE IF NOT EXISTS moderation_log (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            content_type TEXT,
            content_text TEXT,
            reason TEXT,
            action_taken TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """),
    ("community_profiles table", """
        CREATE TABLE IF NOT EXISTS community_profiles (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) UNIQUE,
            display_name TEXT,
            bio TEXT,
            is_public BOOLEAN DEFAULT true,
            posts_count INTEGER DEFAULT 0,
            followers_count INTEGER DEFAULT 0,
            following_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """),
    ("profile_likes table", """
        CREATE TABLE IF NOT EXISTS profile_likes (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            liked_user_id INTEGER REFERENCES users(id),
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, liked_user_id)
        )
    """),
]

for name, sql in steps:
    try:
        cur.execute(sql)
        conn.commit()
        print(f"  {name}: OK")
    except Exception as e:
        print(f"  {name}: ERROR — {e}")
        conn.rollback()

cur.close()
conn.close()
print("Migration v5 complete!")
