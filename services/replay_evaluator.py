import io
import json
import os
import re
from collections import defaultdict
from datetime import datetime

import pandas as pd

import db
from services import stack_calculator
from services.optimizer import Optimizer


DEFAULT_REPLAY_PRESET = {
    "capacity_feet": 53.0,
    "trailer_type": "STEP_DECK",
    "max_detour_pct": 15.0,
    "time_window_days": 7,
    "geo_radius": 100.0,
    "stack_overflow_max_height": 5,
    "max_back_overhang_ft": 4.0,
    "upper_two_across_max_length_ft": 7.0,
    "enforce_time_window": True,
    "algorithm_version": "v2",
    "v2_low_util_threshold": 70.0,
    "v2_lambda_low_util_count": 560.0,
    "v2_lambda_low_util_depth": 24.0,
    "v2_rescue_passes": 4,
    "v2_grade_rescue_passes": 5,
    "v2_grade_rescue_min_savings": -90.0,
    "v2_grade_rescue_min_gain": 0.0,
    "v2_grade_repair_limit": 12,
    "v2_grade_repair_min_savings": -350.0,
    "v2_fd_rebalance_passes": 3,
    "v2_fd_target_util": 55.0,
    "v2_fd_absorb_max_cost_increase_f": 5000.0,
    "v2_fd_absorb_max_cost_increase_d": 2200.0,
    "v2_fd_absorb_detour_cap": 999.0,
    "v2_fd_candidate_limit": 120,
    "v2_allow_order_interleave": True,
    "v2_pair_neighbors": 18,
    "v2_pair_neighbors_low_util": 56,
    "v2_incremental_neighbors": 20,
    "v2_geo_escape_threshold": 40.0,
    "v2_on_way_bearing_deg": 35.0,
    "v2_on_way_radial_gap_miles": 500.0,
    "v2_home_length_priority_enabled": True,
    "v2_home_length_priority_radius_miles": 250.0,
    "v2_home_length_priority_threshold_ft": 12.0,
    "v2_home_length_priority_weight": 1.0,
    "v2_home_length_priority_max_bonus": 12.0,
    "ops_parity_enabled": False,
    "ops_parity_max_utilization_pct": 120.0,
}

EVAL_SCOPE_DAILY_SHIPPED = "DAILY_SHIPPED"
EVAL_SCOPE_WEEKLY_POOLED = "WEEKLY_POOLED"
EVAL_SCOPES = {EVAL_SCOPE_DAILY_SHIPPED, EVAL_SCOPE_WEEKLY_POOLED}
OVERFILL_EPSILON_FT = 0.05

REQUIRED_FIELDS = {"load_number", "order_number"}
DATE_FIELDS = {"shipped_date", "date_created"}
OPTIONAL_FIELDS = {
    "moh_est_freight_cost",
    "truck_use",
    "miles",
    "ship_via_date",
    "full_name",
}

COLUMN_ALIASES = {
    "load_number": "load_number",
    "load_no": "load_number",
    "load": "load_number",
    "load_id": "load_number",
    "date_created": "date_created",
    "date_created_date": "date_created",
    "created_date": "date_created",
    "created_on": "date_created",
    "date": "date_created",
    "shipped_date": "shipped_date",
    "shipped_date_date": "shipped_date",
    "shipped_on": "shipped_date",
    "ship_date_date": "shipped_date",
    "ship_date": "shipped_date",
    "ship_date_created": "shipped_date",
    "order_number": "order_number",
    "order_no": "order_number",
    "order": "order_number",
    "so_num": "order_number",
    "sonum": "order_number",
    "sales_order": "order_number",
    "name": "order_number",
    "moh_est_freight_cost": "moh_est_freight_cost",
    "est_freight_cost": "moh_est_freight_cost",
    "freight_cost": "moh_est_freight_cost",
    "truck_use": "truck_use",
    "truck_utilization": "truck_use",
    "miles": "miles",
    "ship_via_date": "ship_via_date",
    "shipviadate": "ship_via_date",
    "full_name": "full_name",
    "customer_name": "full_name",
    "customer": "full_name",
}


def _normalize_column_name(value):
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def normalize_evaluation_scope(value):
    key = _clean_text(value).upper()
    if key in EVAL_SCOPES:
        return key
    return EVAL_SCOPE_DAILY_SHIPPED


def _clean_text(value):
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def _to_optional_float(value):
    raw = _clean_text(value)
    if not raw:
        return None
    raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _to_bool(value, default=False):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = _clean_text(value).lower()
    if not text:
        return bool(default)
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def _coerce_positive_float(value, default):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    return max(parsed, 0.0)


def _normalize_order_number(value):
    raw = _clean_text(value)
    if not raw:
        return ""
    if raw.endswith(".0"):
        raw = raw[:-2]
    return raw.strip()


def _parse_date_iso(value):
    raw = _clean_text(value)
    if not raw:
        return None
    parsed = pd.to_datetime(raw, errors="coerce")
    if parsed is None or pd.isna(parsed):
        return None
    try:
        return parsed.date().isoformat()
    except Exception:
        return None


def _extract_plant_code(load_number):
    raw = _clean_text(load_number).upper()
    match = re.match(r"^([A-Z]{2})", raw)
    if not match:
        return ""
    return match.group(1)


def _issue(
    issue_type,
    message,
    severity="warning",
    date_created=None,
    plant_code=None,
    load_number=None,
    order_number=None,
    meta=None,
):
    return {
        "issue_type": issue_type,
        "severity": severity or "warning",
        "message": message,
        "date_created": date_created,
        "plant_code": plant_code,
        "load_number": load_number,
        "order_number": order_number,
        "meta_json": json.dumps(meta or {}),
    }


def _read_report_dataframe(file_obj):
    filename = (getattr(file_obj, "filename", None) or "").strip()
    suffix = os.path.splitext(filename)[1].lower()

    stream = file_obj
    if hasattr(file_obj, "stream"):
        stream = file_obj.stream
    if hasattr(stream, "seek"):
        stream.seek(0)
    raw_bytes = stream.read()
    if hasattr(stream, "seek"):
        stream.seek(0)
    if not raw_bytes:
        raise ValueError("Upload is empty.")

    if suffix in {".xlsx", ".xlsm", ".xls"}:
        return pd.read_excel(io.BytesIO(raw_bytes), dtype=str, keep_default_na=False)
    if suffix == ".csv":
        return pd.read_csv(io.BytesIO(raw_bytes), dtype=str, keep_default_na=False)

    # Strict contract: only CSV/XLSX accepted.
    raise ValueError("Unsupported file type. Upload .csv or .xlsx.")


