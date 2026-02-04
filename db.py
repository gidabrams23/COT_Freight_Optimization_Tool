import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "app.db"

DEFAULT_PLANTS = {
    "GA": {"name": "Lavonia", "lat": 34.43611, "lng": -83.10639},
    "IA": {"name": "Missouri Valley", "lat": 41.55944, "lng": -95.90250},
    "TX": {"name": "Mexia", "lat": 31.66222, "lng": -96.49722},
    "VA": {"name": "Montross", "lat": 38.09389, "lng": -76.82611},
    "OR": {"name": "Coburg", "lat": 44.13944, "lng": -123.05889},
    "NV": {"name": "Winnemucca", "lat": 40.96833, "lng": -117.72667},
}

ORDER_LINES_COLUMNS = [
    "id",
    "due_date",
    "customer",
    "plant_full",
    "plant2",
    "plant",
    "item",
    "qty",
    "sales",
    "so_num",
    "cust_name",
    "cpo",
    "salesman",
    "cust_num",
    "bin",
    "load_num",
    "address1",
    "address2",
    "city",
    "state",
    "zip",
    "sku",
    "unit_length_ft",
    "total_length_ft",
    "max_stack_height",
    "stack_position",
    "utilization_pct",
    "is_excluded",
    "created_at",
]

ORDERS_COLUMNS = [
    "id",
    "so_num",
    "due_date",
    "plant",
    "customer",
    "cust_name",
    "state",
    "zip",
    "total_qty",
    "total_sales",
    "total_length_ft",
    "utilization_pct",
    "utilization_grade",
    "utilization_credit_ft",
    "exceeds_capacity",
    "line_count",
    "is_excluded",
    "created_at",
]


def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def _get_columns(connection, table_name):
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def _get_columns_info(connection, table_name):
    return connection.execute(f"PRAGMA table_info({table_name})").fetchall()


def _ensure_column(connection, table_name, column_name, ddl):
    columns = _get_columns(connection, table_name)
    if column_name not in columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")


