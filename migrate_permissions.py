"""
Migration: create admin_permissions table and set subscription_plan='pro' for tg_id=162329668.
"""
import sqlalchemy

DATABASE_URL = "postgresql://mushroomsai_db_user:vg9FRq2ix6FJwJnVPhApc9lTxVbRnk1r@dpg-d6t8dti4d50c73c54ceg-a.oregon-postgres.render.com/mushroomsai_db"

engine = sqlalchemy.create_engine(DATABASE_URL)

with engine.begin() as conn:
    conn.execute(sqlalchemy.text("""
        CREATE TABLE IF NOT EXISTS admin_permissions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) UNIQUE,
            can_dashboard BOOLEAN DEFAULT false,
            can_ai BOOLEAN DEFAULT false,
            can_shop BOOLEAN DEFAULT false,
            can_users BOOLEAN DEFAULT false,
            can_feedback BOOLEAN DEFAULT false,
            can_broadcast BOOLEAN DEFAULT false,
            can_knowledge BOOLEAN DEFAULT false,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """))
    print("admin_permissions table: OK")

    result = conn.execute(sqlalchemy.text(
        "UPDATE users SET subscription_plan='pro' WHERE tg_id=162329668 OR linked_tg_id=162329668"
    ))
    print(f"Set pro plan for tg_id=162329668: {result.rowcount} row(s) updated")

    rows = conn.execute(sqlalchemy.text(
        "SELECT id, name, tg_id, subscription_plan, role FROM users WHERE tg_id=162329668 OR linked_tg_id=162329668"
    ))
    for row in rows:
        print(f"  id={row.id} name={row.name} tg_id={row.tg_id} plan={row.subscription_plan} role={row.role}")

    existing = conn.execute(sqlalchemy.text(
        "SELECT COUNT(*) FROM admin_permissions"
    )).scalar()
    print(f"admin_permissions rows: {existing}")
