import db
import re
from datetime import date, datetime, timedelta

from services import stack_calculator, validation
from services.optimizer import Optimizer

DEFAULT_BUILD_PARAMS = {
    "origin_plant": "",
    "capacity_feet": "53",
    "trailer_type": "STEP_DECK",
    "max_detour_pct": "15",
    "time_window_days": "7",
    "geo_radius": "100",
    "stack_overflow_max_height": "5",
    "max_back_overhang_ft": "4",
    "orders_start_date": "",
    "algorithm_version": "v2",
    "compare_algorithms": False,
    "optimize_mode": "auto",
    "manual_order_input": "",
}


def _clean_value(value):
    if value is None:
        return ""
    return str(value).strip()


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except (TypeError, ValueError):
        return None


def _truthy(value):
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _get_list(form, key):
    if hasattr(form, "getlist"):
        return form.getlist(key)
    value = form.get(key) if form else None
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _clean_list(values, upper=False):
    cleaned = []
    for value in values or []:
        item = str(value or "").strip()
        if not item:
            continue
        cleaned.append(item.upper() if upper else item)
    return cleaned


def _parse_manual_so_nums(raw_text):
    text = str(raw_text or "").strip()
    if not text:
        return []
    tokens = re.split(r"[\s,;]+", text)
    cleaned = []
    seen = set()
    for token in tokens:
        value = token.strip().strip("\"'")
        if not value:
            continue
        normalized = value.upper()
        if normalized in {"SO_NUM", "ORDER", "ORDERS", "ORDER#", "SO#"}:
            continue
        if value in seen:
            continue
        seen.add(value)
        cleaned.append(value)
    return cleaned


def _format_date_for_message(value):
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    parsed = _parse_date(value)
    if parsed:
        return parsed.strftime("%Y-%m-%d")
    return str(value or "").strip()


def _build_no_eligible_orders_message(params, diagnostics):
    message = "No eligible orders found for this plant."
    if not diagnostics:
        return message

    if (
        diagnostics.get("open_orders_total", 0) > 0
        and diagnostics.get("eligible_order_lines", 0) == 0
    ):
        return (
            "No eligible order lines are currently available. Matching lines are already tied "
            "to approved or active draft sessions."
        )

    if (
        params.get("batch_horizon_enabled")
        and diagnostics.get("groups_after_all_filters", 0) == 0
        and diagnostics.get("groups_after_all_filters_no_batch", 0) > 0
    ):
        horizon_label = _format_date_for_message(params.get("batch_max_due_date"))
        earliest_label = _format_date_for_message(diagnostics.get("first_due_no_batch"))
        if horizon_label and earliest_label:
            return (
                f"No matching orders are due by {horizon_label}. "
                f"Earliest matching due date is {earliest_label}. "
                "Clear or extend Batch Orders Up Until."
            )
        return "No matching orders are due within the selected batch horizon."

    if (
        params.get("customer_filters")
        and diagnostics.get("groups_after_all_filters", 0) == 0
        and diagnostics.get("groups_without_customer_filter", 0) > 0
    ):
        return "No eligible orders match the selected customers within the current filters."

    if (
        params.get("state_filters")
        and diagnostics.get("groups_after_all_filters", 0) == 0
        and diagnostics.get("groups_without_state_filter", 0) > 0
    ):
        return "No eligible orders match the selected states within the current filters."

    return message


