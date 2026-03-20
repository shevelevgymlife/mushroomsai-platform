"""Migration v6: ai_training_posts table"""
import psycopg2

DATABASE_URL = "postgresql://mushroomsai_db_user:vg9FRq2ix6FJwJnVPhApc9lTxVbRnk1r@dpg-d6t8dti4d50c73c54ceg-a.oregon-postgres.render.com/mushroomsai_db"

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

steps = [
    ("ai_training_posts table", """
        CREATE TABLE IF NOT EXISTS ai_training_posts (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            category TEXT,
            is_active BOOLEAN DEFAULT true,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """),
    ("idx_ai_training_posts_active", """
        CREATE INDEX IF NOT EXISTS idx_ai_training_posts_active ON ai_training_posts(is_active)
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
print("Migration v6 complete!")