def parse_report(file_obj):
    df = _read_report_dataframe(file_obj)
    if df is None or df.empty:
        raise ValueError("Report has no rows.")

    column_lookup = {}
    for source in df.columns:
        normalized = _normalize_column_name(source)
        canonical = COLUMN_ALIASES.get(normalized)
        if canonical and canonical not in column_lookup:
            column_lookup[canonical] = source

    missing = sorted(field for field in REQUIRED_FIELDS if field not in column_lookup)
    if missing:
        raise ValueError(
            "Missing required columns: "
            + ", ".join(missing)
            + ". Required fields are Load Number and Order Number (often the 'Name' column)."
        )
    if not any(field in column_lookup for field in DATE_FIELDS):
        raise ValueError(
            "Missing required date column. Provide Shipped Date (preferred) or Date Created."
        )

    rows = []
    issues = []
    date_basis = "shipped_date" if "shipped_date" in column_lookup else "date_created"
    for idx, raw in enumerate(df.to_dict(orient="records"), start=2):
        load_number = _clean_text(raw.get(column_lookup["load_number"]))
        order_number = _normalize_order_number(raw.get(column_lookup["order_number"]))
        shipped_date = _parse_date_iso(raw.get(column_lookup["shipped_date"])) if "shipped_date" in column_lookup else None
        created_date = _parse_date_iso(raw.get(column_lookup["date_created"])) if "date_created" in column_lookup else None
        replay_date = shipped_date or created_date

        if not load_number:
            issues.append(
                _issue(
                    "parse_missing_load_number",
                    "Row missing load number; row skipped.",
                    severity="warning",
                    meta={"row_number": idx},
                )
            )
            continue
        if not replay_date:
            raw_ship = _clean_text(raw.get(column_lookup["shipped_date"])) if "shipped_date" in column_lookup else ""
            raw_created = _clean_text(raw.get(column_lookup["date_created"])) if "date_created" in column_lookup else ""
            issues.append(
                _issue(
                    "parse_invalid_replay_date",
                    "Row has invalid Shipped Date / Date Created; row skipped.",
                    severity="warning",
                    load_number=load_number,
                    order_number=order_number or None,
                    meta={
                        "row_number": idx,
                        "raw_shipped_date": raw_ship,
                        "raw_created_date": raw_created,
                    },
                )
            )
            continue
        if not order_number:
            issues.append(
                _issue(
                    "parse_missing_order_number",
                    "Row missing order number; row skipped.",
                    severity="warning",
                    date_created=replay_date,
                    load_number=load_number,
                    meta={"row_number": idx},
                )
            )
            continue

        plant_code = _extract_plant_code(load_number)
        if not plant_code:
            issues.append(
                _issue(
                    "parse_invalid_load_number",
                    "Unable to infer plant from load number; row skipped.",
                    severity="warning",
                    date_created=replay_date,
                    load_number=load_number,
                    order_number=order_number,
                    meta={"row_number": idx},
                )
            )
            continue

        row = {
            # Historical DB fields use date_created; replay now keys by shipped day when present.
            "date_created": replay_date,
            "plant_code": plant_code,
            "load_number": load_number,
            "order_number": order_number,
            "moh_est_freight_cost": _to_optional_float(
                raw.get(column_lookup["moh_est_freight_cost"])
            ) if "moh_est_freight_cost" in column_lookup else None,
            "truck_use": _to_optional_float(
                raw.get(column_lookup["truck_use"])
            ) if "truck_use" in column_lookup else None,
            "miles": _to_optional_float(raw.get(column_lookup["miles"])) if "miles" in column_lookup else None,
            "ship_via_date": _clean_text(raw.get(column_lookup["ship_via_date"])) if "ship_via_date" in column_lookup else "",
            "full_name": _clean_text(raw.get(column_lookup["full_name"])) if "full_name" in column_lookup else "",
            "source_created_date": created_date or "",
            "source_shipped_date": shipped_date or "",
        }
        rows.append(row)

    return {
        "rows": rows,
        "issues": issues,
        "total_rows": len(df),
        "valid_rows": len(rows),
        "date_basis": date_basis,
    }


def _build_optimizer_params(plant_code, preset):
    combined = dict(DEFAULT_REPLAY_PRESET)
    combined.update(preset or {})
    params = dict(combined)
    params["origin_plant"] = plant_code
    params["trailer_type"] = stack_calculator.normalize_trailer_type(
        params.get("trailer_type"),
        default="STEP_DECK",
    )
    params["capacity_feet"] = float(params.get("capacity_feet") or 53.0)
    params["max_detour_pct"] = float(params.get("max_detour_pct") or 15.0)
    params["time_window_days"] = int(params.get("time_window_days") or 0)
    params["geo_radius"] = float(params.get("geo_radius") or 0.0)
    params["stack_overflow_max_height"] = max(int(params.get("stack_overflow_max_height") or 0), 0)
    params["max_back_overhang_ft"] = max(float(params.get("max_back_overhang_ft") or 0.0), 0.0)
    params["upper_two_across_max_length_ft"] = max(
        float(params.get("upper_two_across_max_length_ft") or 0.0),
        0.0,
    )
    params["enforce_time_window"] = bool(params.get("enforce_time_window", True))
    params["algorithm_version"] = "v2"
    params["ops_parity_enabled"] = _to_bool(params.get("ops_parity_enabled"), default=False)
    params["ops_parity_max_utilization_pct"] = _coerce_positive_float(
        params.get("ops_parity_max_utilization_pct"),
        DEFAULT_REPLAY_PRESET.get("ops_parity_max_utilization_pct", 120.0),
    )
    return params


