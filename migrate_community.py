"""
Миграция данных сообщества.
1. Создаёт таблицы community_folders, community_posts, community_comments, community_likes
2. Добавляет столбец wallet_address в users
3. Мигрирует старые posts → community_posts
4. Пытается импортировать данные из amanita-backend.onrender.com (пропускает при ошибке)
"""
import psycopg2
import psycopg2.extras
import requests
import sys
from datetime import datetime

DATABASE_URL = "postgresql://mushroomsai_db_user:vg9FRq2ix6FJwJnVPhApc9lTxVbRnk1r@dpg-d6t8dti4d50c73c54ceg-a.oregon-postgres.render.com/mushroomsai_db"
AMANITA_API = "https://amanita-backend.onrender.com"

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = False
cur = conn.cursor()

print("Connected to DB")

# ── 1. Create tables ──────────────────────────────────────────────────────────

cur.execute("""
CREATE TABLE IF NOT EXISTS community_folders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
""")
print("community_folders: OK")

cur.execute("""
CREATE TABLE IF NOT EXISTS community_posts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    content TEXT NOT NULL,
    image_url TEXT,
    folder_id INTEGER REFERENCES community_folders(id) ON DELETE SET NULL,
    likes_count INTEGER NOT NULL DEFAULT 0,
    comments_count INTEGER NOT NULL DEFAULT 0,
    pinned BOOLEAN NOT NULL DEFAULT FALSE,
    approved BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);
""")
print("community_posts: OK")

cur.execute("""
CREATE TABLE IF NOT EXISTS community_comments (
    id SERIAL PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES community_posts(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
""")
print("community_comments: OK")

cur.execute("""
CREATE TABLE IF NOT EXISTS community_likes (
    id SERIAL PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES community_posts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE(post_id, user_id)
);
""")
print("community_likes: OK")

# ── 2. Add wallet_address to users ───────────────────────────────────────────

cur.execute("""
ALTER TABLE users ADD COLUMN IF NOT EXISTS wallet_address TEXT;
""")
print("users.wallet_address: OK")

# ── 3. Migrate old posts → community_posts ────────────────────────────────────

cur.execute("SELECT COUNT(*) FROM community_posts;")
existing_count = cur.fetchone()[0]
if existing_count == 0:
    cur.execute("""
    INSERT INTO community_posts (user_id, content, likes_count, approved, created_at)
    SELECT user_id, content, likes, approved, created_at FROM posts
    ON CONFLICT DO NOTHING;
    """)
    cur.execute("SELECT COUNT(*) FROM community_posts;")
    migrated = cur.fetchone()[0]
    print(f"Migrated {migrated} posts from old posts table")
else:
    print(f"community_posts already has {existing_count} rows, skipping migration from posts table")

# ── 4. Try to import from amanita API ────────────────────────────────────────

print("\nTrying amanita API...")

def fetch_json(path):
    try:
        r = requests.get(f"{AMANITA_API}{path}", timeout=15)
        if r.status_code == 200:
            return r.json()
        print(f"  {path} returned {r.status_code}, skipping")
        return None
    except Exception as e:
        print(f"  {path} failed: {e}, skipping")
        return None

api_users = fetch_json("/api/users")
api_posts = fetch_json("/api/posts")
api_feed = fetch_json("/api/feed")

if api_users:
    print(f"  Got {len(api_users)} users from amanita API")
    for au in api_users:
        wallet = au.get("wallet_address") or au.get("address") or au.get("walletAddress")
        name = au.get("name") or au.get("username") or wallet
        if not wallet:
            continue
        # Check if user with this wallet already exists
        cur.execute("SELECT id FROM users WHERE wallet_address = %s", (wallet,))
        row = cur.fetchone()
        if not row:
            cur.execute("""
                INSERT INTO users (name, wallet_address, role, subscription_plan)
                VALUES (%s, %s, 'user', 'free')
                ON CONFLICT DO NOTHING
                RETURNING id
            """, (name, wallet))
            result = cur.fetchone()
            if result:
                print(f"    Created user {name} ({wallet[:10]}...)")
else:
    print("  amanita API users unavailable, skipping")

imported_posts = 0
if api_posts or api_feed:
    posts_list = api_posts or api_feed or []
    if isinstance(posts_list, dict):
        posts_list = posts_list.get("posts") or posts_list.get("data") or []
    print(f"  Got {len(posts_list)} posts from amanita API")
    for ap in posts_list:
        content = ap.get("content") or ap.get("text") or ""
        if not content or len(content.strip()) < 2:
            continue
        image_url = ap.get("image_url") or ap.get("imageUrl") or ap.get("photo")
        created_at = ap.get("created_at") or ap.get("createdAt") or ap.get("timestamp")
        if created_at and isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except Exception:
                created_at = datetime.utcnow()
        else:
            created_at = datetime.utcnow()
        # Try to link to user by wallet
        user_id = None
        wallet = (ap.get("author") or {}).get("wallet_address") or (ap.get("author") or {}).get("address")
        if wallet:
            cur.execute("SELECT id FROM users WHERE wallet_address = %s", (wallet,))
            row = cur.fetchone()
            if row:
                user_id = row[0]
        cur.execute("""
            INSERT INTO community_posts (user_id, content, image_url, approved, created_at)
            VALUES (%s, %s, %s, TRUE, %s)
        """, (user_id, content.strip(), image_url, created_at))
        imported_posts += 1
    print(f"  Imported {imported_posts} posts from amanita API")
else:
    print("  amanita API posts unavailable, skipping")

conn.commit()
cur.close()
conn.close()

print("\nMigration complete!")
