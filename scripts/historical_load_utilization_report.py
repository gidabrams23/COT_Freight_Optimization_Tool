import argparse
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import load_builder, stack_calculator

DEFAULT_SKU_CHEAT_SHEET_PATH = ROOT / "data" / "seed" / "sku_specifications.csv"
DEFAULT_PLANT_TRAILER_OVERRIDES = {
    str(key).strip().upper(): str(value).strip().upper()
    for key, value in (load_builder.PLANT_DEFAULT_TRAILER_TYPE_OVERRIDES or {}).items()
}


def _normalize_column_name(value):
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _normalize_text(value):
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def _normalize_order_number(value):
    text = _normalize_text(value)
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _to_positive_float(value):
    text = _normalize_text(value).replace(",", "")
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _to_positive_int(value):
    text = _normalize_text(value).replace(",", "")
    if not text:
        return None
    try:
        parsed = int(float(text))
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _read_dataframe(path):
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    ext = file_path.suffix.lower()
    if ext == ".csv":
        return pd.read_csv(file_path, dtype=str, keep_default_na=False)
    if ext in {".xlsx", ".xls", ".xlsm"}:
        return pd.read_excel(file_path, dtype=str)
    raise ValueError(f"Unsupported file type for {file_path}. Use CSV or XLSX.")


def _normalize_dataframe(df):
    column_map = {col: _normalize_column_name(col) for col in df.columns}
    return df.rename(columns=column_map)


def _resolve_column(df, aliases, required=False):
    cols = set(df.columns)
    for alias in aliases:
        key = _normalize_column_name(alias)
        if key in cols:
            return key
    if required:
        raise ValueError(
            "Missing required column. Expected one of: "
            + ", ".join(sorted({_normalize_column_name(alias) for alias in aliases}))
        )
    return None


def _extract_plant_code_from_load(load_number):
    raw = _normalize_text(load_number).upper()
    match = re.match(r"^([A-Z]{2})", raw)
    return match.group(1) if match else ""


def _normalize_plant_code(value):
    raw = _normalize_text(value).upper()
    if not raw:
        return ""
    match = re.match(r"^([A-Z]{2})", raw)
    return match.group(1) if match else raw[:2]


def _load_sku_cheat_sheet(path=None):
    cheat_path = Path(path) if path else DEFAULT_SKU_CHEAT_SHEET_PATH
    df = _normalize_dataframe(_read_dataframe(cheat_path))

    sku_col = _resolve_column(df, ["sku"], required=True)
    length_col = _resolve_column(df, ["length_with_tongue_ft", "length_ft", "unit_length_ft"], required=True)
    step_col = _resolve_column(df, ["max_stack_step_deck", "max_stack"], required=False)
    flat_col = _resolve_column(df, ["max_stack_flat_bed", "max_stack"], required=False)
    category_col = _resolve_column(df, ["category", "bin"], required=False)

    specs = {}
    for idx, row in enumerate(df.to_dict(orient="records"), start=2):
        sku = _normalize_text(row.get(sku_col)).upper()
        if not sku or sku in specs:
            continue

        length_ft = _to_positive_float(row.get(length_col))
        if length_ft is None:
            continue

        step_stack = _to_positive_int(row.get(step_col)) if step_col else None
        flat_stack = _to_positive_int(row.get(flat_col)) if flat_col else None

        specs[sku] = {
            "sku": sku,
            "length_ft": float(length_ft),
            "max_stack_step_deck": int(step_stack or flat_stack or 1),
            "max_stack_flat_bed": int(flat_stack or step_stack or 1),
            "category": _normalize_text(row.get(category_col)).upper() if category_col else "",
            "source_row": idx,
        }

    if not specs:
        raise ValueError(f"No usable SKU rows found in cheat sheet: {cheat_path}")

    return specs, cheat_path


def _parse_load_trailer_overrides(path):
    if not path:
        return {}

    df = _normalize_dataframe(_read_dataframe(path))
    load_col = _resolve_column(df, ["load_number", "load_no", "load", "load_id"], required=True)
    trailer_col = _resolve_column(df, ["trailer_type", "trailer", "trailer_profile", "trailer_code"], required=False)
    capacity_col = _resolve_column(df, ["capacity_feet", "capacity_ft", "capacity", "trailer_capacity"], required=False)

    overrides = {}
    for row in df.to_dict(orient="records"):
        load_number = _normalize_text(row.get(load_col))
        if not load_number:
            continue
        overrides[load_number] = {
            "trailer_type": _normalize_text(row.get(trailer_col)).upper() if trailer_col else "",
            "capacity_feet": _to_positive_float(row.get(capacity_col)) if capacity_col else None,
        }
    return overrides