def _summarize_loads(loads, total_orders):
    total_loads = len(loads)
    total_miles = sum(float(load.get("estimated_miles") or 0.0) for load in loads)
    total_cost = sum(float(load.get("estimated_cost") or 0.0) for load in loads)
    avg_utilization = 0.0
    if total_loads:
        avg_utilization = sum(float(load.get("utilization_pct") or 0.0) for load in loads) / total_loads
    return {
        "total_loads": total_loads,
        "total_orders": int(total_orders or 0),
        "avg_utilization": float(avg_utilization),
        "total_miles": float(total_miles),
        "total_cost": float(total_cost),
    }


def _load_order_numbers(load):
    values = []
    seen = set()
    for line in load.get("lines") or []:
        so_num = _clean_text(line.get("so_num"))
        if not so_num or so_num in seen:
            continue
        seen.add(so_num)
        values.append(so_num)
    return values


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _load_snapshot(load):
    lines = []
    for line in load.get("lines") or []:
        lines.append(
            {
                "so_num": _clean_text(line.get("so_num")),
                "cust_name": _clean_text(line.get("cust_name")),
                "due_date": _clean_text(line.get("due_date")),
                "city": _clean_text(line.get("city")),
                "state": _clean_text(line.get("state")),
                "zip": _clean_text(line.get("zip")),
                "item": _clean_text(line.get("item")),
                "item_desc": _clean_text(line.get("item_desc")),
                "sku": _clean_text(line.get("sku")),
                "qty": float(line.get("qty") or 0.0),
                "sales": float(line.get("sales") or 0.0),
                "unit_length_ft": float(line.get("unit_length_ft") or 0.0),
                "total_length_ft": float(
                    line.get("total_length_ft") or line.get("line_total_feet") or 0.0
                ),
            }
        )

    stop_keys = {
        f"{_clean_text(line.get('state')).upper()}|{_clean_text(line.get('zip'))}"
        for line in lines
        if _clean_text(line.get("state")) or _clean_text(line.get("zip"))
    }
    stop_count = int(load.get("stop_count") or 0)
    if not stop_count and stop_keys:
        stop_count = len(stop_keys)

    return {
        "load_number": _clean_text(load.get("load_number")),
        "origin_plant": _clean_text(load.get("origin_plant")),
        "destination_state": _clean_text(load.get("destination_state")),
        "trailer_type": _clean_text(load.get("trailer_type")).upper(),
        "utilization_pct": float(load.get("utilization_pct") or 0.0),
        "estimated_miles": float(load.get("estimated_miles") or 0.0),
        "estimated_cost": float(load.get("estimated_cost") or 0.0),
        "rate_per_mile": float(load.get("rate_per_mile") or 0.0),
        "stop_count": stop_count,
        "return_to_origin": bool(load.get("return_to_origin")),
        "return_miles": float(load.get("return_miles") or 0.0),
        "return_cost": float(load.get("return_cost") or 0.0),
        "route": _json_safe(load.get("route") or []),
        "route_legs": _json_safe(load.get("route_legs") or []),
        "lines": _json_safe(lines),
    }


def _loads_cost(loads):
    return sum(float(load.get("estimated_cost") or 0.0) for load in (loads or []))


def _loads_avg_utilization(loads):
    loads = list(loads or [])
    if not loads:
        return 0.0
    return sum(float(load.get("utilization_pct") or 0.0) for load in loads) / len(loads)


def _normalized_overfill_profile(optimizer, groups, params):
    if not groups:
        return {
            "overfill_ft": 0.0,
            "selected_trailer_type": "",
            "selected_utilization_pct": 0.0,
            "candidates": [],
        }

    trailer_preference = stack_calculator.normalize_trailer_type(
        params.get("trailer_type"),
        default="STEP_DECK",
    )
    trailer_candidates = []
    primary_trailer = optimizer._preferred_trailer_for_groups(groups, trailer_preference)
    trailer_candidates.append(primary_trailer)
    if primary_trailer.startswith("STEP_DECK") and not optimizer._groups_require_wedge(groups):
        trailer_candidates.append("FLATBED")

    seen = set()
    profiles = []
    for trailer_type in trailer_candidates:
        if trailer_type in seen:
            continue
        seen.add(trailer_type)
        stack_config = optimizer._stack_config_for_groups(
            groups,
            params,
            trailer_type=trailer_type,
        )
        profiles.append(
            {
                "requested_trailer_type": trailer_type,
                "evaluated_trailer_type": _clean_text(stack_config.get("trailer_type")).upper() or trailer_type,
                "overfill_ft": float(stack_calculator.capacity_overflow_feet(stack_config)),
                "utilization_pct": float(stack_config.get("utilization_pct") or 0.0),
                "exceeds_capacity": bool(stack_config.get("exceeds_capacity")),
            }
        )

    if not profiles:
        return {
            "overfill_ft": 0.0,
            "selected_trailer_type": primary_trailer,
            "selected_utilization_pct": 0.0,
            "candidates": [],
        }

    selected = min(
        profiles,
        key=lambda item: (
            float(item.get("overfill_ft") or 0.0),
            float(item.get("utilization_pct") or 0.0),
        ),
    )
    return {
        "overfill_ft": float(selected.get("overfill_ft") or 0.0),
        "selected_trailer_type": _clean_text(selected.get("evaluated_trailer_type")).upper(),
        "selected_utilization_pct": float(selected.get("utilization_pct") or 0.0),
        "candidates": profiles,
    }


def _build_ops_parity_envelope(optimizer, baseline_group_sets, params):
    entries = []
    for load_number, groups in baseline_group_sets:
        profile = _normalized_overfill_profile(optimizer, groups, params)
        overfill_ft = float(profile.get("overfill_ft") or 0.0)
        entries.append(
            {
                "load_number": load_number,
                "overfill_ft": overfill_ft,
                "selected_trailer_type": profile.get("selected_trailer_type") or "",
            }
        )

    overfilled = [entry for entry in entries if float(entry.get("overfill_ft") or 0.0) > OVERFILL_EPSILON_FT]
    max_overfill_ft = 0.0
    if overfilled:
        max_overfill_ft = max(float(entry.get("overfill_ft") or 0.0) for entry in overfilled)
    return {
        "entries": entries,
        "allowed_overfilled_loads": len(overfilled),
        "max_overfill_ft": float(max_overfill_ft),
    }


