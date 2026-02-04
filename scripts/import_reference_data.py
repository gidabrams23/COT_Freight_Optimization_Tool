import pandas as pd

import db


def import_sku_specs(excel_file):
    df = pd.read_excel(excel_file, sheet_name=0)
    for _, row in df.iterrows():
        sku = str(row.get("SKU", "")).strip()
        if not sku:
            continue
        spec = {
            "sku": sku,
            "category": str(row.get("Category", "")).strip() or "UNKNOWN",
            "length_with_tongue_ft": float(row.get("Length", 0) or 0),
            "max_stack_step_deck": int(row.get("StepDeck", 1) or 1),
            "max_stack_flat_bed": int(row.get("FlatBed", 1) or 1),
            "notes": str(row.get("Notes", "")).strip(),
        }
        db.upsert_sku_spec(spec)


def import_sku_lookups(excel_file):
    df = pd.read_excel(excel_file, sheet_name="Lookups")
    for _, row in df.iterrows():
        entry = {
            "plant": str(row.get("Plant", "")).strip(),
            "bin": str(row.get("BIN", "")).strip(),
            "item_pattern": str(row.get("Item", "")).strip(),
            "sku": str(row.get("SKU", "")).strip(),
        }
        if entry["plant"] and entry["bin"] and entry["sku"]:
            db.add_item_lookup(entry)


def import_rate_matrix(excel_file):
    df = pd.read_excel(excel_file, sheet_name=0)
    for _, row in df.iterrows():
        rate = {
            "origin_plant": str(row.get("Origin", "")).strip(),
            "destination_state": str(row.get("State", "")).strip(),
            "rate_per_mile": float(row.get("Rate", 0) or 0),
            "effective_year": int(row.get("Year", 2026) or 2026),
            "notes": str(row.get("Notes", "")).strip(),
        }
        if rate["origin_plant"] and rate["destination_state"]:
            db.upsert_rate(rate)


if __name__ == "__main__":
    pass
