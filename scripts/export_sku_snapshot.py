"""Export current SKU specifications to a CSV snapshot.

Usage:
    python scripts/export_sku_snapshot.py [--output PATH]

Defaults to writing ``data/exports/sku_specifications_snapshot.csv``.
The snapshot includes a header comment with generation metadata.
"""

import argparse
import csv
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import db

logger = logging.getLogger(__name__)

EXPORT_FIELDS = [
    "sku",
    "category",
    "description",
    "length_with_tongue_ft",
    "max_stack_step_deck",
    "max_stack_flat_bed",
]

DEFAULT_OUTPUT = ROOT / "data" / "exports" / "sku_specifications_snapshot.csv"


def export_sku_snapshot(output_path=None):
    """Read SKU specs from DB and write to CSV."""
    output_path = Path(output_path or DEFAULT_OUTPUT)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    specs = db.list_sku_specs()
    if not specs:
        logger.warning("No SKU specifications found in database.")
        return None

    generated_at = datetime.now(timezone.utc).isoformat()
    row_count = len(specs)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        f.write(f"# generated_at: {generated_at}\n")
        f.write(f"# row_count: {row_count}\n")
        writer = csv.DictWriter(f, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for spec in specs:
            row = {field: spec.get(field, "") for field in EXPORT_FIELDS}
            writer.writerow(row)

    logger.info("Exported %d SKU specs to %s", row_count, output_path)
    return str(output_path)


def main():
    parser = argparse.ArgumentParser(description="Export SKU specifications snapshot")
    parser.add_argument("--output", type=str, default=None, help="Output CSV path")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = export_sku_snapshot(args.output)
    if result:
        print(f"Snapshot written to {result}")
    else:
        print("No data to export.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
