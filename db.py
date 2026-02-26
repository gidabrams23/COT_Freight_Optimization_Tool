import csv
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = (
    "/var/data/app.db"
    if (os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"))
    else str(ROOT / "data" / "db" / "app.db")
)
DB_PATH = Path(os.environ.get("APP_DB_PATH", DEFAULT_DB_PATH))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
SEED_DIR = Path(os.environ.get("APP_SEED_DIR", str(ROOT / "data" / "seed")))

DEFAULT_PLANTS = {
    "GA": {"name": "Lavonia", "lat": 34.43611, "lng": -83.10639},
    "IA": {"name": "Missouri Valley", "lat": 41.55944, "lng": -95.90250},
    "TX": {"name": "Mexia", "lat": 31.66222, "lng": -96.49722},
    "VA": {"name": "Montross", "lat": 38.09389, "lng": -76.82611},
    "CL": {"name": "Callao", "lat": 37.97216, "lng": -76.57288},
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
    "item_desc",
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
    "created_date",
    "ship_date",
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
    "created_date",
    "ship_date",
    "plant",
    "customer",
    "cust_name",
    "address1",
    "address2",
    "city",
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
    "last_upload_id",
    "needs_review",
    "status",
    "last_seen_at",
    "closed_at",
    "created_at",
]


def get_connection():
    timeout_sec_raw = os.environ.get("SQLITE_BUSY_TIMEOUT_SEC", "30")
    try:
        timeout_sec = max(float(timeout_sec_raw), 1.0)
    except (TypeError, ValueError):
        timeout_sec = 30.0
    timeout_ms = int(timeout_sec * 1000)

    connection = sqlite3.connect(DB_PATH, timeout=timeout_sec)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute(f"PRAGMA busy_timeout={timeout_ms}")
    return connection


def _chunked(values, size=900):
    if not values:
        return []
    return [values[i : i + size] for i in range(0, len(values), size)]


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
            item_desc TEXT,
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
            created_date TEXT,
            ship_date TEXT,
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
            created_date TEXT,
            ship_date TEXT,
            plant TEXT NOT NULL,
            customer TEXT,
            cust_name TEXT,
            address1 TEXT,
            address2 TEXT,
            city TEXT,
            state TEXT NOT NULL,
            zip TEXT NOT NULL,
            total_qty INTEGER NOT NULL,
            total_sales REAL,
            total_length_ft REAL NOT NULL,
            utilization_pct REAL,
            line_count INTEGER NOT NULL,
            is_excluded INTEGER DEFAULT 0,
            last_upload_id INTEGER,
            needs_review INTEGER DEFAULT 0,
            status TEXT DEFAULT 'OPEN',
            last_seen_at TEXT,
            closed_at TEXT,
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
            planning_session_id INTEGER,
            origin_plant TEXT NOT NULL,
            destination_state TEXT NOT NULL,
            estimated_miles REAL,
            rate_per_mile REAL,
            estimated_cost REAL,
            route_provider TEXT,
            route_profile TEXT,
            route_total_miles REAL,
            route_legs_json TEXT,
            route_geometry_json TEXT,
            route_fallback INTEGER DEFAULT 0,
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
        route_provider = row["route_provider"] if "route_provider" in old_columns else None
        route_profile = row["route_profile"] if "route_profile" in old_columns else None
        route_total_miles = row["route_total_miles"] if "route_total_miles" in old_columns else None
        route_legs_json = row["route_legs_json"] if "route_legs_json" in old_columns else None
        route_geometry_json = row["route_geometry_json"] if "route_geometry_json" in old_columns else None
        route_fallback = row["route_fallback"] if "route_fallback" in old_columns else 0
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
                row["planning_session_id"] if "planning_session_id" in old_columns else None,
                origin_plant,
                destination_state,
                estimated_miles,
                rate_per_mile,
                estimated_cost,
                route_provider,
                route_profile,
                route_total_miles,
                route_legs_json,
                route_geometry_json,
                route_fallback,
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
                planning_session_id,
                origin_plant,
                destination_state,
                estimated_miles,
                rate_per_mile,
                estimated_cost,
                route_provider,
                route_profile,
                route_total_miles,
                route_legs_json,
                route_geometry_json,
                route_fallback,
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def _rebuild_app_feedback_if_needed(connection):
    columns_info = _get_columns_info(connection, "app_feedback")
    if not columns_info:
        return
    column_names = {column["name"] for column in columns_info}
    required = {"category", "title", "message", "status"}
    needs_rebuild = not required.issubset(column_names)
    if not needs_rebuild:
        return

    connection.execute("ALTER TABLE app_feedback RENAME TO app_feedback_old")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS app_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            page TEXT,
            planner_id TEXT,
            status TEXT NOT NULL DEFAULT 'OPEN',
            resolved_at TEXT,
            resolved_by TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )

    old_rows = connection.execute("SELECT * FROM app_feedback_old").fetchall()
    migrated = []
    for row in old_rows:
        row_dict = dict(row)
        migrated.append(
            (
                row_dict.get("category") or "Other",
                row_dict.get("title") or "",
                row_dict.get("message") or row_dict.get("details") or "",
                row_dict.get("page"),
                row_dict.get("planner_id"),
                row_dict.get("status") or "OPEN",
                row_dict.get("resolved_at"),
                row_dict.get("resolved_by"),
                row_dict.get("created_at"),
            )
        )

    if migrated:
        connection.executemany(
            """
            INSERT INTO app_feedback (
                category, title, message, page, planner_id, status, resolved_at, resolved_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            migrated,
        )

    connection.execute("DROP TABLE app_feedback_old")


def _coerce_seed_value(value):
    if value is None:
        return None
    text = str(value)
    if text == "":
        return None
    return value


def _seed_table_from_csv(connection, table_name, filename, columns):
    path = SEED_DIR / filename
    if not path.exists():
        return False
    existing = connection.execute(
        f"SELECT COUNT(*) FROM {table_name}"
    ).fetchone()
    if existing and existing[0]:
        return False

    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for row in reader:
            rows.append([_coerce_seed_value(row.get(col)) for col in columns])

    if not rows:
        return False

    placeholders = ", ".join("?" for _ in columns)
    column_list = ", ".join(columns)
    connection.executemany(
        f"INSERT INTO {table_name} ({column_list}) VALUES ({placeholders})",
        rows,
    )
    return True


def _seed_reference_data(connection):
    seeds = [
        (
            "plants",
            "plants.csv",
            ["plant_code", "name", "lat", "lng", "address", "created_at"],
        ),
        (
            "sku_specifications",
            "sku_specifications.csv",
            [
                "sku",
                "description",
                "category",
                "length_with_tongue_ft",
                "max_stack_step_deck",
                "max_stack_flat_bed",
                "notes",
                "added_at",
                "created_at",
                "source",
            ],
        ),
        (
            "item_sku_lookup",
            "item_sku_lookup.csv",
            ["plant", "bin", "item_pattern", "sku", "created_at"],
        ),
        (
            "rate_matrix",
            "rate_matrix.csv",
            [
                "origin_plant",
                "destination_state",
                "rate_per_mile",
                "effective_year",
                "notes",
                "created_at",
            ],
        ),
        (
            "planning_settings",
            "planning_settings.csv",
            ["key", "value_text", "updated_at"],
        ),
        (
            "zip_coordinates",
            "zip_coordinates.csv",
            ["zip", "lat", "lng", "city", "state", "created_at"],
        ),
        (
            "optimizer_settings",
            "optimizer_settings.csv",
            [
                "plant_code",
                "capacity_feet",
                "trailer_type",
                "max_detour_pct",
                "time_window_days",
                "geo_radius",
                "baseline_cost",
                "baseline_set_at",
                "updated_at",
            ],
        ),
    ]

    for table_name, filename, columns in seeds:
        try:
            _seed_table_from_csv(connection, table_name, filename, columns)
        except sqlite3.Error:
            continue


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
                item_desc TEXT,
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
                created_date TEXT,
                ship_date TEXT,
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
            created_date TEXT,
            ship_date TEXT,
            plant TEXT NOT NULL,
            customer TEXT,
            cust_name TEXT,
            address1 TEXT,
            address2 TEXT,
            city TEXT,
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
            last_upload_id INTEGER,
            needs_review INTEGER DEFAULT 0,
            status TEXT DEFAULT 'OPEN',
            last_seen_at TEXT,
            closed_at TEXT,
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
            planning_session_id INTEGER,
            origin_plant TEXT NOT NULL,
            destination_state TEXT NOT NULL,
            estimated_miles REAL,
            rate_per_mile REAL,
            estimated_cost REAL,
            route_provider TEXT,
            route_profile TEXT,
            route_total_miles REAL,
            route_legs_json TEXT,
            route_geometry_json TEXT,
            route_fallback INTEGER DEFAULT 0,
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
            CREATE TABLE IF NOT EXISTS load_schematic_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                load_id INTEGER NOT NULL UNIQUE,
                trailer_type TEXT NOT NULL,
                layout_json TEXT NOT NULL,
                warnings_json TEXT,
                is_invalid INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by TEXT,
                FOREIGN KEY(load_id) REFERENCES loads(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sku_specifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL UNIQUE,
                description TEXT,
                category TEXT NOT NULL,
                length_with_tongue_ft REAL NOT NULL,
                max_stack_step_deck INTEGER DEFAULT 1,
                max_stack_flat_bed INTEGER DEFAULT 1,
                notes TEXT,
                added_at TEXT,
                source TEXT,
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
                new_orders INTEGER,
                duplicate_orders INTEGER,
                changed_orders INTEGER,
                unchanged_orders INTEGER,
                reopened_orders INTEGER,
                dropped_orders INTEGER,
                mapping_rate REAL,
                unmapped_count INTEGER,
                deleted_at TEXT
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
            CREATE TABLE IF NOT EXISTS planning_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_code TEXT NOT NULL UNIQUE,
                plant_code TEXT NOT NULL,
                created_by TEXT,
                status TEXT NOT NULL DEFAULT 'DRAFT',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                archived_at TEXT,
                horizon_end TEXT,
                config_json TEXT,
                load_number_prefix TEXT,
                next_load_sequence INTEGER
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS upload_order_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upload_id INTEGER NOT NULL,
                so_num TEXT NOT NULL,
                plant TEXT NOT NULL,
                changes_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (upload_id) REFERENCES upload_history(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS app_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                page TEXT,
                planner_id TEXT,
                status TEXT NOT NULL DEFAULT 'OPEN',
                resolved_at TEXT,
                resolved_by TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        _rebuild_app_feedback_if_needed(connection)

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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS route_cache (
                cache_key TEXT PRIMARY KEY,
                provider TEXT,
                profile TEXT,
                objective TEXT,
                response_json TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS replay_eval_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                status TEXT NOT NULL DEFAULT 'RUNNING',
                created_by TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                completed_at TEXT,
                params_json TEXT,
                summary_json TEXT,
                total_rows INTEGER DEFAULT 0,
                total_days INTEGER DEFAULT 0,
                total_plants INTEGER DEFAULT 0,
                total_orders_matched INTEGER DEFAULT 0,
                total_orders_missing INTEGER DEFAULT 0,
                total_issues INTEGER DEFAULT 0
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS replay_eval_day_plant (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                date_created TEXT NOT NULL,
                plant_code TEXT NOT NULL,
                report_rows INTEGER DEFAULT 0,
                report_loads INTEGER DEFAULT 0,
                report_orders INTEGER DEFAULT 0,
                report_ref_cost REAL,
                report_ref_miles REAL,
                report_ref_avg_truck_use REAL,
                matched_orders INTEGER DEFAULT 0,
                missing_orders INTEGER DEFAULT 0,
                actual_loads INTEGER DEFAULT 0,
                actual_orders INTEGER DEFAULT 0,
                actual_avg_utilization REAL DEFAULT 0.0,
                actual_total_miles REAL DEFAULT 0.0,
                actual_total_cost REAL DEFAULT 0.0,
                optimized_loads INTEGER DEFAULT 0,
                optimized_orders INTEGER DEFAULT 0,
                optimized_strategy TEXT,
                optimized_avg_utilization REAL DEFAULT 0.0,
                optimized_total_miles REAL DEFAULT 0.0,
                optimized_total_cost REAL DEFAULT 0.0,
                delta_loads INTEGER DEFAULT 0,
                delta_avg_utilization REAL DEFAULT 0.0,
                delta_total_miles REAL DEFAULT 0.0,
                delta_total_cost REAL DEFAULT 0.0,
                delta_cost_pct REAL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (run_id) REFERENCES replay_eval_runs(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS replay_eval_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                date_created TEXT,
                plant_code TEXT,
                load_number TEXT,
                order_number TEXT,
                issue_type TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'warning',
                message TEXT NOT NULL,
                meta_json TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (run_id) REFERENCES replay_eval_runs(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS replay_eval_load_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                date_created TEXT NOT NULL,
                plant_code TEXT NOT NULL,
                scenario TEXT NOT NULL,
                load_key TEXT NOT NULL,
                order_count INTEGER DEFAULT 0,
                utilization_pct REAL DEFAULT 0.0,
                estimated_miles REAL DEFAULT 0.0,
                estimated_cost REAL DEFAULT 0.0,
                order_numbers_json TEXT,
                load_json TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (run_id) REFERENCES replay_eval_runs(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS replay_eval_source_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                date_created TEXT NOT NULL,
                plant_code TEXT NOT NULL,
                load_number TEXT NOT NULL,
                order_number TEXT NOT NULL,
                moh_est_freight_cost REAL,
                truck_use REAL,
                miles REAL,
                ship_via_date TEXT,
                full_name TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (run_id) REFERENCES replay_eval_runs(id)
            )
            """
        )

        _ensure_column(connection, "order_lines", "due_date", "due_date TEXT")
        _ensure_column(connection, "order_lines", "customer", "customer TEXT")
        _ensure_column(connection, "order_lines", "plant_full", "plant_full TEXT")
        _ensure_column(connection, "order_lines", "plant2", "plant2 TEXT")
        _ensure_column(connection, "order_lines", "plant", "plant TEXT")
        _ensure_column(connection, "order_lines", "item", "item TEXT")
        _ensure_column(connection, "order_lines", "item_desc", "item_desc TEXT")
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
        _ensure_column(connection, "order_lines", "created_date", "created_date TEXT")
        _ensure_column(connection, "order_lines", "ship_date", "ship_date TEXT")
        _ensure_column(connection, "order_lines", "sku", "sku TEXT")
        _ensure_column(connection, "order_lines", "unit_length_ft", "unit_length_ft REAL")
        _ensure_column(connection, "order_lines", "total_length_ft", "total_length_ft REAL")
        _ensure_column(connection, "order_lines", "max_stack_height", "max_stack_height INTEGER")
        _ensure_column(connection, "order_lines", "stack_position", "stack_position INTEGER DEFAULT 1")
        _ensure_column(connection, "order_lines", "utilization_pct", "utilization_pct REAL")
        _ensure_column(connection, "order_lines", "is_excluded", "is_excluded INTEGER DEFAULT 0")

        _ensure_column(connection, "orders", "so_num", "so_num TEXT")
        _ensure_column(connection, "orders", "due_date", "due_date TEXT")
        _ensure_column(connection, "orders", "created_date", "created_date TEXT")
        _ensure_column(connection, "orders", "ship_date", "ship_date TEXT")
        _ensure_column(connection, "orders", "plant", "plant TEXT")
        _ensure_column(connection, "orders", "customer", "customer TEXT")
        _ensure_column(connection, "orders", "cust_name", "cust_name TEXT")
        _ensure_column(connection, "orders", "address1", "address1 TEXT")
        _ensure_column(connection, "orders", "address2", "address2 TEXT")
        _ensure_column(connection, "orders", "city", "city TEXT")
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
        _ensure_column(connection, "orders", "last_upload_id", "last_upload_id INTEGER")
        _ensure_column(connection, "orders", "needs_review", "needs_review INTEGER DEFAULT 0")
        _ensure_column(connection, "orders", "status", "status TEXT DEFAULT 'OPEN'")
        _ensure_column(connection, "orders", "last_seen_at", "last_seen_at TEXT")
        _ensure_column(connection, "orders", "closed_at", "closed_at TEXT")

        _ensure_column(connection, "loads", "origin_plant", "origin_plant TEXT")
        _ensure_column(connection, "loads", "destination_state", "destination_state TEXT")
        _ensure_column(connection, "loads", "planning_session_id", "planning_session_id INTEGER")
        _ensure_column(connection, "loads", "estimated_miles", "estimated_miles REAL")
        _ensure_column(connection, "loads", "rate_per_mile", "rate_per_mile REAL")
        _ensure_column(connection, "loads", "estimated_cost", "estimated_cost REAL")
        _ensure_column(connection, "loads", "route_provider", "route_provider TEXT")
        _ensure_column(connection, "loads", "route_profile", "route_profile TEXT")
        _ensure_column(connection, "loads", "route_total_miles", "route_total_miles REAL")
        _ensure_column(connection, "loads", "route_legs_json", "route_legs_json TEXT")
        _ensure_column(connection, "loads", "route_geometry_json", "route_geometry_json TEXT")
        _ensure_column(connection, "loads", "route_fallback", "route_fallback INTEGER DEFAULT 0")
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
        _ensure_column(connection, "planning_sessions", "load_number_prefix", "load_number_prefix TEXT")
        _ensure_column(connection, "planning_sessions", "next_load_sequence", "next_load_sequence INTEGER")
        _ensure_column(connection, "optimizer_settings", "baseline_cost", "baseline_cost REAL")
        _ensure_column(connection, "optimizer_settings", "baseline_set_at", "baseline_set_at TEXT")
        _ensure_column(connection, "replay_eval_day_plant", "optimized_strategy", "optimized_strategy TEXT")
        _ensure_column(connection, "replay_eval_load_metrics", "load_json", "load_json TEXT")
        _ensure_column(connection, "upload_history", "new_orders", "new_orders INTEGER")
        _ensure_column(connection, "upload_history", "duplicate_orders", "duplicate_orders INTEGER")
        _ensure_column(connection, "upload_history", "changed_orders", "changed_orders INTEGER")
        _ensure_column(connection, "upload_history", "unchanged_orders", "unchanged_orders INTEGER")
        _ensure_column(connection, "upload_history", "reopened_orders", "reopened_orders INTEGER")
        _ensure_column(connection, "upload_history", "dropped_orders", "dropped_orders INTEGER")
        _ensure_column(connection, "upload_history", "deleted_at", "deleted_at TEXT")
        _ensure_column(connection, "sku_specifications", "description", "description TEXT")
        _ensure_column(connection, "sku_specifications", "added_at", "added_at TEXT")
        _ensure_column(connection, "sku_specifications", "source", "source TEXT")

        connection.execute(
            """
            UPDATE sku_specifications
            SET source = 'planner'
            WHERE source IS NULL AND COALESCE(TRIM(added_at), '') <> ''
            """
        )
        connection.execute(
            """
            UPDATE sku_specifications
            SET source = 'system'
            WHERE source IS NULL OR COALESCE(TRIM(source), '') = ''
            """
        )
        connection.execute(
            """
            UPDATE sku_specifications
            SET added_at = COALESCE(added_at, created_at)
            WHERE source = 'planner' AND COALESCE(TRIM(added_at), '') = ''
            """
        )

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
            "CREATE INDEX IF NOT EXISTS idx_order_lines_plant_due ON order_lines(plant, is_excluded, due_date)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_plant_status_so ON orders(plant, status, so_num)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_plant ON orders(plant)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_due_date ON orders(due_date)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_state ON orders(state)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(cust_name)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_load_lines_order_line ON load_lines(order_line_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_load_schematic_overrides_load_id ON load_schematic_overrides(load_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_zip_lookup ON zip_coordinates(zip)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_route_cache_expires ON route_cache(expires_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_replay_eval_runs_created_at ON replay_eval_runs(created_at DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_replay_eval_day_plant_run_date ON replay_eval_day_plant(run_id, date_created, plant_code)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_replay_eval_issues_run_type ON replay_eval_issues(run_id, issue_type)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_replay_eval_load_metrics_run_scenario ON replay_eval_load_metrics(run_id, scenario)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_replay_eval_source_rows_run_date ON replay_eval_source_rows(run_id, date_created, plant_code)"
        )
        connection.commit()
        _seed_plants(connection)
        _seed_reference_data(connection)


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
                order.get("item_desc"),
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
                order.get("created_date"),
                order.get("ship_date"),
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
                item_desc,
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
                created_date,
                ship_date,
                sku,
                unit_length_ft,
                total_length_ft,
                max_stack_height,
                stack_position,
                utilization_pct,
                is_excluded,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()


