"""Migration v3: profile_likes table"""
import psycopg2

DATABASE_URL = "postgresql://mushroomsai_db_user:vg9FRq2ix6FJwJnVPhApc9lTxVbRnk1r@dpg-d6t8dti4d50c73c54ceg-a.oregon-postgres.render.com/mushroomsai_db"

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

steps = [
    ("profile_likes", """
        CREATE TABLE IF NOT EXISTS profile_likes (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            liked_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, liked_user_id)
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
print("Migration v3 complete!")
