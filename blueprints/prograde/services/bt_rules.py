"""
Big Tex constraint rules — prototype set.
"""
from .models import Violation
from .. import db


def check(positions, carrier) -> list:
    violations = []
    if not positions:
        return violations

    all_bt = db.get_bigtex_skus()
    sku_map = {row["item_number"]: dict(row) for row in all_bt}

    stack_configs = {r["config_id"]: dict(r) for r in db.get_bt_stack_configs()}

    violations += _bt_total_length(positions, sku_map, stack_configs)
    violations += _bt_height(positions, sku_map, stack_configs)
    violations += _bt_dump_hydraulic(positions, sku_map)
    violations += _bt_gn_sequence(positions, sku_map)
    return violations


def _bt_total_length(positions, sku_map, stack_configs):
    """BT_TOTAL_LENGTH — max footprint per stack and combined S1+S2 checks."""
    violations = []

    stacks = {"stack_1": [], "stack_2": [], "stack_3": []}
    for p in positions:
        zone = p["deck_zone"]
        if zone in stacks:
            stacks[zone].append(p)

    def max_footprint(stack_positions):
        fps = []
        for p in stack_positions:
            sku = sku_map.get(p["item_number"])
            if sku:
                fps.append(sku["total_footprint"] or 0.0)
        return max(fps) if fps else 0.0

    s1_fp = max_footprint(stacks["stack_1"])
    s2_fp = max_footprint(stacks["stack_2"])
    s3_fp = max_footprint(stacks["stack_3"])

    # 3-Stack utility combined S1+S2
    combined_cfg = stack_configs.get("utility_3stack_combined_1_2")
    if combined_cfg and (s1_fp + s2_fp) > combined_cfg["max_length_ft"]:
        violations.append(Violation(
            severity="error",
            rule_code="BT_TOTAL_LENGTH",
            message=(
                f"Stack 1 ({s1_fp:.0f}') + Stack 2 ({s2_fp:.0f}') = {s1_fp+s2_fp:.0f}' "
                f"exceeds combined limit {combined_cfg['max_length_ft']:.0f}'."
            ),
            position_ids=[p["position_id"] for p in stacks["stack_1"] + stacks["stack_2"]],
            suggested_fix="Swap to shorter units in Stack 1 or Stack 2.",
        ))

    # Stack 3 individual cap
    s3_cfg = stack_configs.get("utility_3stack_stack_3")
    if s3_cfg and s3_fp > s3_cfg["max_length_ft"]:
        violations.append(Violation(
            severity="error",
            rule_code="BT_TOTAL_LENGTH",
            message=f"Stack 3 footprint {s3_fp:.0f}' exceeds limit {s3_cfg['max_length_ft']:.0f}'.",
            position_ids=[p["position_id"] for p in stacks["stack_3"]],
            suggested_fix="Use a shorter unit in Stack 3.",
        ))
    return violations


def _bt_height(positions, sku_map, stack_configs):
    """BT_HEIGHT — cumulative stack_height per position ≤ height cap."""
    violations = []
    stacks = {"stack_1": [], "stack_2": [], "stack_3": []}
    for p in positions:
        if p["deck_zone"] in stacks:
            stacks[p["deck_zone"]].append(p)

    caps = {
        "stack_1": stack_configs.get("utility_3stack_stack_1", {}).get("max_height_ft", 5.25),
        "stack_2": stack_configs.get("utility_3stack_stack_2", {}).get("max_height_ft", 5.25),
        "stack_3": stack_configs.get("utility_3stack_stack_3", {}).get("max_height_ft", 4.0),
    }

    for zone, ps in stacks.items():
        # Group by sequence (column)
        columns: dict = {}
        for p in ps:
            columns.setdefault(p["sequence"], []).append(p)
        for seq, col in columns.items():
            total_h = sum(
                (sku_map.get(p["item_number"]) or {}).get("stack_height", 0) or 0
                for p in col
            )
            cap = caps.get(zone, 5.25)
            if total_h > cap:
                violations.append(Violation(
                    severity="error",
                    rule_code="BT_HEIGHT",
                    message=(
                        f"{zone.replace('_',' ').title()} column {seq}: "
                        f"height {total_h:.2f}' exceeds {cap:.2f}' cap."
                    ),
                    position_ids=[p["position_id"] for p in col],
                    suggested_fix="Remove a unit from this column or move to a different stack.",
                ))
    return violations


def _bt_dump_hydraulic(positions, sku_map):
    """BT_DUMP_HYDRAULIC — hydraulic jack units should be in Stack 1 or Stack 3, not Stack 2."""
    violations = []
    for p in positions:
        if p["deck_zone"] != "stack_2":
            continue
        sku = sku_map.get(p["item_number"])
        if sku and sku.get("floor_type") == "hydraulic":
            violations.append(Violation(
                severity="warning",
                rule_code="BT_DUMP_HYDRAULIC",
                message=f"{p['item_number']}: Hydraulic unit in Stack 2 — consider moving to Stack 1 or Stack 3.",
                position_ids=[p["position_id"]],
                suggested_fix="Move hydraulic unit to Stack 1 or Stack 3.",
            ))
    return violations


def _bt_gn_sequence(positions, sku_map):
    """BT_GN_SEQUENCE — OA model cannot be bottom unit; tandem dual sequence rules."""
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
        message="Full tandem dual wheel-type sequence rules not yet implemented — verify GN order manually.",
        position_ids=[],
    ))
    return violations