def _parse_order_report(path):
    df = _normalize_dataframe(_read_dataframe(path))

    load_col = _resolve_column(df, ["load_number", "load_no", "load", "load_id", "load_name", "load_"], required=True)
    order_col = _resolve_column(df, ["order_number", "order_no", "order", "so_num", "sonum", "sales_order", "name"], required=True)
    sku_col = _resolve_column(df, ["sku", "item", "itemnum", "item_num"], required=True)
    qty_col = _resolve_column(df, ["qty", "quantity", "ordqty"], required=True)

    plant_col = _resolve_column(df, ["origin_plant", "plant", "plant_code"], required=False)
    trailer_col = _resolve_column(df, ["trailer_type", "trailer", "trailer_profile", "trailer_code"], required=False)
    capacity_col = _resolve_column(df, ["capacity_feet", "capacity_ft", "capacity", "trailer_capacity"], required=False)
    item_desc_col = _resolve_column(df, ["item_desc", "desc", "description"], required=False)
    category_col = _resolve_column(df, ["category", "bin"], required=False)
    stop_col = _resolve_column(df, ["stop_sequence", "stop", "stop_order"], required=False)
    destination_col = _resolve_column(df, ["destination", "state", "ship_to_state", "zip", "ship_to_zip"], required=False)

    rows = []
    issues = []
    for idx, raw in enumerate(df.to_dict(orient="records"), start=2):
        load_number = _normalize_text(raw.get(load_col))
        order_number = _normalize_order_number(raw.get(order_col))
        sku = _normalize_text(raw.get(sku_col)).upper()
        qty = _to_positive_int(raw.get(qty_col))

        if not load_number or not order_number or not sku or qty is None:
            issues.append(
                f"Skipped row {idx}: requires load_number, order_number, sku, and positive qty."
            )
            continue

        plant_code = _normalize_plant_code(raw.get(plant_col)) if plant_col else ""
        if not plant_code:
            plant_code = _extract_plant_code_from_load(load_number)

        rows.append(
            {
                "load_number": load_number,
                "order_number": order_number,
                "sku": sku,
                "qty": int(qty),
                "plant_code": plant_code,
                "trailer_type": _normalize_text(raw.get(trailer_col)).upper() if trailer_col else "",
                "capacity_feet": _to_positive_float(raw.get(capacity_col)) if capacity_col else None,
                "item_desc": _normalize_text(raw.get(item_desc_col)) if item_desc_col else "",
                "category_hint": _normalize_text(raw.get(category_col)).upper() if category_col else "",
                "stop_sequence": _to_positive_int(raw.get(stop_col)) if stop_col else None,
                "destination": _normalize_text(raw.get(destination_col)) if destination_col else "",
            }
        )

    if not rows:
        raise ValueError("Order report parsed but no usable rows were found.")

    return rows, issues


def _default_trailer_for_plant(plant_code):
    preferred = DEFAULT_PLANT_TRAILER_OVERRIDES.get(_normalize_plant_code(plant_code), "STEP_DECK")
    return stack_calculator.normalize_trailer_type(preferred, default="STEP_DECK")


def _is_flatbed_trailer(trailer_type):
    normalized = stack_calculator.normalize_trailer_type(trailer_type, default="STEP_DECK")
    return normalized in {"FLATBED", "FLATBED_48"}


def _pick_load_trailer(rows_for_load, load_number, trailer_overrides):
    override = trailer_overrides.get(load_number) or {}
    override_type = _normalize_text(override.get("trailer_type")).upper()
    override_capacity = _to_positive_float(override.get("capacity_feet"))
    if override_type:
        return stack_calculator.normalize_trailer_type(override_type, default="STEP_DECK"), override_capacity

    reported_trailers = [
        stack_calculator.normalize_trailer_type(row.get("trailer_type"), default="STEP_DECK")
        for row in rows_for_load
        if _normalize_text(row.get("trailer_type"))
    ]
    if reported_trailers:
        return Counter(reported_trailers).most_common(1)[0][0], None

    plants = [_normalize_plant_code(row.get("plant_code")) for row in rows_for_load if _normalize_plant_code(row.get("plant_code"))]
    if plants:
        return _default_trailer_for_plant(Counter(plants).most_common(1)[0][0]), None

    return _default_trailer_for_plant(_extract_plant_code_from_load(load_number)), None