def list_loads(origin_plant=None, session_id=None, include_stack_metrics=True):
    loads = db.list_loads(origin_plant, session_id=session_id)
    if not loads:
        return loads

    load_ids = [load.get("id") for load in loads if load.get("id") is not None]
    lines_by_load_id = db.list_load_lines_for_load_ids(load_ids)
    sku_specs = {spec["sku"]: spec for spec in db.list_sku_specs()} if include_stack_metrics else {}
    for load in loads:
        trailer_type = (load.get("trailer_type") or "STEP_DECK").strip().upper()
        lines = lines_by_load_id.get(load.get("id"), [])
        load["lines"] = lines
        if not include_stack_metrics:
            continue
        order_numbers = {line.get("so_num") for line in lines if line.get("so_num")}
        line_items = []
        for line in lines:
            sku = line.get("sku")
            spec = sku_specs.get(sku) if sku else None
            if trailer_type == "STEP_DECK":
                max_stack = (spec or {}).get("max_stack_step_deck") or (spec or {}).get("max_stack_flat_bed") or 1
            else:
                max_stack = (spec or {}).get("max_stack_flat_bed") or 1
            line_items.append(
                {
                    "item": line.get("item"),
                    "item_desc": line.get("item_desc"),
                    "sku": sku,
                    "qty": line.get("qty") or 0,
                    "unit_length_ft": line.get("unit_length_ft") or 0,
                    "max_stack_height": max_stack,
                    "category": (spec or {}).get("category", ""),
                    "order_id": line.get("so_num"),
                }
            )
        if line_items:
            config = stack_calculator.calculate_stack_configuration(
                line_items,
                trailer_type=trailer_type,
            )
            utilization_pct = config.get("utilization_pct", load.get("utilization_pct", 0)) or 0
            load["utilization_pct"] = utilization_pct
            exceeds_capacity = config.get("exceeds_capacity", False)
            load["over_capacity"] = (
                exceeds_capacity
            ) and len(order_numbers) <= 1
            load["trailer_type"] = trailer_type
        else:
            load["over_capacity"] = False
    return loads