def _rebuild_order_lines_if_needed(connection):
    columns = _get_columns(connection, "order_lines")
    if "customer_id" not in columns:
        return
    connection.execute("ALTER TABLE order_lines RENAME TO order_lines_old")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS order_lines (
            id INTEGER PRIMARY KEY,
            due_date TEXT NOT NULL,
            customer TEXT,
            plant_full TEXT,
            plant2 TEXT,
            plant TEXT NOT NULL,
            item TEXT NOT NULL,
            qty INTEGER NOT NULL,
            sales REAL,
            so_num TEXT,
            cust_name TEXT,
            cpo TEXT,
            salesman TEXT,
            cust_num TEXT,
            bin TEXT,
            load_num TEXT,
            address1 TEXT,
            address2 TEXT,
            city TEXT,
            state TEXT NOT NULL,
            zip TEXT NOT NULL,
            sku TEXT,
            unit_length_ft REAL,
            total_length_ft REAL,
            max_stack_height INTEGER,
            stack_position INTEGER DEFAULT 1,
            utilization_pct REAL,
            is_excluded INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    copy_columns = [col for col in ORDER_LINES_COLUMNS if col in columns]
    if copy_columns:
        column_list = ", ".join(copy_columns)
        connection.execute(
            f"INSERT INTO order_lines ({column_list}) SELECT {column_list} FROM order_lines_old"
        )
    connection.execute("DROP TABLE order_lines_old")


def _rebuild_orders_if_needed(connection):
    columns = _get_columns(connection, "orders")
    legacy_columns = {"customer_id", "origin", "destination", "miles", "rate_cents"}
    if not (columns & legacy_columns):
        return
    connection.execute("ALTER TABLE orders RENAME TO orders_old")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY,
            so_num TEXT NOT NULL,
            due_date TEXT NOT NULL,
            plant TEXT NOT NULL,
            customer TEXT,
            cust_name TEXT,
            state TEXT NOT NULL,
            zip TEXT NOT NULL,
            total_qty INTEGER NOT NULL,
            total_sales REAL,
            total_length_ft REAL NOT NULL,
            utilization_pct REAL,
            line_count INTEGER NOT NULL,
            is_excluded INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    copy_columns = [col for col in ORDERS_COLUMNS if col in columns]
    if copy_columns:
        column_list = ", ".join(copy_columns)
        connection.execute(
            f"INSERT INTO orders ({column_list}) SELECT {column_list} FROM orders_old"
        )
    connection.execute("DROP TABLE orders_old")


def _rebuild_loads_if_needed(connection):
    columns = _get_columns(connection, "loads")
    legacy_columns = {
        "origin",
        "destination",
        "miles",
        "rate_cents",
        "capacity_feet",
        "total_feet",
    }
    if not (columns & legacy_columns):
        return

    connection.execute("ALTER TABLE loads RENAME TO loads_old")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS loads (
            id INTEGER PRIMARY KEY,
            load_number TEXT,
            draft_sequence INTEGER,
            origin_plant TEXT NOT NULL,
            destination_state TEXT NOT NULL,
            estimated_miles REAL,
            rate_per_mile REAL,
            estimated_cost REAL,
            standalone_cost REAL,
            consolidation_savings REAL,
            fragility_score REAL,
            status TEXT DEFAULT 'PROPOSED',
            trailer_type TEXT DEFAULT 'STEP_DECK',
            utilization_pct REAL DEFAULT 0.0,
            optimization_score REAL DEFAULT 0.0,
            build_source TEXT DEFAULT 'OPTIMIZED',
            created_by TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    old_columns = _get_columns(connection, "loads_old")
    select_columns = ", ".join(old_columns)
    rows = connection.execute(f"SELECT {select_columns} FROM loads_old").fetchall()

    migrated_rows = []
    for row in rows:
        origin_plant = ""
        if "origin_plant" in old_columns and row["origin_plant"]:
            origin_plant = row["origin_plant"]
        elif "origin" in old_columns and row["origin"]:
            origin_plant = row["origin"]

        destination_state = ""
        if "destination_state" in old_columns and row["destination_state"]:
            destination_state = row["destination_state"]
        elif "destination" in old_columns and row["destination"]:
            destination_state = row["destination"]

        estimated_miles = None
        if "estimated_miles" in old_columns and row["estimated_miles"] is not None:
            estimated_miles = row["estimated_miles"]
        elif "miles" in old_columns and row["miles"] is not None:
            estimated_miles = row["miles"]

        rate_per_mile = None
        if "rate_per_mile" in old_columns and row["rate_per_mile"] is not None:
            rate_per_mile = row["rate_per_mile"]
        elif "rate_cents" in old_columns and row["rate_cents"] is not None:
            rate_per_mile = row["rate_cents"] / 100.0

        estimated_cost = (
            row["estimated_cost"] if "estimated_cost" in old_columns else None
        )
        standalone_cost = None
        consolidation_savings = None
        fragility_score = None
        status = row["status"] if "status" in old_columns and row["status"] else "PROPOSED"
        trailer_type = (
            row["trailer_type"]
            if "trailer_type" in old_columns and row["trailer_type"]
            else "STEP_DECK"
        )
        utilization_pct = (
            row["utilization_pct"] if "utilization_pct" in old_columns else 0.0
        )
        optimization_score = (
            row["optimization_score"] if "optimization_score" in old_columns else 0.0
        )
        build_source = (
            row["build_source"]
            if "build_source" in old_columns and row["build_source"]
            else "OPTIMIZED"
        )
        created_by = row["created_by"] if "created_by" in old_columns else None
        created_at = (
            row["created_at"]
            if "created_at" in old_columns and row["created_at"]
            else datetime.utcnow().isoformat(timespec="seconds")
        )

        migrated_rows.append(
            (
                row["id"] if "id" in old_columns else None,
                row["load_number"] if "load_number" in old_columns else None,
                row["draft_sequence"] if "draft_sequence" in old_columns else None,
                origin_plant,
                destination_state,
                estimated_miles,
                rate_per_mile,
                estimated_cost,
                standalone_cost,
                consolidation_savings,
                fragility_score,
                status,
                trailer_type,
                utilization_pct,
                optimization_score,
                build_source,
                created_by,
                created_at,
            )
        )

    if migrated_rows:
        connection.executemany(
            """
            INSERT INTO loads (
                id,
                load_number,
                draft_sequence,
                origin_plant,
                destination_state,
                estimated_miles,
                rate_per_mile,
                estimated_cost,
                standalone_cost,
                consolidation_savings,
                fragility_score,
                status,
                trailer_type,
                utilization_pct,
                optimization_score,
                build_source,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            migrated_rows,
        )

    connection.execute("DROP TABLE loads_old")


def _rebuild_load_feedback_if_needed(connection):
    columns_info = _get_columns_info(connection, "load_feedback")
    if not columns_info:
        return
    column_names = {column["name"] for column in columns_info}
    required = {"action_type", "reason_category", "details", "planner_id"}
    needs_rebuild = not required.issubset(column_names)
    order_column = next(
        (column for column in columns_info if column["name"] == "order_id"), None
    )
    if order_column and order_column["notnull"]:
        needs_rebuild = True
    if not needs_rebuild:
        return

    connection.execute("ALTER TABLE load_feedback RENAME TO load_feedback_old")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS load_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            load_id INTEGER NOT NULL,
            order_id TEXT,
            action_type TEXT NOT NULL,
            reason_category TEXT NOT NULL,
            details TEXT NOT NULL,
            planner_id TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (load_id) REFERENCES loads(id)
        )
        """
    )

    old_rows = connection.execute("SELECT * FROM load_feedback_old").fetchall()
    migrated = []
    for row in old_rows:
        row_dict = dict(row)
        action_type = row_dict.get("action_type") or (
            "order_removed" if row_dict.get("order_id") else "load_rejected"
        )
        reason_category = row_dict.get("reason_category") or row_dict.get("reasons") or "Other"
        details = row_dict.get("details")
        if details is None:
            details = row_dict.get("notes") or ""
        migrated.append(
            (
                row_dict.get("load_id"),
                row_dict.get("order_id"),
                action_type,
                reason_category,
                details,
                row_dict.get("planner_id"),
                row_dict.get("created_at"),
            )
        )

    if migrated:
        connection.executemany(
            """
            INSERT INTO load_feedback (
                load_id, order_id, action_type, reason_category, details, planner_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            migrated,
        )

    connection.execute("DROP TABLE load_feedback_old")


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
                due_date TEXT NOT NULL,
                customer TEXT,
                plant_full TEXT,
                plant2 TEXT,
                plant TEXT NOT NULL,
                item TEXT NOT NULL,
                qty INTEGER NOT NULL,
                sales REAL,
                so_num TEXT,
                cust_name TEXT,
                cpo TEXT,
                salesman TEXT,
                cust_num TEXT,
                bin TEXT,
                load_num TEXT,
                address1 TEXT,
                address2 TEXT,
                city TEXT,
                state TEXT NOT NULL,
                zip TEXT NOT NULL,
                sku TEXT,
                unit_length_ft REAL,
                total_length_ft REAL,
                max_stack_height INTEGER,
                stack_position INTEGER DEFAULT 1,
                utilization_pct REAL,
                is_excluded INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        _rebuild_order_lines_if_needed(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY,
                so_num TEXT NOT NULL,
                due_date TEXT NOT NULL,
                plant TEXT NOT NULL,
                customer TEXT,
                cust_name TEXT,
                state TEXT NOT NULL,
                zip TEXT NOT NULL,
                total_qty INTEGER NOT NULL,
                total_sales REAL,
                total_length_ft REAL NOT NULL,
                utilization_pct REAL,
                utilization_grade TEXT,
                utilization_credit_ft REAL,
                exceeds_capacity INTEGER DEFAULT 0,
                line_count INTEGER NOT NULL,
                is_excluded INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        _rebuild_orders_if_needed(connection)
        connection.execute(
            """
        CREATE TABLE IF NOT EXISTS loads (
            id INTEGER PRIMARY KEY,
            load_number TEXT,
            draft_sequence INTEGER,
            origin_plant TEXT NOT NULL,
            destination_state TEXT NOT NULL,
            estimated_miles REAL,
            rate_per_mile REAL,
            estimated_cost REAL,
            standalone_cost REAL,
            consolidation_savings REAL,
            fragility_score REAL,
            status TEXT DEFAULT 'PROPOSED',
            trailer_type TEXT DEFAULT 'STEP_DECK',
            utilization_pct REAL DEFAULT 0.0,
            optimization_score REAL DEFAULT 0.0,
            build_source TEXT DEFAULT 'OPTIMIZED',
            created_by TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
        _rebuild_loads_if_needed(connection)
        _ensure_column(connection, "loads", "build_source", "build_source TEXT DEFAULT 'OPTIMIZED'")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS load_lines (
                id INTEGER PRIMARY KEY,
                load_id INTEGER NOT NULL,
                order_line_id INTEGER NOT NULL,
                line_total_feet REAL NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(load_id) REFERENCES loads(id),
                FOREIGN KEY(order_line_id) REFERENCES order_lines(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sku_specifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                length_with_tongue_ft REAL NOT NULL,
                max_stack_step_deck INTEGER DEFAULT 1,
                max_stack_flat_bed INTEGER DEFAULT 1,
                notes TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS item_sku_lookup (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plant TEXT NOT NULL,
                bin TEXT NOT NULL,
                item_pattern TEXT,
                sku TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(sku) REFERENCES sku_specifications(sku)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS rate_matrix (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                origin_plant TEXT NOT NULL,
                destination_state TEXT NOT NULL,
                rate_per_mile REAL NOT NULL,
                effective_year INTEGER DEFAULT 2026,
                notes TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(origin_plant, destination_state, effective_year)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS zip_coordinates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                zip TEXT NOT NULL UNIQUE,
                lat REAL NOT NULL,
                lng REAL NOT NULL,
                city TEXT,
                state TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS plants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plant_code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                lat REAL NOT NULL,
                lng REAL NOT NULL,
                address TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS optimization_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT NOT NULL DEFAULT (datetime('now')),
                plant_code TEXT NOT NULL,
                flexibility_days INTEGER,
                num_orders_input INTEGER,
                num_loads_before INTEGER,
                num_loads_after INTEGER,
                cost_before REAL,
                cost_after REAL,
                avg_util_before REAL,
                avg_util_after REAL,
                config_json TEXT,
                created_by TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS optimizer_settings (
                plant_code TEXT PRIMARY KEY,
                capacity_feet REAL,
                trailer_type TEXT,
                max_detour_pct REAL,
                time_window_days INTEGER,
                geo_radius REAL,
                baseline_cost REAL,
                baseline_set_at TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS optimized_loads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                load_number INTEGER NOT NULL,
                plant_code TEXT NOT NULL,
                total_util REAL,
                total_miles REAL,
                total_cost REAL,
                num_orders INTEGER,
                route_json TEXT,
                status TEXT DEFAULT 'DRAFT',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (run_id) REFERENCES optimization_runs(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS load_order_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                load_id INTEGER NOT NULL,
                order_so_num TEXT NOT NULL,
                sequence INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (load_id) REFERENCES optimized_loads(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS upload_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uploaded_at TEXT NOT NULL DEFAULT (datetime('now')),
                filename TEXT,
                total_rows INTEGER,
                total_orders INTEGER,
                mapping_rate REAL,
                unmapped_count INTEGER
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS upload_unmapped_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upload_id INTEGER NOT NULL,
                plant TEXT,
                bin TEXT,
                item TEXT,
                sku TEXT,
                reason TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (upload_id) REFERENCES upload_history(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS load_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                load_id INTEGER NOT NULL,
                order_id TEXT,
                action_type TEXT NOT NULL,
                reason_category TEXT NOT NULL,
                details TEXT NOT NULL,
                planner_id TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (load_id) REFERENCES loads(id)
            )
            """
        )
        _rebuild_load_feedback_if_needed(connection)

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS access_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                is_admin INTEGER NOT NULL DEFAULT 0,
                allowed_plants TEXT,
                default_plants TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS planning_settings (
                key TEXT PRIMARY KEY,
                value_text TEXT,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )

        _ensure_column(connection, "order_lines", "due_date", "due_date TEXT")
        _ensure_column(connection, "order_lines", "customer", "customer TEXT")
        _ensure_column(connection, "order_lines", "plant_full", "plant_full TEXT")
        _ensure_column(connection, "order_lines", "plant2", "plant2 TEXT")
        _ensure_column(connection, "order_lines", "plant", "plant TEXT")
        _ensure_column(connection, "order_lines", "item", "item TEXT")
        _ensure_column(connection, "order_lines", "qty", "qty INTEGER")
        _ensure_column(connection, "order_lines", "sales", "sales REAL")
        _ensure_column(connection, "order_lines", "so_num", "so_num TEXT")
        _ensure_column(connection, "order_lines", "cust_name", "cust_name TEXT")
        _ensure_column(connection, "order_lines", "cpo", "cpo TEXT")
        _ensure_column(connection, "order_lines", "salesman", "salesman TEXT")
        _ensure_column(connection, "order_lines", "cust_num", "cust_num TEXT")
        _ensure_column(connection, "order_lines", "bin", "bin TEXT")
        _ensure_column(connection, "order_lines", "load_num", "load_num TEXT")
        _ensure_column(connection, "order_lines", "address1", "address1 TEXT")
        _ensure_column(connection, "order_lines", "address2", "address2 TEXT")
        _ensure_column(connection, "order_lines", "city", "city TEXT")
        _ensure_column(connection, "order_lines", "state", "state TEXT")
        _ensure_column(connection, "order_lines", "zip", "zip TEXT")
        _ensure_column(connection, "order_lines", "sku", "sku TEXT")
        _ensure_column(connection, "order_lines", "unit_length_ft", "unit_length_ft REAL")
        _ensure_column(connection, "order_lines", "total_length_ft", "total_length_ft REAL")
        _ensure_column(connection, "order_lines", "max_stack_height", "max_stack_height INTEGER")
        _ensure_column(connection, "order_lines", "stack_position", "stack_position INTEGER DEFAULT 1")
        _ensure_column(connection, "order_lines", "utilization_pct", "utilization_pct REAL")
        _ensure_column(connection, "order_lines", "is_excluded", "is_excluded INTEGER DEFAULT 0")

        _ensure_column(connection, "orders", "so_num", "so_num TEXT")
        _ensure_column(connection, "orders", "due_date", "due_date TEXT")
        _ensure_column(connection, "orders", "plant", "plant TEXT")
        _ensure_column(connection, "orders", "customer", "customer TEXT")
        _ensure_column(connection, "orders", "cust_name", "cust_name TEXT")
        _ensure_column(connection, "orders", "state", "state TEXT")
        _ensure_column(connection, "orders", "zip", "zip TEXT")
        _ensure_column(connection, "orders", "total_qty", "total_qty INTEGER")
        _ensure_column(connection, "orders", "total_sales", "total_sales REAL")
        _ensure_column(connection, "orders", "total_length_ft", "total_length_ft REAL")
        _ensure_column(connection, "orders", "utilization_pct", "utilization_pct REAL")
        _ensure_column(connection, "orders", "utilization_grade", "utilization_grade TEXT")
        _ensure_column(
            connection, "orders", "utilization_credit_ft", "utilization_credit_ft REAL"
        )
        _ensure_column(
            connection, "orders", "exceeds_capacity", "exceeds_capacity INTEGER DEFAULT 0"
        )
        _ensure_column(connection, "orders", "line_count", "line_count INTEGER")
        _ensure_column(connection, "orders", "is_excluded", "is_excluded INTEGER DEFAULT 0")

        _ensure_column(connection, "loads", "origin_plant", "origin_plant TEXT")
        _ensure_column(connection, "loads", "destination_state", "destination_state TEXT")
        _ensure_column(connection, "loads", "estimated_miles", "estimated_miles REAL")
        _ensure_column(connection, "loads", "rate_per_mile", "rate_per_mile REAL")
        _ensure_column(connection, "loads", "estimated_cost", "estimated_cost REAL")
        _ensure_column(connection, "loads", "draft_sequence", "draft_sequence INTEGER")
        _ensure_column(connection, "loads", "standalone_cost", "standalone_cost REAL")
        _ensure_column(connection, "loads", "consolidation_savings", "consolidation_savings REAL")
        _ensure_column(connection, "loads", "fragility_score", "fragility_score REAL")
        _ensure_column(connection, "loads", "status", "status TEXT DEFAULT 'PROPOSED'")
        _ensure_column(connection, "loads", "load_number", "load_number TEXT")
        _ensure_column(
            connection,
            "loads",
            "trailer_type",
            "trailer_type TEXT DEFAULT 'STEP_DECK'",
        )
        _ensure_column(connection, "loads", "utilization_pct", "utilization_pct REAL DEFAULT 0.0")
        _ensure_column(connection, "loads", "optimization_score", "optimization_score REAL DEFAULT 0.0")
        _ensure_column(connection, "loads", "created_by", "created_by TEXT")
        _ensure_column(connection, "optimizer_settings", "baseline_cost", "baseline_cost REAL")
        _ensure_column(connection, "optimizer_settings", "baseline_set_at", "baseline_set_at TEXT")

        connection.execute(
            "UPDATE loads SET trailer_type = 'STEP_DECK' WHERE trailer_type IS NULL"
        )

        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_item_lookup ON item_sku_lookup(plant, bin)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_rate_lookup ON rate_matrix(origin_plant, destination_state)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_order_lines_so_num ON order_lines(so_num)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_zip_lookup ON zip_coordinates(zip)"
        )
        connection.commit()
        _seed_plants(connection)


def list_customers():
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT id, name, zip, notes, created_at FROM customers ORDER BY id DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def get_customer(customer_id):
    with get_connection() as connection:
        row = connection.execute(
            "SELECT id, name, zip, notes, created_at FROM customers WHERE id = ?",
            (customer_id,),
        ).fetchone()
        return dict(row) if row else None


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


def clear_orders():
    with get_connection() as connection:
        connection.execute("DELETE FROM order_lines")
        connection.execute("DELETE FROM orders")
        connection.commit()


def add_order_lines(order_lines):
    if not order_lines:
        return
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    rows = []
    for order in order_lines:
        rows.append(
            (
                order.get("due_date"),
                order.get("customer"),
                order.get("plant_full"),
                order.get("plant2"),
                order.get("plant"),
                order.get("item"),
                order.get("qty"),
                order.get("sales"),
                order.get("so_num"),
                order.get("cust_name"),
                order.get("cpo"),
                order.get("salesman"),
                order.get("cust_num"),
                order.get("bin"),
                order.get("load_num"),
                order.get("address1"),
                order.get("address2"),
                order.get("city"),
                order.get("state"),
                order.get("zip"),
                order.get("sku"),
                order.get("unit_length_ft"),
                order.get("total_length_ft"),
                order.get("max_stack_height"),
                order.get("stack_position", 1),
                order.get("utilization_pct"),
                order.get("is_excluded", 0),
                created_at,
            )
        )

    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO order_lines (
                due_date,
                customer,
                plant_full,
                plant2,
                plant,
                item,
                qty,
                sales,
                so_num,
                cust_name,
                cpo,
                salesman,
                cust_num,
                bin,
                load_num,
                address1,
                address2,
                city,
                state,
                zip,
                sku,
                unit_length_ft,
                total_length_ft,
                max_stack_height,
                stack_position,
                utilization_pct,
                is_excluded,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()


def add_orders(orders):
    if not orders:
        return
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    rows = []
    for order in orders:
        rows.append(
            (
                order.get("so_num"),
                order.get("due_date"),
                order.get("plant"),
                order.get("customer"),
                order.get("cust_name"),
                order.get("state"),
                order.get("zip"),
                order.get("total_qty"),
                order.get("total_sales"),
                order.get("total_length_ft"),
                order.get("utilization_pct"),
                order.get("utilization_grade"),
                order.get("utilization_credit_ft"),
                order.get("exceeds_capacity", 0),
                order.get("line_count"),
                order.get("is_excluded", 0),
                created_at,
            )
        )
    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO orders (
                so_num,
                due_date,
                plant,
                customer,
                cust_name,
                state,
                zip,
                total_qty,
                total_sales,
                total_length_ft,
                utilization_pct,
                utilization_grade,
                utilization_credit_ft,
                exceeds_capacity,
                line_count,
                is_excluded,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()


def list_orders(filters=None, sort_key="due_date"):
    filters = filters or {}
    where = []
    params = []

    plants = filters.get("plants") or []
    if plants:
        placeholders = ", ".join("?" for _ in plants)
        where.append(f"plant IN ({placeholders})")
        params.extend(plants)
    elif filters.get("plant"):
        where.append("plant = ?")
        params.append(filters["plant"])
    if filters.get("state"):
        where.append("state = ?")
        params.append(filters["state"])
    if filters.get("cust_name"):
        where.append("cust_name = ?")
        params.append(filters["cust_name"])
    if filters.get("due_start"):
        where.append("DATE(due_date) >= DATE(?)")
        params.append(filters["due_start"])
    if filters.get("due_end"):
        where.append("DATE(due_date) <= DATE(?)")
        params.append(filters["due_end"])

    assigned_clause = """
        EXISTS (
            SELECT 1
            FROM order_lines ol
            JOIN load_lines ll ON ll.order_line_id = ol.id
            WHERE ol.so_num = orders.so_num
            LIMIT 1
        )
    """
    assignment_filter = (filters.get("assignment_status") or "").upper()
    if assignment_filter == "ASSIGNED":
        where.append(assigned_clause)
    elif assignment_filter == "UNASSIGNED":
        where.append(f"NOT {assigned_clause}")

    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    order_by = "due_date"
    if sort_key in {"due_date", "plant", "state", "cust_name", "utilization_pct"}:
        order_by = sort_key

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                orders.*,
                (
                    SELECT city
                    FROM order_lines
                    WHERE order_lines.so_num = orders.so_num
                      AND city IS NOT NULL
                      AND city != ''
                    LIMIT 1
                ) AS city,
                {assigned_clause} AS is_assigned
            FROM orders
            {where_clause}
            ORDER BY {order_by} ASC, id DESC
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]


def list_order_lines_by_sonum(so_num):
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM order_lines WHERE so_num = ? ORDER BY id",
            (so_num,),
        ).fetchall()
        return [dict(row) for row in rows]


def list_order_lines_for_so_nums(origin_plant, so_nums):
    if not origin_plant or not so_nums:
        return []
    cleaned = [str(value).strip() for value in so_nums if str(value or "").strip()]
    if not cleaned:
        return []
    placeholders = ", ".join("?" for _ in cleaned)
    params = [origin_plant] + cleaned
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM order_lines
            WHERE is_excluded = 0
              AND plant = ?
              AND so_num IN ({placeholders})
            ORDER BY due_date ASC, id ASC
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]


def filter_eligible_manual_so_nums(origin_plant, so_nums):
    if not origin_plant or not so_nums:
        return set()
    cleaned = [str(value).strip() for value in so_nums if str(value or "").strip()]
    if not cleaned:
        return set()
    placeholders = ", ".join("?" for _ in cleaned)
    params = [origin_plant] + cleaned
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT DISTINCT ol.so_num AS so_num
            FROM loads l
            JOIN load_lines ll ON ll.load_id = l.id
            JOIN order_lines ol ON ol.id = ll.order_line_id
            WHERE l.origin_plant = ?
              AND COALESCE(UPPER(l.status), '') IN ('PROPOSED', 'DRAFT')
              AND COALESCE(UPPER(l.build_source), 'OPTIMIZED') = 'OPTIMIZED'
              AND ol.so_num IN ({placeholders})
            """,
            params,
        ).fetchall()
        return {row["so_num"] for row in rows if row["so_num"]}


def list_eligible_manual_orders(origin_plant, search=None, limit=25):
    if not origin_plant:
        return []
    search_value = (search or "").strip()
    where = [
        "orders.is_excluded = 0",
        "orders.plant = ?",
        """
        EXISTS (
            SELECT 1
            FROM order_lines ol
            JOIN load_lines ll ON ll.order_line_id = ol.id
            JOIN loads l ON l.id = ll.load_id
            WHERE ol.so_num = orders.so_num
              AND l.origin_plant = orders.plant
              AND COALESCE(UPPER(l.status), '') IN ('PROPOSED', 'DRAFT')
              AND COALESCE(UPPER(l.build_source), 'OPTIMIZED') = 'OPTIMIZED'
            LIMIT 1
        )
        """.strip(),
    ]
    params = [origin_plant]
    if search_value:
        where.append("(orders.so_num LIKE ? OR orders.cust_name LIKE ?)")
        params.extend([f"%{search_value}%", f"%{search_value}%"])
    where_clause = " AND ".join(where)
    limit_clause = ""
    if isinstance(limit, int) and limit > 0:
        limit_clause = f"LIMIT {int(limit)}"
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                orders.*,
                (
                    SELECT city
                    FROM order_lines
                    WHERE order_lines.so_num = orders.so_num
                      AND city IS NOT NULL
                      AND city != ''
                    LIMIT 1
                ) AS city
            FROM orders
            WHERE {where_clause}
            ORDER BY DATE(orders.due_date) ASC, orders.so_num ASC
            {limit_clause}
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]


def list_order_lines_for_optimization(origin_plant):
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT ol.*
            FROM order_lines ol
            LEFT JOIN load_lines ll ON ll.order_line_id = ol.id
            WHERE ol.is_excluded = 0
              AND ol.plant = ?
              AND ll.id IS NULL
            ORDER BY ol.due_date ASC, ol.id ASC
            """,
            (origin_plant,),
        ).fetchall()
        return [dict(row) for row in rows]


def list_orders_for_optimization(origin_plant):
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM orders
            WHERE is_excluded = 0
              AND plant = ?
            ORDER BY due_date ASC, id ASC
            """,
            (origin_plant,),
        ).fetchall()
        return [dict(row) for row in rows]


def update_orders_excluded(order_ids, is_excluded):
    if not order_ids:
        return
    with get_connection() as connection:
        connection.executemany(
            "UPDATE orders SET is_excluded = ? WHERE id = ?",
            [(1 if is_excluded else 0, order_id) for order_id in order_ids],
        )
        rows = connection.execute(
            "SELECT so_num FROM orders WHERE id IN ({})".format(
                ",".join("?" for _ in order_ids)
            ),
            order_ids,
        ).fetchall()
        so_nums = [row["so_num"] for row in rows if row["so_num"]]
        if so_nums:
            connection.executemany(
                "UPDATE order_lines SET is_excluded = ? WHERE so_num = ?",
                [(1 if is_excluded else 0, so_num) for so_num in so_nums],
            )
        connection.commit()


def list_sku_specs():
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, sku, category, length_with_tongue_ft, max_stack_step_deck,
                   max_stack_flat_bed, notes, created_at
            FROM sku_specifications
            ORDER BY sku ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def upsert_sku_spec(spec):
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO sku_specifications (
                sku, category, length_with_tongue_ft, max_stack_step_deck,
                max_stack_flat_bed, notes, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sku) DO UPDATE SET
                category = excluded.category,
                length_with_tongue_ft = excluded.length_with_tongue_ft,
                max_stack_step_deck = excluded.max_stack_step_deck,
                max_stack_flat_bed = excluded.max_stack_flat_bed,
                notes = excluded.notes
            """,
            (
                spec.get("sku"),
                spec.get("category"),
                spec.get("length_with_tongue_ft"),
                spec.get("max_stack_step_deck", 1),
                spec.get("max_stack_flat_bed", 1),
                spec.get("notes"),
                created_at,
            ),
        )
        connection.commit()


def update_sku_spec(spec_id, spec):
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE sku_specifications
            SET sku = ?,
                category = ?,
                length_with_tongue_ft = ?,
                max_stack_step_deck = ?,
                max_stack_flat_bed = ?,
                notes = ?
            WHERE id = ?
            """,
            (
                spec.get("sku"),
                spec.get("category"),
                spec.get("length_with_tongue_ft"),
                spec.get("max_stack_step_deck", 1),
                spec.get("max_stack_flat_bed", 1),
                spec.get("notes"),
                spec_id,
            ),
        )
        connection.commit()


def delete_sku_spec(spec_id):
    with get_connection() as connection:
        connection.execute("DELETE FROM sku_specifications WHERE id = ?", (spec_id,))
        connection.commit()


def list_item_lookups():
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, plant, bin, item_pattern, sku, created_at
            FROM item_sku_lookup
            ORDER BY plant ASC, bin ASC, item_pattern ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def add_item_lookup(entry):
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO item_sku_lookup (plant, bin, item_pattern, sku, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                entry.get("plant"),
                entry.get("bin"),
                entry.get("item_pattern"),
                entry.get("sku"),
                created_at,
            ),
        )
        connection.commit()


def update_item_lookup(entry_id, entry):
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE item_sku_lookup
            SET plant = ?,
                bin = ?,
                item_pattern = ?,
                sku = ?
            WHERE id = ?
            """,
            (
                entry.get("plant"),
                entry.get("bin"),
                entry.get("item_pattern"),
                entry.get("sku"),
                entry_id,
            ),
        )
        connection.commit()


def delete_item_lookup(entry_id):
    with get_connection() as connection:
        connection.execute("DELETE FROM item_sku_lookup WHERE id = ?", (entry_id,))
        connection.commit()


def list_rate_matrix():
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, origin_plant, destination_state, rate_per_mile, effective_year, notes, created_at
            FROM rate_matrix
            ORDER BY origin_plant ASC, destination_state ASC, effective_year DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def get_rate_by_id(rate_id):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, origin_plant, destination_state, rate_per_mile, effective_year, notes, created_at
            FROM rate_matrix
            WHERE id = ?
            """,
            (rate_id,),
        ).fetchone()
        return dict(row) if row else None


def get_rate_by_lane(origin_plant, destination_state, effective_year=2026):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, origin_plant, destination_state, rate_per_mile, effective_year, notes, created_at
            FROM rate_matrix
            WHERE origin_plant = ? AND destination_state = ? AND effective_year = ?
            """,
            (origin_plant, destination_state, effective_year),
        ).fetchone()
        return dict(row) if row else None


def list_plants():
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, plant_code, name, lat, lng, address, created_at
            FROM plants
            ORDER BY plant_code ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def update_plant(plant_id, plant):
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE plants
            SET plant_code = ?,
                name = ?,
                lat = ?,
                lng = ?,
                address = ?
            WHERE id = ?
            """,
            (
                plant.get("plant_code"),
                plant.get("name"),
                plant.get("lat"),
                plant.get("lng"),
                plant.get("address"),
                plant_id,
            ),
        )
        connection.commit()


def upsert_rate(rate):
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO rate_matrix (
                origin_plant, destination_state, rate_per_mile, effective_year, notes, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(origin_plant, destination_state, effective_year) DO UPDATE SET
                rate_per_mile = excluded.rate_per_mile,
                notes = excluded.notes
            """,
            (
                rate.get("origin_plant"),
                rate.get("destination_state"),
                rate.get("rate_per_mile"),
                rate.get("effective_year", 2026),
                rate.get("notes"),
                created_at,
            ),
        )
        connection.commit()


def update_rate(rate_id, rate):
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE rate_matrix
            SET origin_plant = ?,
                destination_state = ?,
                rate_per_mile = ?,
                effective_year = ?,
                notes = ?
            WHERE id = ?
            """,
            (
                rate.get("origin_plant"),
                rate.get("destination_state"),
                rate.get("rate_per_mile"),
                rate.get("effective_year", 2026),
                rate.get("notes"),
                rate_id,
            ),
        )
        connection.commit()


def delete_rate(rate_id):
    with get_connection() as connection:
        connection.execute("DELETE FROM rate_matrix WHERE id = ?", (rate_id,))
        connection.commit()


def add_upload_history(entry):
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO upload_history (
                filename, total_rows, total_orders, mapping_rate, unmapped_count
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                entry.get("filename"),
                entry.get("total_rows"),
                entry.get("total_orders"),
                entry.get("mapping_rate"),
                entry.get("unmapped_count"),
            ),
        )
        connection.commit()
        return cursor.lastrowid


def add_upload_unmapped_items(upload_id, items):
    if not upload_id or not items:
        return
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    rows = []
    for item in items:
        rows.append(
            (
                upload_id,
                item.get("plant"),
                item.get("bin"),
                item.get("item"),
                item.get("sku"),
                item.get("reason"),
                created_at,
            )
        )
    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO upload_unmapped_items (
                upload_id, plant, bin, item, sku, reason, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()


def get_last_upload():
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, uploaded_at, filename, total_rows, total_orders,
                   mapping_rate, unmapped_count
            FROM upload_history
            ORDER BY uploaded_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None


def list_upload_history(limit=None):
    with get_connection() as connection:
        if limit:
            rows = connection.execute(
                """
                SELECT id, uploaded_at, filename, total_rows, total_orders,
                       mapping_rate, unmapped_count
                FROM upload_history
                ORDER BY uploaded_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT id, uploaded_at, filename, total_rows, total_orders,
                       mapping_rate, unmapped_count
                FROM upload_history
                ORDER BY uploaded_at DESC, id DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]


def list_upload_unmapped_items(upload_id, limit=10):
    if not upload_id:
        return []
    with get_connection() as connection:
        if limit:
            rows = connection.execute(
                """
                SELECT plant, bin, item, sku, reason, created_at
                FROM upload_unmapped_items
                WHERE upload_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (upload_id, limit),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT plant, bin, item, sku, reason, created_at
                FROM upload_unmapped_items
                WHERE upload_id = ?
                ORDER BY id ASC
                """,
                (upload_id,),
            ).fetchall()
        return [dict(row) for row in rows]


def upsert_optimizer_settings(settings):
    updated_at = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO optimizer_settings (
                plant_code,
                capacity_feet,
                trailer_type,
                max_detour_pct,
                time_window_days,
                geo_radius,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plant_code) DO UPDATE SET
                capacity_feet = excluded.capacity_feet,
                trailer_type = excluded.trailer_type,
                max_detour_pct = excluded.max_detour_pct,
                time_window_days = excluded.time_window_days,
                geo_radius = excluded.geo_radius,
                updated_at = excluded.updated_at
            """,
            (
                settings.get("origin_plant"),
                settings.get("capacity_feet"),
                settings.get("trailer_type"),
                settings.get("max_detour_pct"),
                settings.get("time_window_days"),
                settings.get("geo_radius"),
                updated_at,
            ),
        )
        connection.commit()


def get_optimizer_settings(plant_code):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                plant_code,
                capacity_feet,
                trailer_type,
                max_detour_pct,
                time_window_days,
                geo_radius,
                baseline_cost,
                baseline_set_at,
                updated_at
            FROM optimizer_settings
            WHERE plant_code = ?
            """,
            (plant_code,),
        ).fetchone()
        return dict(row) if row else None


def get_optimizer_baseline(plant_code):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT baseline_cost, baseline_set_at
            FROM optimizer_settings
            WHERE plant_code = ?
            """,
            (plant_code,),
        ).fetchone()
        if not row:
            return {"baseline_cost": None, "baseline_set_at": None}
        return dict(row)


def set_optimizer_baseline(plant_code, baseline_cost):
    updated_at = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO optimizer_settings (
                plant_code,
                baseline_cost,
                baseline_set_at,
                updated_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(plant_code) DO UPDATE SET
                baseline_cost = excluded.baseline_cost,
                baseline_set_at = excluded.baseline_set_at,
                updated_at = excluded.updated_at
            """,
            (
                plant_code,
                baseline_cost,
                updated_at,
                updated_at,
            ),
        )
        connection.commit()


def get_rate(origin_plant, destination_state, effective_year=2026):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT rate_per_mile
            FROM rate_matrix
            WHERE origin_plant = ? AND destination_state = ? AND effective_year = ?
            """,
            (origin_plant, destination_state, effective_year),
        ).fetchone()
        return float(row["rate_per_mile"]) if row else 0.0


def list_loads(origin_plant=None):
    where_clause = ""
    params = []
    if origin_plant:
        where_clause = "WHERE origin_plant = ?"
        params.append(origin_plant)

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                id,
                load_number,
                draft_sequence,
                origin_plant,
                destination_state,
                estimated_miles,
                rate_per_mile,
                estimated_cost,
                standalone_cost,
                consolidation_savings,
                fragility_score,
                status,
                trailer_type,
                utilization_pct,
                optimization_score,
                build_source,
                created_by,
                created_at
            FROM loads
            {where_clause}
            ORDER BY optimization_score DESC, id DESC
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]


def create_load(load):
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO loads (
                load_number,
                draft_sequence,
                origin_plant,
                destination_state,
                estimated_miles,
                rate_per_mile,
                estimated_cost,
                standalone_cost,
                consolidation_savings,
                fragility_score,
                status,
                trailer_type,
                utilization_pct,
                optimization_score,
                build_source,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                load.get("load_number"),
                load.get("draft_sequence"),
                load.get("origin_plant"),
                load.get("destination_state"),
                load.get("estimated_miles"),
                load.get("rate_per_mile"),
                load.get("estimated_cost"),
                load.get("standalone_cost"),
                load.get("consolidation_savings"),
                load.get("fragility_score"),
                load.get("status", "PROPOSED"),
                load.get("trailer_type", "STEP_DECK"),
                load.get("utilization_pct", 0.0),
                load.get("optimization_score", 0.0),
                load.get("build_source") or "OPTIMIZED",
                load.get("created_by"),
                created_at,
            ),
        )
        connection.commit()
        return cursor.lastrowid


def create_load_line(load_id, order_line_id, line_total_feet):
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO load_lines (load_id, order_line_id, line_total_feet, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (load_id, order_line_id, line_total_feet, created_at),
        )
        connection.commit()


def list_load_lines(load_id):
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                load_lines.id,
                load_lines.order_line_id,
                load_lines.line_total_feet,
                order_lines.due_date,
                order_lines.sales,
                order_lines.plant,
                order_lines.item,
                order_lines.qty,
                order_lines.so_num,
                order_lines.cust_name,
                order_lines.bin,
                order_lines.city,
                order_lines.state,
                order_lines.zip,
                order_lines.sku,
                order_lines.unit_length_ft,
                order_lines.total_length_ft,
                order_lines.max_stack_height,
                order_lines.utilization_pct,
                order_lines.is_excluded
            FROM load_lines
            JOIN order_lines ON order_lines.id = load_lines.order_line_id
            WHERE load_lines.load_id = ?
            ORDER BY load_lines.id
            """,
            (load_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_load(load_id):
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM loads WHERE id = ?",
            (load_id,),
        ).fetchone()
        return dict(row) if row else None


def _list_load_numbers_for_prefix(prefix):
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT load_number FROM loads WHERE load_number LIKE ?",
            (f"{prefix}%",),
        ).fetchall()
        return [row["load_number"] for row in rows if row["load_number"]]


def get_next_load_sequence(plant_code, year_suffix):
    prefix = f"{plant_code}{year_suffix}-"
    numbers = _list_load_numbers_for_prefix(prefix)
    max_seq = 0
    for value in numbers:
        if not value.startswith(prefix):
            continue
        remainder = value[len(prefix):]
        seq_part = remainder.split("-", 1)[0]
        if seq_part.isdigit():
            max_seq = max(max_seq, int(seq_part))
    return max_seq + 1


def update_load_status(load_id, status, load_number=None):
    with get_connection() as connection:
        if load_number is None:
            connection.execute(
                "UPDATE loads SET status = ? WHERE id = ?",
                (status, load_id),
            )
        else:
            connection.execute(
                "UPDATE loads SET status = ?, load_number = ? WHERE id = ?",
                (status, load_number, load_id),
            )
        connection.commit()


def update_load_trailer_type(load_id, trailer_type):
    with get_connection() as connection:
        connection.execute(
            "UPDATE loads SET trailer_type = ? WHERE id = ?",
            (trailer_type, load_id),
        )
        connection.commit()


def add_load_feedback(
    load_id,
    order_id=None,
    action_type=None,
    reason_category=None,
    details=None,
    planner_id=None,
    reasons=None,
    notes=None,
):
    if not action_type:
        action_type = "order_removed" if order_id else "load_rejected"

    reasons_value = None
    if isinstance(reasons, (list, tuple)):
        reasons_value = ", ".join([str(item) for item in reasons if item])
    elif reasons:
        reasons_value = str(reasons)

    if not reason_category:
        reason_category = reasons_value or "Other"

    if details is None:
        details = notes or ""

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO load_feedback (
                load_id, order_id, action_type, reason_category, details, planner_id
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (load_id, order_id, action_type, reason_category, details, planner_id),
        )
        connection.commit()


def list_load_feedback(filters=None, limit=None):
    filters = filters or {}
    clauses = []
    params = []

    start_date = filters.get("start_date")
    end_date = filters.get("end_date")
    if start_date:
        clauses.append("DATE(f.created_at) >= DATE(?)")
        params.append(start_date)
    if end_date:
        clauses.append("DATE(f.created_at) <= DATE(?)")
        params.append(end_date)

    planner_id = filters.get("planner_id")
    if planner_id:
        clauses.append("f.planner_id = ?")
        params.append(planner_id)

    action_type = filters.get("action_type")
    if action_type:
        clauses.append("f.action_type = ?")
        params.append(action_type)

    reason_category = filters.get("reason_category")
    if reason_category:
        clauses.append("f.reason_category = ?")
        params.append(reason_category)

    search = (filters.get("search") or "").strip()
    if search:
        clauses.append("f.details LIKE ?")
        params.append(f"%{search}%")

    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sort_key = (filters.get("sort") or "timestamp_desc").strip().lower()
    sort_map = {
        "timestamp_desc": "f.created_at DESC",
        "timestamp_asc": "f.created_at ASC",
        "planner": "f.planner_id ASC, f.created_at DESC",
        "load": "f.load_id ASC, f.created_at DESC",
    }
    order_clause = sort_map.get(sort_key, "f.created_at DESC")
    limit_clause = f"LIMIT {int(limit)}" if limit else ""

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT f.*, l.load_number
            FROM load_feedback f
            LEFT JOIN loads l ON l.id = f.load_id
            {where_clause}
            ORDER BY {order_clause}
            {limit_clause}
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def list_feedback_filter_options():
    with get_connection() as connection:
        planners = [
            row["planner_id"]
            for row in connection.execute(
                "SELECT DISTINCT planner_id FROM load_feedback WHERE planner_id IS NOT NULL"
            ).fetchall()
        ]
        action_types = [
            row["action_type"]
            for row in connection.execute(
                "SELECT DISTINCT action_type FROM load_feedback WHERE action_type IS NOT NULL"
            ).fetchall()
        ]
        reasons = [
            row["reason_category"]
            for row in connection.execute(
                "SELECT DISTINCT reason_category FROM load_feedback WHERE reason_category IS NOT NULL"
            ).fetchall()
        ]
    return {
        "planners": sorted({value for value in planners if value}),
        "action_types": sorted({value for value in action_types if value}),
        "reason_categories": sorted({value for value in reasons if value}),
    }


def remove_order_from_load(load_id, order_id):
    with get_connection() as connection:
        connection.execute(
            """
            DELETE FROM load_lines
            WHERE load_id = ?
              AND order_line_id IN (
                  SELECT id FROM order_lines WHERE so_num = ?
              )
            """,
            (load_id, order_id),
        )
        connection.commit()


def count_load_lines(load_id):
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) as total FROM load_lines WHERE load_id = ?",
            (load_id,),
        ).fetchone()
        return row["total"] if row else 0


def delete_load(load_id):
    with get_connection() as connection:
        connection.execute("DELETE FROM load_lines WHERE load_id = ?", (load_id,))
        connection.execute("DELETE FROM loads WHERE id = ?", (load_id,))
        connection.commit()

def clear_loads_for_plant(origin_plant=None):
    with get_connection() as connection:
        if origin_plant:
            connection.execute(
                "DELETE FROM load_lines WHERE load_id IN (SELECT id FROM loads WHERE origin_plant = ?)",
                (origin_plant,),
            )
            connection.execute(
                "DELETE FROM loads WHERE origin_plant = ?",
                (origin_plant,),
            )
        else:
            connection.execute("DELETE FROM load_lines")
            connection.execute("DELETE FROM loads")
        connection.commit()


def clear_draft_loads(origin_plant=None):
    where_clause = "status = 'PROPOSED' AND COALESCE(UPPER(build_source), 'OPTIMIZED') != 'MANUAL'"
    params = []
    if origin_plant:
        where_clause += " AND origin_plant = ?"
        params.append(origin_plant)

    with get_connection() as connection:
        connection.execute(
            f"DELETE FROM load_lines WHERE load_id IN (SELECT id FROM loads WHERE {where_clause})",
            params,
        )
        connection.execute(
            f"DELETE FROM loads WHERE {where_clause}",
            params,
        )
        connection.commit()


def clear_unapproved_loads(origin_plant=None):
    where_clause = (
        "COALESCE(UPPER(status), '') != 'APPROVED'"
        " AND COALESCE(UPPER(build_source), 'OPTIMIZED') != 'MANUAL'"
    )
    params = []
    if origin_plant:
        where_clause += " AND origin_plant = ?"
        params.append(origin_plant)

    with get_connection() as connection:
        connection.execute(
            f"DELETE FROM load_lines WHERE load_id IN (SELECT id FROM loads WHERE {where_clause})",
            params,
        )
        connection.execute(
            f"DELETE FROM loads WHERE {where_clause}",
            params,
        )
        connection.commit()


def clear_loads():
    with get_connection() as connection:
        connection.execute("DELETE FROM load_lines")
        connection.execute("DELETE FROM loads")
        connection.commit()


def _seed_plants(connection):
    rows = []
    for code, info in DEFAULT_PLANTS.items():
        rows.append(
            (
                code,
                info["name"],
                info["lat"],
                info["lng"],
                "",
            )
        )
    connection.executemany(
        """
        INSERT INTO plants (plant_code, name, lat, lng, address)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(plant_code) DO UPDATE SET
            name = excluded.name,
            lat = excluded.lat,
            lng = excluded.lng,
            address = excluded.address
        """,
        rows,
    )
    connection.commit()


def list_access_profiles():
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, name, is_admin, allowed_plants, default_plants, created_at
            FROM access_profiles
            ORDER BY is_admin DESC, name ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def get_access_profile(profile_id):
    if not profile_id:
        return None
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, name, is_admin, allowed_plants, default_plants, created_at
            FROM access_profiles
            WHERE id = ?
            """,
            (profile_id,),
        ).fetchone()
        return dict(row) if row else None