def _analyze_candidate_overfill(optimizer, loads, params, max_utilization_pct):
    records = []
    overfilled = 0
    max_overfill_ft = 0.0
    max_utilization = 0.0
    util_violations = []
    for idx, load in enumerate(loads or [], start=1):
        groups = load.get("groups") or []
        profile = _normalized_overfill_profile(optimizer, groups, params)
        overfill_ft = float(profile.get("overfill_ft") or 0.0)
        utilization_pct = float(load.get("utilization_pct") or 0.0)
        if utilization_pct > max_utilization:
            max_utilization = utilization_pct
        if overfill_ft > OVERFILL_EPSILON_FT:
            overfilled += 1
            max_overfill_ft = max(max_overfill_ft, overfill_ft)
        if utilization_pct > (max_utilization_pct + 1e-6):
            util_violations.append(
                {
                    "load_index": idx,
                    "utilization_pct": utilization_pct,
                    "cap_pct": max_utilization_pct,
                }
            )
        records.append(
            {
                "load_index": idx,
                "overfill_ft": overfill_ft,
                "utilization_pct": utilization_pct,
                "normalized_trailer_type": profile.get("selected_trailer_type") or "",
            }
        )

    return {
        "overfilled_loads": overfilled,
        "max_overfill_ft": max_overfill_ft,
        "max_utilization_pct": max_utilization,
        "utilization_violations": util_violations,
        "records": records,
    }


def _select_optimized_replay_result(
    optimizer,
    optimization_groups,
    params,
    baseline_group_sets,
):
    strict_strategy, strict_loads, strict_params = _optimize_groups_v2_with_trailer_candidates(
        optimizer,
        optimization_groups,
        params,
    )
    result = {
        "strategy": strict_strategy,
        "loads": strict_loads,
        "params": strict_params,
        "strict_strategy": strict_strategy,
        "strict_loads": strict_loads,
        "strict_params": strict_params,
        "parity_enabled": False,
        "parity_applied": False,
        "parity_envelope": {},
        "parity_candidate": {},
        "parity_reject_reason": "",
    }

    if not _to_bool(params.get("ops_parity_enabled"), default=False):
        return result

    max_utilization_pct = _coerce_positive_float(
        params.get("ops_parity_max_utilization_pct"),
        DEFAULT_REPLAY_PRESET.get("ops_parity_max_utilization_pct", 120.0),
    )
    envelope = _build_ops_parity_envelope(optimizer, baseline_group_sets, params)
    result["parity_enabled"] = True
    result["parity_envelope"] = dict(envelope)
    if envelope["allowed_overfilled_loads"] <= 0 and envelope["max_overfill_ft"] <= OVERFILL_EPSILON_FT:
        result["parity_reject_reason"] = "Baseline has no overfill envelope; strict optimization retained."
        return result

    relaxed_params = dict(params)
    relaxed_params["max_back_overhang_ft"] = float(params.get("max_back_overhang_ft") or 0.0) + float(
        envelope.get("max_overfill_ft") or 0.0
    )

    parity_strategy, parity_loads, parity_params = _optimize_groups_v2_with_trailer_candidates(
        optimizer,
        optimization_groups,
        relaxed_params,
    )
    parity_analysis = _analyze_candidate_overfill(
        optimizer,
        parity_loads,
        params,
        max_utilization_pct=max_utilization_pct,
    )
    result["parity_candidate"] = dict(parity_analysis)

    if parity_analysis["overfilled_loads"] > int(envelope.get("allowed_overfilled_loads") or 0):
        result["parity_reject_reason"] = "Candidate exceeded allowed overfilled-load count."
        return result
    if parity_analysis["max_overfill_ft"] > (float(envelope.get("max_overfill_ft") or 0.0) + 1e-6):
        result["parity_reject_reason"] = "Candidate exceeded allowed overfill severity."
        return result
    if parity_analysis["utilization_violations"]:
        result["parity_reject_reason"] = "Candidate exceeded utilization hard cap."
        return result

    strict_cost = _loads_cost(strict_loads)
    parity_cost = _loads_cost(parity_loads)
    if parity_cost > (strict_cost + 1e-6):
        result["parity_reject_reason"] = "Candidate did not beat strict optimized cost."
        return result

    result["strategy"] = f"{parity_strategy}_ops_parity"
    result["loads"] = parity_loads
    result["params"] = parity_params
    result["parity_applied"] = True
    return result


def _optimize_groups_v2(optimizer, groups, params):
    if not groups:
        return []

    runtime_params = optimizer._runtime_tuned_params(params, len(groups))
    singleton_loads = [optimizer._build_load([group], runtime_params) for group in groups]
    active = {load["_merge_id"]: load for load in singleton_loads}
    time_window_days = (
        runtime_params.get("time_window_days")
        if runtime_params.get("enforce_time_window", True)
        else None
    )
    objective_weights = optimizer._v2_objective_weights(runtime_params)
    max_detour_pct = runtime_params.get("max_detour_pct")

    candidates = optimizer._build_merge_candidates(
        active,
        runtime_params,
        min_savings=0.0,
        radius=runtime_params.get("geo_radius"),
        time_window_days=time_window_days,
        max_detour_pct=max_detour_pct,
        objective_weights=objective_weights,
        min_gain=0.0,
    )
    active = optimizer._merge_candidates(
        active,
        candidates,
        runtime_params,
        min_savings=0.0,
        radius=runtime_params.get("geo_radius"),
        time_window_days=time_window_days,
        max_detour_pct=max_detour_pct,
        objective_weights=objective_weights,
        min_gain=0.0,
    )

    rescue_radius = optimizer._expanded_radius(runtime_params.get("geo_radius") or 0.0)
    rescue_detour_pct = optimizer._rescue_detour_pct(runtime_params.get("max_detour_pct"))
    rescue_passes = int(runtime_params.get("v2_rescue_passes") or 0)
    for _ in range(max(rescue_passes, 0)):
        before = len(active)
        rescue_candidates = optimizer._build_merge_candidates(
            active,
            runtime_params,
            min_savings=-50.0,
            radius=rescue_radius,
            time_window_days=time_window_days,
            require_orphan=True,
            max_detour_pct=rescue_detour_pct,
            objective_weights=objective_weights,
            min_gain=0.0,
        )
        active = optimizer._merge_candidates(
            active,
            rescue_candidates,
            runtime_params,
            min_savings=-50.0,
            radius=rescue_radius,
            time_window_days=time_window_days,
            require_orphan=True,
            max_detour_pct=rescue_detour_pct,
            objective_weights=objective_weights,
            min_gain=0.0,
        )
        if len(active) >= before:
            break

    active = optimizer._grade_rescue_low_util(
        active,
        runtime_params,
        objective_weights,
        time_window_days,
    )
    active = optimizer._rebalance_fd_loads(
        active,
        runtime_params,
        objective_weights,
        time_window_days,
    )
    return list(active.values())


