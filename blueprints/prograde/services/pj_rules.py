"""
PJ constraint rules - prototype set.
height_ref is loaded once at the top of check() and passed through;
no inner-loop DB calls.
"""
from .models import Violation
from .. import db

_PJ_GOOSENECK_HEIGHT_FT = 6.0
_PJ_GOOSENECK_CATEGORIES = {
    "gooseneck",
    "gooseneck_flatdeck",
    "gooseneck_quest",
    "gooseneck_pintle",
    "gooseneck_variants",
}
_PJ_GOOSENECK_MODEL_PREFIXES = {"LD", "LQ", "LS", "LX", "LY", "PL"}
_PJ_DUMP_CATEGORIES = {
    "dump_lowside",
    "dump_highside_3ft",
    "dump_highside_4ft",
    "dump_small",
    "dump_variants",
    "dump_gn",
}
_REAR_POCKET_LEN_FT = 5.0


def _normalize_dump_height_ft(value):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if abs(parsed - 3.0) <= 0.05:
        return 3.0
    if abs(parsed - 4.0) <= 0.05:
        return 4.0
    return None


def _row_to_dict(row):
    if isinstance(row, dict):
        return row
    return dict(row or {})


def _position_uses_gooseneck(row, sku):
    pos = _row_to_dict(row)
    override_reason = str(pos.get("override_reason") or "")
    for token in override_reason.split(";"):
        t = token.strip().lower()
        if t == "tongue_profile:gooseneck":
            return True
        if t == "tongue_profile:standard":
            return False

    sku_map = _row_to_dict(sku)
    category = str(sku_map.get("pj_category") or "").strip().lower()
    if category in _PJ_GOOSENECK_CATEGORIES:
        return True

    model = str(sku_map.get("model") or "").upper()
    model_prefix = "".join(ch for ch in model if ch.isalnum())[:2]
    return model_prefix in _PJ_GOOSENECK_MODEL_PREFIXES


def _position_dump_height_override_ft(row, sku):
    pos = _row_to_dict(row)
    sku_map = _row_to_dict(sku)
    category = str(sku_map.get("pj_category") or "").strip().lower()
    if category not in _PJ_DUMP_CATEGORIES:
        return None
    override_reason = str(pos.get("override_reason") or "")
    for token in override_reason.split(";"):
        token_clean = token.strip()
        if not token_clean or ":" not in token_clean:
            continue
        key, raw_value = token_clean.split(":", 1)
        if key.strip().lower() != "dump_height_ft":
            continue
        return _normalize_dump_height_ft(raw_value.strip())
    return None


def _position_deck_length_ft(position, sku):
    sku_map = _row_to_dict(sku)
    bed = float(sku_map.get("bed_length_measured") or sku_map.get("bed_length_stated") or 0.0)
    if bed > 0:
        return bed
    total = float(sku_map.get("total_footprint") or 0.0)
    tongue = float(sku_map.get("tongue_feet") or 0.0)
    return max(total - tongue, 0.0)


def _position_render_tongue_ft(position, sku):
    if _position_uses_gooseneck(position, sku):
        return 9.0
    sku_map = _row_to_dict(sku)
    return float(sku_map.get("tongue_feet") or 0.0)


def _adjusted_position_footprint(position, sku, offsets):
    pos = _row_to_dict(position)
    deck_len = _position_deck_length_ft(pos, sku)
    render_tongue = _position_render_tongue_ft(pos, sku)
    footprint = float(deck_len + render_tongue)

    # GN nested inside a dump: subtract hidden feet.
    if bool(pos.get("is_nested")) and _position_uses_gooseneck(pos, sku):
        footprint = max(0.0, footprint - float((offsets or {}).get("gn_in_dump_hidden_ft", 7.0) or 7.0))

    # Nested D5: footprint counted inside host, not separately.
    if bool(pos.get("is_nested")) and (sku or {}).get("model") == "D5":
        footprint = 0.0

    return float(footprint)