def get_access_profile_by_name(name):
    name = (name or "").strip()
    if not name:
        return None
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, name, is_admin, allowed_plants, default_plants, created_at
            FROM access_profiles
            WHERE name = ?
            """,
            (name,),
        ).fetchone()
        return dict(row) if row else None


def create_access_profile(name, is_admin, allowed_plants, default_plants):
    name = (name or "").strip()
    if not name:
        raise ValueError("Profile name is required.")

    if isinstance(allowed_plants, (list, tuple, set)):
        allowed = ",".join([str(value).strip() for value in allowed_plants if str(value).strip()])
    else:
        allowed = (allowed_plants or "").strip()

    if isinstance(default_plants, (list, tuple, set)):
        default = ",".join([str(value).strip() for value in default_plants if str(value).strip()])
    else:
        default = (default_plants or "").strip()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO access_profiles (name, is_admin, allowed_plants, default_plants)
            VALUES (?, ?, ?, ?)
            """,
            (name, 1 if is_admin else 0, allowed, default),
        )
        connection.commit()
        return cursor.lastrowid


def update_access_profile(profile_id, name, is_admin, allowed_plants, default_plants):
    if not profile_id:
        raise ValueError("Profile id is required.")
    name = (name or "").strip()
    if not name:
        raise ValueError("Profile name is required.")

    if isinstance(allowed_plants, (list, tuple, set)):
        allowed = ",".join([str(value).strip() for value in allowed_plants if str(value).strip()])
    else:
        allowed = (allowed_plants or "").strip()

    if isinstance(default_plants, (list, tuple, set)):
        default = ",".join([str(value).strip() for value in default_plants if str(value).strip()])
    else:
        default = (default_plants or "").strip()

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE access_profiles
            SET name = ?, is_admin = ?, allowed_plants = ?, default_plants = ?
            WHERE id = ?
            """,
            (name, 1 if is_admin else 0, allowed, default, profile_id),
        )
        connection.commit()


def delete_access_profile(profile_id):
    if not profile_id:
        return
    with get_connection() as connection:
        connection.execute("DELETE FROM access_profiles WHERE id = ?", (profile_id,))
        connection.commit()


def ensure_default_access_profiles(profiles):
    if not profiles:
        return

    with get_connection() as connection:
        existing = connection.execute("SELECT name FROM access_profiles").fetchall()
        existing_names = {row["name"] for row in existing}

        to_insert = []
        for profile in profiles:
            name = (profile.get("name") or "").strip()
            if not name or name in existing_names:
                continue
            to_insert.append(
                (
                    name,
                    1 if profile.get("is_admin") else 0,
                    profile.get("allowed_plants"),
                    profile.get("default_plants"),
                )
            )

        if to_insert:
            connection.executemany(
                """
                INSERT INTO access_profiles (name, is_admin, allowed_plants, default_plants)
                VALUES (?, ?, ?, ?)
                """,
                to_insert,
            )
            connection.commit()


def get_planning_setting(key):
    key = (key or "").strip()
    if not key:
        return None
    with get_connection() as connection:
        row = connection.execute(
            "SELECT key, value_text, updated_at FROM planning_settings WHERE key = ?",
            (key,),
        ).fetchone()
        return dict(row) if row else None


def upsert_planning_setting(key, value_text):
    key = (key or "").strip()
    if not key:
        raise ValueError("Setting key is required.")
    value_text = None if value_text is None else str(value_text)
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO planning_settings (key, value_text, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET
                value_text = excluded.value_text,
                updated_at = excluded.updated_at
            """,
            (key, value_text),
        )
        connection.commit()


def ensure_default_planning_settings(settings):
    if not settings:
        return
    with get_connection() as connection:
        existing_rows = connection.execute("SELECT key FROM planning_settings").fetchall()
        existing_keys = {row["key"] for row in existing_rows}
        to_insert = []
        for key, value in settings.items():
            normalized_key = (key or "").strip()
            if not normalized_key or normalized_key in existing_keys:
                continue
            to_insert.append((normalized_key, None if value is None else str(value)))
        if to_insert:
            connection.executemany(
                """
                INSERT INTO planning_settings (key, value_text)
                VALUES (?, ?)
                """,
                to_insert,
            )
            connection.commit()
