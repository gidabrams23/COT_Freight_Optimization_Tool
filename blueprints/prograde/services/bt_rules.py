"""
Big Tex constraint rules - prototype set.
"""
from .models import Violation
from .. import db


def _row_to_dict(row):
    if isinstance(row, dict):
        return row
    return dict(row or {})


def _normalize_bt_zone(zone: str) -> str:
    zone_value = (zone or "").strip()
    if zone_value in {"lower_deck", "upper_deck"}:
        return zone_value
    legacy_map = {
        "stack_1": "lower_deck",
        "stack_2": "lower_deck",
        "stack_3": "upper_deck",
    }
    return legacy_map.get(zone_value, zone_value)


def _group_columns_by_zone(positions):
    grouped = {"lower_deck": {}, "upper_deck": {}}
    for p in positions:
        pos = _row_to_dict(p)
        zone = _normalize_bt_zone(pos["deck_zone"])
        if zone not in grouped:
            continue
        seq = int(pos["sequence"] or 0)
        grouped[zone].setdefault(seq, []).append(pos)
    return grouped


def _clearance_cap_for_zone(carrier, zone):
    if not carrier:
        return 10.0 if zone == "lower_deck" else 8.5
    max_height = float(carrier.get("max_height_ft") or 13.5)
    if zone == "upper_deck":
        ground = float(carrier.get("upper_deck_ground_height_ft") or 5.0)
    else:
        ground = float(carrier.get("lower_deck_ground_height_ft") or 3.5)
    return max(max_height - ground, 0.0)


def compute_bt_length_metrics(
    positions,
    sku_map=None,
    lower_cap_ft=41.0,
    upper_cap_ft=12.0,
    step_gap_ft=1.5,
):
    """
    Compute Big Tex footprint metrics with cross-deck tongue overlap awareness.

    Rule modeled:
    - Deck footprints cannot overlap across the step.
    - Tongues facing each other across the seam may overlap.
    """
    normalized_positions = [_row_to_dict(p) for p in (positions or [])]
    if sku_map is None:
        sku_map = {p["item_number"]: dict(db.get_bigtex_sku(p["item_number"]) or {}) for p in normalized_positions}

    grouped = _group_columns_by_zone(normalized_positions)
    base_units_by_zone = {"lower_deck": {}, "upper_deck": {}}
    base_length_by_zone = {"lower_deck": 0.0, "upper_deck": 0.0}

    for zone in ("lower_deck", "upper_deck"):
        for seq, col in grouped.get(zone, {}).items():
            if not col:
                continue
            bottom = min(col, key=lambda p: int(p["layer"] or 0))
            base_units_by_zone[zone][seq] = bottom
            sku = sku_map.get(bottom["item_number"]) or {}
            base_length_by_zone[zone] += float(sku.get("total_footprint") or 0.0)

    lower_cols = base_units_by_zone.get("lower_deck", {})
    upper_cols = base_units_by_zone.get("upper_deck", {})
    lower_interface = lower_cols.get(max(lower_cols.keys())) if lower_cols else None
    upper_interface = upper_cols.get(min(upper_cols.keys())) if upper_cols else None

    lower_toward_ft = 0.0
    upper_toward_ft = 0.0

    if lower_interface:
        lower_sku = sku_map.get(lower_interface["item_number"]) or {}
        lower_tongue = float(lower_sku.get("tongue") or 0.0)
        # Lower deck seam is to the right; rotated units point tongue right.
        if bool(lower_interface.get("is_rotated")):
            lower_toward_ft = lower_tongue

    if upper_interface:
        upper_sku = sku_map.get(upper_interface["item_number"]) or {}
        upper_tongue = float(upper_sku.get("tongue") or 0.0)
        # Upper deck seam is to the left; non-rotated units point tongue left.
        if not bool(upper_interface.get("is_rotated")):
            upper_toward_ft = upper_tongue

    overlap_credit_ft = min(lower_toward_ft, upper_toward_ft)
    upper_seam_span_ft = max(float(base_length_by_zone.get("upper_deck", 0.0)) - float(upper_cap_ft or 0.0), 0.0)
    lower_seam_span_ft = max(float(base_length_by_zone.get("lower_deck", 0.0)) - float(lower_cap_ft or 0.0), 0.0)
    low_profile_seam_span_ft = 0.0
    lower_grouped = grouped.get("lower_deck", {})
    for seq in sorted(lower_grouped.keys(), reverse=True):
        col = lower_grouped.get(seq) or []
        if not col:
            continue
        total_h = sum(float((sku_map.get(p["item_number"]) or {}).get("stack_height") or 0.0) for p in col)
        if total_h <= 0 or total_h > float(step_gap_ft or 0.0):
            break
        bottom = min(col, key=lambda p: int(p["layer"] or 0))
        sku = sku_map.get(bottom["item_number"]) or {}
        low_profile_seam_span_ft += float(sku.get("total_footprint") or 0.0)

    seam_clearance_credit_ft = min(
        max(upper_seam_span_ft - overlap_credit_ft, 0.0),
        max(low_profile_seam_span_ft, 0.0),
    )

    blocked_lower_ft = max(upper_seam_span_ft - overlap_credit_ft - seam_clearance_credit_ft, 0.0)
    blocked_upper_ft = max(lower_seam_span_ft - overlap_credit_ft, 0.0)
    lower_base = float(base_length_by_zone.get("lower_deck", 0.0))
    upper_base = float(base_length_by_zone.get("upper_deck", 0.0))
    legacy_total = lower_base + upper_base
    effective_total = max(legacy_total - overlap_credit_ft - seam_clearance_credit_ft, 0.0)
    adjusted_lower_usage = lower_base + blocked_lower_ft
    adjusted_upper_usage = upper_base + blocked_upper_ft

    return {
        "legacy_total_ft": round(legacy_total, 3),
        "effective_total_ft": round(effective_total, 3),
        "overlap_credit_ft": round(overlap_credit_ft, 3),
        "seam_clearance_credit_ft": round(seam_clearance_credit_ft, 3),
        "blocked_lower_ft": round(blocked_lower_ft, 3),
        "blocked_upper_ft": round(blocked_upper_ft, 3),
        "lower_base_ft": round(lower_base, 3),
        "upper_base_ft": round(upper_base, 3),
        "upper_seam_span_ft": round(upper_seam_span_ft, 3),
        "lower_seam_span_ft": round(lower_seam_span_ft, 3),
        "low_profile_seam_span_ft": round(low_profile_seam_span_ft, 3),
        "adjusted_lower_usage_ft": round(adjusted_lower_usage, 3),
        "adjusted_upper_usage_ft": round(adjusted_upper_usage, 3),
        "lower_toward_ft": round(lower_toward_ft, 3),
        "upper_toward_ft": round(upper_toward_ft, 3),
    }


