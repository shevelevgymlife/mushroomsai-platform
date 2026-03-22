"""Migration v4: community_profiles table"""
import psycopg2

DATABASE_URL = "postgresql://mushroomsai_db_user:vg9FRq2ix6FJwJnVPhApc9lTxVbRnk1r@dpg-d6t8dti4d50c73c54ceg-a.oregon-postgres.render.com/mushroomsai_db"

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

steps = [
    ("community_profiles", """
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
print("Migration v4 complete!")
