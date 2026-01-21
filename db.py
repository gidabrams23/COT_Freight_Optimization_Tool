import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "app.db"


def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                zip TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.commit()


def list_customers():
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT id, name, zip, notes, created_at FROM customers ORDER BY id DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def add_customer(name, zip_code, notes):
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO customers (name, zip, notes, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (name, zip_code, notes, created_at),
        )
        connection.commit()


def delete_customer(customer_id):
    with get_connection() as connection:
        connection.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
        connection.commit()
