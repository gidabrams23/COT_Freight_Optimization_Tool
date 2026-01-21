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
            CREATE TABLE IF NOT EXISTS order_lines (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                qty INTEGER NOT NULL,
                feet_per_unit REAL NOT NULL,
                due_date TEXT,
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


def validate_order_line(qty, feet_per_unit):
    errors = {}
    if qty <= 0:
        errors["qty"] = "Quantity must be greater than 0."
    if feet_per_unit <= 0:
        errors["feet_per_unit"] = "Feet per unit must be greater than 0."
    return errors


def list_order_lines():
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT order_lines.id,
                   order_lines.customer_id,
                   order_lines.qty,
                   order_lines.feet_per_unit,
                   order_lines.due_date,
                   order_lines.notes,
                   order_lines.created_at,
                   customers.name AS customer_name
            FROM order_lines
            JOIN customers ON customers.id = order_lines.customer_id
            ORDER BY order_lines.id DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def add_order_line(customer_id, qty, feet_per_unit, due_date, notes):
    errors = validate_order_line(qty, feet_per_unit)
    if errors:
        raise ValueError("Invalid order line values.")

    created_at = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO order_lines (
                customer_id,
                qty,
                feet_per_unit,
                due_date,
                notes,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (customer_id, qty, feet_per_unit, due_date, notes, created_at),
        )
        connection.commit()


def delete_order_line(order_line_id):
    with get_connection() as connection:
        connection.execute("DELETE FROM order_lines WHERE id = ?", (order_line_id,))
        connection.commit()


def clear_order_lines():
    with get_connection() as connection:
        connection.execute("DELETE FROM order_lines")
        connection.commit()
