"""Batch utilization scorer for historical load data."""

import io
import re

from cot_utilization.stack_calculator import (
    TRAILER_CONFIGS,
    calculate_stack_configuration,
    normalize_trailer_type,
)

_SKU_DIMENSION_PATTERN = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*[Xx]\s*(\d+(?:\.\d+)?)"
)

_DEFAULT_COLUMN_MAP = {
    "load_number": "load_number",
    "qty": "qty",
    "sku": "sku",
    "trailer_hint": "trailer_hint",
}

_DEFAULT_TRAILER_RULES = {
    "default": "STEP_DECK",
    "overrides": {},
}


def _normalize_sku_lookup(raw):
    """Build case-insensitive SKU lookup dict."""
    lookup = {}
    for key, value in (raw or {}).items():
        normalized_key = str(key).strip().upper()
        if not normalized_key:
            continue
        lookup[normalized_key] = {
            "length_with_tongue_ft": float(value.get("length_with_tongue_ft") or 0),
            "max_stack_step_deck": int(float(value.get("max_stack_step_deck") or 1)),
            "max_stack_flat_bed": int(float(value.get("max_stack_flat_bed") or 1)),
            "category": str(value.get("category") or "UNKNOWN").strip().upper(),
        }
    return lookup


def _parse_sku_dimensions(sku_text):
    """Attempt to extract length from SKU name like '5X10GW' -> 10.0."""
    match = _SKU_DIMENSION_PATTERN.search(str(sku_text or ""))
    if not match:
        return None
    try:
        dim_a = float(match.group(1))
        dim_b = float(match.group(2))
        return dim_b
    except (TypeError, ValueError):
        return None


def _resolve_sku(sku_text, sku_lookup, trailer_type):
    """Resolve a SKU to dimensions and stacking rules.

    Returns (spec_dict, unmapped_flag) where unmapped_flag is True
    if the SKU could not be found in the lookup or parsed from its name.
    """
    key = str(sku_text or "").strip().upper()
    spec = sku_lookup.get(key)
    if spec:
        is_step_deck = trailer_type.startswith("STEP_DECK")
        max_stack = spec["max_stack_step_deck"] if is_step_deck else spec["max_stack_flat_bed"]
        return {
            "unit_length_ft": spec["length_with_tongue_ft"],
            "max_stack_height": max(max_stack, 1),
            "category": spec["category"],
        }, False

    parsed_length = _parse_sku_dimensions(sku_text)
    if parsed_length is not None:
        return {
            "unit_length_ft": parsed_length,
            "max_stack_height": 1,
            "category": "UNKNOWN",
        }, False

    return None, True


def _determine_trailer_type(rows, trailer_hint_col, trailer_rules):
    """Determine trailer type for a load based on trailer_rules and row values."""
    overrides = trailer_rules.get("overrides") or {}
    default = trailer_rules.get("default") or "STEP_DECK"
    for value in rows:
        hint = str(value.get(trailer_hint_col) or "").strip()
        if hint in overrides:
            return normalize_trailer_type(overrides[hint], default=default)
    return normalize_trailer_type(default)


class UtilizationScorer:
    """Score historical loads using the COT bin-packing utilization algorithm.

    Accepts a pre-built SKU lookup dict. The caller is responsible for
    loading SKU data from whatever source (CSV, blob snapshot, etc.).
    """

    def __init__(self, sku_lookup):
        self._sku_lookup = _normalize_sku_lookup(sku_lookup)

    @classmethod
    def from_csv(cls, path):
        """Convenience constructor for local dev/testing -- load SKU specs from CSV."""
        import csv

        lookup = {}
        with open(path, newline="", encoding="utf-8") as f:
            # Snapshot exports may include leading metadata comment lines.
            data_lines = []
            for line in f:
                if not data_lines and line.lstrip().startswith("#"):
                    continue
                data_lines.append(line)
            reader = csv.DictReader(io.StringIO("".join(data_lines)))
            for row in reader:
                sku = (row.get("sku") or "").strip()
                if not sku:
                    continue
                lookup[sku] = {
                    "length_with_tongue_ft": row.get("length_with_tongue_ft", 0),
                    "max_stack_step_deck": row.get("max_stack_step_deck", 1),
                    "max_stack_flat_bed": row.get("max_stack_flat_bed", 1),
                    "category": row.get("category", "UNKNOWN"),
                }
        return cls(lookup)

    def score_loads(self, df, column_map=None, trailer_rules=None):
        """Score a DataFrame of load records.

        Parameters
        ----------
        df : pandas.DataFrame
            Input data with one row per load line item.
        column_map : dict, optional
            Maps scorer fields to DataFrame column names.
            Keys: load_number, qty, sku, trailer_hint.
        trailer_rules : dict, optional
            default: fallback trailer type (default "STEP_DECK").
            overrides: map of trailer_hint values to trailer types.

        Returns
        -------
        pandas.DataFrame
            One row per load with utilization scores.
        """
        import pandas as pd

        cmap = dict(_DEFAULT_COLUMN_MAP)
        if column_map:
            cmap.update(column_map)

        rules = dict(_DEFAULT_TRAILER_RULES)
        if trailer_rules:
            rules.update(trailer_rules)

        load_col = cmap["load_number"]
        qty_col = cmap["qty"]
        sku_col = cmap["sku"]
        hint_col = cmap["trailer_hint"]

        results = []

        for load_number, group in df.groupby(load_col, sort=False):
            rows = group.to_dict("records")

            trailer_type = _determine_trailer_type(rows, hint_col, rules)
            trailer_config = TRAILER_CONFIGS.get(
                trailer_type, TRAILER_CONFIGS["STEP_DECK"]
            )
            capacity = trailer_config["capacity"]

            line_items = []
            unmapped_skus = []

            for row in rows:
                sku_text = row.get(sku_col, "")
                raw_qty = row.get(qty_col)
                try:
                    qty = int(float(raw_qty)) if raw_qty is not None else 0
                except (TypeError, ValueError):
                    qty = 0
                if qty <= 0:
                    continue

                resolved, is_unmapped = _resolve_sku(
                    sku_text, self._sku_lookup, trailer_type
                )
                if is_unmapped:
                    unmapped_skus.append(str(sku_text))
                    continue

                line_items.append(
                    {
                        "item": str(sku_text),
                        "sku": str(sku_text).strip().upper(),
                        "qty": qty,
                        "unit_length_ft": resolved["unit_length_ft"],
                        "max_stack_height": resolved["max_stack_height"],
                        "category": resolved["category"],
                    }
                )

            config = calculate_stack_configuration(
                line_items,
                trailer_type=trailer_type,
                capacity_feet=capacity,
                stack_overflow_max_height=0,
            )

            results.append(
                {
                    "load_number": load_number,
                    "utilization_pct": config.get("utilization_pct", 0),
                    "utilization_grade": config.get("utilization_grade", "F"),
                    "utilization_credit_ft": config.get("utilization_credit_ft", 0),
                    "total_linear_feet": config.get("total_linear_feet", 0),
                    "trailer_type": trailer_type,
                    "capacity_ft": capacity,
                    "position_count": len(config.get("positions") or []),
                    "line_count": len(line_items),
                    "unmapped_skus": unmapped_skus,
                }
            )

        return pd.DataFrame(results)