def _base_units_by_zone(positions):
    columns_by_zone = {}
    for p in positions:
        pos = _row_to_dict(p)
        zone = pos.get("deck_zone")
        seq = pos.get("sequence")
        if zone is None or seq is None:
            continue
        columns_by_zone.setdefault(zone, {}).setdefault(int(seq), []).append(pos)

    base_units_by_zone = {}
    for zone, cols in columns_by_zone.items():
        base_units_by_zone[zone] = {}
        for seq, col in cols.items():
            if not col:
                continue
            sorted_col = sorted(col, key=lambda row: int((row or {}).get("layer") or 0))
            base = next((row for row in sorted_col if int((row or {}).get("layer") or 0) == 1), sorted_col[0])
            base_units_by_zone[zone][seq] = base
    return base_units_by_zone


def _group_columns_by_zone(positions):
    grouped = {"lower_deck": {}, "upper_deck": {}}
    for p in positions:
        pos = _row_to_dict(p)
        zone = pos.get("deck_zone")
        if zone not in grouped:
            continue
        seq = int(pos.get("sequence") or 0)
        grouped[zone].setdefault(seq, []).append(pos)
    for zone in grouped:
        for seq in list(grouped[zone].keys()):
            grouped[zone][seq] = sorted(grouped[zone][seq], key=lambda row: int((row or {}).get("layer") or 0))
    return grouped


def _base_dims_for_column(col, skus, rear_pocket_len_ft=_REAR_POCKET_LEN_FT):
    if not col:
        return {
            "deck_len_ft": 0.0,
            "left_tongue_ft": 0.0,
            "right_tongue_ft": 0.0,
            "rear_pocket_left_ft": 0.0,
            "rear_pocket_right_ft": 0.0,
            "full_span_ft": 0.0,
        }
    base = next((row for row in col if int((row or {}).get("layer") or 0) == 1), col[0])
    sku = skus.get(base.get("item_number")) or {}
    deck_len_ft = max(_position_deck_length_ft(base, sku), 0.0)
    tongue_len_ft = max(_position_render_tongue_ft(base, sku), 0.0)
    is_rotated = bool(base.get("is_rotated"))
    left_tongue_ft = tongue_len_ft if is_rotated else 0.0
    right_tongue_ft = 0.0 if is_rotated else tongue_len_ft
    rear_pocket_ft = min(deck_len_ft, float(rear_pocket_len_ft or _REAR_POCKET_LEN_FT))
    return {
        "deck_len_ft": deck_len_ft,
        "left_tongue_ft": left_tongue_ft,
        "right_tongue_ft": right_tongue_ft,
        "rear_pocket_left_ft": 0.0 if is_rotated else rear_pocket_ft,
        "rear_pocket_right_ft": rear_pocket_ft if is_rotated else 0.0,
        "full_span_ft": max(deck_len_ft + left_tongue_ft + right_tongue_ft, 0.0),
    }


def _column_x_positions_by_zone(grouped_columns, skus, offsets):
    """
    Returns dict[(zone, sequence)] -> local x position (ft) computed from prior column footprints.
    """
    x_positions = {}
    for zone in ("lower_deck", "upper_deck"):
        cols = (grouped_columns or {}).get(zone) or {}
        cursor = 0.0
        prev_dims = None
        prev_right_edge = None
        for seq in sorted(cols.keys()):
            col = cols.get(seq) or []
            dims = _base_dims_for_column(col, skus)
            if zone == "lower_deck" and prev_dims is not None and prev_right_edge is not None:
                overlap_left_tongue = min(
                    max(dims.get("left_tongue_ft", 0.0), 0.0),
                    max(prev_dims.get("rear_pocket_right_ft", 0.0), 0.0),
                )
                overlap_prev_tongue = min(
                    max(prev_dims.get("right_tongue_ft", 0.0), 0.0),
                    max(dims.get("rear_pocket_left_ft", 0.0), 0.0),
                )
                cursor = prev_right_edge + max(dims.get("left_tongue_ft", 0.0), 0.0)
                cursor -= (overlap_left_tongue + overlap_prev_tongue)

            x_positions[(zone, int(seq))] = cursor
            prev_right_edge = (
                cursor
                + max(dims.get("deck_len_ft", 0.0), 0.0)
                + max(dims.get("right_tongue_ft", 0.0), 0.0)
            )
            prev_dims = dims
            cursor = max(prev_right_edge, cursor)
    return x_positions