def _optimize_groups_v2_with_trailer_candidates(optimizer, groups, params):
    base_params = dict(params or {})
    base_trailer = stack_calculator.normalize_trailer_type(
        base_params.get("trailer_type"),
        default="STEP_DECK",
    )

    candidates = []
    base_loads = _optimize_groups_v2(optimizer, groups, base_params)
    candidates.append((f"v2_{base_trailer.lower()}", base_loads, base_params))

    if base_trailer.startswith("STEP_DECK"):
        flatbed_params = dict(base_params)
        flatbed_params["trailer_type"] = "FLATBED"
        flatbed_loads = _optimize_groups_v2(optimizer, groups, flatbed_params)
        candidates.append(("v2_flatbed", flatbed_loads, flatbed_params))

    best_strategy, best_loads, best_params = min(
        candidates,
        key=lambda item: (
            _loads_cost(item[1]),
            len(item[1]),
            -_loads_avg_utilization(item[1]),
            sum(float(load.get("estimated_miles") or 0.0) for load in (item[1] or [])),
        ),
    )
    return best_strategy, best_loads, best_params


def _safe_pct(delta, baseline):
    if not baseline:
        return None
    return (delta / baseline) * 100.0


def _build_weekly_bucket_label(parsed_rows):
    dates = sorted(
        {
            _clean_text(row.get("date_created"))
            for row in (parsed_rows or [])
            if _clean_text(row.get("date_created"))
        }
    )
    if not dates:
        return "WEEKLY"
    if len(dates) == 1:
        return f"WEEK OF {dates[0]}"
    return f"WEEK {dates[0]} to {dates[-1]}"