def upsert_order_lines(order_lines):
    if not order_lines:
        return
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    so_nums = sorted({str(line.get("so_num") or "").strip() for line in order_lines if str(line.get("so_num") or "").strip()})
    existing_map = {}
    with get_connection() as connection:
        if so_nums:
            for chunk in _chunked(so_nums):
                placeholders = ", ".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""
                    SELECT id, so_num, plant, item, bin, is_excluded
                    FROM order_lines
                    WHERE so_num IN ({placeholders})
                    """,
                    chunk,
                ).fetchall()
                for row in rows:
                    key = (
                        (row["so_num"] or "").strip(),
                        (row["plant"] or "").strip(),
                        (row["item"] or "").strip(),
                        (row["bin"] or "").strip(),
                    )
                    existing_map[key] = row

        update_rows = []
        insert_rows = []
        for order in order_lines:
            so_num = (order.get("so_num") or "").strip()
            plant = (order.get("plant") or "").strip()
            item = (order.get("item") or "").strip()
            bin_code = (order.get("bin") or "").strip()
            key = (so_num, plant, item, bin_code) if so_num and plant and item and bin_code else None
            existing = existing_map.get(key) if key else None
            is_excluded = 1 if (existing and existing["is_excluded"]) else (1 if order.get("is_excluded") else 0)

            if existing:
                update_rows.append(
                    (
                        order.get("due_date"),
                        order.get("customer"),
                        order.get("plant_full"),
                        order.get("plant2"),
                        order.get("plant"),
                        order.get("item"),
                        order.get("item_desc"),
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
                        order.get("created_date"),
                        order.get("ship_date"),
                        order.get("sku"),
                        order.get("unit_length_ft"),
                        order.get("total_length_ft"),
                        order.get("max_stack_height"),
                        order.get("stack_position", 1),
                        order.get("utilization_pct"),
                        is_excluded,
                        existing["id"],
                    )
                )
            else:
                insert_rows.append(
                    (
                        order.get("due_date"),
                        order.get("customer"),
                        order.get("plant_full"),
                        order.get("plant2"),
                        order.get("plant"),
                        order.get("item"),
                        order.get("item_desc"),
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
                        order.get("created_date"),
                        order.get("ship_date"),
                        order.get("sku"),
                        order.get("unit_length_ft"),
                        order.get("total_length_ft"),
                        order.get("max_stack_height"),
                        order.get("stack_position", 1),
                        order.get("utilization_pct"),
                        is_excluded,
                        created_at,
                    )
                )

        if update_rows:
            connection.executemany(
                """
                UPDATE order_lines
                SET due_date = ?,
                    customer = ?,
                    plant_full = ?,
                    plant2 = ?,
                    plant = ?,
                    item = ?,
                    item_desc = ?,
                    qty = ?,
                    sales = ?,
                    so_num = ?,
                    cust_name = ?,
                    cpo = ?,
                    salesman = ?,
                    cust_num = ?,
                    bin = ?,
                    load_num = ?,
                    address1 = ?,
                    address2 = ?,
                    city = ?,
                    state = ?,
                    zip = ?,
                    created_date = ?,
                    ship_date = ?,
                    sku = ?,
                    unit_length_ft = ?,
                    total_length_ft = ?,
                    max_stack_height = ?,
                    stack_position = ?,
                    utilization_pct = ?,
                    is_excluded = ?
                WHERE id = ?
                """,
                update_rows,
            )

        if insert_rows:
            connection.executemany(
                """
                INSERT INTO order_lines (
                    due_date,
                    customer,
                    plant_full,
                    plant2,
                    plant,
                    item,
                    item_desc,
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
                    created_date,
                    ship_date,
                    sku,
                    unit_length_ft,
                    total_length_ft,
                    max_stack_height,
                    stack_position,
                    utilization_pct,
                    is_excluded,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                insert_rows,
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
                order.get("created_date"),
                order.get("ship_date"),
                order.get("plant"),
                order.get("customer"),
                order.get("cust_name"),
                order.get("address1"),
                order.get("address2"),
                order.get("city"),
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
                order.get("status", "OPEN"),
                order.get("last_seen_at"),
                order.get("closed_at"),
                created_at,
            )
        )
    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO orders (
                so_num,
                due_date,
                created_date,
                ship_date,
                plant,
                customer,
                cust_name,
                address1,
                address2,
                city,
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
                status,
                last_seen_at,
                closed_at,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()


def upsert_orders(orders):
    if not orders:
        return
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    so_nums = sorted({str(order.get("so_num") or "").strip() for order in orders if str(order.get("so_num") or "").strip()})
    existing_map = {}
    with get_connection() as connection:
        if so_nums:
            for chunk in _chunked(so_nums):
                placeholders = ", ".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""
                    SELECT id, so_num, is_excluded, created_at
                    FROM orders
                    WHERE so_num IN ({placeholders})
                    """,
                    chunk,
                ).fetchall()
                for row in rows:
                    key = (row["so_num"] or "").strip()
                    existing_map[key] = row

        update_rows = []
        insert_rows = []
        for order in orders:
            so_num = (order.get("so_num") or "").strip()
            existing = existing_map.get(so_num)
            is_excluded = 1 if (existing and existing["is_excluded"]) else (1 if order.get("is_excluded") else 0)
            if existing:
                update_rows.append(
                    (
                        order.get("so_num"),
                        order.get("due_date"),
                        order.get("created_date"),
                        order.get("ship_date"),
                        order.get("plant"),
                        order.get("customer"),
                        order.get("cust_name"),
                        order.get("address1"),
                        order.get("address2"),
                        order.get("city"),
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
                        is_excluded,
                        order.get("status", "OPEN"),
                        order.get("last_seen_at"),
                        order.get("closed_at"),
                        existing["id"],
                    )
                )
            else:
                insert_rows.append(
                    (
                        order.get("so_num"),
                        order.get("due_date"),
                        order.get("created_date"),
                        order.get("ship_date"),
                        order.get("plant"),
                        order.get("customer"),
                        order.get("cust_name"),
                        order.get("address1"),
                        order.get("address2"),
                        order.get("city"),
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
                        is_excluded,
                        order.get("status", "OPEN"),
                        order.get("last_seen_at"),
                        order.get("closed_at"),
                        existing.get("created_at") if existing else created_at,
                    )
                )

        if update_rows:
            connection.executemany(
                """
                UPDATE orders
                SET so_num = ?,
                    due_date = ?,
                    created_date = ?,
                    ship_date = ?,
                    plant = ?,
                    customer = ?,
                    cust_name = ?,
                    address1 = ?,
                    address2 = ?,
                    city = ?,
                    state = ?,
                    zip = ?,
                    total_qty = ?,
                    total_sales = ?,
                    total_length_ft = ?,
                    utilization_pct = ?,
                    utilization_grade = ?,
                    utilization_credit_ft = ?,
                    exceeds_capacity = ?,
                    line_count = ?,
                    is_excluded = ?,
                    status = ?,
                    last_seen_at = ?,
                    closed_at = ?
                WHERE id = ?
                """,
                update_rows,
            )

        if insert_rows:
            connection.executemany(
                """
                INSERT INTO orders (
                    so_num,
                    due_date,
                    created_date,
                    ship_date,
                    plant,
                    customer,
                    cust_name,
                    address1,
                    address2,
                    city,
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
                    status,
                    last_seen_at,
                    closed_at,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                insert_rows,
            )
        connection.commit()


def list_orders_by_so_nums_any(so_nums, include_closed=True):
    if not so_nums:
        return []
    cleaned = [str(value).strip() for value in so_nums if str(value or "").strip()]
    if not cleaned:
        return []
    placeholders = ", ".join("?" for _ in cleaned)
    where_clause = f"so_num IN ({placeholders})"
    if not include_closed:
        where_clause += " AND COALESCE(UPPER(status), 'OPEN') != 'CLOSED'"
    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT * FROM orders WHERE {where_clause}",
            cleaned,
        ).fetchall()
        return [dict(row) for row in rows]