def _pick_capacity_override(rows_for_load, trailer_overrides, load_number):
    override = trailer_overrides.get(load_number) or {}
    override_capacity = _to_positive_float(override.get("capacity_feet"))
    if override_capacity is not None:
        return override_capacity

    reported_caps = [
        _to_positive_float(row.get("capacity_feet"))
        for row in rows_for_load
        if _to_positive_float(row.get("capacity_feet")) is not None
    ]
    if reported_caps:
        return Counter(reported_caps).most_common(1)[0][0]
    return None


def build_utilization_report(order_report_path, sku_cheat_sheet_path=None, load_trailer_overrides_path=None):
    sku_specs, cheat_path = _load_sku_cheat_sheet(sku_cheat_sheet_path)
    order_rows, parse_issues = _parse_order_report(order_report_path)
    trailer_overrides = _parse_load_trailer_overrides(load_trailer_overrides_path)

    grouped = defaultdict(list)
    for row in order_rows:
        grouped[row["load_number"]].append(row)

    assumptions = stack_calculator.get_stack_capacity_assumptions(force_refresh=True)
    issues = list(parse_issues)
    output_rows = []

    for load_number in sorted(grouped.keys()):
        rows_for_load = grouped[load_number]
        trailer_type, trailer_capacity_override = _pick_load_trailer(rows_for_load, load_number, trailer_overrides)
        capacity_feet = trailer_capacity_override
        if capacity_feet is None:
            capacity_feet = _pick_capacity_override(rows_for_load, trailer_overrides, load_number)

        calc_lines = []
        missing_skus = []
        for row in rows_for_load:
            spec = sku_specs.get(row["sku"])
            if not spec:
                missing_skus.append(row["sku"])
                continue

            if _is_flatbed_trailer(trailer_type):
                max_stack = int(spec.get("max_stack_flat_bed") or 1)
                upper_max_stack = max_stack
            else:
                max_stack = int(spec.get("max_stack_step_deck") or spec.get("max_stack_flat_bed") or 1)
                upper_max_stack = int(spec.get("max_stack_flat_bed") or max_stack)

            calc_lines.append(
                {
                    "order_id": row["order_number"],
                    "item": row["sku"],
                    "sku": row["sku"],
                    "item_desc": row.get("item_desc") or "",
                    "category": spec.get("category") or row.get("category_hint") or "UNKNOWN",
                    "qty": int(row["qty"]),
                    "unit_length_ft": float(spec["length_ft"]),
                    "max_stack_height": max(max_stack, 1),
                    "upper_deck_max_stack_height": max(upper_max_stack, 1),
                    "stop_sequence": row.get("stop_sequence"),
                }
            )

        if missing_skus:
            for sku in sorted(set(missing_skus)):
                issues.append(f"Load {load_number}: SKU {sku} missing from cheat sheet; skipped from utilization calc.")

        if not calc_lines:
            issues.append(f"Load {load_number}: no calculable rows after SKU cheat-sheet match.")
            continue

        config = stack_calculator.calculate_stack_configuration(
            calc_lines,
            trailer_type=trailer_type,
            capacity_feet=capacity_feet,
        )

        plants = [_normalize_plant_code(row.get("plant_code")) for row in rows_for_load if _normalize_plant_code(row.get("plant_code"))]
        dominant_plant = Counter(plants).most_common(1)[0][0] if plants else _extract_plant_code_from_load(load_number)

        output_rows.append(
            {
                "load_number": load_number,
                "origin_plant_assumed": dominant_plant,
                "trailer_type_used": config.get("trailer_type") or trailer_type,
                "capacity_feet_used": float(config.get("capacity_feet") or 0.0),
                "orders_on_load": len({row["order_number"] for row in rows_for_load}),
                "rows_in_report_load": len(rows_for_load),
                "rows_used_in_calc": len(calc_lines),
                "units_on_load": int(sum(int(row.get("qty") or 0) for row in calc_lines)),
                "utilization_pct": float(config.get("utilization_pct") or 0.0),
                "utilization_grade": config.get("utilization_grade") or "F",
                "utilization_credit_ft": float(config.get("utilization_credit_ft") or 0.0),
                "total_linear_feet": float(config.get("total_linear_feet") or 0.0),
                "lower_deck_used_length_ft": float(config.get("lower_deck_used_length_ft") or 0.0),
                "upper_deck_effective_length_ft": float(config.get("upper_deck_effective_length_ft") or 0.0),
                "exceeds_capacity": bool(config.get("exceeds_capacity")),
                "warning_count": len(config.get("warnings") or []),
                "missing_sku_rows": len(missing_skus),
                "missing_skus": ",".join(sorted(set(missing_skus))),
            }
        )

    output_rows.sort(key=lambda row: (row["utilization_pct"], row["load_number"]))
    return {
        "rows": output_rows,
        "issues": issues,
        "assumptions": assumptions,
        "sku_cheat_sheet_path": str(cheat_path),
        "sku_count": len(sku_specs),
    }


