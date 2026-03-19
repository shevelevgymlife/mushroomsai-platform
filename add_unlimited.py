"""One-time script: set subscription_plan='pro' for tg_id=162329668."""
import sqlalchemy

DATABASE_URL = "postgresql://mushroomsai_db_user:vg9FRq2ix6FJwJnVPhApc9lTxVbRnk1r@dpg-d6t8dti4d50c73c54ceg-a.oregon-postgres.render.com/mushroomsai_db"

engine = sqlalchemy.create_engine(DATABASE_URL)

with engine.begin() as conn:
    result = conn.execute(sqlalchemy.text(
        "UPDATE users SET subscription_plan='pro' WHERE tg_id=162329668 OR linked_tg_id=162329668"
    ))
    print(f"Updated {result.rowcount} row(s) to subscription_plan=pro")

    rows = conn.execute(sqlalchemy.text(
        "SELECT id, name, email, tg_id, linked_tg_id, subscription_plan FROM users WHERE tg_id=162329668 OR linked_tg_id=162329668"
    ))
    for row in rows:
        print(f"  id={row.id} name={row.name} tg_id={row.tg_id} subscription_plan={row.subscription_plan}")
