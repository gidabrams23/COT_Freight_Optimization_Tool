import db
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


def list_loads(origin_plant=None):
    loads = db.list_loads(origin_plant)
    sku_specs = {spec["sku"]: spec for spec in db.list_sku_specs()}
    for load in loads:
        trailer_type = (load.get("trailer_type") or "STEP_DECK").strip().upper()
        lines = db.list_load_lines(load["id"])
        load["lines"] = lines
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
                exceeds_capacity or utilization_pct > 100
            ) and len(order_numbers) <= 1
            load["trailer_type"] = trailer_type
        else:
            load["over_capacity"] = False
    return loads


def build_loads(form, reset_proposed=True, store_settings=True):
    ui_toggles = "opt_toggles" in form
    enforce_time_window = _truthy(form.get("enforce_time_window")) if ui_toggles else True
    batch_horizon_enabled = _truthy(form.get("batch_horizon_enabled")) if ui_toggles else False
    state_filters = _clean_list(_get_list(form, "opt_states"), upper=True)
    customer_filters = _clean_list(_get_list(form, "opt_customers"))

    form_data = {
        "origin_plant": _clean_value(form.get("origin_plant", "")),
        "capacity_feet": _clean_value(form.get("capacity_feet", "")),
        "trailer_type": _clean_value(form.get("trailer_type", "")).upper(),
        "max_detour_pct": _clean_value(form.get("max_detour_pct", "")),
        "time_window_days": _clean_value(form.get("time_window_days", "")),
        "geo_radius": _clean_value(form.get("geo_radius", "")),
        "enforce_time_window": enforce_time_window,
        "batch_horizon_enabled": batch_horizon_enabled,
        "batch_end_date": _clean_value(form.get("batch_end_date", "")),
        "state_filters": state_filters,
        "customer_filters": customer_filters,
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

    batch_end_date = None
    if batch_horizon_enabled:
        validation.validate_required(form_data["batch_end_date"], "batch_end_date", errors)
        batch_end_date = _parse_date(form_data["batch_end_date"])
        if form_data["batch_end_date"] and not batch_end_date:
            errors["batch_end_date"] = "Enter a valid date (YYYY-MM-DD)."

    if form_data["trailer_type"] and form_data["trailer_type"] not in {"STEP_DECK", "FLATBED", "WEDGE"}:
        errors["trailer_type"] = "Select a valid trailer type."

    if errors:
        return {
            "errors": errors,
            "form_data": form_data,
            "success_message": "",
            "summary": None,
        }

    params = {
        "origin_plant": form_data["origin_plant"],
        "capacity_feet": float(form_data["capacity_feet"]),
        "trailer_type": form_data["trailer_type"],
        "max_detour_pct": float(form_data["max_detour_pct"]),
        "time_window_days": int(form_data["time_window_days"] or 0),
        "geo_radius": float(form_data["geo_radius"]),
        "enforce_time_window": enforce_time_window,
        "batch_horizon_enabled": batch_horizon_enabled,
        "batch_end_date": batch_end_date,
        "state_filters": state_filters,
        "customer_filters": customer_filters,
    }

    flex_days = params["time_window_days"] if enforce_time_window else 0
    if batch_horizon_enabled and batch_end_date:
        params["batch_max_due_date"] = batch_end_date + timedelta(days=flex_days)
    else:
        params["batch_max_due_date"] = None

    if store_settings:
        db.upsert_optimizer_settings(params)

    optimizer = Optimizer()
    optimized_loads = optimizer.build_optimized_loads(params)
    baseline_loads = optimizer.build_baseline_loads(params)

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

    if not optimized_loads:
        return {
            "errors": {"order_lines": "No eligible orders found for this plant."},
            "form_data": form_data,
            "success_message": "",
            "summary": None,
        }

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
        db.clear_draft_loads(params["origin_plant"])
    for idx, load in enumerate(sorted_loads, start=1):
        load["draft_sequence"] = idx
        load_id = db.create_load(load)
        for line in load["lines"]:
            db.create_load_line(load_id, line["id"], line.get("total_length_ft") or 0)

    summary = build_summary(baseline_loads, optimized_loads)

    return {
        "errors": {},
        "form_data": form_data,
        "success_message": f"Built {len(optimized_loads)} proposed load(s).",
        "summary": summary,
    }


def clear_draft_loads(origin_plant=None):
    db.clear_draft_loads(origin_plant)


def create_manual_load(origin_plant, so_nums, trailer_type=None, created_by=None):
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
    manual_load["status"] = "PROPOSED"
    manual_load["build_source"] = "MANUAL"
    manual_load["created_by"] = created_by

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