def compute_pj_length_metrics(
    positions,
    skus=None,
    offsets=None,
    lower_cap_ft=41.0,
    upper_cap_ft=12.0,
    height_ref=None,
    step_gap_ft=1.5,
):
    """
    Compute PJ footprint metrics with first-pass cross-deck tongue awareness.

    Rule modeled:
    - Deck footprints do not overlap.
    - Tongues that face each other across the step can overlap.
    - Overlap credit is min(tongue_left_facing_from_upper, tongue_right_facing_from_lower).
    """
    normalized_positions = [_row_to_dict(p) for p in (positions or [])]

    if skus is None:
        skus = {p["item_number"]: dict(db.get_pj_sku(p["item_number"]) or {}) for p in normalized_positions}
    if offsets is None:
        offsets = db.get_pj_offsets_dict()
    if height_ref is None:
        height_ref = db.get_pj_height_ref_dict()

    legacy_total = 0.0
    for p in normalized_positions:
        sku = skus.get(p.get("item_number"))
        if not sku:
            continue
        legacy_total += _adjusted_position_footprint(p, sku, offsets)

    base_units_by_zone = _base_units_by_zone(normalized_positions)
    grouped_by_zone = _group_columns_by_zone(normalized_positions)
    lower_cols = base_units_by_zone.get("lower_deck", {})
    upper_cols = base_units_by_zone.get("upper_deck", {})

    lower_interface = None
    upper_interface = None
    if lower_cols:
        lower_interface = lower_cols.get(max(lower_cols.keys()))
    if upper_cols:
        upper_interface = upper_cols.get(min(upper_cols.keys()))

    lower_toward_ft = 0.0
    upper_toward_ft = 0.0
    lower_base_ft = 0.0
    upper_base_ft = 0.0

    for base in lower_cols.values():
        sku = skus.get(base.get("item_number"))
        if not sku:
            continue
        lower_base_ft += _adjusted_position_footprint(base, sku, offsets)

    for base in upper_cols.values():
        sku = skus.get(base.get("item_number"))
        if not sku:
            continue
        upper_base_ft += _adjusted_position_footprint(base, sku, offsets)

    rear_underride_credit_ft = 0.0
    for zone in ("lower_deck", "upper_deck"):
        cols = grouped_by_zone.get(zone, {}) or {}
        prev_dims = None
        for seq in sorted(cols.keys()):
            col = cols.get(seq) or []
            dims = _base_dims_for_column(col, skus)
            if prev_dims is not None:
                overlap_left_tongue = min(
                    max(dims.get("left_tongue_ft", 0.0), 0.0),
                    max(prev_dims.get("rear_pocket_right_ft", 0.0), 0.0),
                )
                overlap_prev_tongue = min(
                    max(prev_dims.get("right_tongue_ft", 0.0), 0.0),
                    max(dims.get("rear_pocket_left_ft", 0.0), 0.0),
                )
                rear_underride_credit_ft += max(overlap_left_tongue + overlap_prev_tongue, 0.0)
            prev_dims = dims

    if lower_interface:
        lower_sku = skus.get(lower_interface.get("item_number"), {})
        lower_tongue = _position_render_tongue_ft(lower_interface, lower_sku)
        # Lower deck seam is to the right; non-rotated units point tongue right.
        if not bool(lower_interface.get("is_rotated")):
            lower_toward_ft = lower_tongue

    if upper_interface:
        upper_sku = skus.get(upper_interface.get("item_number"), {})
        upper_tongue = _position_render_tongue_ft(upper_interface, upper_sku)
        # Upper deck seam is to the left; rotated units point tongue left.
        if bool(upper_interface.get("is_rotated")):
            upper_toward_ft = upper_tongue

    overlap_credit_ft = min(lower_toward_ft, upper_toward_ft)
    upper_seam_span_ft = max(float(upper_base_ft) - float(upper_cap_ft or 0.0), 0.0)
    lower_seam_span_ft = max(float(lower_base_ft) - float(lower_cap_ft or 0.0), 0.0)

    low_profile_seam_span_ft = 0.0
    lower_grouped = grouped_by_zone.get("lower_deck", {})
    for seq in sorted(lower_grouped.keys(), reverse=True):
        col_sorted = lower_grouped.get(seq) or []
        if not col_sorted:
            continue
        col_height = float(_col_height(col_sorted, skus, height_ref))
        if col_height <= 0 or col_height > float(step_gap_ft or 0.0):
            break
        base = next((row for row in col_sorted if int((row or {}).get("layer") or 0) == 1), col_sorted[0])
        sku = skus.get(base.get("item_number"))
        if not sku:
            break
        low_profile_seam_span_ft += _adjusted_position_footprint(base, sku, offsets)

    seam_clearance_credit_ft = min(
        max(upper_seam_span_ft - overlap_credit_ft, 0.0),
        max(low_profile_seam_span_ft, 0.0),
    )

    blocked_lower_ft = max(upper_seam_span_ft - overlap_credit_ft - seam_clearance_credit_ft, 0.0)
    blocked_upper_ft = max(lower_seam_span_ft - overlap_credit_ft, 0.0)
    effective_total = max(legacy_total - overlap_credit_ft - seam_clearance_credit_ft - rear_underride_credit_ft, 0.0)

    return {
        "legacy_total_ft": round(legacy_total, 3),
        "effective_total_ft": round(effective_total, 3),
        "overlap_credit_ft": round(overlap_credit_ft, 3),
        "rear_underride_credit_ft": round(rear_underride_credit_ft, 3),
        "seam_clearance_credit_ft": round(seam_clearance_credit_ft, 3),
        "blocked_lower_ft": round(blocked_lower_ft, 3),
        "blocked_upper_ft": round(blocked_upper_ft, 3),
        "lower_base_ft": round(lower_base_ft, 3),
        "upper_base_ft": round(upper_base_ft, 3),
        "upper_seam_span_ft": round(upper_seam_span_ft, 3),
        "lower_seam_span_ft": round(lower_seam_span_ft, 3),
        "low_profile_seam_span_ft": round(low_profile_seam_span_ft, 3),
        "lower_toward_ft": round(lower_toward_ft, 3),
        "upper_toward_ft": round(upper_toward_ft, 3),
    }