def update_orders_upload_meta(so_nums, upload_id, changed_so_nums=None):
    if not so_nums or not upload_id:
        return
    cleaned = [str(value).strip() for value in so_nums if str(value or "").strip()]
    if not cleaned:
        return
    changed_so_nums = {str(value).strip() for value in (changed_so_nums or []) if str(value or "").strip()}
    placeholders = ", ".join("?" for _ in cleaned)
    where_clause = f"so_num IN ({placeholders})"
    with get_connection() as connection:
        connection.execute(
            f"UPDATE orders SET last_upload_id = ? WHERE {where_clause}",
            [upload_id] + cleaned,
        )
        if changed_so_nums:
            change_placeholders = ", ".join("?" for _ in changed_so_nums)
            connection.execute(
                f"UPDATE orders SET needs_review = 1 WHERE so_num IN ({change_placeholders})",
                list(changed_so_nums),
            )
        connection.commit()


def mark_orders_seen(so_nums, seen_at=None):
    if not so_nums:
        return
    cleaned = [str(value).strip() for value in so_nums if str(value or "").strip()]
    if not cleaned:
        return
    placeholders = ", ".join("?" for _ in cleaned)
    timestamp = seen_at or datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            f"""
            UPDATE orders
            SET last_seen_at = ?,
                status = 'OPEN',
                closed_at = NULL
            WHERE so_num IN ({placeholders})
            """,
            [timestamp] + cleaned,
        )
        connection.commit()


