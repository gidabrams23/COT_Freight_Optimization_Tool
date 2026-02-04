import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import db
from services import geo_utils


def load_uszips(file_path: Path, chunk_size: int = 5000) -> int:
    df = pd.read_excel(file_path, dtype={"zip": str})
    expected = {"zip", "lat", "lng", "city", "state_id"}
    missing = expected - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns in uszips file: {sorted(missing)}")

    records = df[["zip", "lat", "lng", "city", "state_id"]].dropna(subset=["zip", "lat", "lng"])

    inserted = 0
    rows = []
    with db.get_connection() as connection:
        for row in records.itertuples(index=False):
            zip_code = geo_utils.normalize_zip(row.zip)
            if not zip_code:
                continue
            city = str(row.city).strip() if row.city is not None else ""
            state = str(row.state_id).strip() if row.state_id is not None else ""
            rows.append((zip_code, float(row.lat), float(row.lng), city, state))

            if len(rows) >= chunk_size:
                inserted += _upsert_rows(connection, rows)
                rows = []

        if rows:
            inserted += _upsert_rows(connection, rows)

    return inserted


def _upsert_rows(connection, rows):
    connection.executemany(
        """
        INSERT INTO zip_coordinates (zip, lat, lng, city, state)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(zip) DO UPDATE SET
            lat = excluded.lat,
            lng = excluded.lng,
            city = excluded.city,
            state = excluded.state
        """,
        rows,
    )
    connection.commit()
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="Import US ZIP coordinates into SQLite.")
    parser.add_argument(
        "--file",
        default="uszips.xlsx",
        help="Path to uszips.xlsx (default: uszips.xlsx in repo root)",
    )
    args = parser.parse_args()
    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"File not found: {path}")

    db.init_db()
    count = load_uszips(path)
    print(f"Imported {count} ZIP coordinates from {path}.")


if __name__ == "__main__":
    main()