def check(positions, carrier) -> list:
    violations = []
    if not positions:
        return violations

    skus = {p["item_number"]: dict(db.get_pj_sku(p["item_number"]) or {}) for p in positions}
    offsets = db.get_pj_offsets_dict()
    height_ref = db.get_pj_height_ref_dict()   # {category: dict} - no DB calls inside rules

    violations += _pj_total_length(positions, carrier, skus, offsets, height_ref)
    violations += _pj_height_lower(positions, carrier, skus, height_ref)
    violations += _pj_height_upper(positions, carrier, skus, height_ref)
    violations += _pj_step_crossing(positions, carrier, skus, offsets)
    violations += _pj_gn_lower_deck(positions, carrier, skus)
    violations += _pj_dtj_offset(positions, skus)
    violations += _pj_d5_nesting(positions, skus)
    violations += _pj_gn_dump_orientation(positions, skus)
    return violations


def _pj_total_length(positions, carrier, skus, offsets, height_ref):
    """PJ_TOTAL_LENGTH - sum of all footprints must be <= 53'."""
    carrier_map = _row_to_dict(carrier) if carrier else {}
    lower_cap = float(carrier_map.get("lower_deck_length_ft") or 41.0)
    upper_cap = float(carrier_map.get("upper_deck_length_ft") or 12.0)
    metrics = compute_pj_length_metrics(
        positions,
        skus=skus,
        offsets=offsets,
        lower_cap_ft=lower_cap,
        upper_cap_ft=upper_cap,
        height_ref=height_ref,
    )
    total = float(metrics.get("effective_total_ft") or 0.0)
    overlap_credit = float(metrics.get("overlap_credit_ft") or 0.0)
    rear_underride_credit = float(metrics.get("rear_underride_credit_ft") or 0.0)
    seam_clearance_credit = float(metrics.get("seam_clearance_credit_ft") or 0.0)

    implicated = [p["position_id"] for p in positions]

    cap = float(carrier_map.get("total_length_ft") or 53.0)
    if total > cap:
        notes = []
        if overlap_credit > 0:
            notes.append(f"{overlap_credit:.1f}' tongue-overlap credit")
        if rear_underride_credit > 0:
            notes.append(f"{rear_underride_credit:.1f}' tongue-under-rear credit")
        if seam_clearance_credit > 0:
            notes.append(f"{seam_clearance_credit:.1f}' low-profile seam credit")
        overlap_note = f" (includes {'; '.join(notes)})." if notes else "."
        return [Violation(
            severity="error",
            rule_code="PJ_TOTAL_LENGTH",
            message=(
                f"Total footprint {total:.1f}' exceeds deck length {cap:.0f}' by {total - cap:.1f}'"
                f"{overlap_note}"
            ),
            position_ids=implicated,
            suggested_fix="Remove or swap a unit to reduce total footprint.",
        )]
    return []


