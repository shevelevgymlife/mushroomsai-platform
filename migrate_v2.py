"""
Migration v2: adds follower/DM/save tables + new columns
"""
import psycopg2

DATABASE_URL = "postgresql://mushroomsai_db_user:vg9FRq2ix6FJwJnVPhApc9lTxVbRnk1r@dpg-d6t8dti4d50c73c54ceg-a.oregon-postgres.render.com/mushroomsai_db"

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

steps = [
    ("users.bio",              "ALTER TABLE users ADD COLUMN IF NOT EXISTS bio TEXT"),
    ("users.followers_count",  "ALTER TABLE users ADD COLUMN IF NOT EXISTS followers_count INTEGER DEFAULT 0"),
    ("users.following_count",  "ALTER TABLE users ADD COLUMN IF NOT EXISTS following_count INTEGER DEFAULT 0"),
    ("community_posts.tags",        "ALTER TABLE community_posts ADD COLUMN IF NOT EXISTS tags TEXT"),
    ("community_posts.saves_count", "ALTER TABLE community_posts ADD COLUMN IF NOT EXISTS saves_count INTEGER DEFAULT 0"),
    ("community_follows", """
        CREATE TABLE IF NOT EXISTS community_follows (
            id SERIAL PRIMARY KEY,
            follower_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            following_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(follower_id, following_id)
        )
    """),
    ("community_saved", """
        CREATE TABLE IF NOT EXISTS community_saved (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            post_id INTEGER REFERENCES community_posts(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, post_id)
        )
    """),
    ("community_messages", """
        CREATE TABLE IF NOT EXISTS community_messages (
            id SERIAL PRIMARY KEY,
            sender_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            recipient_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            text TEXT NOT NULL,
            is_read BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """),
]

for name, sql in steps:
    try:
        cur.execute(sql)
        print(f"  {name}: OK")
    except Exception as e:
        print(f"  {name}: ERROR — {e}")
        conn.rollback()

conn.commit()
cur.close()
conn.close()
print("Migration v2 complete!")