def _parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Build load-level historical utilization using one order report, embedded SKU cheat-sheet assumptions, "
            "and default plant trailer rules."
        )
    )
    parser.add_argument(
        "--order-report",
        required=True,
        help="CSV/XLSX with load_number, order_number, sku, qty, and optionally origin_plant/trailer_type.",
    )
    parser.add_argument(
        "--sku-cheat-sheet",
        required=False,
        default=str(DEFAULT_SKU_CHEAT_SHEET_PATH),
        help="Optional override path for SKU specs cheat sheet. Defaults to data/seed/sku_specifications.csv.",
    )
    parser.add_argument(
        "--load-trailers",
        required=False,
        help="Optional load-level trailer/capacity override file (load_number + trailer_type + capacity_feet).",
    )
    parser.add_argument(
        "--output",
        required=False,
        help="Output CSV path. Defaults to exports/load_utilization_report_<YYYY-MM-DD>.csv",
    )
    return parser.parse_args()


def _make_output_path(output_arg):
    if output_arg:
        return Path(output_arg)
    return Path("exports") / f"load_utilization_report_{date.today().isoformat()}.csv"


def main():
    args = _parse_args()

    result = build_utilization_report(
        order_report_path=args.order_report,
        sku_cheat_sheet_path=args.sku_cheat_sheet,
        load_trailer_overrides_path=args.load_trailers,
    )

    rows = result.get("rows") or []
    if not rows:
        raise SystemExit("No load rows were produced. Check required columns and SKU coverage.")

    output_path = _make_output_path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(output_path, index=False)

    util_values = [float(row.get("utilization_pct") or 0.0) for row in rows]
    avg_util = sum(util_values) / len(util_values) if util_values else 0.0

    print(f"Wrote {len(rows)} loads to {output_path}")
    print(f"Average utilization: {avg_util:.1f}%")
    print(f"SKU cheat sheet: {result.get('sku_cheat_sheet_path')} ({result.get('sku_count')} SKUs)")

    assumptions = result.get("assumptions") or {}
    print("Stack assumptions used:")
    print(
        "  stack_overflow_max_height={stack}, max_back_overhang_ft={back}, upper_two_across_max_length_ft={two}".format(
            stack=assumptions.get("stack_overflow_max_height"),
            back=assumptions.get("max_back_overhang_ft"),
            two=assumptions.get("upper_two_across_max_length_ft"),
        )
    )
    print(
        "  upper_deck_exception_max_length_ft={mx}, upper_deck_exception_overhang_allowance_ft={oh}, upper_deck_exception_categories={cats}".format(
            mx=assumptions.get("upper_deck_exception_max_length_ft"),
            oh=assumptions.get("upper_deck_exception_overhang_allowance_ft"),
            cats=",".join(assumptions.get("upper_deck_exception_categories") or []),
        )
    )

    issues = result.get("issues") or []
    if issues:
        print(f"Data quality notes ({len(issues)}):")
        for issue in issues[:25]:
            print(f"  - {issue}")
        if len(issues) > 25:
            print(f"  - ... {len(issues) - 25} more notes omitted")


if __name__ == "__main__":
    main()
