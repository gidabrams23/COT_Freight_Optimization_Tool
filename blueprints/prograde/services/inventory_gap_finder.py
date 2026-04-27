from __future__ import annotations

import re
import uuid
from collections import defaultdict

from .. import db
from . import bt_rules, pj_rules

_EPS = 1e-9
_PJ_DUMP_CATEGORIES = {
    "dump_lowside",
    "dump_highside_3ft",
    "dump_highside_4ft",
    "dump_small",
    "dump_gn",
    "dump_variants",
}
_PJ_GOOSENECK_CATEGORIES = {
    "gooseneck",
    "gooseneck_flatdeck",
    "gooseneck_quest",
    "gooseneck_pintle",
    "gooseneck_variants",
    "pintle",
}
_PJ_GOOSENECK_MODEL_PREFIXES = {"LD", "LQ", "LS", "LX", "LY", "PL"}
_BT_LEN_RE = re.compile(r"(?<!\d)(\d{2})(?!\d)")


def build_inventory_gap_data(*, session_id, brand, carrier, canvas, bt_whse="", inventory_whse=None):
    brand_key = str(brand or "").strip().lower()
    if brand_key not in {"bigtex", "pj"}:
        return {
            "remaining_ft": 0.0,
            "remaining_ft_raw": 0.0,
            "rows": [],
            "stack_slots": [],
            "total_available_units": 0,
            "upload_meta": None,
            "mode": "unsupported",
            "warehouse_options": [],
            "selected_warehouse": "ALL",
        }

    positions = _normalized_positions(db.get_positions(session_id), brand_key, carrier_map=dict(carrier or {}))
    columns = _group_columns(positions)
    stack_slots = _build_stack_slots(canvas, columns)
    bt_sku_map = _build_bigtex_sku_map() if brand_key == "bigtex" else {}
    pj_sku_map = _build_pj_sku_map() if brand_key == "pj" else {}
    pj_offsets = db.get_pj_offsets_dict() if brand_key == "pj" else {}
    pj_height_ref = db.get_pj_height_ref_dict() if brand_key == "pj" else {}
    carrier_map = dict(carrier or {})
    baseline_errors = _error_violations(
        brand_key,
        positions,
        carrier_map,
        bt_sku_map=bt_sku_map,
        pj_sku_map=pj_sku_map,
        pj_offsets=pj_offsets,
        pj_height_ref=pj_height_ref,
    )
    baseline_signatures = _error_signatures(baseline_errors)
    baseline_error_count = len(baseline_errors)

    selected_whse_raw = inventory_whse
    if selected_whse_raw is None:
        selected_whse_raw = bt_whse

    if brand_key == "bigtex":
        upload_meta = db.get_bt_inventory_upload_meta()
        whse_codes = db.get_bt_inventory_whse_codes()
        selected_whse = str(selected_whse_raw or "").strip().upper()
        if selected_whse == "ALL":
            selected_whse = ""
        if selected_whse and selected_whse not in whse_codes:
            selected_whse = ""
        candidates = _build_bt_candidates(bt_sku_map, whse_code=selected_whse)
        warehouse_options = [{"value": "ALL", "label": "All Warehouses"}]
        warehouse_options.extend({"value": code, "label": code} for code in whse_codes)
        mode = "bt_upload"
    else:
        upload_meta = db.get_pj_inventory_upload_meta()
        whse_codes = db.get_pj_inventory_whse_codes()
        selected_whse = str(selected_whse_raw or "").strip().upper()
        if selected_whse == "ALL":
            selected_whse = ""
        if selected_whse and selected_whse not in whse_codes:
            selected_whse = ""

        upload_candidates = _build_pj_upload_candidates(
            pj_sku_map,
            whse_code=selected_whse,
            pj_height_ref=pj_height_ref,
        )
        if upload_meta or upload_candidates:
            candidates = upload_candidates
            warehouse_options = [{"value": "ALL", "label": "All Warehouses"}]
            warehouse_options.extend({"value": code, "label": code} for code in whse_codes)
            mode = "pj_upload"
        else:
            candidates = _build_pj_catalog_candidates(pj_sku_map, pj_height_ref=pj_height_ref)
            selected_whse = ""
            warehouse_options = []
            mode = "pj_catalog"

    rows = []
    for candidate in candidates:
        stack_fits = _evaluate_stack_fits(
            candidate,
            stack_slots,
            positions,
            columns,
            brand_key,
            carrier_map,
            baseline_signatures,
            baseline_error_count,
            bt_sku_map=bt_sku_map,
            pj_sku_map=pj_sku_map,
            pj_offsets=pj_offsets,
            pj_height_ref=pj_height_ref,
        )
        rows.append(_build_inventory_row(candidate, stack_fits))

    rows.sort(key=_row_sort_key)

    total_length_ft = _as_float(
        ((canvas or {}).get("trailer_geometry") or {}).get("total_length_ft"),
        _as_float(carrier_map.get("total_length_ft"), 53.0),
    )
    used_ft = _as_float((canvas or {}).get("total_footprint"), 0.0)
    remaining_ft_raw = round(total_length_ft - used_ft, 2)
    remaining_ft = max(remaining_ft_raw, 0.0)

    if brand_key == "bigtex":
        total_available_units = sum(int(r.get("available_count") or 0) for r in rows)
    elif mode == "pj_upload":
        total_available_units = sum(int(r.get("available_count") or 0) for r in rows)
    else:
        total_available_units = len(rows)

    return {
        "remaining_ft": remaining_ft,
        "remaining_ft_raw": remaining_ft_raw,
        "rows": rows,
        "stack_slots": stack_slots,
        "total_available_units": total_available_units,
        "upload_meta": dict(upload_meta) if upload_meta else None,
        "mode": mode,
        "warehouse_options": warehouse_options,
        "selected_warehouse": selected_whse or "ALL",
    }


