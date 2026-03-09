import argparse
import csv
import os
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = Path(os.environ.get("APP_DB_PATH", str(ROOT / "data" / "db" / "app.db")))
DEFAULT_SEED_DIR = Path(os.environ.get("APP_SEED_DIR", str(ROOT / "data" / "seed")))

TABLES = {
    "plants": {
        "columns": ["plant_code", "name", "lat", "lng", "address", "created_at"],
        "order_by": "plant_code ASC",
    },
    "sku_specifications": {
        "columns": [
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
        "order_by": "sku ASC",
    },
    "item_sku_lookup": {
        "columns": ["plant", "bin", "item_pattern", "sku", "created_at"],
        "order_by": "plant ASC, bin ASC, item_pattern ASC, sku ASC",
    },
    "rate_matrix": {
        "columns": [
            "origin_plant",
            "destination_state",
            "rate_per_mile",
            "effective_year",
            "notes",
            "created_at",
        ],
        "order_by": "origin_plant ASC, destination_state ASC, effective_year DESC",
    },
    "planning_settings": {
        "columns": ["key", "value_text", "updated_at"],
        "order_by": "key ASC",
    },
    "access_profiles": {
        "columns": ["name", "is_admin", "is_sandbox", "allowed_plants", "default_plants", "created_at"],
        "order_by": "is_admin DESC, name ASC",
    },
    "zip_coordinates": {
        "columns": ["zip", "lat", "lng", "city", "state", "created_at"],
        "order_by": "zip ASC",
    },
    "optimizer_settings": {
        "columns": [
            "plant_code",
            "capacity_feet",
            "trailer_type",
            "max_detour_pct",
            "time_window_days",
            "geo_radius",
            "auto_hotshot_enabled",
            "baseline_cost",
            "baseline_set_at",
            "updated_at",
        ],
        "order_by": "plant_code ASC",
    },
}


def _export_table(cursor, seed_dir, table_name, columns, order_by):
    query = f"SELECT {', '.join(columns)} FROM {table_name}"
    if order_by:
        query += f" ORDER BY {order_by}"
    rows = cursor.execute(query).fetchall()

    path = seed_dir / f"{table_name}.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row[idx] for idx in range(len(columns))])
    return len(rows)


def _parse_args():
    parser = argparse.ArgumentParser(description="Export SQLite reference/settings tables to data/seed CSV files.")
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help=f"Path to SQLite DB (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--seed-dir",
        default=str(DEFAULT_SEED_DIR),
        help=f"Output directory for CSV files (default: {DEFAULT_SEED_DIR})",
    )
    parser.add_argument(
        "--tables",
        nargs="+",
        choices=sorted(TABLES.keys()),
        help="Optional subset of tables to export.",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    db_path = Path(args.db_path).expanduser().resolve()
    seed_dir = Path(args.seed_dir).expanduser().resolve()
    table_names = args.tables or list(TABLES.keys())

    if not db_path.exists():
        raise SystemExit(f"Database not found at {db_path}")
    seed_dir.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as connection:
        cursor = connection.cursor()
        for table in table_names:
            meta = TABLES[table]
            count = _export_table(
                cursor,
                seed_dir,
                table,
                meta["columns"],
                meta.get("order_by"),
            )
            print(f"{table}: {count} rows")


if __name__ == "__main__":
    main()