def mark_orders_closed(so_nums, closed_at=None):
    if not so_nums:
        return
    cleaned = [str(value).strip() for value in so_nums if str(value or "").strip()]
    if not cleaned:
        return
    placeholders = ", ".join("?" for _ in cleaned)
    timestamp = closed_at or datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            f"""
            UPDATE orders
            SET status = 'CLOSED',
                closed_at = ?,
                needs_review = 0
            WHERE so_num IN ({placeholders})
            """,
            [timestamp] + cleaned,
        )
        connection.commit()


def list_open_order_so_nums():
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT so_num
            FROM orders
            WHERE COALESCE(UPPER(status), 'OPEN') != 'CLOSED'
            """
        ).fetchall()
        return [row["so_num"] for row in rows if row["so_num"]]


def purge_closed_orders(retention_days=30):
    try:
        days = int(retention_days)
    except (TypeError, ValueError):
        return 0
    if days <= 0:
        return 0
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat(timespec="seconds")
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT so_num
            FROM orders
            WHERE COALESCE(UPPER(status), 'OPEN') = 'CLOSED'
              AND closed_at IS NOT NULL
              AND closed_at < ?
            """,
            (cutoff,),
        ).fetchall()
        so_nums = [row["so_num"] for row in rows if row["so_num"]]
        if not so_nums:
            return 0
        placeholders = ", ".join("?" for _ in so_nums)
        connection.execute(
            f"DELETE FROM order_lines WHERE so_num IN ({placeholders})",
            so_nums,
        )
        connection.execute(
            f"DELETE FROM orders WHERE so_num IN ({placeholders})",
            so_nums,
        )
        connection.commit()
        return len(so_nums)


def add_upload_order_changes(upload_id, changes):
    if not upload_id or not changes:
        return
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    rows = []
    for entry in changes:
        rows.append(
            (
                upload_id,
                entry.get("so_num"),
                entry.get("plant"),
                entry.get("changes_json"),
                created_at,
            )
        )
    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO upload_order_changes (
                upload_id, so_num, plant, changes_json, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()


def list_upload_order_changes(upload_id, limit=None):
    if not upload_id:
        return []
    with get_connection() as connection:
        if limit:
            rows = connection.execute(
                """
                SELECT so_num, plant, changes_json, created_at
                FROM upload_order_changes
                WHERE upload_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (upload_id, limit),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT so_num, plant, changes_json, created_at
                FROM upload_order_changes
                WHERE upload_id = ?
                ORDER BY id ASC
                """,
                (upload_id,),
            ).fetchall()
        return [dict(row) for row in rows]


def list_orders(filters=None, sort_key="due_date"):
    filters = filters or {}
    where = []
    params = []

    if not filters.get("include_closed"):
        where.append("COALESCE(UPPER(status), 'OPEN') != 'CLOSED'")

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
    needs_assigned = False
    if assignment_filter == "ASSIGNED":
        where.append(assigned_clause)
        needs_assigned = True
    elif assignment_filter == "UNASSIGNED":
        where.append(f"NOT {assigned_clause}")
        needs_assigned = True
    elif filters.get("include_assigned"):
        needs_assigned = True

    assigned_select = f"{assigned_clause} AS is_assigned" if needs_assigned else "0 AS is_assigned"

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
                {assigned_select}
            FROM orders
            {where_clause}
            ORDER BY {order_by} ASC, id DESC
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]


def count_orders_by_plant(filters=None):
    filters = filters or {}
    where = ["COALESCE(UPPER(status), 'OPEN') != 'CLOSED'"]
    params = []

    plants = filters.get("plants") or []
    if plants:
        placeholders = ", ".join("?" for _ in plants)
        where.append(f"plant IN ({placeholders})")
        params.extend(plants)
    if filters.get("state"):
        where.append("state = ?")
        params.append(filters["state"])
    if filters.get("cust_name"):
        where.append("cust_name = ?")
        params.append(filters["cust_name"])

    where.append("COALESCE(is_excluded, 0) = 0")
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT plant, COUNT(*) AS order_count
            FROM orders
            {where_clause}
            GROUP BY plant
            """,
            params,
        ).fetchall()
        return {row["plant"]: row["order_count"] for row in rows if row["plant"]}


def list_order_lines_by_sonum(so_num):
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM order_lines WHERE so_num = ? ORDER BY id",
            (so_num,),
        ).fetchall()
        return [dict(row) for row in rows]


def list_order_lines_by_so_nums(so_nums):
    if not so_nums:
        return []
    cleaned = [str(value).strip() for value in so_nums if str(value or "").strip()]
    if not cleaned:
        return []
    placeholders = ", ".join("?" for _ in cleaned)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM order_lines
            WHERE so_num IN ({placeholders})
            ORDER BY so_num ASC, id ASC
            """,
            cleaned,
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