def _col_height(col_sorted, skus, height_ref, use_axle_drop=True):
    """Compute cumulative stack height for a sorted list of positions (layer 1 -> top)."""
    total = 0.0
    for i, row in enumerate(col_sorted):
        p = _row_to_dict(row)
        sku = skus.get(p.get("item_number"))
        if not sku:
            continue
        dump_height_override_ft = _position_dump_height_override_ft(p, sku)
        if dump_height_override_ft is not None:
            total += dump_height_override_ft
            continue
        if _position_uses_gooseneck(p, sku):
            total += _PJ_GOOSENECK_HEIGHT_FT
            continue
        cat = sku.get("pj_category", "")
        ref = height_ref.get(cat)
        if not ref:
            continue
        is_top = (i == len(col_sorted) - 1)
        # GN with axle dropped uses the lower height value
        if use_axle_drop and bool(p.get("gn_axle_dropped")) and ref.get("gn_axle_dropped_ft") is not None:
            total += ref["gn_axle_dropped_ft"]
        elif is_top:
            total += ref["height_top_ft"]
        else:
            total += ref["height_mid_ft"]
    return round(total, 3)


def _pj_height_lower(positions, carrier, skus, height_ref):
    """PJ_HEIGHT_LOWER - column height on lower deck <= clearance above lower deck."""
    clearance = 10.0
    if carrier:
        clearance = round(carrier["max_height_ft"] - carrier["lower_deck_ground_height_ft"], 3)

    lower = [p for p in positions if p["deck_zone"] == "lower_deck"]
    columns: dict = {}
    for p in lower:
        columns.setdefault(p["sequence"], []).append(p)

    violations = []
    for seq, col in columns.items():
        col_sorted = sorted(col, key=lambda p: p["layer"])
        total_height = _col_height(col_sorted, skus, height_ref)
        if total_height > clearance:
            violations.append(Violation(
                severity="error",
                rule_code="PJ_HEIGHT_LOWER",
                message=(
                    f"Column {seq} on lower deck: stacked height {total_height:.2f}' "
                    f"exceeds {clearance:.1f}' clearance."
                ),
                position_ids=[p["position_id"] for p in col_sorted],
                suggested_fix="Move a unit to a different column or swap for a shorter unit.",
            ))
    return violations