def _evaluate_buckets(parsed_rows, preset, evaluation_scope=EVAL_SCOPE_DAILY_SHIPPED):
    optimizer = Optimizer()
    issues = []
    day_plant_rows = []
    load_metrics = []
    scope_key = normalize_evaluation_scope(evaluation_scope)
    weekly_bucket_label = _build_weekly_bucket_label(parsed_rows) if scope_key == EVAL_SCOPE_WEEKLY_POOLED else ""

    buckets = defaultdict(list)
    for row in parsed_rows:
        bucket_date = row["date_created"]
        if scope_key == EVAL_SCOPE_WEEKLY_POOLED:
            bucket_date = weekly_bucket_label
        buckets[(bucket_date, row["plant_code"])].append(row)

    for (date_created, plant_code), bucket_rows in sorted(buckets.items()):
        params = _build_optimizer_params(plant_code, preset)

        load_order_map = {}
        bucket_order_sequence = []
        seen_bucket_orders = set()
        for row in bucket_rows:
            load_number = row["load_number"]
            order_number = row["order_number"]
            load_orders = load_order_map.setdefault(load_number, [])
            if order_number not in load_orders:
                load_orders.append(order_number)
            if order_number not in seen_bucket_orders:
                seen_bucket_orders.add(order_number)
                bucket_order_sequence.append(order_number)

        so_to_loads = defaultdict(set)
        for load_number, order_numbers in load_order_map.items():
            for so_num in order_numbers:
                so_to_loads[so_num].add(load_number)
        for so_num, load_set in so_to_loads.items():
            if len(load_set) <= 1:
                continue
            issues.append(
                _issue(
                    "duplicate_order_multiple_loads",
                    "Order appears in multiple reported loads; baseline keeps report as-is.",
                    date_created=date_created,
                    plant_code=plant_code,
                    order_number=so_num,
                    meta={"load_numbers": sorted(load_set)},
                )
            )

        reported_orders = set(bucket_order_sequence)
        order_rows = db.list_orders_by_so_nums(plant_code, sorted(reported_orders))
        order_summary_map = {
            _clean_text(row.get("so_num")): row
            for row in order_rows
            if _clean_text(row.get("so_num"))
        }
        matched_orders = set(order_summary_map.keys())
        missing_orders = sorted(reported_orders - matched_orders)
        for so_num in missing_orders:
            issues.append(
                _issue(
                    "missing_order",
                    "Order from report not found in current app order data for this plant.",
                    date_created=date_created,
                    plant_code=plant_code,
                    order_number=so_num,
                )
            )

        line_rows = db.list_order_lines_for_so_nums(plant_code, sorted(matched_orders))
        grouped = optimizer._group_by_so_num(line_rows, order_summary_map)
        group_map = {group.get("key"): group for group in grouped if group.get("key")}

        missing_line_orders = sorted(matched_orders - set(group_map.keys()))
        for so_num in missing_line_orders:
            issues.append(
                _issue(
                    "missing_order_lines",
                    "Order exists but has no eligible line items for replay calculation.",
                    date_created=date_created,
                    plant_code=plant_code,
                    order_number=so_num,
                )
            )

        usable_orders = set(group_map.keys())
        baseline_loads = []
        baseline_group_sets = []
        for load_number, order_numbers in load_order_map.items():
            groups_for_load = [group_map[so_num] for so_num in order_numbers if so_num in usable_orders]
            if not groups_for_load:
                issues.append(
                    _issue(
                        "load_without_matched_orders",
                        "Reported load has no matched orders after reconciliation.",
                        date_created=date_created,
                        plant_code=plant_code,
                        load_number=load_number,
                    )
                )
                continue

            baseline_group_sets.append((load_number, groups_for_load))
            load_data = optimizer._build_load(groups_for_load, params)
            load_data["load_number"] = load_number
            baseline_loads.append(load_data)
            load_metrics.append(
                {
                    "date_created": date_created,
                    "plant_code": plant_code,
                    "scenario": "ACTUAL",
                    "load_key": load_number,
                    "order_count": len(_load_order_numbers(load_data)),
                    "utilization_pct": float(load_data.get("utilization_pct") or 0.0),
                    "estimated_miles": float(load_data.get("estimated_miles") or 0.0),
                    "estimated_cost": float(load_data.get("estimated_cost") or 0.0),
                    "order_numbers_json": json.dumps(_load_order_numbers(load_data)),
                    "load_json": json.dumps(_load_snapshot(load_data)),
                }
            )

        optimization_groups = []
        seen_opt_orders = set()
        for so_num in bucket_order_sequence:
            if so_num in seen_opt_orders or so_num not in usable_orders:
                continue
            seen_opt_orders.add(so_num)
            optimization_groups.append(group_map[so_num])

        optimized_choice = _select_optimized_replay_result(
            optimizer,
            optimization_groups,
            params,
            baseline_group_sets,
        )
        optimized_strategy = optimized_choice["strategy"]
        optimized_loads = optimized_choice["loads"]
        optimized_params = optimized_choice["params"]
        strict_strategy = optimized_choice["strict_strategy"]
        strict_loads = optimized_choice["strict_loads"]

        if _clean_text(optimized_params.get("trailer_type")).upper() != _clean_text(params.get("trailer_type")).upper():
            issues.append(
                _issue(
                    "optimizer_trailer_mode",
                    "Replay used FLATBED trailer mode because it produced a better modeled optimization outcome.",
                    severity="info",
                    date_created=date_created,
                    plant_code=plant_code,
                    meta={
                        "selected_strategy": optimized_strategy,
                        "selected_trailer_type": optimized_params.get("trailer_type"),
                    },
                )
            )

        if optimized_choice.get("parity_enabled"):
            parity_envelope = optimized_choice.get("parity_envelope") or {}
            parity_candidate = optimized_choice.get("parity_candidate") or {}
            issues.append(
                _issue(
                    "ops_parity_envelope",
                    "Replay benchmark used observed overfill envelope (count + max severity) from reported baseline loads.",
                    severity="info",
                    date_created=date_created,
                    plant_code=plant_code,
                    meta={
                        "allowed_overfilled_loads": int(parity_envelope.get("allowed_overfilled_loads") or 0),
                        "max_overfill_ft": float(parity_envelope.get("max_overfill_ft") or 0.0),
                        "candidate_overfilled_loads": int(parity_candidate.get("overfilled_loads") or 0),
                        "candidate_max_overfill_ft": float(parity_candidate.get("max_overfill_ft") or 0.0),
                    },
                )
            )
            if optimized_choice.get("parity_applied"):
                issues.append(
                    _issue(
                        "ops_parity_applied",
                        "Optimized replay used benchmark parity overfill tolerance and still beat strict optimized cost.",
                        severity="info",
                        date_created=date_created,
                        plant_code=plant_code,
                        meta={
                            "strict_strategy": strict_strategy,
                            "selected_strategy": optimized_strategy,
                            "strict_cost": _loads_cost(strict_loads),
                            "selected_cost": _loads_cost(optimized_loads),
                        },
                    )
                )
            else:
                issues.append(
                    _issue(
                        "ops_parity_fallback_strict",
                        "Benchmark parity candidate was rejected; strict optimization was retained.",
                        severity="info",
                        date_created=date_created,
                        plant_code=plant_code,
                        meta={
                            "reason": optimized_choice.get("parity_reject_reason") or "Eligibility guardrail",
                            "strict_strategy": strict_strategy,
                        },
                    )
                )

        if optimized_choice.get("parity_enabled"):
            for idx, load_data in enumerate(strict_loads, start=1):
                load_metrics.append(
                    {
                        "date_created": date_created,
                        "plant_code": plant_code,
                        "scenario": "OPTIMIZED_STRICT",
                        "load_key": f"OPT-STRICT-{idx:03d}",
                        "order_count": len(_load_order_numbers(load_data)),
                        "utilization_pct": float(load_data.get("utilization_pct") or 0.0),
                        "estimated_miles": float(load_data.get("estimated_miles") or 0.0),
                        "estimated_cost": float(load_data.get("estimated_cost") or 0.0),
                        "order_numbers_json": json.dumps(_load_order_numbers(load_data)),
                        "load_json": json.dumps(_load_snapshot(load_data)),
                    }
                )

        for idx, load_data in enumerate(optimized_loads, start=1):
            load_metrics.append(
                {
                    "date_created": date_created,
                    "plant_code": plant_code,
                    "scenario": "OPTIMIZED",
                    "load_key": f"OPT-{idx:03d}",
                    "order_count": len(_load_order_numbers(load_data)),
                    "utilization_pct": float(load_data.get("utilization_pct") or 0.0),
                    "estimated_miles": float(load_data.get("estimated_miles") or 0.0),
                    "estimated_cost": float(load_data.get("estimated_cost") or 0.0),
                    "order_numbers_json": json.dumps(_load_order_numbers(load_data)),
                    "load_json": json.dumps(_load_snapshot(load_data)),
                }
            )

        matched_order_count = len(usable_orders)
        baseline_summary = _summarize_loads(baseline_loads, total_orders=matched_order_count)
        optimized_summary = _summarize_loads(optimized_loads, total_orders=matched_order_count)

        ref_cost = sum(float(row["moh_est_freight_cost"] or 0.0) for row in bucket_rows if row.get("moh_est_freight_cost") is not None)
        ref_miles = sum(float(row["miles"] or 0.0) for row in bucket_rows if row.get("miles") is not None)
        truck_use_values = [float(row["truck_use"]) for row in bucket_rows if row.get("truck_use") is not None]
        ref_truck_use = (
            sum(truck_use_values) / len(truck_use_values)
            if truck_use_values
            else None
        )

        delta_cost = optimized_summary["total_cost"] - baseline_summary["total_cost"]
        day_plant_rows.append(
            {
                "date_created": date_created,
                "plant_code": plant_code,
                "report_rows": len(bucket_rows),
                "report_loads": len(load_order_map),
                "report_orders": len(reported_orders),
                "report_ref_cost": ref_cost,
                "report_ref_miles": ref_miles,
                "report_ref_avg_truck_use": ref_truck_use,
                "matched_orders": matched_order_count,
                "missing_orders": len(missing_orders) + len(missing_line_orders),
                "actual_loads": baseline_summary["total_loads"],
                "actual_orders": baseline_summary["total_orders"],
                "actual_avg_utilization": baseline_summary["avg_utilization"],
                "actual_total_miles": baseline_summary["total_miles"],
                "actual_total_cost": baseline_summary["total_cost"],
                "optimized_loads": optimized_summary["total_loads"],
                "optimized_orders": optimized_summary["total_orders"],
                "optimized_avg_utilization": optimized_summary["avg_utilization"],
                "optimized_total_miles": optimized_summary["total_miles"],
                "optimized_total_cost": optimized_summary["total_cost"],
                "delta_loads": optimized_summary["total_loads"] - baseline_summary["total_loads"],
                "delta_avg_utilization": optimized_summary["avg_utilization"] - baseline_summary["avg_utilization"],
                "delta_total_miles": optimized_summary["total_miles"] - baseline_summary["total_miles"],
                "delta_total_cost": delta_cost,
                "delta_cost_pct": _safe_pct(delta_cost, baseline_summary["total_cost"]),
                "optimized_strategy": optimized_strategy,
                "ops_parity_enabled": bool(optimized_choice.get("parity_enabled")),
                "ops_parity_applied": bool(optimized_choice.get("parity_applied")),
                "ops_parity_envelope_loads": int(
                    (optimized_choice.get("parity_envelope") or {}).get("allowed_overfilled_loads") or 0
                ),
                "ops_parity_envelope_max_overfill_ft": float(
                    (optimized_choice.get("parity_envelope") or {}).get("max_overfill_ft") or 0.0
                ),
                "ops_parity_candidate_loads": int(
                    (optimized_choice.get("parity_candidate") or {}).get("overfilled_loads") or 0
                ),
                "ops_parity_candidate_max_overfill_ft": float(
                    (optimized_choice.get("parity_candidate") or {}).get("max_overfill_ft") or 0.0
                ),
                "ops_parity_reject_reason": optimized_choice.get("parity_reject_reason") or "",
            }
        )

    return day_plant_rows, issues, load_metrics