def list_orders_by_so_nums(origin_plant, so_nums):
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
            FROM orders
            WHERE is_excluded = 0
              AND plant = ?
              AND COALESCE(UPPER(status), 'OPEN') != 'CLOSED'
              AND so_num IN ({placeholders})
            ORDER BY DATE(due_date) ASC, so_num ASC
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
        "COALESCE(UPPER(orders.status), 'OPEN') != 'CLOSED'",
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


def list_order_lines_for_optimization(origin_plant, min_due_date=None):
    due_clause = ""
    params = [origin_plant]
    if min_due_date:
        due_clause = "AND DATE(ol.due_date) >= DATE(?)"
        params.append(min_due_date)
    params.append(origin_plant)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT ol.*
            FROM order_lines ol
            WHERE ol.is_excluded = 0
              AND ol.plant = ?
              {due_clause}
              AND ol.so_num IN (
                SELECT so_num
                FROM orders
                WHERE plant = ?
                  AND COALESCE(UPPER(status), 'OPEN') != 'CLOSED'
              )
              AND NOT EXISTS (
                SELECT 1
                FROM load_lines ll
                JOIN loads l ON l.id = ll.load_id
                LEFT JOIN planning_sessions ps ON ps.id = l.planning_session_id
                WHERE ll.order_line_id = ol.id
                  AND (
                    COALESCE(UPPER(l.status), '') = 'APPROVED'
                      OR COALESCE(UPPER(ps.status), '') IN ('DRAFT', 'ACTIVE')
                  )
              )
            ORDER BY ol.due_date ASC, ol.id ASC
            """,
            params,
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
              AND COALESCE(UPPER(status), 'OPEN') != 'CLOSED'
            ORDER BY due_date ASC, id ASC
            """,
            (origin_plant,),
        ).fetchall()
        return [dict(row) for row in rows]


def list_orders_by_ids(order_ids):
    cleaned = []
    seen = set()
    for value in order_ids or []:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed <= 0 or parsed in seen:
            continue
        seen.add(parsed)
        cleaned.append(parsed)
    if not cleaned:
        return []

    placeholders = ", ".join("?" for _ in cleaned)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT id, so_num, plant, is_excluded, status
            FROM orders
            WHERE id IN ({placeholders})
            """,
            cleaned,
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


def include_orders_for_plants(plants=None):
    cleaned = []
    if plants:
        seen = set()
        for plant in plants:
            code = (plant or "").strip().upper()
            if not code or code in seen:
                continue
            seen.add(code)
            cleaned.append(code)

    with get_connection() as connection:
        if cleaned:
            placeholders = ", ".join("?" for _ in cleaned)
            connection.execute(
                f"UPDATE orders SET is_excluded = 0 WHERE plant IN ({placeholders})",
                cleaned,
            )
            connection.execute(
                f"UPDATE order_lines SET is_excluded = 0 WHERE plant IN ({placeholders})",
                cleaned,
            )
        else:
            connection.execute("UPDATE orders SET is_excluded = 0")
            connection.execute("UPDATE order_lines SET is_excluded = 0")
        connection.commit()


def list_sku_specs():
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, sku, description, category, length_with_tongue_ft, max_stack_step_deck,
                   max_stack_flat_bed, notes, added_at, source, created_at
            FROM sku_specifications
            ORDER BY sku ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def upsert_sku_spec(spec):
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    added_at = spec.get("added_at") or created_at
    source = (spec.get("source") or "planner").strip().lower()
    if source not in {"planner", "system"}:
        source = "planner"
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO sku_specifications (
                sku, description, category, length_with_tongue_ft, max_stack_step_deck,
                max_stack_flat_bed, notes, added_at, created_at, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sku) DO UPDATE SET
                category = excluded.category,
                length_with_tongue_ft = excluded.length_with_tongue_ft,
                max_stack_step_deck = excluded.max_stack_step_deck,
                max_stack_flat_bed = excluded.max_stack_flat_bed,
                description = excluded.description,
                notes = excluded.notes,
                added_at = COALESCE(sku_specifications.added_at, excluded.added_at),
                source = COALESCE(sku_specifications.source, excluded.source)
            """,
            (
                spec.get("sku"),
                spec.get("description"),
                spec.get("category"),
                spec.get("length_with_tongue_ft"),
                spec.get("max_stack_step_deck", 1),
                spec.get("max_stack_flat_bed", 1),
                spec.get("notes"),
                added_at,
                created_at,
                source,
            ),
        )
        connection.commit()


def update_sku_spec(spec_id, spec):
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE sku_specifications
            SET sku = ?,
                description = ?,
                category = ?,
                length_with_tongue_ft = ?,
                max_stack_step_deck = ?,
                max_stack_flat_bed = ?,
                notes = ?
            WHERE id = ?
            """,
            (
                spec.get("sku"),
                spec.get("description"),
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
            ORDER BY
                CASE WHEN plant_code = 'CL' THEN 1 ELSE 0 END ASC,
                plant_code ASC
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
                filename, total_rows, total_orders, new_orders, duplicate_orders, changed_orders,
                unchanged_orders, reopened_orders, dropped_orders, mapping_rate, unmapped_count, deleted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.get("filename"),
                entry.get("total_rows"),
                entry.get("total_orders"),
                entry.get("new_orders"),
                entry.get("duplicate_orders"),
                entry.get("changed_orders"),
                entry.get("unchanged_orders"),
                entry.get("reopened_orders"),
                entry.get("dropped_orders"),
                entry.get("mapping_rate"),
                entry.get("unmapped_count"),
                entry.get("deleted_at"),
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
                   new_orders, duplicate_orders, changed_orders, unchanged_orders, reopened_orders, dropped_orders,
                   mapping_rate, unmapped_count, deleted_at
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
                       new_orders, duplicate_orders, changed_orders, unchanged_orders, reopened_orders, dropped_orders,
                       mapping_rate, unmapped_count, deleted_at
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
                       new_orders, duplicate_orders, changed_orders, unchanged_orders, reopened_orders, dropped_orders,
                       mapping_rate, unmapped_count, deleted_at
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


def _safe_json_loads(value, default):
    if value in {None, ""}:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _decode_load_route_fields(load):
    if not load:
        return load
    load["route_legs"] = _safe_json_loads(load.get("route_legs_json"), [])
    load["route_geometry"] = _safe_json_loads(load.get("route_geometry_json"), [])
    return load


def list_loads(origin_plant=None, session_id=None):
    where = []
    params = []
    if origin_plant:
        where.append("origin_plant = ?")
        params.append(origin_plant)
    if session_id:
        where.append("planning_session_id = ?")
        params.append(session_id)
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                id,
                load_number,
                draft_sequence,
                planning_session_id,
                origin_plant,
                destination_state,
                estimated_miles,
                rate_per_mile,
                estimated_cost,
                route_provider,
                route_profile,
                route_total_miles,
                route_legs_json,
                route_geometry_json,
                route_fallback,
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


def create_planning_session(
    session_code,
    plant_code,
    created_by,
    config_json,
    horizon_end=None,
    status="DRAFT",
    created_at=None,
):
    created_at = created_at or datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO planning_sessions (
                session_code,
                plant_code,
                created_by,
                status,
                created_at,
                horizon_end,
                config_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_code,
                plant_code,
                created_by,
                status,
                created_at,
                horizon_end,
                config_json,
            ),
        )
        connection.commit()
        return cursor.lastrowid


def mark_upload_history_deleted(upload_ids=None, deleted_at=None):
    timestamp = deleted_at or datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as connection:
        if upload_ids:
            cleaned = [str(value).strip() for value in upload_ids if str(value or "").strip()]
            if not cleaned:
                return 0
            placeholders = ", ".join("?" for _ in cleaned)
            cursor = connection.execute(
                f"UPDATE upload_history SET deleted_at = ? WHERE id IN ({placeholders})",
                [timestamp] + cleaned,
            )
        else:
            cursor = connection.execute(
                "UPDATE upload_history SET deleted_at = ? WHERE deleted_at IS NULL",
                (timestamp,),
            )
        connection.commit()
        return cursor.rowcount
        return cursor.lastrowid


def archive_planning_session(session_id):
    if not session_id:
        return
    archived_at = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE planning_sessions
            SET status = 'ARCHIVED',
                archived_at = ?
            WHERE id = ?
            """,
            (archived_at, session_id),
        )
        connection.commit()


def delete_planning_session(session_id, clear_loads=True):
    if not session_id:
        return
    with get_connection() as connection:
        if clear_loads:
            connection.execute(
                "DELETE FROM load_schematic_overrides WHERE load_id IN (SELECT id FROM loads WHERE planning_session_id = ?)",
                (session_id,),
            )
            connection.execute(
                "DELETE FROM load_lines WHERE load_id IN (SELECT id FROM loads WHERE planning_session_id = ?)",
                (session_id,),
            )
            connection.execute(
                "DELETE FROM loads WHERE planning_session_id = ?",
                (session_id,),
            )
        connection.execute(
            "DELETE FROM planning_sessions WHERE id = ?",
            (session_id,),
        )
        connection.commit()


