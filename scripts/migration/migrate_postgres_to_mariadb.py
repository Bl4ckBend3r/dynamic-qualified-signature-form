import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import json

load_dotenv()

POSTGRES_URL = "postgresql+psycopg://formularz_user:formularz_pass@127.0.0.1:5432/formularz_db"
MARIADB_URL = os.getenv("DATABASE_URL")

TABLES = [
    "users",
    "forms",
    "form_fields",
    "form_permissions",
    "logos",
    "mail_templates",
    "mail_footers",
    "mail_template_assets",
    "form_submissions",
    "submission_files",
    "email_logs",
    "submission_workflow_events",
    "submission_decisions",
]

def q(name: str) -> str:
    return f"`{name}`"

def normalize_value(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value

def main():
    if not MARIADB_URL:
        raise RuntimeError("Brak DATABASE_URL w .env")

    pg = create_engine(POSTGRES_URL)
    maria = create_engine(MARIADB_URL)

    with pg.connect() as pg_conn, maria.begin() as maria_conn:
        maria_conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))

        for table in TABLES:
            print(f"Przenoszę tabelę: {table}")

            rows = pg_conn.execute(text(f'SELECT * FROM "{table}"')).mappings().all()

            if not rows:
                print(f"  - brak danych")
                continue

            columns = list(rows[0].keys())

            maria_conn.execute(text(f"DELETE FROM {q(table)}"))

            col_sql = ", ".join(q(col) for col in columns)
            val_sql = ", ".join(f":{col}" for col in columns)

            stmt = text(f"INSERT INTO {q(table)} ({col_sql}) VALUES ({val_sql})")

            payload = [
                {key: normalize_value(value) for key, value in dict(row).items()}
                for row in rows
                ]
            maria_conn.execute(stmt, payload)

            print(f"  - przeniesiono: {len(rows)}")

        maria_conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))

    print("Migracja zakończona.")

if __name__ == "__main__":
    main()