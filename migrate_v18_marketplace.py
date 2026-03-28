import asyncio
from db.database import database

async def run():
    await database.connect()
    sqls = [
        """CREATE TABLE IF NOT EXISTS marketplace_products (
            id SERIAL PRIMARY KEY,
            seller_id INTEGER REFERENCES users(id),
            title VARCHAR(500) NOT NULL,
            description TEXT,
            price DECIMAL(10,2),
            old_price DECIMAL(10,2),
            category VARCHAR(100),
            brand VARCHAR(100),
            in_stock BOOLEAN DEFAULT true,
            stock_count INTEGER DEFAULT 0,
            rating DECIMAL(3,2) DEFAULT 0,
            reviews_count INTEGER DEFAULT 0,
            views_count INTEGER DEFAULT 0,
            orders_count INTEGER DEFAULT 0,
            is_active BOOLEAN DEFAULT true,
            is_featured BOOLEAN DEFAULT false,
            attributes JSONB DEFAULT '{}',
            created_at TIMESTAMP DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS marketplace_photos (
            id SERIAL PRIMARY KEY,
            product_id INTEGER REFERENCES marketplace_products(id) ON DELETE CASCADE,
            url TEXT NOT NULL,
            position INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS marketplace_reviews (
            id SERIAL PRIMARY KEY,
            product_id INTEGER REFERENCES marketplace_products(id),
            user_id INTEGER REFERENCES users(id),
            rating INTEGER CHECK(rating BETWEEN 1 AND 5),
            text TEXT,
            pros TEXT,
            cons TEXT,
            photo_url TEXT,
            is_verified BOOLEAN DEFAULT false,
            helpful_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS marketplace_favorites (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            product_id INTEGER REFERENCES marketplace_products(id),
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, product_id)
        )""",
        """CREATE TABLE IF NOT EXISTS marketplace_cart (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            product_id INTEGER REFERENCES marketplace_products(id),
            quantity INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, product_id)
        )""",
        """CREATE TABLE IF NOT EXISTS marketplace_orders (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            total_price DECIMAL(10,2),
            status VARCHAR(50) DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS marketplace_order_items (
            id SERIAL PRIMARY KEY,
            order_id INTEGER REFERENCES marketplace_orders(id),
            product_id INTEGER REFERENCES marketplace_products(id),
            quantity INTEGER,
            price DECIMAL(10,2)
        )""",
        """CREATE TABLE IF NOT EXISTS marketplace_questions (
            id SERIAL PRIMARY KEY,
            product_id INTEGER REFERENCES marketplace_products(id),
            user_id INTEGER REFERENCES users(id),
            question TEXT NOT NULL,
            answer TEXT,
            answered_by INTEGER REFERENCES users(id),
            created_at TIMESTAMP DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS idx_mp_seller ON marketplace_products(seller_id)",
        "CREATE INDEX IF NOT EXISTS idx_mp_category ON marketplace_products(category)",
        "CREATE INDEX IF NOT EXISTS idx_mp_active ON marketplace_products(is_active)",
        "CREATE INDEX IF NOT EXISTS idx_mp_photos ON marketplace_photos(product_id)",
        "CREATE INDEX IF NOT EXISTS idx_mp_reviews ON marketplace_reviews(product_id)",
        "CREATE INDEX IF NOT EXISTS idx_mp_fav ON marketplace_favorites(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_mp_cart ON marketplace_cart(user_id)",
    ]
    for sql in sqls:
        try:
            await database.execute(sql)
        except Exception as e:
            print(f"Migration warning: {e}")
    await database.disconnect()

if __name__ == "__main__":
    asyncio.run(run())