def _row_sort_key(row):
    if row.get("fits_gap"):
        return (
            0,
            -int(row.get("fit_count") or 0),
            -float(row.get("best_fill_efficiency") or 0.0),
            float(row.get("best_residual_ft") or 10**6),
            str(row.get("item_number") or ""),
        )
    return (
        1,
        str(row.get("item_number") or ""),
    )


def _build_inventory_row(candidate, stack_fits):
    fit_list = list(stack_fits or [])
    fit_count = sum(1 for fit in fit_list if fit.get("fits"))
    best_fill_efficiency = max((float(fit.get("fill_efficiency") or 0.0) for fit in fit_list), default=0.0)
    best_residual_ft = min(
        (float(fit.get("residual_ft")) for fit in fit_list if fit.get("residual_ft") is not None),
        default=None,
    )
    suggested_qty = max((int(fit.get("suggested_qty") or 0) for fit in fit_list), default=0)

    row = {
        "item_number": candidate["item_number"],
        "model": candidate.get("model") or "",
        "mcat": candidate.get("mcat") or "",
        "footprint_each": round(_as_float(candidate.get("footprint_each"), 0.0), 2),
        "stack_height_each": round(_as_float(candidate.get("stack_height_each"), 0.0), 2),
        "available_count": candidate.get("available_count"),
        "total_count": candidate.get("total_count"),
        "assigned_count": candidate.get("assigned_count"),
        "built_count": candidate.get("built_count"),
        "future_build_count": candidate.get("future_build_count"),
        "available_built_count": candidate.get("available_built_count"),
        "available_future_count": candidate.get("available_future_count"),
        "is_unmapped": bool(candidate.get("is_unmapped")),
        "sku_in_db": bool(candidate.get("sku_in_db", True)),
        "default_tongue_profile": candidate.get("default_tongue_profile") or "standard",
        "catalog_only": bool(candidate.get("catalog_only")),
        "fits_gap": fit_count > 0,
        "fit_count": fit_count,
        "suggested_qty": suggested_qty,
        "best_fill_efficiency": round(best_fill_efficiency, 6),
        "best_residual_ft": round(best_residual_ft, 2) if best_residual_ft is not None else None,
        "stack_fits": fit_list,
    }
    return row


def _evaluate_stack_fits(
    candidate,
    stack_slots,
    base_positions,
    base_columns,
    brand,
    carrier_map,
    baseline_signatures,
    baseline_error_count,
    *,
    bt_sku_map,
    pj_sku_map,
    pj_offsets,
    pj_height_ref,
):
    results = []
    for slot in stack_slots:
        results.append(
            _evaluate_single_stack_fit(
                candidate,
                slot,
                base_positions,
                base_columns,
                brand,
                carrier_map,
                baseline_signatures,
                baseline_error_count,
                bt_sku_map=bt_sku_map,
                pj_sku_map=pj_sku_map,
                pj_offsets=pj_offsets,
                pj_height_ref=pj_height_ref,
            )
        )
    return results