def compute_planning_session_status(session_id):
    if not session_id:
        return None
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total_loads,
                SUM(CASE WHEN UPPER(status) = 'APPROVED' THEN 1 ELSE 0 END) AS approved_loads
            FROM loads
            WHERE planning_session_id = ?
            """,
            (session_id,),
        ).fetchone()
        if not row:
            return "DRAFT"
        total = row["total_loads"] or 0
        approved = row["approved_loads"] or 0
        if total > 0 and total == approved:
            return "COMPLETED"
        return "DRAFT"


def update_planning_session_status(session_id, status):
    if not session_id or not status:
        return
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE planning_sessions
            SET status = ?
            WHERE id = ?
            """,
            (status, session_id),
        )
        connection.commit()


def get_planning_session(session_id):
    if not session_id:
        return None
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM planning_sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        return dict(row) if row else None


def get_planning_session_by_code(session_code):
    if not session_code:
        return None
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM planning_sessions
            WHERE session_code = ?
            """,
            (session_code,),
        ).fetchone()
        return dict(row) if row else None


def count_planning_sessions_for_day(created_by, plant_code, date_value):
    if not created_by or not plant_code or not date_value:
        return 0
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM planning_sessions
            WHERE created_by = ?
              AND plant_code = ?
              AND DATE(created_at) = DATE(?)
            """,
            (created_by, plant_code, date_value),
        ).fetchone()
        return int(row["total"] or 0) if row else 0


def list_planning_sessions(filters=None):
    filters = filters or {}
    where = []
    params = []
    if filters.get("plant_code"):
        where.append("ps.plant_code = ?")
        params.append(filters["plant_code"])
    if filters.get("created_by"):
        where.append("ps.created_by = ?")
        params.append(filters["created_by"])
    if filters.get("status"):
        where.append("ps.status = ?")
        params.append(filters["status"])
    if filters.get("start_date"):
        where.append("DATE(ps.created_at) >= DATE(?)")
        params.append(filters["start_date"])
    if filters.get("end_date"):
        where.append("DATE(ps.created_at) <= DATE(?)")
        params.append(filters["end_date"])
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                ps.*,
                COUNT(DISTINCT l.id) AS load_count,
                AVG(l.utilization_pct) AS avg_utilization,
                COUNT(DISTINCT ol.so_num) AS order_count,
                SUM(l.estimated_cost) AS total_spend
            FROM planning_sessions ps
            LEFT JOIN loads l ON l.planning_session_id = ps.id
            LEFT JOIN load_lines ll ON ll.load_id = l.id
            LEFT JOIN order_lines ol ON ol.id = ll.order_line_id
            {where_clause}
            GROUP BY ps.id
            ORDER BY ps.created_at DESC
            """,
            params,
        ).fetchall()
        return [_decode_load_route_fields(dict(row)) for row in rows]

def create_load(load, connection=None):
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    params = (
        load.get("load_number"),
        load.get("draft_sequence"),
        load.get("planning_session_id"),
        load.get("origin_plant"),
        load.get("destination_state"),
        load.get("estimated_miles"),
        load.get("rate_per_mile"),
        load.get("estimated_cost"),
        load.get("route_provider"),
        load.get("route_profile"),
        load.get("route_total_miles"),
        json.dumps(load.get("route_legs") or []),
        json.dumps(load.get("route_geometry") or []),
        1 if load.get("route_fallback") else 0,
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
    )
    if connection is not None:
        cursor = connection.execute(
            """
            INSERT INTO loads (
                load_number,
                draft_sequence,
                planning_session_id,
                origin_plant,
                destination_state,
                estimated_miles,
                rate_per_mile,
                estimated_cost,
                route_provider,
                route_profile,
                route_total_miles,
                route_legs_json,
                route_geometry_json,
                route_fallback,
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        return cursor.lastrowid

    with get_connection() as inner_connection:
        cursor = inner_connection.execute(
            """
            INSERT INTO loads (
                load_number,
                draft_sequence,
                planning_session_id,
                origin_plant,
                destination_state,
                estimated_miles,
                rate_per_mile,
                estimated_cost,
                route_provider,
                route_profile,
                route_total_miles,
                route_legs_json,
                route_geometry_json,
                route_fallback,
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        inner_connection.commit()
        return cursor.lastrowid


def create_load_line(load_id, order_line_id, line_total_feet, connection=None):
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    if connection is not None:
        connection.execute(
            """
            INSERT INTO load_lines (load_id, order_line_id, line_total_feet, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (load_id, order_line_id, line_total_feet, created_at),
        )
        return

    with get_connection() as inner_connection:
        inner_connection.execute(
            """
            INSERT INTO load_lines (load_id, order_line_id, line_total_feet, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (load_id, order_line_id, line_total_feet, created_at),
        )
        inner_connection.commit()


def list_load_lines(load_id):
    lines_by_load = list_load_lines_for_load_ids([load_id])
    return lines_by_load.get(load_id, [])


def list_load_lines_for_load_ids(load_ids):
    cleaned_ids = []
    for value in load_ids or []:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            cleaned_ids.append(parsed)
    if not cleaned_ids:
        return {}

    placeholders = ", ".join("?" for _ in cleaned_ids)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                load_lines.load_id,
                load_lines.id,
                load_lines.order_line_id,
                load_lines.line_total_feet,
                order_lines.due_date,
                order_lines.sales,
                order_lines.plant,
                order_lines.item,
                order_lines.item_desc,
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
            WHERE load_lines.load_id IN ({placeholders})
            ORDER BY load_lines.load_id, load_lines.id
            """,
            cleaned_ids,
        ).fetchall()

    grouped = {load_id: [] for load_id in cleaned_ids}
    for row in rows:
        row_dict = dict(row)
        row_load_id = row_dict.pop("load_id", None)
        if row_load_id is None:
            continue
        grouped.setdefault(row_load_id, []).append(row_dict)
    return grouped


def get_load(load_id):
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM loads WHERE id = ?",
            (load_id,),
        ).fetchone()
        if not row:
            return None
        return _decode_load_route_fields(dict(row))


def update_load_build_source(load_id, build_source):
    with get_connection() as connection:
        connection.execute(
            "UPDATE loads SET build_source = ? WHERE id = ?",
            (build_source, load_id),
        )
        connection.commit()


def update_load_route_data(load_id, route_data):
    if not load_id or not route_data:
        return
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE loads
            SET route_provider = ?,
                route_profile = ?,
                route_total_miles = ?,
                route_legs_json = ?,
                route_geometry_json = ?,
                route_fallback = ?
            WHERE id = ?
            """,
            (
                route_data.get("route_provider"),
                route_data.get("route_profile"),
                route_data.get("route_total_miles"),
                json.dumps(route_data.get("route_legs") or []),
                json.dumps(route_data.get("route_geometry") or []),
                1 if route_data.get("route_fallback") else 0,
                load_id,
            ),
        )
        connection.commit()


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


def reserve_planning_session_load_number(
    session_id,
    plant_code,
    year_suffix,
    starting_sequence=None,
):
    if not session_id or not plant_code or not year_suffix:
        return {"error": "invalid_arguments"}
    prefix = f"{str(plant_code).strip().upper()}{str(year_suffix).strip()}"
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT load_number_prefix, next_load_sequence
            FROM planning_sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if not row:
            return {"error": "session_not_found"}

        stored_prefix = (row["load_number_prefix"] or "").strip().upper()
        next_sequence = row["next_load_sequence"]

        if next_sequence is None:
            if starting_sequence is None:
                return {
                    "needs_start": True,
                    "prefix": f"{prefix}-",
                }
            assigned_sequence = int(starting_sequence)
            next_sequence = assigned_sequence + 1
            connection.execute(
                """
                UPDATE planning_sessions
                SET load_number_prefix = ?,
                    next_load_sequence = ?
                WHERE id = ?
                """,
                (prefix, next_sequence, session_id),
            )
            connection.commit()
            return {
                "load_number": f"{prefix}-{assigned_sequence:04d}",
                "assigned_sequence": assigned_sequence,
                "next_sequence": next_sequence,
                "prefix": f"{prefix}-",
            }

        assigned_sequence = int(next_sequence)
        effective_prefix = stored_prefix or prefix
        connection.execute(
            """
            UPDATE planning_sessions
            SET load_number_prefix = ?,
                next_load_sequence = ?
            WHERE id = ?
            """,
            (effective_prefix, assigned_sequence + 1, session_id),
        )
        connection.commit()
        return {
            "load_number": f"{effective_prefix}-{assigned_sequence:04d}",
            "assigned_sequence": assigned_sequence,
            "next_sequence": assigned_sequence + 1,
            "prefix": f"{effective_prefix}-",
        }


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


def get_load_schematic_override(load_id):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                id,
                load_id,
                trailer_type,
                layout_json,
                warnings_json,
                is_invalid,
                created_at,
                updated_at,
                updated_by
            FROM load_schematic_overrides
            WHERE load_id = ?
            LIMIT 1
            """,
            (load_id,),
        ).fetchone()
        return dict(row) if row else None


