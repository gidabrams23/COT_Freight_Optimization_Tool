#!/usr/bin/env python3
"""
One-time cleanup script for ECOM lookup rows.

It scans `item_sku_lookup` rows with BIN=ECOM and remaps their target SKU to the
closest non-ECOM canonical SKU when a confident match is available.
"""

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import db
from services.order_importer import OrderImporter


def _is_ecom_label(value):
    normalized = str(value or "").strip().upper()
    if not normalized:
        return False
    return (
        normalized == "ECOM"
        or normalized.endswith("-ECOM")
        or normalized.startswith("ECOM-")
    )


def _spec_index():
    index = {}
    for spec in db.list_sku_specs():
        sku_key = str(spec.get("sku") or "").strip().upper()
        if sku_key:
            index[sku_key] = spec
    return index


def _is_non_ecom_spec(spec):
    if not spec:
        return False
    return not _is_ecom_label(spec.get("category"))


def main():
    parser = argparse.ArgumentParser(description="Remap ECOM lookup rows to non-ECOM canonical SKUs.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist updates to item_sku_lookup. Omit for dry-run.",
    )
    args = parser.parse_args()

    importer = OrderImporter()
    specs_by_sku = _spec_index()

    with db.get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, plant, bin, item_pattern, sku
            FROM item_sku_lookup
            WHERE UPPER(TRIM(COALESCE(bin, ''))) = 'ECOM'
            ORDER BY plant ASC, item_pattern ASC, id ASC
            """
        ).fetchall()

        updates = []
        unresolved = []
        already_non_ecom = 0

        for row in rows:
            row_id = int(row["id"])
            item_pattern = str(row["item_pattern"] or "").strip().upper()
            current_sku = str(row["sku"] or "").strip().upper()
            current_spec = specs_by_sku.get(current_sku)

            if _is_non_ecom_spec(current_spec):
                already_non_ecom += 1
                continue

            candidate = importer._suggest_non_ecom_sku(item_pattern, desc=(current_spec or {}).get("description", ""))
            if not candidate:
                unresolved.append(
                    {
                        "id": row_id,
                        "plant": row["plant"],
                        "item_pattern": item_pattern,
                        "current_sku": current_sku,
                        "reason": "No confident non-ECOM candidate found.",
                    }
                )
                continue

            candidate_key = str(candidate).strip().upper()
            if candidate_key == current_sku:
                continue

            updates.append(
                {
                    "id": row_id,
                    "plant": row["plant"],
                    "item_pattern": item_pattern,
                    "from_sku": current_sku,
                    "to_sku": candidate_key,
                }
            )

        if args.apply and updates:
            connection.executemany(
                "UPDATE item_sku_lookup SET sku = ? WHERE id = ?",
                [(entry["to_sku"], entry["id"]) for entry in updates],
            )
            connection.commit()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] scanned {len(rows)} ECOM lookup rows")
    print(f"[{mode}] already mapped to non-ECOM SKUs: {already_non_ecom}")
    print(f"[{mode}] proposed remaps: {len(updates)}")
    print(f"[{mode}] unresolved rows: {len(unresolved)}")

    if updates:
        print("\nSample remaps:")
        for entry in updates[:30]:
            print(
                f"  id={entry['id']} plant={entry['plant']} item={entry['item_pattern']} "
                f"{entry['from_sku']} -> {entry['to_sku']}"
            )
        if len(updates) > 30:
            print(f"  ... {len(updates) - 30} additional remaps not shown")

    if unresolved:
        print("\nUnresolved sample:")
        for entry in unresolved[:20]:
            print(
                f"  id={entry['id']} plant={entry['plant']} item={entry['item_pattern']} "
                f"current={entry['current_sku'] or '<empty>'} reason={entry['reason']}"
            )
        if len(unresolved) > 20:
            print(f"  ... {len(unresolved) - 20} additional unresolved rows not shown")


if __name__ == "__main__":
    main()