def _pj_height_upper(positions, carrier, skus, height_ref):
    """PJ_HEIGHT_UPPER - column height on upper deck <= clearance above upper deck."""
    clearance = 8.5
    if carrier:
        clearance = round(carrier["max_height_ft"] - carrier["upper_deck_ground_height_ft"], 3)

    upper = [p for p in positions if p["deck_zone"] == "upper_deck"]
    columns: dict = {}
    for p in upper:
        columns.setdefault(p["sequence"], []).append(p)

    violations = []
    for seq, col in columns.items():
        col_sorted = sorted(col, key=lambda p: p["layer"])
        total_height = _col_height(col_sorted, skus, height_ref)
        if total_height > clearance:
            violations.append(Violation(
                severity="error",
                rule_code="PJ_HEIGHT_UPPER",
                message=(
                    f"Column {seq} on upper deck: stacked height {total_height:.2f}' "
                    f"exceeds {clearance:.1f}' clearance."
                ),
                position_ids=[p["position_id"] for p in col_sorted],
                suggested_fix="Move unit to lower deck or swap for a shorter unit.",
            ))
    return violations


def _pj_step_crossing(positions, carrier, skus, offsets):
    """
    PJ_STEP_CROSSING - lower deck unit body cannot cross step transition.
    Tongues/necks are appendages and may cross.
    """
    carrier_map = _row_to_dict(carrier) if carrier else {}
    step_x_ft = float(carrier_map.get("lower_deck_length_ft") or 41.5)
    grouped = _group_columns_by_zone(positions)
    x_positions = _column_x_positions_by_zone(grouped, skus, offsets)
    violations = []

    lower_cols = grouped.get("lower_deck", {}) or {}
    for seq, col in lower_cols.items():
        if not col:
            continue
        base = next((row for row in col if int((row or {}).get("layer") or 0) == 1), col[0])
        sku = skus.get(base.get("item_number"))
        if not sku:
            continue
        x_start = float(x_positions.get(("lower_deck", int(seq)), 0.0))
        deck_len_ft = _position_deck_length_ft(base, sku)
        x_end = x_start + max(deck_len_ft, 0.0)
        if x_end > step_x_ft + 1e-9:
            violations.append(
                Violation(
                    severity="error",
                    rule_code="PJ_STEP_CROSSING",
                    message=(
                        f"Unit deck body extends {x_end - step_x_ft:.1f} ft past the step at {step_x_ft:.1f} ft."
                    ),
                    position_ids=[base.get("position_id")],
                    suggested_fix="Move unit to upper deck or use a shorter bed length.",
                )
            )
    return violations


def _pj_gn_lower_deck(positions, carrier, skus):
    """PJ_GN_LOWER_DECK - warn if GN bed > 32' (will span the step)."""
    cap = carrier["gn_max_lower_deck_ft"] if carrier else 32.0
    violations = []
    for p in positions:
        if p["deck_zone"] != "lower_deck":
            continue
        sku = skus.get(p["item_number"])
        if not sku or not _position_uses_gooseneck(p, sku):
            continue
        bed_len = _position_deck_length_ft(p, sku)
        if bed_len > cap:
            violations.append(Violation(
                severity="warning",
                rule_code="PJ_GN_LOWER_DECK",
                message=(
                    f"{p['item_number']}: GN bed {bed_len:.0f}' > {cap:.0f}' - "
                    "unit will span the step. Verify clearance manually."
                ),
                position_ids=[p["position_id"]],
                suggested_fix="Confirm span is acceptable with planner; acknowledge to proceed.",
            ))
    return violations


def _pj_dtj_offset(positions, skus):
    """PJ_DTJ_OFFSET - info reminder that DTJ measured length includes +1' cylinder."""
    violations = []
    for p in positions:
        sku = skus.get(p["item_number"])
        if sku and (sku.get("model") or "").upper().startswith("DTJ"):
            violations.append(Violation(
                severity="info",
                rule_code="PJ_DTJ_OFFSET",
                message=f"{p['item_number']}: Measured length includes +1' DTJ cylinder offset.",
                position_ids=[p["position_id"]],
            ))
    return violations