def upsert_load_schematic_override(
    load_id,
    trailer_type,
    layout_json,
    warnings_json=None,
    is_invalid=False,
    updated_by=None,
):
    now = datetime.utcnow().isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO load_schematic_overrides (
                load_id,
                trailer_type,
                layout_json,
                warnings_json,
                is_invalid,
                created_at,
                updated_at,
                updated_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(load_id) DO UPDATE SET
                trailer_type = excluded.trailer_type,
                layout_json = excluded.layout_json,
                warnings_json = excluded.warnings_json,
                is_invalid = excluded.is_invalid,
                updated_at = excluded.updated_at,
                updated_by = excluded.updated_by
            """,
            (
                load_id,
                trailer_type,
                layout_json,
                warnings_json,
                1 if is_invalid else 0,
                now,
                now,
                updated_by,
            ),
        )
        connection.commit()


def delete_load_schematic_override(load_id):
    with get_connection() as connection:
        connection.execute(
            "DELETE FROM load_schematic_overrides WHERE load_id = ?",
            (load_id,),
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


def add_app_feedback(category, title, message, page=None, planner_id=None):
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO app_feedback (
                category, title, message, page, planner_id
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (category, title, message, page, planner_id),
        )
        connection.commit()


def list_app_feedback(filters=None, limit=None):
    filters = filters or {}
    clauses = []
    params = []

    start_date = filters.get("start_date")
    end_date = filters.get("end_date")
    if start_date:
        clauses.append("DATE(created_at) >= DATE(?)")
        params.append(start_date)
    if end_date:
        clauses.append("DATE(created_at) <= DATE(?)")
        params.append(end_date)

    planner_id = filters.get("planner_id")
    if planner_id:
        clauses.append("planner_id = ?")
        params.append(planner_id)

    category = filters.get("category")
    if category:
        clauses.append("category = ?")
        params.append(category)

    status = filters.get("status")
    if status:
        clauses.append("status = ?")
        params.append(status)

    search = (filters.get("search") or "").strip()
    if search:
        clauses.append("(title LIKE ? OR message LIKE ? OR page LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sort_key = (filters.get("sort") or "timestamp_desc").strip().lower()
    sort_map = {
        "timestamp_desc": "created_at DESC",
        "timestamp_asc": "created_at ASC",
        "planner": "planner_id ASC, created_at DESC",
        "status": "status ASC, created_at DESC",
    }
    order_clause = sort_map.get(sort_key, "created_at DESC")
    limit_clause = f"LIMIT {int(limit)}" if limit else ""

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM app_feedback
            {where_clause}
            ORDER BY {order_clause}
            {limit_clause}
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def resolve_app_feedback(feedback_id, resolved_by=None):
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE app_feedback
            SET status = 'RESOLVED',
                resolved_at = datetime('now'),
                resolved_by = ?
            WHERE id = ?
            """,
            (resolved_by, feedback_id),
        )
        connection.commit()


def list_app_feedback_filter_options():
    with get_connection() as connection:
        planners = [
            row["planner_id"]
            for row in connection.execute(
                "SELECT DISTINCT planner_id FROM app_feedback WHERE planner_id IS NOT NULL"
            ).fetchall()
        ]
        categories = [
            row["category"]
            for row in connection.execute(
                "SELECT DISTINCT category FROM app_feedback WHERE category IS NOT NULL"
            ).fetchall()
        ]
        statuses = [
            row["status"]
            for row in connection.execute(
                "SELECT DISTINCT status FROM app_feedback WHERE status IS NOT NULL"
            ).fetchall()
        ]
    return {
        "planners": sorted({value for value in planners if value}),
        "categories": sorted({value for value in categories if value}),
        "statuses": sorted({value for value in statuses if value}),
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
        connection.execute(
            "DELETE FROM load_schematic_overrides WHERE load_id = ?",
            (load_id,),
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
        connection.execute(
            "DELETE FROM load_schematic_overrides WHERE load_id = ?",
            (load_id,),
        )
        connection.execute("DELETE FROM load_lines WHERE load_id = ?", (load_id,))
        connection.execute("DELETE FROM loads WHERE id = ?", (load_id,))
        connection.commit()

def clear_loads_for_plant(origin_plant=None):
    with get_connection() as connection:
        if origin_plant:
            connection.execute(
                """
                DELETE FROM load_schematic_overrides
                WHERE load_id IN (SELECT id FROM loads WHERE origin_plant = ?)
                """,
                (origin_plant,),
            )
            connection.execute(
                "DELETE FROM load_lines WHERE load_id IN (SELECT id FROM loads WHERE origin_plant = ?)",
                (origin_plant,),
            )
            connection.execute(
                "DELETE FROM loads WHERE origin_plant = ?",
                (origin_plant,),
            )
        else:
            connection.execute("DELETE FROM load_schematic_overrides")
            connection.execute("DELETE FROM load_lines")
            connection.execute("DELETE FROM loads")
        connection.commit()


def clear_loads_for_session(session_id):
    if not session_id:
        return
    with get_connection() as connection:
        connection.execute(
            "DELETE FROM load_schematic_overrides WHERE load_id IN (SELECT id FROM loads WHERE planning_session_id = ?)",
            (session_id,),
        )
        connection.execute(
            "DELETE FROM load_lines WHERE load_id IN (SELECT id FROM loads WHERE planning_session_id = ?)",
            (session_id,),
        )
        connection.execute(
            "DELETE FROM loads WHERE planning_session_id = ?",
            (session_id,),
        )
        connection.commit()


def list_loads_without_session():
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, origin_plant, created_at
            FROM loads
            WHERE planning_session_id IS NULL
            """
        ).fetchall()
        return [dict(row) for row in rows]


def assign_loads_to_session(session_id, load_ids):
    if not session_id or not load_ids:
        return
    cleaned = [int(value) for value in load_ids if value]
    if not cleaned:
        return
    placeholders = ", ".join("?" for _ in cleaned)
    params = [session_id] + cleaned
    with get_connection() as connection:
        connection.execute(
            f"UPDATE loads SET planning_session_id = ? WHERE id IN ({placeholders})",
            params,
        )
        connection.commit()


def clear_draft_loads(origin_plant=None, session_id=None):
    where_clause = "status = 'PROPOSED' AND COALESCE(UPPER(build_source), 'OPTIMIZED') != 'MANUAL'"
    params = []
    if session_id:
        where_clause += " AND planning_session_id = ?"
        params.append(session_id)
    elif origin_plant:
        where_clause += " AND origin_plant = ?"
        params.append(origin_plant)

    with get_connection() as connection:
        connection.execute(
            f"DELETE FROM load_schematic_overrides WHERE load_id IN (SELECT id FROM loads WHERE {where_clause})",
            params,
        )
        connection.execute(
            f"DELETE FROM load_lines WHERE load_id IN (SELECT id FROM loads WHERE {where_clause})",
            params,
        )
        connection.execute(
            f"DELETE FROM loads WHERE {where_clause}",
            params,
        )
        connection.commit()


def clear_unapproved_loads(origin_plant=None, session_id=None):
    where_clause = (
        "COALESCE(UPPER(status), '') != 'APPROVED'"
        " AND COALESCE(UPPER(build_source), 'OPTIMIZED') != 'MANUAL'"
    )
    params = []
    if session_id:
        where_clause += " AND planning_session_id = ?"
        params.append(session_id)
    elif origin_plant:
        where_clause += " AND origin_plant = ?"
        params.append(origin_plant)

    with get_connection() as connection:
        connection.execute(
            f"DELETE FROM load_schematic_overrides WHERE load_id IN (SELECT id FROM loads WHERE {where_clause})",
            params,
        )
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
        connection.execute("DELETE FROM load_schematic_overrides")
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
        ON CONFLICT(plant_code) DO NOTHING
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


def get_route_cache(cache_key):
    key = (cache_key or "").strip()
    if not key:
        return None
    now = datetime.utcnow().isoformat(timespec="seconds")
    try:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT response_json, expires_at
                FROM route_cache
                WHERE cache_key = ?
                """,
                (key,),
            ).fetchone()
            if not row:
                return None
            expires_at = row["expires_at"] or ""
            if expires_at and expires_at <= now:
                connection.execute("DELETE FROM route_cache WHERE cache_key = ?", (key,))
                connection.commit()
                return None
            return _safe_json_loads(row["response_json"], None)
    except sqlite3.Error:
        return None


def upsert_route_cache(cache_key, response, provider=None, profile=None, objective=None, ttl_days=30):
    key = (cache_key or "").strip()
    if not key:
        return
    now_dt = datetime.utcnow()
    ttl_value = max(int(ttl_days or 0), 1)
    expires_at = (now_dt + timedelta(days=ttl_value)).isoformat(timespec="seconds")
    now = now_dt.isoformat(timespec="seconds")
    payload = json.dumps(response or {})
    try:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO route_cache (
                    cache_key,
                    provider,
                    profile,
                    objective,
                    response_json,
                    expires_at,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    provider = excluded.provider,
                    profile = excluded.profile,
                    objective = excluded.objective,
                    response_json = excluded.response_json,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    provider,
                    profile,
                    objective,
                    payload,
                    expires_at,
                    now,
                    now,
                ),
            )
            connection.execute("DELETE FROM route_cache WHERE expires_at <= ?", (now,))
            connection.commit()
    except sqlite3.Error:
        return