def build_network_daily_rollup(day_rows):
    grouped = defaultdict(list)
    for row in day_rows or []:
        grouped[row.get("date_created")].append(row)

    rollups = []
    for date_created in sorted(grouped.keys()):
        rows = grouped[date_created]
        actual_loads = sum(int(row.get("actual_loads") or 0) for row in rows)
        optimized_loads = sum(int(row.get("optimized_loads") or 0) for row in rows)

        actual_util_num = sum(float(row.get("actual_avg_utilization") or 0.0) * int(row.get("actual_loads") or 0) for row in rows)
        optimized_util_num = sum(float(row.get("optimized_avg_utilization") or 0.0) * int(row.get("optimized_loads") or 0) for row in rows)
        actual_avg_util = (actual_util_num / actual_loads) if actual_loads else 0.0
        optimized_avg_util = (optimized_util_num / optimized_loads) if optimized_loads else 0.0

        actual_total_cost = sum(float(row.get("actual_total_cost") or 0.0) for row in rows)
        optimized_total_cost = sum(float(row.get("optimized_total_cost") or 0.0) for row in rows)
        delta_cost = optimized_total_cost - actual_total_cost

        rollups.append(
            {
                "date_created": date_created,
                "plants": len(rows),
                "matched_orders": sum(int(row.get("matched_orders") or 0) for row in rows),
                "missing_orders": sum(int(row.get("missing_orders") or 0) for row in rows),
                "actual_loads": actual_loads,
                "actual_avg_utilization": actual_avg_util,
                "actual_total_miles": sum(float(row.get("actual_total_miles") or 0.0) for row in rows),
                "actual_total_cost": actual_total_cost,
                "optimized_loads": optimized_loads,
                "optimized_avg_utilization": optimized_avg_util,
                "optimized_total_miles": sum(float(row.get("optimized_total_miles") or 0.0) for row in rows),
                "optimized_total_cost": optimized_total_cost,
                "delta_loads": optimized_loads - actual_loads,
                "delta_avg_utilization": optimized_avg_util - actual_avg_util,
                "delta_total_miles": sum(float(row.get("delta_total_miles") or 0.0) for row in rows),
                "delta_total_cost": delta_cost,
                "delta_cost_pct": _safe_pct(delta_cost, actual_total_cost),
                "report_ref_cost": sum(float(row.get("report_ref_cost") or 0.0) for row in rows if row.get("report_ref_cost") is not None),
                "report_ref_miles": sum(float(row.get("report_ref_miles") or 0.0) for row in rows if row.get("report_ref_miles") is not None),
            }
        )
    return rollups


