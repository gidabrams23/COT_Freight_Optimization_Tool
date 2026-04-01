"""
PJ constraint rules — prototype set.
height_ref is loaded once at the top of check() and passed through;
no inner-loop DB calls.
"""
from services.models import Violation
import db


def check(positions, carrier) -> list:
    violations = []
    if not positions:
        return violations

    skus = {p["item_number"]: dict(db.get_pj_sku(p["item_number"]) or {}) for p in positions}
    offsets = db.get_pj_offsets_dict()
    height_ref = db.get_pj_height_ref_dict()   # {category: dict} — no DB calls inside rules

    violations += _pj_total_length(positions, carrier, skus, offsets)
    violations += _pj_height_lower(positions, carrier, skus, height_ref)
    violations += _pj_height_upper(positions, carrier, skus, height_ref)
    violations += _pj_gn_lower_deck(positions, carrier, skus)
    violations += _pj_dtj_offset(positions, skus)
    violations += _pj_d5_nesting(positions, skus)
    return violations


def _pj_total_length(positions, carrier, skus, offsets):
    """PJ_TOTAL_LENGTH — sum of all footprints must be ≤ 53'."""
    total = 0.0
    implicated = []
    for p in positions:
        sku = skus.get(p["item_number"])
        if not sku:
            continue
        footprint = sku.get("total_footprint") or 0.0

        # GN nested inside a dump: subtract hidden feet
        if p["is_nested"] and sku.get("pj_category") == "gooseneck":
            footprint = max(0, footprint - offsets.get("gn_in_dump_hidden_ft", 7.0))

        # Nested D5: footprint counted inside host, not separately
        if p["is_nested"] and sku.get("model") == "D5":
            footprint = 0.0

        total += footprint
        implicated.append(p["position_id"])

    cap = carrier["total_length_ft"] if carrier else 53.0
    if total > cap:
        return [Violation(
            severity="error",
            rule_code="PJ_TOTAL_LENGTH",
            message=f"Total footprint {total:.1f}' exceeds deck length {cap:.0f}' by {total - cap:.1f}'.",
            position_ids=implicated,
            suggested_fix="Remove or swap a unit to reduce total footprint.",
        )]
    return []


def _col_height(col_sorted, skus, height_ref, use_axle_drop=True):
    """Compute cumulative stack height for a sorted list of positions (layer 1 → top)."""
    total = 0.0
    for i, p in enumerate(col_sorted):
        sku = skus.get(p["item_number"])
        if not sku:
            continue
        cat = sku.get("pj_category", "")
        ref = height_ref.get(cat)
        if not ref:
            continue
        is_top = (i == len(col_sorted) - 1)
        # GN with axle dropped uses the lower height value
        if use_axle_drop and p["gn_axle_dropped"] and ref.get("gn_axle_dropped_ft") is not None:
            total += ref["gn_axle_dropped_ft"]
        elif is_top:
            total += ref["height_top_ft"]
        else:
            total += ref["height_mid_ft"]
    return round(total, 3)


def _pj_height_lower(positions, carrier, skus, height_ref):
    """PJ_HEIGHT_LOWER — column height on lower deck ≤ clearance above lower deck."""
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
    """PJ_HEIGHT_UPPER — column height on upper deck ≤ clearance above upper deck."""
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


def _pj_gn_lower_deck(positions, carrier, skus):
    """PJ_GN_LOWER_DECK — warn if GN bed > 32' (will span the step)."""
    cap = carrier["gn_max_lower_deck_ft"] if carrier else 32.0
    violations = []
    for p in positions:
        if p["deck_zone"] != "lower_deck":
            continue
        sku = skus.get(p["item_number"])
        if not sku or sku.get("pj_category") != "gooseneck":
            continue
        if (sku.get("bed_length_measured") or 0) > cap:
            violations.append(Violation(
                severity="warning",
                rule_code="PJ_GN_LOWER_DECK",
                message=(
                    f"{p['item_number']}: GN bed {sku['bed_length_measured']:.0f}' > {cap:.0f}' — "
                    "unit will span the step. Verify clearance manually."
                ),
                position_ids=[p["position_id"]],
                suggested_fix="Confirm span is acceptable with planner; acknowledge to proceed.",
            ))
    return violations


def _pj_dtj_offset(positions, skus):
    """PJ_DTJ_OFFSET — info reminder that DTJ measured length includes +1' cylinder."""
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
    """PJ_D5_NESTING — D5 must be nested inside a valid dump host."""
    from brand_config import PJ_DUMP_HOSTS
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
                            f"{p['item_number']}: D5 nested inside {host_sku.get('model')} — "
                            "invalid host."
                        ),
                        position_ids=[p["position_id"], host["position_id"]],
                        suggested_fix="Move D5 into a valid host: DL, DV, DX, D7, or DM.",
                    ))
    return violations


# ── Height lookup (used by app.py to compute per-column display heights) ──────

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
