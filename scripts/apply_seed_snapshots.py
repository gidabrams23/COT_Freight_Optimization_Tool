import argparse
import csv
import os
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = Path(
    os.environ.get(
        "APP_DB_PATH",
        "/var/data/app.db" if Path("/var/data").exists() else str(ROOT / "data" / "db" / "app.db"),
    )
)
DEFAULT_SEED_DIR = Path(os.environ.get("APP_SEED_DIR", str(ROOT / "data" / "seed")))

UPSERT_TABLES = {
    "plants": {
        "columns": ["plant_code", "name", "lat", "lng", "address", "created_at"],
        "key_columns": ["plant_code"],
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
        "key_columns": ["sku"],
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
        "key_columns": ["origin_plant", "destination_state", "effective_year"],
    },
    "planning_settings": {
        "columns": ["key", "value_text", "updated_at"],
        "key_columns": ["key"],
    },
    "access_profiles": {
        "columns": ["name", "is_admin", "is_sandbox", "allowed_plants", "default_plants", "created_at"],
        "key_columns": ["name"],
    },
    "zip_coordinates": {
        "columns": ["zip", "lat", "lng", "city", "state", "created_at"],
        "key_columns": ["zip"],
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
        "key_columns": ["plant_code"],
    },
}


def _normalize_seed_value(value):
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    return text


def _read_seed_rows(seed_path, columns):
    if not seed_path.exists():
        return []
    with seed_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for row in reader:
            rows.append([_normalize_seed_value(row.get(col)) for col in columns])
        return rows


def _upsert_table(connection, table_name, columns, key_columns, rows):
    if not rows:
        return 0
    placeholders = ", ".join("?" for _ in columns)
    update_columns = [col for col in columns if col not in key_columns]
    update_clause = ", ".join(f"{col} = excluded.{col}" for col in update_columns)
    key_clause = ", ".join(key_columns)
    query = f"""
        INSERT INTO {table_name} ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT({key_clause}) DO UPDATE SET
            {update_clause}
    """
    connection.executemany(query, rows)
    return len(rows)


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Upsert seed snapshot CSVs into an existing SQLite database (useful for Render disk sync)."
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help=f"Path to SQLite DB (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--seed-dir",
        default=str(DEFAULT_SEED_DIR),
        help=f"Seed CSV directory (default: {DEFAULT_SEED_DIR})",
    )
    parser.add_argument(
        "--tables",
        nargs="+",
        choices=sorted(UPSERT_TABLES.keys()),
        default=["optimizer_settings", "sku_specifications", "planning_settings"],
        help="Tables to upsert from seed snapshots.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print row counts without writing to the database.",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    db_path = Path(args.db_path).expanduser().resolve()
    seed_dir = Path(args.seed_dir).expanduser().resolve()

    if not db_path.exists():
        raise SystemExit(f"Database not found at {db_path}")
    if not seed_dir.exists():
        raise SystemExit(f"Seed directory not found at {seed_dir}")

    with sqlite3.connect(db_path) as connection:
        for table_name in args.tables:
            meta = UPSERT_TABLES[table_name]
            seed_path = seed_dir / f"{table_name}.csv"
            rows = _read_seed_rows(seed_path, meta["columns"])
            if args.dry_run:
                print(f"{table_name}: {len(rows)} rows (dry-run)")
                continue
            count = _upsert_table(
                connection,
                table_name,
                meta["columns"],
                meta["key_columns"],
                rows,
            )
            print(f"{table_name}: upserted {count} rows")
        if not args.dry_run:
            connection.commit()


if __name__ == "__main__":
    main()
