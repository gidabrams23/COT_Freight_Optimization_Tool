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
                order_reference TEXT,
                description TEXT,
                due_date TEXT,
                line_total_feet REAL NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS loads (
                id INTEGER PRIMARY KEY,
                capacity_feet REAL NOT NULL,
                total_feet REAL NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS load_lines (
                id INTEGER PRIMARY KEY,
                load_id INTEGER NOT NULL,
                order_line_id INTEGER NOT NULL,
                line_total_feet REAL NOT NULL,
                line_number INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(load_id) REFERENCES loads(id),
                FOREIGN KEY(order_line_id) REFERENCES order_lines(id)
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


def list_order_lines():
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id,
                   order_reference,
                   description,
                   due_date,
                   line_total_feet,
                   created_at
            FROM order_lines
            ORDER BY created_at ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def clear_loads():
    with get_connection() as connection:
        connection.execute("DELETE FROM load_lines")
        connection.execute("DELETE FROM loads")
        connection.commit()


def insert_loads(loads):
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as connection:
        for load in loads:
            cursor = connection.execute(
                """
                INSERT INTO loads (capacity_feet, total_feet, created_at)
                VALUES (?, ?, ?)
                """,
                (load["capacity_feet"], load["total_feet"], created_at),
            )
            load_id = cursor.lastrowid
            for line_number, line in enumerate(load["lines"], start=1):
                connection.execute(
                    """
                    INSERT INTO load_lines (
                        load_id,
                        order_line_id,
                        line_total_feet,
                        line_number,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        load_id,
                        line["id"],
                        line["line_total_feet"],
                        line_number,
                        created_at,
                    ),
                )
        connection.commit()


def list_loads_with_lines():
    with get_connection() as connection:
        load_rows = connection.execute(
            """
            SELECT id, capacity_feet, total_feet, created_at
            FROM loads
            ORDER BY id ASC
            """
        ).fetchall()
        loads = [
            {**dict(load_row), "lines": []}
            for load_row in load_rows
        ]
        if not loads:
            return loads
        load_map = {load["id"]: load for load in loads}
        line_rows = connection.execute(
            """
            SELECT load_lines.load_id,
                   load_lines.line_number,
                   load_lines.line_total_feet,
                   order_lines.order_reference,
                   order_lines.description,
                   order_lines.due_date
            FROM load_lines
            JOIN order_lines ON order_lines.id = load_lines.order_line_id
            ORDER BY load_lines.load_id ASC, load_lines.line_number ASC
            """
        ).fetchall()
        for row in line_rows:
            line = dict(row)
            load = load_map.get(line["load_id"])
            if load is not None:
                load["lines"].append(line)
        return loads
