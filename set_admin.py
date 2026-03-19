"""One-time script: set role=admin for user with tg_id=742166400 or linked_tg_id=742166400."""
import sqlalchemy

DATABASE_URL = "postgresql://mushroomsai_db_user:vg9FRq2ix6FJwJnVPhApc9lTxVbRnk1r@dpg-d6t8dti4d50c73c54ceg-a.oregon-postgres.render.com/mushroomsai_db"

engine = sqlalchemy.create_engine(DATABASE_URL)

with engine.begin() as conn:
    result = conn.execute(sqlalchemy.text(
        "UPDATE users SET role='admin' WHERE tg_id=742166400 OR linked_tg_id=742166400"
    ))
    print(f"Updated {result.rowcount} row(s) to role=admin")

    rows = conn.execute(sqlalchemy.text(
        "SELECT id, email, tg_id, linked_tg_id, role FROM users WHERE tg_id=742166400 OR linked_tg_id=742166400"
    ))
    for row in rows:
        print(f"  id={row.id} email={row.email} tg_id={row.tg_id} linked_tg_id={row.linked_tg_id} role={row.role}")
