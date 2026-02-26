import csv
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "db" / "app.db"
SEED_DIR = ROOT / "data" / "seed"

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
            "baseline_cost",
            "baseline_set_at",
            "updated_at",
        ],
        "order_by": "plant_code ASC",
    },
}


def _export_table(cursor, table_name, columns, order_by):
    query = f"SELECT {', '.join(columns)} FROM {table_name}"
    if order_by:
        query += f" ORDER BY {order_by}"
    rows = cursor.execute(query).fetchall()

    if not rows:
        return 0

    path = SEED_DIR / f"{table_name}.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row[idx] for idx in range(len(columns))])
    return len(rows)


def main():
    if not DB_PATH.exists():
        raise SystemExit(f"Database not found at {DB_PATH}")
    SEED_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.cursor()
        for table, meta in TABLES.items():
            count = _export_table(
                cursor, table, meta["columns"], meta.get("order_by")
            )
            print(f"{table}: {count} rows")


if __name__ == "__main__":
    main()
