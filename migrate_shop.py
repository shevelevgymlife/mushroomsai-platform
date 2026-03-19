"""Migration: add category/in_stock columns to shop_products, create product_reviews."""
import sqlalchemy

DATABASE_URL = "postgresql://mushroomsai_db_user:vg9FRq2ix6FJwJnVPhApc9lTxVbRnk1r@dpg-d6t8dti4d50c73c54ceg-a.oregon-postgres.render.com/mushroomsai_db"

engine = sqlalchemy.create_engine(DATABASE_URL)

with engine.begin() as conn:
    conn.execute(sqlalchemy.text(
        "ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS category TEXT"
    ))
    print("shop_products.category: OK")

    conn.execute(sqlalchemy.text(
        "ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS in_stock BOOLEAN DEFAULT true"
    ))
    print("shop_products.in_stock: OK")

    conn.execute(sqlalchemy.text("""
        CREATE TABLE IF NOT EXISTS product_reviews (
            id SERIAL PRIMARY KEY,
            product_id INTEGER REFERENCES shop_products(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            rating INTEGER CHECK (rating >= 1 AND rating <= 5),
            text TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """))
    print("product_reviews table: OK")

    count = conn.execute(sqlalchemy.text("SELECT COUNT(*) FROM product_reviews")).scalar()
    print(f"product_reviews rows: {count}")