def _evaluate_single_stack_fit(
    candidate,
    slot,
    base_positions,
    base_columns,
    brand,
    carrier_map,
    baseline_signatures,
    baseline_error_count,
    *,
    bt_sku_map,
    pj_sku_map,
    pj_offsets,
    pj_height_ref,
):
    stack_index = int(slot.get("stack_index") or 0)
    target_zone = slot.get("target_zone")
    target_sequence = int(slot.get("target_sequence") or 0)
    stack_on_position_id = slot.get("stack_on_position_id")
    remaining_height_ft = _as_float(slot.get("remaining_height_ft"), 0.0)
    stack_length_ft = _as_float(slot.get("stack_length_ft"), 0.0)
    candidate_height_ft = _as_float(candidate.get("stack_height_each"), 0.0)
    fit_result = {
        "stack_index": stack_index,
        "target_zone": target_zone,
        "target_sequence": target_sequence,
        "stack_on_position_id": stack_on_position_id,
        "fits": False,
        "suggested_qty": 0,
        "fill_efficiency": 0.0,
        "residual_ft": None,
    }

    if not target_zone or target_sequence <= 0 or not stack_on_position_id:
        return fit_result
    if remaining_height_ft <= _EPS:
        return fit_result
    if not _candidate_fits_stack_length(candidate, stack_length_ft):
        return fit_result
    if candidate_height_ft > _EPS and candidate_height_ft > (remaining_height_ft + _EPS):
        return fit_result

    available_count = candidate.get("available_count")
    if available_count is not None and int(available_count or 0) <= 0:
        return fit_result
    if candidate_height_ft <= _EPS:
        return fit_result

    max_qty = int(remaining_height_ft // candidate_height_ft)
    if available_count is not None:
        max_qty = min(max_qty, int(available_count or 0))
    if max_qty <= 0:
        return fit_result

    used_height = max_qty * candidate_height_ft
    fill_efficiency = min(used_height / remaining_height_ft, 1.0) if remaining_height_ft > _EPS else 0.0
    residual_ft = max(remaining_height_ft - used_height, 0.0)

    fit_result["fits"] = True
    fit_result["suggested_qty"] = int(max_qty)
    fit_result["fill_efficiency"] = round(float(fill_efficiency), 6)
    fit_result["residual_ft"] = round(float(residual_ft), 3)
    return fit_result


def _candidate_fits_stack_length(candidate, stack_length_ft):
    stack_len = _as_float(stack_length_ft, 0.0)
    footprint = _as_float(candidate.get("footprint_each"), 0.0)
    if stack_len <= _EPS or footprint <= _EPS:
        return False
    return footprint <= stack_len + _EPS


def _introduces_new_errors(
    option,
    simulated_positions,
    brand,
    carrier_map,
    baseline_signatures,
    baseline_error_count,
    *,
    bt_sku_map,
    pj_sku_map,
    pj_offsets,
    pj_height_ref,
):
    sim_errors = _error_violations(
        brand,
        simulated_positions,
        carrier_map,
        bt_sku_map=bt_sku_map,
        pj_sku_map=pj_sku_map,
        pj_offsets=pj_offsets,
        pj_height_ref=pj_height_ref,
    )
    sim_signatures = _error_signatures(sim_errors)
    candidate_pid = option.get("candidate_position_id")
    return (
        len(sim_errors) > baseline_error_count
        or not sim_signatures.issubset(baseline_signatures)
        or any(candidate_pid in _position_id_tuple(v.position_ids) for v in sim_errors)
    )


def _build_stack_slots(canvas, grouped_columns):
    canvas_map = dict(canvas or {})
    clearances = canvas_map.get("clearances") or {}
    col_heights = canvas_map.get("col_heights") or {}
    x_positions = canvas_map.get("x_positions") or {}
    zone_origin_x_ft = canvas_map.get("zone_origin_x_ft") or {}
    measure_segments_by_zone = canvas_map.get("measure_segments_by_zone") or {}

    seg_length_map = {}
    seg_x_abs_map = {}
    for zone, segments in (measure_segments_by_zone or {}).items():
        zone_origin = _as_float((zone_origin_x_ft or {}).get(zone), 0.0)
        for seg in segments or []:
            if str(seg.get("kind") or "") != "stack":
                continue
            sequence = int(seg.get("sequence") or 0)
            if sequence <= 0:
                continue
            key = (zone, sequence)
            seg_length_map[key] = round(_as_float(seg.get("length_ft"), 0.0), 3)
            seg_x_abs_map[key] = round(zone_origin + _as_float(seg.get("x_local_ft"), 0.0), 3)

    slots = []
    for zone in ("lower_deck", "upper_deck"):
        zone_cols = (grouped_columns or {}).get(zone) or {}
        for sequence in sorted(zone_cols.keys()):
            col = list(zone_cols.get(sequence) or [])
            if not col:
                continue
            top_row = max(col, key=lambda p: int(p.get("layer") or 0))
            base_row = next((p for p in col if int(p.get("layer") or 0) == 1), col[0])
            clearance = _as_float((clearances.get(zone) if clearances else None), 0.0)
            col_height = _as_float(((col_heights.get(zone) or {}).get(sequence) if col_heights else None), 0.0)
            remaining_height_ft = max(clearance - col_height, 0.0)
            key = (zone, int(sequence))
            stack_length_ft = _as_float(
                seg_length_map.get(key),
                _as_float(
                    base_row.get("render_footprint_ft"),
                    _as_float(base_row.get("footprint"), _as_float(base_row.get("total_footprint"), 0.0)),
                ),
            )
            x_abs = _as_float(
                seg_x_abs_map.get(key),
                _as_float((zone_origin_x_ft.get(zone) if zone_origin_x_ft else 0.0), 0.0)
                + _as_float((x_positions.get(zone) or {}).get(sequence) if x_positions else 0.0, 0.0),
            )
            slots.append(
                {
                    "target_zone": zone,
                    "target_sequence": int(sequence),
                    "stack_on_position_id": top_row.get("position_id"),
                    "stack_length_ft": round(max(stack_length_ft, 0.0), 2),
                    "remaining_height_ft": round(remaining_height_ft, 2),
                    "zone_clearance_ft": round(max(clearance, 0.0), 2),
                    "current_height_ft": round(max(col_height, 0.0), 2),
                    "x_abs_ft": round(x_abs, 3),
                }
            )

    slots.sort(
        key=lambda slot: (
            float(slot.get("x_abs_ft") or 0.0),
            str(slot.get("target_zone") or ""),
            int(slot.get("target_sequence") or 0),
        )
    )
    for idx, slot in enumerate(slots, start=1):
        slot["stack_index"] = int(idx)
    return slots


def _pick_best_placement(
    candidate,
    opportunities,
    base_positions,
    base_columns,
    brand,
    carrier_map,
    baseline_signatures,
    baseline_error_count,
    *,
    bt_sku_map,
    pj_sku_map,
    pj_offsets,
    pj_height_ref,
):
    options = []
    for opp in opportunities:
        option = _dimension_check(
            candidate,
            opp,
            base_columns,
            brand,
            pj_sku_map=pj_sku_map,
            pj_height_ref=pj_height_ref,
        )
        if option:
            options.append(option)

    options.sort(key=_placement_sort_key)

    for option in options:
        simulated = _simulate_add(base_positions, candidate, option)
        sim_errors = _error_violations(
            brand,
            simulated,
            carrier_map,
            bt_sku_map=bt_sku_map,
            pj_sku_map=pj_sku_map,
            pj_offsets=pj_offsets,
            pj_height_ref=pj_height_ref,
        )
        sim_signatures = _error_signatures(sim_errors)
        candidate_pid = option.get("candidate_position_id")
        introduces_new = (
            len(sim_errors) > baseline_error_count
            or not sim_signatures.issubset(baseline_signatures)
            or any(candidate_pid in _position_id_tuple(v.position_ids) for v in sim_errors)
        )
        if introduces_new:
            continue
        return option
    return None


def _placement_sort_key(option):
    return (
        -float(option.get("fill_efficiency") or 0.0),
        float(option.get("residual_ft") or 0.0),
        0 if option.get("fit_type") == "stack_top" else 1,
        str(option.get("target_zone") or ""),
        int(option.get("target_sequence") or 10**6),
        int(option.get("target_insert_index") if option.get("target_insert_index") is not None else 10**6),
    )


def _dimension_check(candidate, opp, base_columns, brand, *, pj_sku_map, pj_height_ref):
    fit_type = opp.get("fit_type")
    if fit_type == "horizontal":
        return _dimension_check_horizontal(candidate, opp)
    if fit_type == "stack_top":
        if brand == "bigtex":
            return _dimension_check_stack_top_bt(candidate, opp)
        return _dimension_check_stack_top_pj(candidate, opp, base_columns, pj_sku_map, pj_height_ref)
    return None


def _dimension_check_horizontal(candidate, opp):
    footprint_each = _as_float(candidate.get("footprint_each"), 0.0)
    gap_len = _as_float(opp.get("slot_capacity_ft"), 0.0)
    if footprint_each <= _EPS or gap_len <= _EPS or footprint_each > gap_len + _EPS:
        return None

    max_qty = int(gap_len // footprint_each)
    if candidate.get("available_count") is not None:
        max_qty = min(max_qty, int(candidate.get("available_count") or 0))
    if max_qty <= 0:
        return None

    fill_len = max_qty * footprint_each
    fill_efficiency = min(fill_len / gap_len, 1.0) if gap_len > _EPS else 0.0
    residual_ft = max(gap_len - fill_len, 0.0)
    return {
        "fit_type": "horizontal",
        "target_zone": opp.get("target_zone"),
        "target_sequence": opp.get("target_sequence"),
        "target_insert_index": opp.get("target_insert_index"),
        "slot_capacity_ft": round(gap_len, 3),
        "slot_headroom_ft": None,
        "fill_efficiency": round(fill_efficiency, 6),
        "residual_ft": round(residual_ft, 3),
        "suggested_qty": int(max_qty),
        "target_label": _horizontal_target_label(opp.get("target_zone"), opp.get("target_sequence"), opp.get("target_insert_index")),
        "candidate_position_id": f"gap-cand-{uuid.uuid4().hex}",
    }


def _dimension_check_stack_top_bt(candidate, opp):
    headroom = _as_float(opp.get("slot_headroom_ft"), 0.0)
    unit_height = _as_float(candidate.get("stack_height_each"), 0.0)
    if headroom <= _EPS or unit_height <= _EPS or unit_height > headroom + _EPS:
        return None

    max_qty = int(headroom // unit_height)
    if candidate.get("available_count") is not None:
        max_qty = min(max_qty, int(candidate.get("available_count") or 0))
    if max_qty <= 0:
        return None

    used_height = max_qty * unit_height
    fill_efficiency = min(used_height / headroom, 1.0) if headroom > _EPS else 0.0
    residual_ft = max(headroom - used_height, 0.0)
    return {
        "fit_type": "stack_top",
        "target_zone": opp.get("target_zone"),
        "target_sequence": int(opp.get("target_sequence") or 0),
        "target_insert_index": None,
        "slot_capacity_ft": round(headroom, 3),
        "slot_headroom_ft": round(headroom, 3),
        "fill_efficiency": round(fill_efficiency, 6),
        "residual_ft": round(residual_ft, 3),
        "suggested_qty": int(max_qty),
        "target_label": _stack_target_label(opp.get("target_zone"), opp.get("target_sequence")),
        "candidate_position_id": f"gap-cand-{uuid.uuid4().hex}",
    }


def _dimension_check_stack_top_pj(candidate, opp, base_columns, pj_sku_map, pj_height_ref):
    zone = opp.get("target_zone")
    sequence = int(opp.get("target_sequence") or 0)
    clearance = _as_float(opp.get("zone_clearance_ft"), 0.0)
    headroom = _as_float(opp.get("slot_headroom_ft"), 0.0)
    if clearance <= _EPS or headroom <= _EPS or sequence <= 0:
        return None

    col_rows = list((base_columns.get(zone) or {}).get(sequence) or [])
    candidate_base = _base_candidate_position(candidate, zone=zone)
    max_qty, used_height = _pj_stack_top_capacity(
        col_rows,
        candidate_base,
        clearance,
        pj_sku_map,
        pj_height_ref,
    )
    if max_qty <= 0 or used_height <= _EPS:
        return None

    fill_efficiency = min(used_height / headroom, 1.0) if headroom > _EPS else 0.0
    residual_ft = max(headroom - used_height, 0.0)
    return {
        "fit_type": "stack_top",
        "target_zone": zone,
        "target_sequence": sequence,
        "target_insert_index": None,
        "slot_capacity_ft": round(headroom, 3),
        "slot_headroom_ft": round(headroom, 3),
        "fill_efficiency": round(fill_efficiency, 6),
        "residual_ft": round(residual_ft, 3),
        "suggested_qty": int(max_qty),
        "target_label": _stack_target_label(zone, sequence),
        "candidate_position_id": f"gap-cand-{uuid.uuid4().hex}",
    }


def _pj_stack_top_capacity(col_rows, candidate_base, clearance, pj_sku_map, pj_height_ref):
    if clearance <= _EPS:
        return 0, 0.0

    working = sorted([dict(p) for p in (col_rows or [])], key=lambda p: int(p.get("layer") or 0))
    current_h = pj_rules._col_height(working, pj_sku_map, pj_height_ref) if working else 0.0
    qty = 0
    used_height = 0.0
    max_layer = max((int(p.get("layer") or 0) for p in working), default=0)

    while qty < 20:
        probe = dict(candidate_base)
        probe["layer"] = max_layer + 1
        next_col = list(working)
        next_col.append(probe)
        next_h = pj_rules._col_height(next_col, pj_sku_map, pj_height_ref)
        delta = next_h - current_h
        if delta <= _EPS or next_h > clearance + _EPS:
            break
        qty += 1
        used_height += delta
        working = next_col
        current_h = next_h
        max_layer += 1
    return qty, used_height


def _simulate_add(base_positions, candidate, option):
    fit_type = option.get("fit_type")
    zone = option.get("target_zone")
    candidate_pid = option.get("candidate_position_id")
    if fit_type == "stack_top":
        sequence = int(option.get("target_sequence") or 0)
        return _simulate_stack_top(base_positions, candidate, zone, sequence, candidate_pid)
    insert_index = int(option.get("target_insert_index") or 0)
    return _simulate_horizontal_insert(base_positions, candidate, zone, insert_index, candidate_pid)


def _simulate_stack_top(base_positions, candidate, zone, sequence, candidate_pid):
    positions = [dict(p) for p in base_positions]
    max_layer = max(
        (
            int(p.get("layer") or 0)
            for p in positions
            if str(p.get("deck_zone") or "") == zone and int(p.get("sequence") or 0) == int(sequence)
        ),
        default=0,
    )
    candidate_pos = _base_candidate_position(candidate, zone=zone, position_id=candidate_pid)
    candidate_pos["sequence"] = int(sequence)
    candidate_pos["layer"] = int(max_layer + 1)
    positions.append(candidate_pos)
    return positions


def _simulate_horizontal_insert(base_positions, candidate, zone, insert_index, candidate_pid):
    positions = [dict(p) for p in base_positions]
    zone_rows = [dict(p) for p in positions if str(p.get("deck_zone") or "") == zone]
    other_rows = [dict(p) for p in positions if str(p.get("deck_zone") or "") != zone]
    columns = {}
    for p in zone_rows:
        seq = int(p.get("sequence") or 0)
        if seq <= 0:
            continue
        columns.setdefault(seq, []).append(p)
    ordered = [sorted(columns[seq], key=lambda r: int(r.get("layer") or 0)) for seq in sorted(columns.keys())]
    idx = max(0, min(int(insert_index), len(ordered)))
    candidate_col = [_base_candidate_position(candidate, zone=zone, position_id=candidate_pid)]
    ordered.insert(idx, candidate_col)

    rebuilt = []
    for seq_num, col in enumerate(ordered, start=1):
        for layer_num, row in enumerate(col, start=1):
            row_copy = dict(row)
            row_copy["sequence"] = int(seq_num)
            row_copy["layer"] = int(layer_num)
            row_copy["deck_zone"] = zone
            rebuilt.append(row_copy)
    return other_rows + rebuilt


def _base_candidate_position(candidate, *, zone, position_id=None):
    return {
        "position_id": position_id or f"gap-cand-{uuid.uuid4().hex}",
        "session_id": None,
        "brand": "pj" if candidate.get("brand") == "pj" else "bigtex",
        "item_number": candidate["item_number"],
        "deck_zone": zone,
        "layer": 1,
        "sequence": 1,
        "is_nested": 0,
        "nested_inside": None,
        "gn_axle_dropped": 0,
        "is_rotated": 0,
        "override_reason": candidate.get("default_override_reason"),
        "added_at": None,
    }


def _build_opportunities(canvas):
    canvas_map = dict(canvas or {})
    zone_cols = canvas_map.get("zone_cols") or {}
    col_heights = canvas_map.get("col_heights") or {}
    clearances = canvas_map.get("clearances") or {}
    x_positions = canvas_map.get("x_positions") or {}
    zone_caps = canvas_map.get("z_caps") or {}
    zones = []
    for z in ("lower_deck", "upper_deck"):
        if z in zone_caps or z in zone_cols:
            zones.append(z)

    opportunities = []
    for zone in zones:
        cols = zone_cols.get(zone) or {}
        for seq in sorted(cols.keys()):
            col = cols.get(seq) or []
            if not col:
                continue
            top_row = max(col, key=lambda p: int(p.get("layer") or 0))
            clearance = _as_float((clearances or {}).get(zone), 0.0)
            current_h = _as_float((col_heights.get(zone) or {}).get(seq), 0.0)
            headroom = max(clearance - current_h, 0.0)
            if headroom > _EPS:
                opportunities.append(
                    {
                        "fit_type": "stack_top",
                        "target_zone": zone,
                        "target_sequence": int(seq),
                        "target_insert_index": None,
                        "stack_on_position_id": top_row.get("position_id"),
                        "slot_capacity_ft": round(headroom, 3),
                        "slot_headroom_ft": round(headroom, 3),
                        "zone_clearance_ft": round(clearance, 3),
                    }
                )

        opportunities.extend(
            _zone_horizontal_gaps(
                zone=zone,
                cols=cols,
                x_by_seq=x_positions.get(zone) or {},
                zone_cap_ft=_as_float(zone_caps.get(zone), 0.0),
            )
        )
    return opportunities


def _zone_horizontal_gaps(*, zone, cols, x_by_seq, zone_cap_ft):
    cap = max(_as_float(zone_cap_ft, 0.0), 0.0)
    seqs = sorted((cols or {}).keys())
    if cap <= _EPS:
        return []
    if not seqs:
        return [
            {
                "fit_type": "horizontal",
                "target_zone": zone,
                "target_sequence": None,
                "target_insert_index": 0,
                "slot_capacity_ft": round(cap, 3),
                "slot_headroom_ft": None,
            }
        ]

    entries = []
    for seq in seqs:
        col = cols.get(seq) or []
        base = next((p for p in col if int(p.get("layer") or 0) == 1), col[0] if col else None)
        if not base:
            continue
        local_x = _as_float((x_by_seq or {}).get(seq), 0.0)
        deck_len = _as_float(base.get("deck_length_ft"), _as_float(base.get("bed_length"), 0.0))
        tongue_len = _as_float(base.get("render_tongue_length_ft"), _as_float(base.get("tongue_length"), 0.0))
        is_rotated = bool(base.get("is_rotated"))
        left_edge = local_x - (tongue_len if is_rotated else 0.0)
        right_edge = local_x + deck_len + (0.0 if is_rotated else tongue_len)
        entries.append(
            {
                "sequence": int(seq),
                "left": left_edge,
                "right": right_edge,
            }
        )
    entries.sort(key=lambda e: e["sequence"])
    gaps = []
    cursor = 0.0
    for idx, entry in enumerate(entries):
        start = max(min(entry["left"], cap), 0.0)
        end = max(min(entry["right"], cap), 0.0)
        if start - cursor > _EPS:
            gaps.append(
                {
                    "fit_type": "horizontal",
                    "target_zone": zone,
                    "target_sequence": int(entry["sequence"]),
                    "target_insert_index": int(idx),
                    "slot_capacity_ft": round(start - cursor, 3),
                    "slot_headroom_ft": None,
                }
            )
        cursor = max(cursor, end)
    if cap - cursor > _EPS:
        gaps.append(
            {
                "fit_type": "horizontal",
                "target_zone": zone,
                "target_sequence": None,
                "target_insert_index": int(len(entries)),
                "slot_capacity_ft": round(cap - cursor, 3),
                "slot_headroom_ft": None,
            }
        )
    return gaps


def _bt_norm_token(value):
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _bt_family_key(item_number):
    item = str(item_number or "").strip().upper()
    if not item:
        return ""
    if "-" in item:
        return item.split("-", 1)[0].strip()
    m = re.match(r"^([0-9]*[A-Z]+)", _bt_norm_token(item))
    return m.group(1) if m else item[:4]


def _bt_parse_length(item_number):
    raw = str(item_number or "").strip().upper()
    if not raw:
        return None
    parts = [p for p in raw.split("-") if p]
    for token in parts[1:]:
        m = re.match(r"^(\d{2})", token)
        if not m:
            continue
        length = int(m.group(1))
        if 8 <= length <= 53:
            return length
    tail = "-".join(parts[1:]) if len(parts) > 1 else raw
    for m in _BT_LEN_RE.finditer(tail):
        length = int(m.group(1))
        if 8 <= length <= 53:
            return length
    return None


def _common_prefix_len(a, b):
    count = 0
    for ch_a, ch_b in zip(str(a or ""), str(b or "")):
        if ch_a != ch_b:
            break
        count += 1
    return count


def _build_bt_family_index(bt_sku_map):
    index = defaultdict(list)
    for sku in (bt_sku_map or {}).values():
        item = str((sku or {}).get("item_number") or "").strip().upper()
        if not item:
            continue
        family = _bt_family_key(item)
        if family:
            index[family].append(dict(sku))
    return index


def _approximate_bt_sku(item_number, bt_sku_map, family_index):
    raw = str(item_number or "").strip().upper()
    if not raw:
        return None, "none"
    if raw in bt_sku_map:
        return bt_sku_map.get(raw), "exact"

    parts = [p for p in raw.split("-") if p]
    for cut in range(len(parts) - 1, 1, -1):
        trimmed = "-".join(parts[:cut]).strip().upper()
        if trimmed in bt_sku_map:
            return bt_sku_map.get(trimmed), "trim_suffix"

    family = _bt_family_key(raw)
    length = _bt_parse_length(raw)
    if family and length is not None:
        direct_keys = [
            f"{family}-{length:02d}",
            f"{family}-{length}",
            f"{family}-{length:02d}BK",
            f"{family}-{length}BK",
        ]
        for key in direct_keys:
            if key in bt_sku_map:
                return bt_sku_map.get(key), "family_length"

    candidates = list((family_index or {}).get(family) or [])
    if not candidates:
        return None, "none"

    raw_norm = _bt_norm_token(raw)

    def _candidate_sort_key(sku):
        sku_item = str((sku or {}).get("item_number") or "").strip().upper()
        sku_norm = _bt_norm_token(sku_item)
        prefix_len = _common_prefix_len(raw_norm, sku_norm)
        sku_len = _bt_parse_length(sku_item)
        len_diff = abs((sku_len if sku_len is not None else 0) - (length if length is not None else 0))
        if length is None:
            len_diff = 0
        return (
            len_diff,
            -prefix_len,
            abs(len(sku_norm) - len(raw_norm)),
            sku_item,
        )

    best = sorted(candidates, key=_candidate_sort_key)[0]
    return best, "family_nearest"


def _build_bt_candidates(bt_sku_map, whse_code=""):
    family_index = _build_bt_family_index(bt_sku_map)
    candidates = []
    for row in db.get_bt_inventory_snapshot_rows(limit=500, whse_code=whse_code):
        available = int(row["available_count"] or 0)
        if available <= 0:
            continue
        item_number = str(row["item_number"] or "").strip().upper()
        sku = bt_sku_map.get(item_number) or {}
        bt_match_method = "exact" if sku else "none"
        if item_number and not sku:
            approx_sku, bt_match_method = _approximate_bt_sku(item_number, bt_sku_map, family_index)
            if approx_sku:
                sku = approx_sku

        footprint = _as_float(row["sku_total_footprint"], 0.0)
        if footprint <= _EPS:
            footprint = _as_float((sku or {}).get("total_footprint"), 0.0)
        stack_height = _as_float((sku or {}).get("stack_height"), 0.0)

        model = str(row["sku_model"] or (sku or {}).get("model") or "").strip().upper()
        mcat = db.normalize_bigtex_mcat(row["sku_mcat"] or (sku or {}).get("mcat") or "")
        is_unmapped = not bool(model or mcat or footprint)
        candidates.append(
            {
                "brand": "bigtex",
                "item_number": item_number,
                "model": model,
                "mcat": mcat,
                "footprint_each": footprint,
                "stack_height_each": stack_height,
                "available_count": available,
                "total_count": int(row["total_count"] or 0),
                "assigned_count": int(row["assigned_count"] or 0),
                "built_count": int(row["built_count"] or 0),
                "future_build_count": int(row["future_build_count"] or 0),
                "available_built_count": int(row["available_built_count"] or 0),
                "available_future_count": int(row["available_future_count"] or 0),
                "is_unmapped": is_unmapped,
                "approximate_match_method": bt_match_method if bt_match_method != "none" else None,
                "default_tongue_profile": "standard",
                "default_override_reason": None,
                "catalog_only": False,
            }
        )
    return candidates


def _build_pj_catalog_candidates(pj_sku_map, *, pj_height_ref):
    candidates = []
    for row in db.get_pj_skus():
        sku = dict(row)
        item_number = str(sku.get("item_number") or "").strip().upper()
        if not item_number:
            continue
        default_tongue = _pj_default_tongue_profile(sku)
        footprint = _pj_render_footprint(sku, default_tongue)
        stack_height = _pj_stack_height_hint(sku, default_tongue, pj_height_ref=pj_height_ref)
        category = str(sku.get("pj_category") or "").strip().lower()
        model = str(sku.get("model") or "").strip().upper()
        default_override = _pj_default_override_reason(sku, default_tongue)
        candidates.append(
            {
                "brand": "pj",
                "item_number": item_number,
                "model": model,
                "mcat": category.replace("_", " ").title() if category else "",
                "footprint_each": footprint,
                "stack_height_each": stack_height,
                "available_count": None,
                "total_count": None,
                "assigned_count": None,
                "built_count": None,
                "future_build_count": None,
                "available_built_count": None,
                "available_future_count": None,
                "is_unmapped": False,
                "default_tongue_profile": default_tongue,
                "default_override_reason": default_override,
                "catalog_only": True,
            }
        )
        pj_sku_map.setdefault(item_number, sku)
    return candidates


def _build_pj_upload_candidates(pj_sku_map, whse_code="", *, pj_height_ref):
    candidates = []
    for source_row in db.get_pj_inventory_snapshot_rows(limit=1500, whse_code=whse_code):
        row = dict(source_row or {})
        available = int(row.get("available_count") or 0)
        if available <= 0:
            continue

        item_number = str(row.get("item_number") or "").strip().upper()
        if not item_number:
            continue
        sku_in_db = bool(pj_sku_map.get(item_number))
        sku = dict(pj_sku_map.get(item_number) or {})
        if not sku and row.get("sku_model"):
            sku = {
                "item_number": item_number,
                "model": row.get("sku_model"),
                "pj_category": row.get("sku_pj_category"),
                "total_footprint": row.get("sku_total_footprint"),
                "tongue_feet": row.get("sku_tongue_feet"),
                "dump_side_height_ft": row.get("sku_dump_side_height_ft"),
            }

        category = str(sku.get("pj_category") or row.get("normalized_category") or "").strip().lower()
        model = str(sku.get("model") or row.get("normalized_model") or "").strip().upper()
        default_tongue = _pj_default_tongue_profile(sku) if sku else _pj_default_tongue_profile_from_values(model, category)
        default_override = (
            _pj_default_override_reason(sku, default_tongue)
            if sku
            else _pj_default_override_reason_from_values(
                category=category,
                tongue_profile=default_tongue,
                dump_side_height_ft=row.get("sku_dump_side_height_ft"),
            )
        )
        footprint = (
            _pj_render_footprint(sku, default_tongue)
            if sku
            else _as_float(row.get("footprint_each"), 0.0)
        )
        stack_height = _as_float(row.get("stack_height_each"), 0.0)
        if sku:
            stack_height = max(
                stack_height,
                _pj_stack_height_hint(
                    sku,
                    default_tongue,
                    pj_height_ref=pj_height_ref,
                ),
            )
        elif stack_height <= _EPS:
            stack_height = _pj_stack_height_hint_from_values(
                category=category,
                tongue_profile=default_tongue,
                pj_height_ref=pj_height_ref,
                dump_side_height_ft=row.get("sku_dump_side_height_ft"),
            )
        match_method = str(row.get("match_method") or "").strip().lower()

        candidates.append(
            {
                "brand": "pj",
                "item_number": item_number,
                "model": model,
                "mcat": category.replace("_", " ").title() if category else "",
                "footprint_each": footprint,
                "stack_height_each": stack_height,
                "available_count": available,
                "total_count": int(row["total_count"] or 0),
                "assigned_count": int(row["assigned_count"] or 0),
                "built_count": 0,
                "future_build_count": 0,
                "available_built_count": 0,
                "available_future_count": 0,
                "is_unmapped": match_method == "unmapped",
                "sku_in_db": sku_in_db,
                "default_tongue_profile": default_tongue,
                "default_override_reason": default_override,
                "catalog_only": False,
            }
        )
        if sku:
            pj_sku_map.setdefault(item_number, sku)
    return candidates


def _error_violations(brand, positions, carrier_map, *, bt_sku_map, pj_sku_map, pj_offsets, pj_height_ref):
    if brand == "bigtex":
        violations = []
        violations.extend(bt_rules._bt_total_length(positions, bt_sku_map, carrier_map, {}))
        violations.extend(bt_rules._bt_height(positions, bt_sku_map, carrier_map, {}))
    else:
        violations = []
        violations.extend(pj_rules._pj_total_length(positions, carrier_map, pj_sku_map, pj_offsets, pj_height_ref))
        violations.extend(pj_rules._pj_height_lower(positions, carrier_map, pj_sku_map, pj_height_ref, pj_offsets))
        violations.extend(pj_rules._pj_height_upper(positions, carrier_map, pj_sku_map, pj_height_ref, pj_offsets))
        violations.extend(pj_rules._pj_step_crossing(positions, carrier_map, pj_sku_map, pj_offsets))
        violations.extend(pj_rules._pj_d5_nesting(positions, pj_sku_map))
        violations.extend(pj_rules._pj_gn_dump_orientation(positions, pj_sku_map))
    return [v for v in violations if getattr(v, "severity", "") == "error"]


def _error_signatures(violations):
    return {
        (
            str(v.rule_code or "").strip().upper(),
            _position_id_tuple(v.position_ids),
        )
        for v in (violations or [])
    }


def _position_id_tuple(position_ids):
    normalized = [str(pid) for pid in (position_ids or []) if str(pid)]
    normalized.sort()
    return tuple(normalized)


def _build_bigtex_sku_map():
    return {
        str(row["item_number"]).strip().upper(): dict(row)
        for row in db.get_bigtex_skus()
    }


def _build_pj_sku_map():
    return {
        str(row["item_number"]).strip().upper(): dict(row)
        for row in db.get_pj_skus()
    }


def _group_columns(positions):
    grouped = {"lower_deck": {}, "upper_deck": {}}
    for p in positions:
        zone = str(p.get("deck_zone") or "")
        if zone not in grouped:
            continue
        seq = int(p.get("sequence") or 0)
        if seq <= 0:
            continue
        grouped[zone].setdefault(seq, []).append(dict(p))
    for zone in grouped:
        for seq in list(grouped[zone].keys()):
            grouped[zone][seq] = sorted(grouped[zone][seq], key=lambda row: int(row.get("layer") or 0))
    return grouped


def _normalized_positions(rows, brand, carrier_map=None):
    normalized = []
    for row in rows or []:
        p = dict(row)
        p["deck_zone"] = _normalize_zone(brand, p.get("deck_zone"), carrier_map=carrier_map)
        normalized.append(p)
    return normalized


def _normalize_zone(brand, zone, carrier_map=None):
    zone_value = str(zone or "").strip()
    if brand != "bigtex":
        return zone_value
    legacy_map = {
        "stack_1": "lower_deck",
        "stack_2": "lower_deck",
        "stack_3": "upper_deck",
    }
    normalized = legacy_map.get(zone_value, zone_value)
    carrier_key = str((carrier_map or {}).get("carrier_type") or "").strip().lower()
    upper_len = _as_float((carrier_map or {}).get("upper_deck_length_ft"), 0.0)
    if normalized == "upper_deck" and (carrier_key == "ground_pull" or upper_len <= 0.0):
        return "lower_deck"
    return normalized


def _pj_default_tongue_profile(sku):
    category = str(sku.get("pj_category") or "").strip().lower()
    if category in _PJ_GOOSENECK_CATEGORIES:
        return "gooseneck"
    model = "".join(ch for ch in str(sku.get("model") or "").upper() if ch.isalnum())
    if model[:2] in _PJ_GOOSENECK_MODEL_PREFIXES:
        return "gooseneck"
    return "standard"


def _pj_default_tongue_profile_from_values(model, category):
    category_key = str(category or "").strip().lower()
    if category_key in _PJ_GOOSENECK_CATEGORIES:
        return "gooseneck"
    model_norm = "".join(ch for ch in str(model or "").upper() if ch.isalnum())
    if model_norm[:2] in _PJ_GOOSENECK_MODEL_PREFIXES:
        return "gooseneck"
    return "standard"


def _pj_deck_length_ft(sku):
    return max(
        _as_float(sku.get("bed_length_measured"), _as_float(sku.get("bed_length_stated"), 0.0)),
        0.0,
    )


def _pj_render_tongue_ft(sku, tongue_profile):
    if str(tongue_profile or "").strip().lower() == "gooseneck":
        return 9.0
    return max(_as_float(sku.get("tongue_feet"), 0.0), 0.0)


def _pj_render_footprint(sku, tongue_profile):
    return round(_pj_deck_length_ft(sku) + _pj_render_tongue_ft(sku, tongue_profile), 2)


def _pj_stack_height_hint(sku, tongue_profile, *, pj_height_ref=None):
    category = str(sku.get("pj_category") or "").strip().lower()
    if category in _PJ_DUMP_CATEGORIES:
        dump_wall = _normalize_dump_height(sku.get("dump_side_height_ft"))
        if dump_wall is not None:
            return _dump_stacked_height_from_wall(dump_wall) or dump_wall
    top_sku = _as_float(sku.get("height_top_ft"), 0.0)
    if top_sku > _EPS:
        return top_sku
    mid_sku = _as_float(sku.get("height_mid_ft"), 0.0)
    if mid_sku > _EPS:
        return mid_sku
    ref = dict((pj_height_ref or {}).get(category) or {})
    top_h = _as_float(ref.get("height_top_ft"), 0.0)
    if top_h > _EPS:
        return top_h
    mid_h = _as_float(ref.get("height_mid_ft"), 0.0)
    if mid_h > _EPS:
        return mid_h
    return 2.0


def _pj_stack_height_hint_from_values(*, category, tongue_profile, pj_height_ref=None, dump_side_height_ft=None):
    category_key = str(category or "").strip().lower()
    if category_key in _PJ_DUMP_CATEGORIES:
        dump_wall = _normalize_dump_height(dump_side_height_ft)
        if dump_wall is not None:
            return _dump_stacked_height_from_wall(dump_wall) or dump_wall
    ref = dict((pj_height_ref or {}).get(category_key) or {})
    top_h = _as_float(ref.get("height_top_ft"), 0.0)
    if top_h > _EPS:
        return top_h
    mid_h = _as_float(ref.get("height_mid_ft"), 0.0)
    if mid_h > _EPS:
        return mid_h
    return 2.0


def _pj_default_override_reason(sku, tongue_profile):
    tokens = [f"tongue_profile:{tongue_profile}"]
    category = str(sku.get("pj_category") or "").strip().lower()
    if category in _PJ_DUMP_CATEGORIES:
        dump_h = _normalize_dump_height(sku.get("dump_side_height_ft"))
        if dump_h is not None:
            tokens.append(f"dump_height_ft:{dump_h:.1f}")
    return ";".join(tokens)


def _pj_default_override_reason_from_values(*, category, tongue_profile, dump_side_height_ft=None):
    tokens = [f"tongue_profile:{tongue_profile}"]
    category_key = str(category or "").strip().lower()
    if category_key in _PJ_DUMP_CATEGORIES:
        dump_h = _normalize_dump_height(dump_side_height_ft)
        if dump_h is not None:
            tokens.append(f"dump_height_ft:{dump_h:.1f}")
    return ";".join(tokens)


def _normalize_dump_height(value):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if abs(parsed - 2.0) <= 0.05:
        return 2.0
    if abs(parsed - 3.0) <= 0.05:
        return 3.0
    if abs(parsed - 4.0) <= 0.05:
        return 4.0
    return None


def _dump_stacked_height_from_wall(wall_height_ft):
    wall = _as_float(wall_height_ft, 0.0)
    if abs(wall - 2.0) <= 0.05:
        return 4.0
    if abs(wall - 3.0) <= 0.05:
        return 5.0
    if abs(wall - 4.0) <= 0.05:
        return 6.0
    return None


def _stack_target_label(zone, sequence):
    return f"{_zone_label(zone)} S{int(sequence)} Top"


def _horizontal_target_label(zone, target_sequence, insert_index):
    if target_sequence:
        return f"{_zone_label(zone)} before S{int(target_sequence)}"
    return f"{_zone_label(zone)} insert {int(insert_index) + 1}"


def _zone_label(zone):
    if zone == "lower_deck":
        return "Lower Deck"
    if zone == "upper_deck":
        return "Upper Deck"
    return str(zone or "")


def _as_float(value, default=0.0):
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)
