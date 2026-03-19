import psycopg2
import os
import sys

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://mushroomsai_db_user:vg9FRq2ix6FJwJnVPhApc9lTxVbRnk1r@dpg-d6t8dti4d50c73c54ceg-a.oregon-postgres.render.com/mushroomsai_db"
)

migrations = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS linked_tg_id BIGINT;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS linked_google_id VARCHAR(128);",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS primary_user_id INTEGER REFERENCES users(id);",
    "ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS image_url TEXT;",
    """CREATE TABLE IF NOT EXISTS feedback (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id),
        message TEXT NOT NULL,
        status VARCHAR(20) DEFAULT 'new',
        created_at TIMESTAMP DEFAULT NOW()
    );""",
    """CREATE TABLE IF NOT EXISTS ai_settings (
        id SERIAL PRIMARY KEY,
        prompt_text TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT NOW(),
        updated_by BIGINT
    );""",
]

def run():
    print(f"Connecting to database...")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()

    for sql in migrations:
        short = sql.strip().splitlines()[0][:80]
        print(f"Running: {short}")
        cur.execute(sql)
        print("  OK")

    cur.close()
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    run()