def create_replay_eval_run(entry):
    entry = entry or {}
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO replay_eval_runs (
                filename,
                status,
                created_by,
                created_at,
                params_json,
                summary_json,
                total_rows,
                total_days,
                total_plants,
                total_orders_matched,
                total_orders_missing,
                total_issues
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.get("filename"),
                entry.get("status") or "RUNNING",
                entry.get("created_by"),
                entry.get("created_at") or datetime.utcnow().isoformat(timespec="seconds"),
                entry.get("params_json"),
                entry.get("summary_json"),
                int(entry.get("total_rows") or 0),
                int(entry.get("total_days") or 0),
                int(entry.get("total_plants") or 0),
                int(entry.get("total_orders_matched") or 0),
                int(entry.get("total_orders_missing") or 0),
                int(entry.get("total_issues") or 0),
            ),
        )
        connection.commit()
        return cursor.lastrowid


def update_replay_eval_run(run_id, updates):
    if not run_id or not updates:
        return
    allowed = {
        "filename",
        "status",
        "created_by",
        "created_at",
        "completed_at",
        "params_json",
        "summary_json",
        "total_rows",
        "total_days",
        "total_plants",
        "total_orders_matched",
        "total_orders_missing",
        "total_issues",
    }
    fields = []
    params = []
    for key, value in updates.items():
        if key not in allowed:
            continue
        fields.append(f"{key} = ?")
        if key in {
            "total_rows",
            "total_days",
            "total_plants",
            "total_orders_matched",
            "total_orders_missing",
            "total_issues",
        }:
            params.append(int(value or 0))
        else:
            params.append(value)
    if not fields:
        return
    params.append(run_id)
    with get_connection() as connection:
        connection.execute(
            f"UPDATE replay_eval_runs SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        connection.commit()


def get_replay_eval_run(run_id):
    if not run_id:
        return None
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM replay_eval_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        return dict(row) if row else None


def list_replay_eval_runs(limit=50):
    limit = int(limit or 50)
    if limit <= 0:
        limit = 50
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM replay_eval_runs
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def add_replay_eval_day_plant(run_id, rows):
    if not run_id or not rows:
        return
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    payload = []
    for row in rows:
        payload.append(
            (
                run_id,
                row.get("date_created"),
                row.get("plant_code"),
                int(row.get("report_rows") or 0),
                int(row.get("report_loads") or 0),
                int(row.get("report_orders") or 0),
                row.get("report_ref_cost"),
                row.get("report_ref_miles"),
                row.get("report_ref_avg_truck_use"),
                int(row.get("matched_orders") or 0),
                int(row.get("missing_orders") or 0),
                int(row.get("actual_loads") or 0),
                int(row.get("actual_orders") or 0),
                float(row.get("actual_avg_utilization") or 0.0),
                float(row.get("actual_total_miles") or 0.0),
                float(row.get("actual_total_cost") or 0.0),
                int(row.get("optimized_loads") or 0),
                int(row.get("optimized_orders") or 0),
                row.get("optimized_strategy"),
                float(row.get("optimized_avg_utilization") or 0.0),
                float(row.get("optimized_total_miles") or 0.0),
                float(row.get("optimized_total_cost") or 0.0),
                int(row.get("delta_loads") or 0),
                float(row.get("delta_avg_utilization") or 0.0),
                float(row.get("delta_total_miles") or 0.0),
                float(row.get("delta_total_cost") or 0.0),
                row.get("delta_cost_pct"),
                created_at,
            )
        )
    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO replay_eval_day_plant (
                run_id,
                date_created,
                plant_code,
                report_rows,
                report_loads,
                report_orders,
                report_ref_cost,
                report_ref_miles,
                report_ref_avg_truck_use,
                matched_orders,
                missing_orders,
                actual_loads,
                actual_orders,
                actual_avg_utilization,
                actual_total_miles,
                actual_total_cost,
                optimized_loads,
                optimized_orders,
                optimized_strategy,
                optimized_avg_utilization,
                optimized_total_miles,
                optimized_total_cost,
                delta_loads,
                delta_avg_utilization,
                delta_total_miles,
                delta_total_cost,
                delta_cost_pct,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        connection.commit()


def list_replay_eval_day_plant(run_id):
    if not run_id:
        return []
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM replay_eval_day_plant
            WHERE run_id = ?
            ORDER BY date_created ASC, plant_code ASC
            """,
            (run_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def add_replay_eval_issues(run_id, issues):
    if not run_id or not issues:
        return
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    rows = []
    for issue in issues:
        rows.append(
            (
                run_id,
                issue.get("date_created"),
                issue.get("plant_code"),
                issue.get("load_number"),
                issue.get("order_number"),
                issue.get("issue_type") or "unknown",
                issue.get("severity") or "warning",
                issue.get("message") or "",
                issue.get("meta_json"),
                created_at,
            )
        )
    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO replay_eval_issues (
                run_id,
                date_created,
                plant_code,
                load_number,
                order_number,
                issue_type,
                severity,
                message,
                meta_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()


def list_replay_eval_issues(run_id, issue_type=None):
    if not run_id:
        return []
    params = [run_id]
    where = ["run_id = ?"]
    if issue_type:
        where.append("issue_type = ?")
        params.append(issue_type)
    where_clause = " AND ".join(where)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM replay_eval_issues
            WHERE {where_clause}
            ORDER BY date_created ASC, plant_code ASC, load_number ASC, order_number ASC, id ASC
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]


def add_replay_eval_load_metrics(run_id, rows):
    if not run_id or not rows:
        return
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    payload = []
    for row in rows:
        payload.append(
            (
                run_id,
                row.get("date_created"),
                row.get("plant_code"),
                row.get("scenario"),
                row.get("load_key"),
                int(row.get("order_count") or 0),
                float(row.get("utilization_pct") or 0.0),
                float(row.get("estimated_miles") or 0.0),
                float(row.get("estimated_cost") or 0.0),
                row.get("order_numbers_json"),
                row.get("load_json"),
                created_at,
            )
        )
    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO replay_eval_load_metrics (
                run_id,
                date_created,
                plant_code,
                scenario,
                load_key,
                order_count,
                utilization_pct,
                estimated_miles,
                estimated_cost,
                order_numbers_json,
                load_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        connection.commit()


def add_replay_eval_source_rows(run_id, rows):
    if not run_id or not rows:
        return
    created_at = datetime.utcnow().isoformat(timespec="seconds")
    payload = []
    for row in rows:
        payload.append(
            (
                run_id,
                row.get("date_created"),
                row.get("plant_code"),
                row.get("load_number"),
                row.get("order_number"),
                row.get("moh_est_freight_cost"),
                row.get("truck_use"),
                row.get("miles"),
                row.get("ship_via_date"),
                row.get("full_name"),
                created_at,
            )
        )
    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO replay_eval_source_rows (
                run_id,
                date_created,
                plant_code,
                load_number,
                order_number,
                moh_est_freight_cost,
                truck_use,
                miles,
                ship_via_date,
                full_name,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        connection.commit()


def list_replay_eval_source_rows(run_id, date_created=None, plant_code=None):
    if not run_id:
        return []
    where = ["run_id = ?"]
    params = [run_id]
    if date_created:
        where.append("date_created = ?")
        params.append(str(date_created))
    if plant_code:
        where.append("plant_code = ?")
        params.append(str(plant_code).strip().upper())
    where_clause = " AND ".join(where)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM replay_eval_source_rows
            WHERE {where_clause}
            ORDER BY date_created ASC, plant_code ASC, load_number ASC, order_number ASC, id ASC
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]


def list_replay_eval_load_metrics(run_id, scenario=None):
    if not run_id:
        return []
    where = ["run_id = ?"]
    params = [run_id]
    if scenario:
        where.append("scenario = ?")
        params.append(str(scenario).upper())
    where_clause = " AND ".join(where)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM replay_eval_load_metrics
            WHERE {where_clause}
            ORDER BY date_created ASC, plant_code ASC, scenario ASC, load_key ASC, id ASC
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]
