import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import db

CHEAT_FILE = ROOT / "data" / "reference" / "Master Load Building Cheat Sheet (01.22.25).xlsx"
RATE_FILE = ROOT / "data" / "reference" / "COT Rate MAtrix 2026.xlsx"


def _clean(value):
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def _parse_length(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    # Normalize quotes
    text = text.replace("’", "'").replace("“", "\"").replace("”", "\"")
    # Format like 10'4"
    if "'" in text:
        parts = text.split("'")
        feet = float(parts[0]) if parts[0].strip() else 0.0
        inches = 0.0
        if len(parts) > 1:
            inch_part = parts[1].replace("\"", "").strip()
            inches = float(inch_part) if inch_part else 0.0
        return feet + (inches / 12.0)
    try:
        return float(text)
    except ValueError:
        return 0.0


def _parse_rate(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    # Remove common non-numeric characters
    for ch in ["$", "*"]:
        text = text.replace(ch, "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def import_cheat_sheet():
    df = pd.read_excel(CHEAT_FILE, sheet_name="Sheet1", header=None)

    # Left table: full cheat sheet
    header_row = None
    for idx, row in df.iterrows():
        if _clean(row[0]) and _clean(row[1]).lower() == "category":
            header_row = idx
            break
    if header_row is None:
        raise ValueError("Cheat sheet header row not found")

    for _, row in df.iloc[header_row + 1 :].iterrows():
        sku = _clean(row[0])
        category = _clean(row[1])
        length = row[2]
        max_step = row[3]
        max_flat = row[4]
        if not sku:
            break
        spec = {
            "sku": sku,
            "category": category or "UNKNOWN",
            "length_with_tongue_ft": _parse_length(length),
            "max_stack_step_deck": int(max_step) if max_step else 1,
            "max_stack_flat_bed": int(max_flat) if max_flat else 1,
            "notes": "",
        }
        db.upsert_sku_spec(spec)

    # Right table: order SKU translation
    translations = {}
    for _, row in df.iloc[header_row + 1 :].iterrows():
        category = _clean(row[6])
        vin_series = _clean(row[7])
        cheat_sku = _clean(row[8])
        if not category and not vin_series and not cheat_sku:
            continue
        if vin_series and cheat_sku:
            translations[vin_series.upper()] = (cheat_sku, category)

    return translations


def import_lookups(translations):
    df = pd.read_excel(CHEAT_FILE, sheet_name="Lookups")
    for _, row in df.iterrows():
        plant = _clean(row.get("Plant")).upper()
        bin_code = _clean(row.get("BIN")).upper()
        item_pattern = _clean(row.get("SKU")).upper()
        if not plant or not bin_code or not item_pattern:
            continue
        cheat_sku = translations.get(item_pattern, (item_pattern, bin_code))[0]
        entry = {
            "plant": plant,
            "bin": bin_code,
            "item_pattern": item_pattern,
            "sku": cheat_sku,
        }
        db.add_item_lookup(entry)

    # Add generic rules from translation table for any plant
    for vin_series, (cheat_sku, category) in translations.items():
        entry = {
            "plant": "*",
            "bin": category.upper() if category else "*",
            "item_pattern": vin_series,
            "sku": cheat_sku,
        }
        db.add_item_lookup(entry)


def import_rates():
    df = pd.read_excel(RATE_FILE, sheet_name="2026 Bid", header=None)
    plants = [str(val).strip() for val in df.iloc[3, 1:7].tolist()]

    for _, row in df.iloc[5:].iterrows():
        destination = _clean(row[0])
        if not destination:
            break
        if destination.lower().startswith("destination"):
            continue
        state = destination.split("-")[0].strip().upper()
        for idx, plant in enumerate(plants):
            rate = row[idx + 1]
            if rate is None or (isinstance(rate, float) and math.isnan(rate)):
                continue
            db.upsert_rate(
                {
                    "origin_plant": plant.upper(),
                    "destination_state": state,
                    "rate_per_mile": _parse_rate(rate),
                    "effective_year": 2026,
                    "notes": "",
                }
            )


if __name__ == "__main__":
    missing_files = [path for path in (CHEAT_FILE, RATE_FILE) if not path.exists()]
    if missing_files:
        missing_list = ", ".join(str(path) for path in missing_files)
        raise SystemExit(f"Missing required input file(s): {missing_list}")

    translations = import_cheat_sheet()
    import_lookups(translations)
    import_rates()
    print("Import complete")