def check(positions, carrier) -> list:
    violations = []
    if not positions:
        return violations

    all_bt = db.get_bigtex_skus()
    sku_map = {row["item_number"]: dict(row) for row in all_bt}
    carrier_map = dict(carrier) if carrier else {}

    stack_configs = {r["config_id"]: dict(r) for r in db.get_bt_stack_configs()}

    violations += _bt_total_length(positions, sku_map, carrier_map, stack_configs)
    violations += _bt_height(positions, sku_map, carrier_map, stack_configs)
    violations += _bt_dump_hydraulic(positions, sku_map)
    violations += _bt_gn_sequence(positions, sku_map)
    return violations


def _bt_total_length(positions, sku_map, carrier, _stack_configs):
    """BT_TOTAL_LENGTH - deck footprint usage must fit each deck length cap."""
    violations = []
    lower_cap = float((carrier or {}).get("lower_deck_length_ft") or 41.0)
    upper_cap = float((carrier or {}).get("upper_deck_length_ft") or 12.0)
    metrics = compute_bt_length_metrics(
        positions,
        sku_map=sku_map,
        lower_cap_ft=lower_cap,
        upper_cap_ft=upper_cap,
    )
    deck_caps = {"lower_deck": lower_cap, "upper_deck": upper_cap}
    deck_labels = {"lower_deck": "Lower Deck", "upper_deck": "Upper Deck"}
    deck_usage = {
        "lower_deck": float(metrics.get("adjusted_lower_usage_ft") or 0.0),
        "upper_deck": float(metrics.get("adjusted_upper_usage_ft") or 0.0),
    }
    deck_blocked = {
        "lower_deck": float(metrics.get("blocked_lower_ft") or 0.0),
        "upper_deck": float(metrics.get("blocked_upper_ft") or 0.0),
    }
    overlap_credit = float(metrics.get("overlap_credit_ft") or 0.0)
    seam_clearance_credit = float(metrics.get("seam_clearance_credit_ft") or 0.0)
    affected = [p["position_id"] for p in positions]

    for zone, cap in deck_caps.items():
        if not cap:
            continue
        deck_length = deck_usage.get(zone, 0.0)
        if deck_length > cap:
            notes = []
            seam_block = deck_blocked.get(zone, 0.0)
            if seam_block > 0:
                notes.append(f"{seam_block:.1f}' blocked at step seam")
            if overlap_credit > 0:
                notes.append(f"{overlap_credit:.1f}' tongue-overlap credit applied")
            if seam_clearance_credit > 0 and zone == "lower_deck":
                notes.append(f"{seam_clearance_credit:.1f}' low-profile seam credit applied")
            note_suffix = f" ({'; '.join(notes)})." if notes else "."
            violations.append(Violation(
                severity="error",
                rule_code="BT_TOTAL_LENGTH",
                message=f"{deck_labels.get(zone, zone)} usage {deck_length:.1f}' exceeds {cap:.1f}' capacity{note_suffix}",
                position_ids=affected,
                suggested_fix=f"Move one or more stacks off {deck_labels.get(zone, zone).lower()} or use shorter units.",
            ))
    return violations