def _pj_d5_nesting(positions, skus):
    """PJ_D5_NESTING - D5 must be nested inside a valid dump host."""
    from ..brand_config import PJ_DUMP_HOSTS
    violations = []
    for p in positions:
        sku = skus.get(p["item_number"])
        if not sku or sku.get("model") != "D5":
            continue
        if not p["is_nested"] or not p["nested_inside"]:
            violations.append(Violation(
                severity="error",
                rule_code="PJ_D5_NESTING",
                message=f"{p['item_number']}: D5 must be nested inside a dump host (DL/DV/DX/D7/DM).",
                position_ids=[p["position_id"]],
                suggested_fix="Assign D5 as nested inside a compatible dump unit.",
            ))
        else:
            host = next((x for x in positions if x["position_id"] == p["nested_inside"]), None)
            if host:
                host_sku = skus.get(host["item_number"])
                if host_sku and host_sku.get("model") not in PJ_DUMP_HOSTS:
                    violations.append(Violation(
                        severity="error",
                        rule_code="PJ_D5_NESTING",
                        message=(
                            f"{p['item_number']}: D5 nested inside {host_sku.get('model')} - "
                            "invalid host."
                        ),
                        position_ids=[p["position_id"], host["position_id"]],
                        suggested_fix="Move D5 into a valid host: DL, DV, DX, D7, or DM.",
                    ))
    return violations


def _pj_gn_dump_orientation(positions, skus):
    """
    PJ_GN_DUMP_ORIENTATION - nested GN must face same direction as dump host.
    """
    by_id = {_row_to_dict(p).get("position_id"): _row_to_dict(p) for p in positions}
    violations = []
    seen_pairs = set()

    for row in positions:
        pos = _row_to_dict(row)
        if not bool(pos.get("is_nested")):
            continue
        host_id = pos.get("nested_inside")
        if not host_id:
            continue
        sku = skus.get(pos.get("item_number"))
        if not sku or not _position_uses_gooseneck(pos, sku):
            continue
        host = by_id.get(host_id)
        if not host:
            continue
        host_sku = skus.get(host.get("item_number"))
        host_cat = str((host_sku or {}).get("pj_category") or "").strip().lower()
        if host_cat not in _PJ_DUMP_CATEGORIES:
            continue
        pair_key = tuple(sorted((str(pos.get("position_id") or ""), str(host.get("position_id") or ""))))
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        if bool(pos.get("is_rotated")) != bool(host.get("is_rotated")):
            gn_model = (sku or {}).get("model") or pos.get("item_number") or "GN unit"
            dump_model = (host_sku or {}).get("model") or host.get("item_number") or "dump host"
            violations.append(
                Violation(
                    severity="error",
                    rule_code="PJ_GN_DUMP_ORIENTATION",
                    message=(
                        f"GN unit {gn_model} and dump host {dump_model} face opposite directions - "
                        "neck cannot reach the open gate. Rotate one unit so both face the same direction."
                    ),
                    position_ids=[pos.get("position_id"), host.get("position_id")],
                    suggested_fix="Rotate the GN or the dump so both face the same direction.",
                )
            )
    return violations


# -- Height lookup (used by app.py to compute per-column display heights) --

def compute_column_heights(positions, brand, height_ref):
    """
    Given all positions for a session and the height_ref dict,
    return {zone: {sequence: float}} with cumulative stacked height per column.
    Only applies to PJ; BT uses stack_height directly.
    """
    result = {}
    columns_by_zone: dict = {}
    for p in positions:
        zone = p["deck_zone"]
        seq = p["sequence"]
        columns_by_zone.setdefault(zone, {}).setdefault(seq, []).append(p)

    if brand != "pj":
        return result

    skus = {p["item_number"]: dict(db.get_pj_sku(p["item_number"]) or {}) for p in positions}
    for zone, cols in columns_by_zone.items():
        result[zone] = {}
        for seq, col in cols.items():
            sorted_col = sorted(col, key=lambda p: p["layer"])
            result[zone][seq] = _col_height(sorted_col, skus, height_ref)
    return result