def build_loads(form, reset_proposed=True, store_settings=True, session_id=None, session_factory=None, created_by=None):
    ui_toggles = "opt_toggles" in form
    enforce_time_window = _truthy(form.get("enforce_time_window")) if ui_toggles else True
    batch_horizon_enabled = _truthy(form.get("batch_horizon_enabled")) if ui_toggles else False
    state_filters = _clean_list(_get_list(form, "opt_states"), upper=True)
    customer_filters = _clean_list(_get_list(form, "opt_customers"))
    algorithm_version = "v2"
    compare_algorithms = False
    optimize_mode = _clean_value(form.get("optimize_mode", "auto")).lower() if form else "auto"
    if optimize_mode not in {"auto", "manual"}:
        optimize_mode = "auto"
    manual_order_input = _clean_value(form.get("manual_order_input", "")) if form else ""
    selected_so_nums = _parse_manual_so_nums(manual_order_input) if optimize_mode == "manual" else []
    reference_date = _parse_date(form.get("today")) if form else None
    if not reference_date:
        reference_date = date.today()
    orders_start_date = _clean_value(form.get("orders_start_date", "")) if form else ""
    if not orders_start_date:
        orders_start_date = reference_date.strftime("%Y-%m-%d")

    form_data = {
        "origin_plant": _clean_value(form.get("origin_plant", "")),
        "capacity_feet": _clean_value(form.get("capacity_feet", "")),
        "trailer_type": _clean_value(form.get("trailer_type", "")).upper(),
        "max_detour_pct": _clean_value(form.get("max_detour_pct", "")),
        "time_window_days": _clean_value(form.get("time_window_days", "")),
        "geo_radius": _clean_value(form.get("geo_radius", "")),
        "stack_overflow_max_height": _clean_value(
            form.get("stack_overflow_max_height", DEFAULT_BUILD_PARAMS.get("stack_overflow_max_height", "5"))
        ),
        "max_back_overhang_ft": _clean_value(
            form.get("max_back_overhang_ft", DEFAULT_BUILD_PARAMS.get("max_back_overhang_ft", "4"))
        ),
        "enforce_time_window": enforce_time_window,
        "batch_horizon_enabled": batch_horizon_enabled,
        "batch_end_date": _clean_value(form.get("batch_end_date", "")),
        "state_filters": state_filters,
        "customer_filters": customer_filters,
        "orders_start_date": orders_start_date,
        "algorithm_version": algorithm_version,
        "compare_algorithms": compare_algorithms,
        "optimize_mode": optimize_mode,
        "manual_order_input": manual_order_input,
    }

    errors = {}
    validation.validate_required(form_data["origin_plant"], "origin_plant", errors)
    validation.validate_positive_float(form_data["capacity_feet"], "capacity_feet", errors)
    validation.validate_required(form_data["trailer_type"], "trailer_type", errors)
    validation.validate_positive_float(form_data["max_detour_pct"], "max_detour_pct", errors)
    if enforce_time_window:
        validation.validate_positive_int(form_data["time_window_days"], "time_window_days", errors)
    else:
        # When toggle is off, allow missing value and treat as "no date flexibility".
        if not form_data["time_window_days"]:
            form_data["time_window_days"] = "0"
    validation.validate_positive_float(form_data["geo_radius"], "geo_radius", errors)
    parsed_orders_start_date = _parse_date(form_data["orders_start_date"])
    if not parsed_orders_start_date:
        errors["orders_start_date"] = "Enter a valid orders start date (YYYY-MM-DD)."

    batch_end_date = None
    if batch_horizon_enabled:
        validation.validate_required(form_data["batch_end_date"], "batch_end_date", errors)
        batch_end_date = _parse_date(form_data["batch_end_date"])
        if form_data["batch_end_date"] and not batch_end_date:
            errors["batch_end_date"] = "Enter a valid date (YYYY-MM-DD)."

    if form_data["trailer_type"] and form_data["trailer_type"] not in {"STEP_DECK", "FLATBED", "WEDGE"}:
        errors["trailer_type"] = "Select a valid trailer type."
    if optimize_mode == "manual" and not selected_so_nums:
        errors["manual_order_input"] = "Paste at least one order number to run manual selection."

    if errors:
        return {
            "errors": errors,
            "form_data": form_data,
            "success_message": "",
            "summary": None,
        }

    try:
        stack_overflow_max_height = int(form_data["stack_overflow_max_height"] or 0)
    except (TypeError, ValueError):
        stack_overflow_max_height = int(DEFAULT_BUILD_PARAMS.get("stack_overflow_max_height", 5) or 5)
    stack_overflow_max_height = max(stack_overflow_max_height, 0)

    try:
        max_back_overhang_ft = float(form_data["max_back_overhang_ft"] or 0)
    except (TypeError, ValueError):
        max_back_overhang_ft = float(DEFAULT_BUILD_PARAMS.get("max_back_overhang_ft", 4) or 4)
    max_back_overhang_ft = max(max_back_overhang_ft, 0.0)

    params = {
        "origin_plant": form_data["origin_plant"],
        "capacity_feet": float(form_data["capacity_feet"]),
        "trailer_type": form_data["trailer_type"],
        "max_detour_pct": float(form_data["max_detour_pct"]),
        "time_window_days": int(form_data["time_window_days"] or 0),
        "geo_radius": float(form_data["geo_radius"]),
        "stack_overflow_max_height": stack_overflow_max_height,
        "max_back_overhang_ft": round(max_back_overhang_ft, 2),
        "enforce_time_window": enforce_time_window,
        "batch_horizon_enabled": batch_horizon_enabled,
        "batch_end_date": batch_end_date,
        "state_filters": [] if optimize_mode == "manual" else state_filters,
        "customer_filters": [] if optimize_mode == "manual" else customer_filters,
        "selected_so_nums": selected_so_nums,
        "orders_start_date": parsed_orders_start_date,
        # Backward compatibility for logic that still checks this flag.
        "ignore_past_due": bool(parsed_orders_start_date),
        "reference_date": reference_date,
        "algorithm_version": algorithm_version,
        "compare_algorithms": compare_algorithms,
        # v2 objective tuning defaults.
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
    }

    flex_days = params["time_window_days"] if enforce_time_window else 0
    if batch_horizon_enabled and batch_end_date:
        # Respect the explicit batch horizon without extending by flex days.
        params["batch_max_due_date"] = batch_end_date
    else:
        params["batch_max_due_date"] = None

    if store_settings:
        db.upsert_optimizer_settings(params)

    optimizer = Optimizer()
    if algorithm_version == "v2":
        optimized_loads = optimizer.build_optimized_loads_v2(params)
    else:
        optimized_loads = optimizer.build_optimized_loads(params)
    baseline_loads = optimizer.build_baseline_loads(params)
    algorithm_comparison = None
    if compare_algorithms:
        if algorithm_version == "v2":
            v1_loads = optimizer.build_optimized_loads(params)
            v2_loads = optimized_loads
        else:
            v1_loads = optimized_loads
            v2_loads = optimizer.build_optimized_loads_v2(params)
        algorithm_comparison = {
            "selected": algorithm_version,
            "v1": _summarize_loads(v1_loads),
            "v2": _summarize_loads(v2_loads),
        }

    if batch_horizon_enabled and batch_end_date:
        optimized_loads = [
            load
            for load in optimized_loads
            if not load.get("due_date_min") or load.get("due_date_min") <= batch_end_date
        ]
        baseline_loads = [
            load
            for load in baseline_loads
            if not load.get("due_date_min") or load.get("due_date_min") <= batch_end_date
        ]

    filtered_multi_order_capacity = 0
    filtered_optimized_loads = []
    for load in optimized_loads:
        if optimizer._load_is_multi_order_capacity_violation(load):
            filtered_multi_order_capacity += 1
            continue
        filtered_optimized_loads.append(load)
    optimized_loads = filtered_optimized_loads
    baseline_loads = [
        load for load in baseline_loads
        if not optimizer._load_is_multi_order_capacity_violation(load)
    ]

    if not optimized_loads:
        if filtered_multi_order_capacity:
            return {
                "errors": {
                    "order_lines": (
                        "Selected orders only fit as over-capacity multi-order combinations. "
                        "Over-capacity is only allowed for single-order loads."
                    )
                },
                "form_data": form_data,
                "success_message": "",
                "summary": None,
            }
        if optimize_mode == "manual":
            return {
                "errors": {"order_lines": "No eligible draft orders were found for the pasted order numbers."},
                "form_data": form_data,
                "success_message": "",
                "summary": None,
            }
        diagnostics = optimizer.describe_order_group_eligibility(params)
        return {
            "errors": {"order_lines": _build_no_eligible_orders_message(params, diagnostics)},
            "form_data": form_data,
            "success_message": "",
            "summary": None,
        }

    if session_id is None and session_factory:
        session_id = session_factory(form_data, params)

    def approval_sort_key(load):
        utilization = load.get("utilization_pct") or 0
        fragility = load.get("fragility_score") or 0
        stop_count = load.get("stop_count") or 0
        if stop_count <= 1:
            tier = 1
            primary = -utilization
        elif fragility < 0.10:
            tier = 2
            primary = fragility
        else:
            tier = 3
            primary = -fragility
        return (
            tier,
            primary,
            -utilization,
            -(load.get("estimated_cost") or 0),
        )

    sorted_loads = sorted(optimized_loads, key=approval_sort_key)

    if reset_proposed:
        if session_id:
            db.clear_draft_loads(session_id=session_id)
        else:
            db.clear_draft_loads(params["origin_plant"])
    with db.get_connection() as connection:
        for idx, load in enumerate(sorted_loads, start=1):
            load["draft_sequence"] = idx
            if created_by:
                load["created_by"] = created_by
            if session_id:
                load["planning_session_id"] = session_id
            load_id = db.create_load(load, connection=connection)
            for line in load["lines"]:
                db.create_load_line(
                    load_id,
                    line["id"],
                    line.get("total_length_ft") or 0,
                    connection=connection,
                )
        connection.commit()

    summary = build_summary(baseline_loads, optimized_loads)

    return {
        "errors": {},
        "form_data": form_data,
        "success_message": f"Built {len(optimized_loads)} proposed load(s).",
        "summary": summary,
        "session_id": session_id,
        "algorithm_comparison": algorithm_comparison,
    }


