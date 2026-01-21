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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER,
                origin TEXT NOT NULL,
                destination TEXT NOT NULL,
                miles INTEGER NOT NULL,
                rate_cents INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(customer_id) REFERENCES customers(id)
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


def list_orders():
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                orders.id,
                orders.customer_id,
                customers.name AS customer_name,
                orders.origin,
                orders.destination,
                orders.miles,
                orders.rate_cents,
                orders.created_at
            FROM orders
            LEFT JOIN customers ON customers.id = orders.customer_id
            ORDER BY orders.id DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def add_order(customer_id, origin, destination, miles, rate_cents):
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO orders (customer_id, origin, destination, miles, rate_cents, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (customer_id, origin, destination, miles, rate_cents, created_at),
        )
        connection.commit()


def delete_order(order_id):
    with get_connection() as connection:
        connection.execute("DELETE FROM orders WHERE id = ?", (order_id,))
        connection.commit()