def _bt_height(positions, sku_map, carrier, _stack_configs):
    """BT_HEIGHT - cumulative stack_height per column must fit deck clearance."""
    violations = []
    grouped = _group_columns_by_zone(positions)

    for zone, columns in grouped.items():
        cap = _clearance_cap_for_zone(carrier, zone)
        for seq, col in columns.items():
            total_h = sum(
                (sku_map.get(p["item_number"]) or {}).get("stack_height", 0) or 0
                for p in col
            )
            if total_h > cap:
                zone_label = "Upper Deck" if zone == "upper_deck" else "Lower Deck"
                violations.append(Violation(
                    severity="error",
                    rule_code="BT_HEIGHT",
                    message=(
                        f"{zone_label} column {seq}: "
                        f"height {total_h:.2f}' exceeds {cap:.2f}' cap."
                    ),
                    position_ids=[p["position_id"] for p in col],
                    suggested_fix="Remove a unit from this column or move to a different stack.",
                ))
    return violations


def _bt_dump_hydraulic(positions, sku_map):
    """BT_DUMP_HYDRAULIC - hydraulic units should be reviewed if loaded on upper deck."""
    violations = []
    for p in positions:
        zone = _normalize_bt_zone(p["deck_zone"])
        if zone != "upper_deck":
            continue
        sku = sku_map.get(p["item_number"])
        if sku and sku.get("floor_type") == "hydraulic":
            violations.append(Violation(
                severity="warning",
                rule_code="BT_DUMP_HYDRAULIC",
                message=f"{p['item_number']}: Hydraulic unit on upper deck - confirm this placement is acceptable.",
                position_ids=[p["position_id"]],
                suggested_fix="Move hydraulic unit to lower deck if needed.",
            ))
    return violations


def _bt_gn_sequence(positions, sku_map):
    """BT_GN_SEQUENCE - OA model cannot be bottom unit; tandem dual sequence rules."""
    violations = []

    # Check OA not on layer 1
    for p in positions:
        sku = sku_map.get(p["item_number"])
        if sku and sku.get("model") == "OA" and p["layer"] == 1:
            violations.append(Violation(
                severity="warning",
                rule_code="BT_GN_SEQUENCE",
                message=f"{p['item_number']}: OA model cannot be the bottom unit in a stack.",
                position_ids=[p["position_id"]],
                suggested_fix="Place a different GN model as the bottom unit; move OA to layer 2+.",
            ))

    # Stub: full tandem dual sequence logic not yet implemented
    violations.append(Violation(
        severity="info",
        rule_code="BT_GN_SEQUENCE_STUB",
        message="Full tandem dual wheel-type sequence rules not yet implemented - verify GN order manually.",
        position_ids=[],
    ))
    return violations