def _finalize_replay_run(
    run_id,
    rows,
    parse_issues,
    preset,
    parsed_total_rows,
    summary_meta=None,
    evaluation_scope=EVAL_SCOPE_DAILY_SHIPPED,
):
    scope_key = normalize_evaluation_scope(evaluation_scope)
    parity_requested = _to_bool((preset or {}).get("ops_parity_enabled"), default=False)
    day_rows, eval_issues, load_metrics = _evaluate_buckets(rows, preset, evaluation_scope=scope_key)
    all_issues = list(parse_issues or []) + list(eval_issues or [])

    db.add_replay_eval_source_rows(run_id, rows)
    db.add_replay_eval_day_plant(run_id, day_rows)
    db.add_replay_eval_issues(run_id, all_issues)
    db.add_replay_eval_load_metrics(run_id, load_metrics)

    network_rows = build_network_daily_rollup(day_rows)
    summary_payload = {
        "network_daily": network_rows,
        "day_count": len({row.get("date_created") for row in day_rows}),
        "plant_day_count": len(day_rows),
        "total_matched_orders": sum(int(row.get("matched_orders") or 0) for row in day_rows),
        "total_missing_orders": sum(int(row.get("missing_orders") or 0) for row in day_rows),
        "actual_total_cost": sum(float(row.get("actual_total_cost") or 0.0) for row in day_rows),
        "optimized_total_cost": sum(float(row.get("optimized_total_cost") or 0.0) for row in day_rows),
        "delta_total_cost": sum(float(row.get("delta_total_cost") or 0.0) for row in day_rows),
        "issue_count": len(all_issues),
        "evaluation_scope": scope_key,
        "ops_parity_enabled": parity_requested,
        "ops_parity_buckets_total": sum(1 for row in day_rows if row.get("ops_parity_enabled")),
        "ops_parity_buckets_applied": sum(1 for row in day_rows if row.get("ops_parity_applied")),
        "ops_parity_rejected_buckets": sum(
            1
            for row in day_rows
            if row.get("ops_parity_enabled") and not row.get("ops_parity_applied")
        ),
        "ops_parity_envelope_overfilled_loads": sum(
            int(row.get("ops_parity_envelope_loads") or 0) for row in day_rows
        ),
        "ops_parity_envelope_max_overfill_ft": max(
            [float(row.get("ops_parity_envelope_max_overfill_ft") or 0.0) for row in day_rows] or [0.0]
        ),
    }
    if summary_meta:
        summary_payload.update(summary_meta)

    db.update_replay_eval_run(
        run_id,
        {
            "status": "COMPLETED",
            "completed_at": datetime.utcnow().isoformat(timespec="seconds"),
            "summary_json": json.dumps(summary_payload),
            "total_rows": int(parsed_total_rows or 0),
            "total_days": len({row.get("date_created") for row in day_rows}),
            "total_plants": len({row.get("plant_code") for row in day_rows}),
            "total_orders_matched": summary_payload["total_matched_orders"],
            "total_orders_missing": summary_payload["total_missing_orders"],
            "total_issues": len(all_issues),
        },
    )


def _start_replay_run(filename, preset, created_by=None):
    return db.create_replay_eval_run(
        {
            "filename": (filename or "").strip(),
            "status": "RUNNING",
            "created_by": created_by,
            "params_json": json.dumps(preset or {}, sort_keys=True),
            "created_at": datetime.utcnow().isoformat(timespec="seconds"),
        }
    )


def run_replay_evaluation(file_obj, preset, created_by=None, evaluation_scope=EVAL_SCOPE_DAILY_SHIPPED):
    filename = (getattr(file_obj, "filename", None) or "").strip()
    scope_key = normalize_evaluation_scope(evaluation_scope)
    preset_with_scope = dict(preset or {})
    preset_with_scope["_replay_scope"] = scope_key
    run_id = _start_replay_run(filename, preset=preset_with_scope, created_by=created_by)

    parsed_total_rows = 0
    try:
        parsed = parse_report(file_obj)
        parsed_total_rows = int(parsed.get("total_rows") or 0)
        rows = parsed.get("rows") or []
        parse_issues = parsed.get("issues") or []
        if not rows:
            raise ValueError("Report did not contain any valid rows after parsing.")

        _finalize_replay_run(
            run_id=run_id,
            rows=rows,
            parse_issues=parse_issues,
            preset=preset,
            parsed_total_rows=parsed_total_rows,
            evaluation_scope=scope_key,
            summary_meta={"date_basis": parsed.get("date_basis") or "shipped_date"},
        )
        return run_id
    except Exception as exc:
        db.update_replay_eval_run(
            run_id,
            {
                "status": "FAILED",
                "completed_at": datetime.utcnow().isoformat(timespec="seconds"),
                "summary_json": json.dumps({"error": str(exc)}),
                "total_rows": parsed_total_rows,
            },
        )
        raise


def reproduce_replay_bucket(source_run_id, date_created, plant_code, created_by=None):
    source_run = db.get_replay_eval_run(source_run_id)
    if not source_run:
        raise ValueError("Source replay run was not found.")
    if (source_run.get("status") or "").upper() != "COMPLETED":
        raise ValueError("Source replay run must be completed before bucket reproduction.")

    date_key = _clean_text(date_created)
    plant_key = _clean_text(plant_code).upper()
    if not date_key or not plant_key:
        raise ValueError("Bucket reproduction requires both date_created and plant_code.")

    rows = db.list_replay_eval_source_rows(
        source_run_id,
        date_created=date_key,
        plant_code=plant_key,
    )
    if not rows:
        raise ValueError(
            "No stored source rows were found for that date/plant. Re-run upload first to seed source rows."
        )

    preset = {}
    scope_key = None
    raw_params = source_run.get("params_json")
    if raw_params:
        try:
            parsed_params = json.loads(raw_params)
            if isinstance(parsed_params, dict):
                preset = parsed_params
                if "_replay_scope" in parsed_params:
                    scope_key = normalize_evaluation_scope(parsed_params.get("_replay_scope"))
        except json.JSONDecodeError:
            preset = {}
    if scope_key is None:
        summary = source_run.get("summary_json")
        try:
            summary_data = json.loads(summary) if summary else {}
        except json.JSONDecodeError:
            summary_data = {}
        if isinstance(summary_data, dict):
            scope_key = normalize_evaluation_scope(summary_data.get("evaluation_scope"))
    if scope_key is None:
        scope_key = EVAL_SCOPE_DAILY_SHIPPED

    source_filename = (source_run.get("filename") or "").strip()
    run_label = (
        f"{source_filename} | reproduce {date_key} {plant_key} from #{int(source_run_id)}"
        if source_filename
        else f"reproduce {date_key} {plant_key} from #{int(source_run_id)}"
    )
    run_id = _start_replay_run(run_label, preset=preset, created_by=created_by)

    try:
        _finalize_replay_run(
            run_id=run_id,
            rows=rows,
            parse_issues=[],
            preset=preset,
            parsed_total_rows=len(rows),
            evaluation_scope=scope_key,
            summary_meta={
                "reproduced_from_run_id": int(source_run_id),
                "reproduced_bucket": {"date_created": date_key, "plant_code": plant_key},
            },
        )
        return run_id
    except Exception as exc:
        db.update_replay_eval_run(
            run_id,
            {
                "status": "FAILED",
                "completed_at": datetime.utcnow().isoformat(timespec="seconds"),
                "summary_json": json.dumps({"error": str(exc)}),
                "total_rows": len(rows),
            },
        )
        raise