def clear_draft_loads(origin_plant=None):
    db.clear_draft_loads(origin_plant)


def create_manual_load(origin_plant, so_nums, trailer_type=None, created_by=None, session_id=None):
    if not origin_plant:
        return {"errors": {"origin_plant": "Missing plant code."}, "load_id": None}
    if not so_nums:
        return {"errors": {"so_nums": "Select at least one order."}, "load_id": None}

    normalized = [str(value).strip() for value in so_nums if str(value or "").strip()]
    if not normalized:
        return {"errors": {"so_nums": "Select at least one order."}, "load_id": None}

    settings = db.get_optimizer_settings(origin_plant) or {}
    capacity_value = settings.get("capacity_feet")
    if capacity_value is None or capacity_value == "":
        capacity_value = DEFAULT_BUILD_PARAMS.get("capacity_feet") or "53"
    try:
        capacity_feet = float(capacity_value)
    except (TypeError, ValueError):
        capacity_feet = float(DEFAULT_BUILD_PARAMS.get("capacity_feet") or 53)

    trailer_choice = (trailer_type or settings.get("trailer_type") or DEFAULT_BUILD_PARAMS.get("trailer_type") or "STEP_DECK").strip().upper()
    if trailer_choice not in {"STEP_DECK", "FLATBED", "WEDGE"}:
        return {"errors": {"trailer_type": "Select a valid trailer type."}, "load_id": None}

    optimizer = Optimizer()
    order_lines = db.list_order_lines_for_so_nums(origin_plant, normalized)
    if not order_lines:
        return {"errors": {"so_nums": "No eligible order lines found."}, "load_id": None}

    present = {line.get("so_num") for line in order_lines if line.get("so_num")}
    missing = [value for value in normalized if value not in present]
    if missing:
        missing_display = ", ".join(missing[:5])
        suffix = "..." if len(missing) > 5 else ""
        return {
            "errors": {"so_nums": f"Missing order details for: {missing_display}{suffix}"},
            "load_id": None,
        }

    order_summary_map = optimizer._build_order_summary_map(origin_plant)
    groups = optimizer._group_by_so_num(order_lines, order_summary_map)
    if not groups:
        return {"errors": {"so_nums": "Unable to group selected orders."}, "load_id": None}

    params = {
        "origin_plant": origin_plant,
        "trailer_type": trailer_choice,
        "capacity_feet": capacity_feet,
        "enforce_time_window": True,
        "time_window_days": 0,
        "geo_radius": 0,
    }
    manual_load = optimizer._build_load(groups, params)
    if optimizer._load_is_multi_order_capacity_violation(manual_load):
        return {
            "errors": {
                "so_nums": (
                    "Selected orders exceed deck capacity when combined. "
                    "Only single-order loads may exceed deck capacity."
                )
            },
            "load_id": None,
        }
    manual_load["status"] = "PROPOSED"
    manual_load["build_source"] = "MANUAL"
    manual_load["created_by"] = created_by
    if session_id:
        manual_load["planning_session_id"] = session_id

    load_id = db.create_load(manual_load)
    for line in manual_load.get("lines", []):
        db.create_load_line(load_id, line["id"], line.get("total_length_ft") or 0)

    return {"errors": {}, "load_id": load_id}


def build_summary(baseline_loads, optimized_loads):
    baseline = _summarize_loads(baseline_loads)
    optimized = _summarize_loads(optimized_loads)

    return {
        "baseline": baseline,
        "optimized": optimized,
        "delta": {
            "loads": optimized["total_loads"] - baseline["total_loads"],
            "avg_utilization": optimized["avg_utilization"] - baseline["avg_utilization"],
            "total_miles": optimized["total_miles"] - baseline["total_miles"],
            "est_cost": optimized["est_cost"] - baseline["est_cost"],
        },
    }


def _summarize_loads(loads):
    total_loads = len(loads)
    total_miles = sum(load["estimated_miles"] or 0 for load in loads)
    avg_utilization = (
        sum(load["utilization_pct"] for load in loads) / total_loads
        if total_loads
        else 0.0
    )
    est_cost = sum(load["estimated_cost"] or 0 for load in loads)
    return {
        "total_loads": total_loads,
        "avg_utilization": avg_utilization,
        "total_miles": total_miles,
        "est_cost": est_cost,
    }
