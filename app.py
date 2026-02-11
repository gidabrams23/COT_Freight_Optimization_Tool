import math
import os
from datetime import date, datetime, timedelta

from flask import (
    Flask,
    redirect,
    render_template,
    request,
    url_for,
    Response,
    jsonify,
    session,
    abort,
)

import csv
import io

import db
from services import load_builder, orders as order_service, stack_calculator, geo_utils, tsp_solver, customer_rules
from services.optimizer_engine import OptimizerEngine
from services.order_importer import OrderImporter

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-session-key")

@app.template_filter("short_date")
def short_date(value):
    if value is None:
        return "—"

    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        raw = str(value).strip()
        if not raw:
            return "—"

        parsed = None
        try:
            parsed = date.fromisoformat(raw)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
            except ValueError:
                for fmt in ("%m/%d/%Y", "%Y/%m/%d", "%b %d %Y", "%b %d, %Y"):
                    try:
                        parsed = datetime.strptime(raw, fmt).date()
                        break
                    except ValueError:
                        continue

        if parsed is None:
            return raw

    return f"{parsed.strftime('%b')} {parsed.day}"

PLANT_CODES = ["GA", "TX", "VA", "IA", "OR", "NV"]
PLANT_NAMES = {
    "GA": "Lavonia",
    "IA": "Missouri Valley",
    "TX": "Mexia",
    "VA": "Montross",
    "OR": "Coburg",
    "NV": "Winnemucca",
}
STATUS_PROPOSED = "PROPOSED"
STATUS_DRAFT = "DRAFT"
STATUS_APPROVED = "APPROVED"
ROLE_ADMIN = "admin"
ROLE_PLANNER = "planner"
SESSION_PROFILE_ID_KEY = "profile_id"
SESSION_PROFILE_NAME_KEY = "profile_name"
SESSION_PROFILE_DEFAULT_PLANTS_KEY = "profile_default_plants"
ORDER_REMOVAL_REASONS = [
    "Customer mixing conflict",
    "Capacity exceeded",
    "Geographic infeasibility",
    "Delivery date conflict",
    "Other",
]
LOAD_REJECTION_REASONS = [
    "Customer mixing conflict",
    "Capacity exceeded",
    "Geographic infeasibility",
    "Route inefficiency",
    "Delivery date conflicts",
    "Other",
]


db.init_db()
db.ensure_default_access_profiles(
    [
        {
            "name": "Admin",
            "is_admin": True,
            "allowed_plants": "ALL",
            "default_plants": "ALL",
        },
        {
            "name": "Chris",
            "is_admin": False,
            "allowed_plants": "ALL",
            "default_plants": "OR",
        },
        {
            "name": "Basil",
            "is_admin": False,
            "allowed_plants": "ALL",
            "default_plants": "IA",
        },
        {
            "name": "Mario",
            "is_admin": False,
            "allowed_plants": "ALL",
            "default_plants": "TX",
        },
        {
            "name": "Ed",
            "is_admin": False,
            "allowed_plants": "ALL",
            "default_plants": "GA,VA,NV",
        },
    ]
)
db.ensure_default_planning_settings(
    {
        "strategic_customers": "\n".join(
            [
                "Lowe's|LOWE'S,LOWES",
                "Tractor Supply|TRACTOR SUPPLY,TRACTORSUPPLY",
            ]
        )
    }
)


@app.route("/session", methods=["GET", "POST"])
def session_setup():
    # Kept for backwards-compatibility with older links/bookmarks.
    # Access is now handled via named profiles in the bottom-left menu.
    return redirect(url_for("dashboard"))


@app.route("/session/reset")
def session_reset():
    session.clear()
    return redirect(url_for("dashboard"))


def _safe_next_url(value):
    value = (value or "").strip()
    if not value:
        return None
    if value.startswith("/"):
        return value
    return None


@app.route("/access/switch", methods=["POST"])
def access_switch():
    _require_session()
    profile_id = request.form.get("profile_id")
    try:
        profile_id = int(profile_id)
    except (TypeError, ValueError):
        profile_id = None

    profile = db.get_access_profile(profile_id) if profile_id else None
    if not profile:
        return redirect(url_for("dashboard"))

    _apply_profile_to_session(profile, reset_filters=True)
    next_url = _safe_next_url(request.form.get("next")) or url_for("dashboard")
    return redirect(next_url)


@app.route("/access/manage", methods=["GET", "POST"])
def access_manage():
    _require_session()
    _require_admin()

    error = None
    edit_profile = None
    edit_allowed = []
    edit_defaults = []
    edit_id = request.args.get("edit_id")
    if edit_id:
        try:
            edit_id = int(edit_id)
        except (TypeError, ValueError):
            edit_id = None
    if edit_id:
        edit_profile = db.get_access_profile(edit_id)
        if edit_profile:
            allowed_parsed = _parse_plant_filters(edit_profile.get("allowed_plants"))
            edit_allowed = allowed_parsed if allowed_parsed else list(PLANT_CODES)
            defaults_parsed = _parse_plant_filters(edit_profile.get("default_plants"))
            # Empty means "no focus (all plants)"
            edit_defaults = defaults_parsed if defaults_parsed else []

    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower() or "create"
        profile_id = request.form.get("profile_id")
        try:
            profile_id = int(profile_id) if profile_id else None
        except (TypeError, ValueError):
            profile_id = None

        name = (request.form.get("name") or "").strip()
        is_admin_flag = (request.form.get("is_admin") or "").strip().lower() in {
            "1",
            "true",
            "on",
            "yes",
        }
        allowed = [_normalize_plant_code(code) for code in request.form.getlist("allowed_plants")]
        allowed = [code for code in allowed if code in PLANT_CODES]
        default_plants = [_normalize_plant_code(code) for code in request.form.getlist("default_plants")]
        default_plants = [code for code in default_plants if code in PLANT_CODES]

        if not name:
            error = "Profile name is required."
        elif action == "update" and not profile_id:
            error = "Missing profile to update."
        elif action not in {"create", "update"}:
            error = "Unsupported action."
        elif is_admin_flag:
            try:
                if action == "create":
                    db.create_access_profile(name, True, "ALL", "ALL")
                else:
                    db.update_access_profile(profile_id, name, True, "ALL", "ALL")
                return redirect(url_for("access_manage"))
            except Exception:
                error = "Unable to save profile. Profile names must be unique."
        else:
            if not allowed:
                error = "Select at least one allowed plant."
            else:
                default_plants = [code for code in default_plants if code in allowed]
                allowed_csv = ",".join(allowed) if len(allowed) < len(PLANT_CODES) else "ALL"
                # Empty defaults => no focus (treat as ALL)
                default_csv = ",".join(default_plants) if default_plants else "ALL"
                try:
                    if action == "create":
                        db.create_access_profile(name, False, allowed_csv, default_csv)
                    else:
                        db.update_access_profile(profile_id, name, False, allowed_csv, default_csv)
                    return redirect(url_for("access_manage"))
                except Exception:
                    error = "Unable to save profile. Profile names must be unique."

    profiles = db.list_access_profiles()
    return render_template(
        "access_manage.html",
        profiles=profiles,
        plants=PLANT_CODES,
        plant_names=PLANT_NAMES,
        error=error,
        edit_profile=edit_profile,
        edit_allowed=edit_allowed,
        edit_defaults=edit_defaults,
    )


@app.route("/access/delete", methods=["POST"])
def access_delete():
    _require_session()
    _require_admin()

    profile_id = request.form.get("profile_id")
    try:
        profile_id = int(profile_id)
    except (TypeError, ValueError):
        return redirect(url_for("access_manage"))

    profiles = db.list_access_profiles()
    if len(profiles) <= 1:
        return redirect(url_for("access_manage"))

    admin_count = sum(1 for profile in profiles if profile.get("is_admin"))
    target = next((p for p in profiles if p.get("id") == profile_id), None)
    if not target:
        return redirect(url_for("access_manage"))

    if target.get("is_admin") and admin_count <= 1:
        return redirect(url_for("access_manage"))

    # If deleting the active session profile, switch to Admin first.
    if session.get(SESSION_PROFILE_ID_KEY) == profile_id:
        fallback = db.get_access_profile_by_name("Admin")
        if fallback and fallback.get("id") != profile_id:
            _apply_profile_to_session(fallback, reset_filters=True)
        else:
            other = next((p for p in profiles if p.get("id") != profile_id), None)
            if other:
                _apply_profile_to_session(other, reset_filters=True)

    db.delete_access_profile(profile_id)
    return redirect(url_for("access_manage"))


def _default_optimize_form():
    form_data = dict(load_builder.DEFAULT_BUILD_PARAMS)
    if not form_data["origin_plant"] and PLANT_CODES:
        form_data["origin_plant"] = PLANT_CODES[0]
    return form_data


def _distinct(values):
    return sorted({value for value in values if value})


def _build_rate_matrix(rates):
    plants = sorted({rate["origin_plant"] for rate in rates if rate.get("origin_plant")})
    states = sorted({rate["destination_state"] for rate in rates if rate.get("destination_state")})
    matrix = {state: {plant: None for plant in plants} for state in states}
    for rate in rates:
        state = rate.get("destination_state")
        plant = rate.get("origin_plant")
        if state and plant and matrix[state][plant] is None:
            matrix[state][plant] = rate.get("rate_per_mile")
    return plants, states, matrix


def _build_rate_matrix_records(rates):
    plants = sorted({rate["origin_plant"] for rate in rates if rate.get("origin_plant")})
    states = sorted({rate["destination_state"] for rate in rates if rate.get("destination_state")})
    matrix = {state: {plant: None for plant in plants} for state in states}
    for rate in rates:
        state = rate.get("destination_state")
        plant = rate.get("origin_plant")
        if not state or not plant:
            continue
        current = matrix[state][plant]
        if not current or (rate.get("effective_year") or 0) > (current.get("effective_year") or 0):
            matrix[state][plant] = rate
    return plants, states, matrix


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(value).date()
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except (TypeError, ValueError):
            continue
    return None


def _resolve_today_override(value):
    token = (value or "").strip().lower()
    if token == "clear":
        session.pop("today_override", None)
        return None
    if value:
        parsed = _parse_date(value)
        if parsed:
            session["today_override"] = parsed.strftime("%Y-%m-%d")
        return parsed
    stored = session.get("today_override")
    return _parse_date(stored)


def _compute_load_progress_snapshot(plant_scope=None, all_loads=None, allowed_plants=None):
    allowed_plants = allowed_plants or _get_allowed_plants()
    plant_scope = plant_scope or allowed_plants
    if all_loads is None:
        all_loads = load_builder.list_loads(None)
    all_loads = [load for load in all_loads if load.get("origin_plant") in allowed_plants]
    loads_for_progress = [load for load in all_loads if load.get("origin_plant") in plant_scope]

    optimized_loads = []
    for load in loads_for_progress:
        status = (load.get("status") or STATUS_PROPOSED).upper()
        build_source = (load.get("build_source") or "OPTIMIZED").upper()
        if status not in {STATUS_PROPOSED, STATUS_DRAFT}:
            continue
        if build_source == "MANUAL":
            continue
        optimized_loads.append(load)

    optimized_order_ids = {
        line.get("so_num")
        for load in optimized_loads
        for line in load.get("lines", [])
        if line.get("so_num")
    }
    order_status_map = {so_num: "UNASSIGNED" for so_num in optimized_order_ids if so_num}

    status_priority = {
        "UNASSIGNED": 0,
        STATUS_PROPOSED: 1,
        STATUS_DRAFT: 2,
        STATUS_APPROVED: 3,
    }

    for load in loads_for_progress:
        load_status = (load.get("status") or STATUS_PROPOSED).upper()
        for line in load.get("lines", []):
            so_num = line.get("so_num")
            if so_num in order_status_map:
                current = order_status_map.get(so_num, "UNASSIGNED")
                if status_priority.get(load_status, 0) > status_priority.get(current, 0):
                    order_status_map[so_num] = load_status

    order_status_counts = {
        "unassigned": 0,
        "proposed": 0,
        "draft": 0,
        "approved": 0,
    }
    for status in order_status_map.values():
        if status == STATUS_PROPOSED:
            order_status_counts["proposed"] += 1
        elif status == STATUS_DRAFT:
            order_status_counts["draft"] += 1
        elif status == STATUS_APPROVED:
            order_status_counts["approved"] += 1
        else:
            order_status_counts["unassigned"] += 1

    load_status_counts = {"proposed": 0, "draft": 0, "approved": 0}
    for load in loads_for_progress:
        status = (load.get("status") or STATUS_PROPOSED).upper()
        if status == STATUS_DRAFT:
            load_status_counts["draft"] += 1
        elif status == STATUS_APPROVED:
            load_status_counts["approved"] += 1
        else:
            load_status_counts["proposed"] += 1

    total_orders = len(order_status_map)
    approved_orders = order_status_counts["draft"] + order_status_counts["approved"]
    progress_pct = round((approved_orders / total_orders) * 100, 1) if total_orders else 0.0

    return {
        "order_status_counts": order_status_counts,
        "load_status_counts": load_status_counts,
        "approved_orders": approved_orders,
        "total_orders": total_orders,
        "progress_pct": progress_pct,
        "draft_tab_count": load_status_counts["draft"] + load_status_counts["proposed"],
        "final_tab_count": load_status_counts["approved"],
    }


def _due_status(due_date_value, today=None, due_soon_days=14):
    """Return a simple bucket for UI badges."""
    today = today or date.today()
    due_date = _parse_date(due_date_value)
    if not due_date:
        return ""
    if due_date < today:
        return "PAST_DUE"
    if due_date <= today + timedelta(days=due_soon_days):
        return "DUE_SOON"
    return ""


def _annotate_orders_due_status(orders, today=None):
    today = today or date.today()
    for order in orders or []:
        order["due_status"] = _due_status(order.get("due_date"), today=today)
    return orders


def _city_abbr(value):
    if not value:
        return ""
    parts = [part for part in str(value).replace("-", " ").split() if part]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0][:3].upper()
    abbr = "".join(part[0] for part in parts)
    return abbr[:3].upper()


def _utilization_grade(utilization_pct):
    if utilization_pct >= 85:
        return "A"
    if utilization_pct >= 70:
        return "B"
    if utilization_pct >= 55:
        return "C"
    if utilization_pct >= 40:
        return "D"
    return "F"


def _normalize_plant_code(value):
    return (value or "").strip().upper()


def _parse_plant_filters(value):
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        raw_values = value
    else:
        text = str(value).strip()
        if not text:
            return []
        if text.upper() == "ALL":
            return []
        raw_values = text.replace(",", " ").split()

    normalized = []
    for entry in raw_values:
        code = _normalize_plant_code(entry)
        if not code:
            continue
        if code == "ALL":
            return []
        if code not in normalized:
            normalized.append(code)
    return normalized


def _parse_strategic_customers(value_text):
    entries = []
    used_keys = set()
    lines = (value_text or "").splitlines()
    for raw in lines:
        line = (raw or "").strip()
        if not line or line.startswith("#"):
            continue

        if "|" in line:
            label_part, patterns_part = line.split("|", 1)
        else:
            label_part, patterns_part = line, line

        label = (label_part or "").strip() or (patterns_part or "").strip()
        patterns = [part.strip() for part in (patterns_part or "").split(",") if part.strip()]
        if not patterns:
            patterns = [label]

        base_key = customer_rules.normalize_customer_text(label).lower().replace(" ", "_") or "customer"
        key = base_key
        suffix = 2
        while key in used_keys:
            key = f"{base_key}_{suffix}"
            suffix += 1
        used_keys.add(key)
        entries.append({"key": key, "label": label, "patterns": patterns})

    return entries


def _sync_legacy_plant_filter(selected, allowed):
    if not selected or len(selected) >= len(allowed):
        session["plant_filter"] = "ALL"
    elif len(selected) == 1:
        session["plant_filter"] = selected[0]
    else:
        session["plant_filter"] = ",".join(selected)


def _get_session_profile_name():
    name = (session.get(SESSION_PROFILE_NAME_KEY) or "").strip()
    return name or None


def _profile_allowed_plants(profile):
    parsed = _parse_plant_filters(profile.get("allowed_plants"))
    # In profile storage, empty/ALL means "all plants".
    if not parsed:
        return list(PLANT_CODES)
    return [code for code in parsed if code in PLANT_CODES]


def _profile_default_plants(profile, allowed):
    parsed = _parse_plant_filters(profile.get("default_plants"))
    # Empty/ALL means "default to all allowed plants". Represent as empty list.
    if not parsed:
        return []
    filtered = [code for code in parsed if code in allowed]
    if not filtered or len(filtered) >= len(allowed):
        return []
    return filtered


def _apply_profile_to_session(profile, *, reset_filters=False):
    allowed = _profile_allowed_plants(profile)
    default_plants = _profile_default_plants(profile, allowed)

    session[SESSION_PROFILE_ID_KEY] = profile["id"]
    session[SESSION_PROFILE_NAME_KEY] = profile["name"]
    session[SESSION_PROFILE_DEFAULT_PLANTS_KEY] = default_plants

    session["role"] = ROLE_ADMIN if profile.get("is_admin") else ROLE_PLANNER
    session["allowed_plants"] = allowed

    if reset_filters or session.get("plant_filters") is None:
        session["plant_filters"] = list(default_plants)
        _sync_legacy_plant_filter(session["plant_filters"], allowed)


def _ensure_active_profile():
    profile_id = session.get(SESSION_PROFILE_ID_KEY)
    profile = db.get_access_profile(profile_id) if profile_id else None
    if not profile:
        profile = db.get_access_profile_by_name("Admin")
    if not profile:
        profiles = db.list_access_profiles()
        profile = profiles[0] if profiles else None
    if not profile:
        return None

    # Apply if missing/mismatched or if allowed plants were cleared.
    if session.get(SESSION_PROFILE_ID_KEY) != profile["id"] or not _get_allowed_plants():
        _apply_profile_to_session(profile, reset_filters=True)
    return profile


def _get_session_role():
    role = session.get("role")
    return role if role in {ROLE_ADMIN, ROLE_PLANNER} else None


def _get_allowed_plants():
    allowed = session.get("allowed_plants") or []
    return [code for code in allowed if code in PLANT_CODES]


def _resolve_plant_filters(selected):
    role = _get_session_role()
    allowed = _get_allowed_plants()
    if not role or not allowed:
        return []

    parsed = _parse_plant_filters(selected)
    if parsed is not None:
        filtered = [code for code in parsed if code in allowed]
        if not filtered or len(filtered) >= len(allowed):
            session["plant_filters"] = []
        else:
            session["plant_filters"] = filtered

    current = session.get("plant_filters")
    if current is None:
        legacy = session.get("plant_filter")
        if legacy and legacy != "ALL":
            current = [legacy] if legacy in allowed else []
        else:
            current = []
        session["plant_filters"] = current

    current = [code for code in current if code in allowed]
    if not current or len(current) >= len(allowed):
        session["plant_filters"] = []
        _sync_legacy_plant_filter([], allowed)
        return []

    session["plant_filters"] = current
    _sync_legacy_plant_filter(current, allowed)
    return current


def _get_current_plant_filters():
    allowed = _get_allowed_plants()
    current = session.get("plant_filters")
    if current is None:
        legacy = session.get("plant_filter")
        if legacy and legacy != "ALL":
            current = [legacy] if legacy in allowed else []
        else:
            current = []
    current = [code for code in current if code in allowed]
    if not current or len(current) >= len(allowed):
        return []
    return current


def _format_plant_filter_label(selected, allowed):
    if not selected or len(selected) >= len(allowed):
        return "All Plants"
    return ", ".join(selected)


def _build_plant_filter_cards(allowed, selected):
    if not allowed:
        return []
    selected_set = set(selected or [])
    util_by_plant = {}
    with db.get_connection() as connection:
        rows = connection.execute(
            """
            SELECT origin_plant, AVG(utilization_pct) AS avg_util
            FROM loads
            WHERE status = 'APPROVED'
              AND DATE(created_at) >= DATE('now', '-6 days')
            GROUP BY origin_plant
            """
        ).fetchall()
        for row in rows:
            util_by_plant[row["origin_plant"]] = row["avg_util"]

    cards = []
    for plant in allowed:
        avg_util = util_by_plant.get(plant)
        util_value = round(avg_util, 0) if avg_util is not None else None
        util_width = min(max(int(util_value), 0), 100) if util_value is not None else 0
        status = "neutral"
        if util_value is not None:
            if util_value >= 80:
                status = "success"
            elif util_value >= 70:
                status = "warning"
            else:
                status = "error"
        cards.append(
            {
                "code": plant,
                "name": PLANT_NAMES.get(plant, plant),
                "utilization": util_value,
                "util_width": util_width,
                "util_display": f"{int(util_value)}%" if util_value is not None else "--",
                "status": status,
                "selected": plant in selected_set,
            }
        )
    return cards


def _week_bounds(today):
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    next_start = end + timedelta(days=1)
    next_end = next_start + timedelta(days=6)
    return start, end, next_start, next_end


def _default_batch_end_date(today=None):
    """Default planning horizon: Friday of the week that is two weeks from today."""
    today = today or date.today()
    anchor = today + timedelta(days=14)
    # Friday = 4 (Mon=0 ... Sun=6)
    return anchor + timedelta(days=(4 - anchor.weekday()))


def _is_full_truckload(load):
    utilization = load.get("utilization_pct") or 0
    order_numbers = {line.get("so_num") for line in load.get("lines", []) if line.get("so_num")}
    is_single_order = len(order_numbers) <= 1
    return is_single_order and (load.get("over_capacity") or utilization > 90)


def _period_range(period, today):
    key = (period or "").strip().lower()
    if key == "today":
        return today, today
    if key == "this_month":
        start = today.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        return start, end
    if key == "last_30_days":
        return today - timedelta(days=29), today
    if key == "last_7_days":
        return today - timedelta(days=6), today
    # default to this_week
    start, end, _, _ = _week_bounds(today)
    return start, end


def _build_load_thumbnail(load, sku_specs, color_palette, max_blocks=4):
    lines = db.list_load_lines(load["id"])
    if not lines:
        return []

    order_colors = {}
    for line in lines:
        so_num = line.get("so_num")
        if so_num and so_num not in order_colors:
            order_colors[so_num] = color_palette[len(order_colors) % len(color_palette)]

    trailer_type = (load.get("trailer_type") or "STEP_DECK").strip().upper()
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

    schematic = stack_calculator.calculate_stack_configuration(
        line_items,
        trailer_type=trailer_type,
    )
    positions = []
    for pos in schematic.get("positions", []) or []:
        colors = []
        for item in pos.get("items", []) or []:
            color = order_colors.get(item.get("order_id"), "#30363D")
            units = item.get("units") or 0
            for _ in range(min(units, max_blocks)):
                colors.append(color)
                if len(colors) >= max_blocks:
                    break
            if len(colors) >= max_blocks:
                break
        positions.append(
            {
                "width_pct": pos.get("width_pct") or 0,
                "colors": colors,
            }
        )
    return positions


def _build_orders_snapshot(orders, today=None):
    today = today or date.today()
    _, end_week, next_start, next_end = _week_bounds(today)
    active_orders = [order for order in orders if not order.get("is_excluded")]
    snapshot = {
        "total": len(active_orders),
        "past_due": 0,
        "this_week": 0,
        "next_week": 0,
        "unassigned": 0,
    }
    for order in active_orders:
        due_date = _parse_date(order.get("due_date"))
        if due_date:
            if due_date < today:
                snapshot["past_due"] += 1
            elif today <= due_date <= end_week:
                snapshot["this_week"] += 1
            elif next_start <= due_date <= next_end:
                snapshot["next_week"] += 1
        if not order.get("is_assigned"):
            snapshot["unassigned"] += 1
    return snapshot


def _require_session():
    profile = _ensure_active_profile()
    if not profile or not _get_allowed_plants():
        abort(500)
    return None


def _require_admin():
    if _get_session_role() != ROLE_ADMIN:
        abort(403)


def _year_suffix(value=None):
    value = value or date.today()
    return f"{value.year % 100:02d}"


def _format_load_number(plant_code, year_suffix, sequence, draft=False):
    base = f"{plant_code}{year_suffix}-{sequence:04d}"
    return f"{base}-D" if draft else base


def _normalize_load_number(load_number):
    if not load_number:
        return None, None
    trimmed = load_number.strip()
    parts = trimmed.split("-")
    if len(parts) < 2:
        return trimmed, None
    return trimmed, parts[-1]


def _reoptimize_for_plant(plant_code):
    if not plant_code:
        return {"errors": {"origin_plant": "Missing plant code."}}
    db.clear_unapproved_loads(plant_code)
    settings = db.get_optimizer_settings(plant_code) or {}
    form_data = dict(load_builder.DEFAULT_BUILD_PARAMS)
    form_data["origin_plant"] = plant_code
    if settings.get("capacity_feet") is not None:
        form_data["capacity_feet"] = str(settings.get("capacity_feet"))
    if settings.get("trailer_type"):
        form_data["trailer_type"] = settings.get("trailer_type")
    if settings.get("max_detour_pct") is not None:
        form_data["max_detour_pct"] = str(settings.get("max_detour_pct"))
    if settings.get("time_window_days") is not None:
        form_data["time_window_days"] = str(settings.get("time_window_days"))
    if settings.get("geo_radius") is not None:
        form_data["geo_radius"] = str(settings.get("geo_radius"))
    return load_builder.build_loads(form_data, reset_proposed=False, store_settings=False)


@app.context_processor
def inject_session_context():
    profile = _ensure_active_profile()
    role = _get_session_role()
    allowed_plants = _get_allowed_plants()
    selected_plants = _resolve_plant_filters(request.args.get("plants") or request.args.get("plant"))
    return {
        "session_role": role,
        "session_profile_name": _get_session_profile_name(),
        "session_profile_id": session.get(SESSION_PROFILE_ID_KEY),
        "session_profile_default_plants": session.get(SESSION_PROFILE_DEFAULT_PLANTS_KEY) or [],
        "access_profiles": db.list_access_profiles(),
        "session_allowed_plants": allowed_plants,
        "session_plant_filter": session.get("plant_filter"),
        "session_plant_filters": selected_plants,
        "session_plant_filter_label": _format_plant_filter_label(selected_plants, allowed_plants),
        "plant_filter_cards": _build_plant_filter_cards(allowed_plants, selected_plants),
        "is_admin": role == ROLE_ADMIN,
    }


def _build_command_center_dashboard_context():
    allowed_plants = _get_allowed_plants()
    plant_filters = _resolve_plant_filters(request.args.get("plants") or request.args.get("plant"))
    plant_scope = plant_filters or allowed_plants

    period_options = [
        ("last_30_days", "Last 30 Days"),
        ("last_7_days", "Last 7 Days"),
        ("this_month", "This Month"),
        ("today", "Today"),
    ]
    period_label_map = dict(period_options)
    period = (request.args.get("period") or "last_30_days").strip().lower()
    if period not in period_label_map:
        period = "last_30_days"

    today = date.today()
    start_date, end_date = _period_range(period, today)
    period_len_days = (end_date - start_date).days + 1
    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=period_len_days - 1)

    def _iso(value):
        return value.strftime("%Y-%m-%d")

    start_iso = _iso(start_date)
    end_iso = _iso(end_date)
    prev_start_iso = _iso(prev_start)
    prev_end_iso = _iso(prev_end)

    plant_filter_param = ",".join(plant_filters) if plant_filters else ""
    plant_scope_label = (
        ", ".join([PLANT_NAMES.get(code, code) for code in plant_filters])
        if plant_filters
        else "All Plants"
    )
    period_label = period_label_map.get(period, "Last 30 Days")
    date_range_label = f"{start_date.strftime('%b %d')} - {end_date.strftime('%b %d')}"

    def _format_int(value):
        try:
            return f"{int(value or 0):,}"
        except (TypeError, ValueError):
            return "0"

    def _format_currency(amount, decimals=2):
        amount = float(amount or 0)
        return f"${amount:,.{decimals}f}"

    def _format_compact_currency(amount):
        amount = float(amount or 0)
        abs_amount = abs(amount)
        if abs_amount >= 1_000_000:
            return f"${amount / 1_000_000:.2f}M"
        if abs_amount >= 100_000:
            return f"${amount / 1_000:.0f}K"
        if abs_amount >= 10_000:
            return f"${amount / 1_000:.1f}K"
        return f"${amount:,.0f}"

    def _pct_change(current, previous):
        if current is None or previous is None:
            return None
        current = float(current or 0)
        previous = float(previous or 0)
        if previous == 0:
            return None
        return (current - previous) / previous * 100.0

    def _trend_meta(delta_pct):
        if delta_pct is None:
            return {"display": None, "icon": "trending_flat", "class": ""}
        icon = "trending_up" if delta_pct >= 0 else "trending_down"
        cls = "positive" if delta_pct >= 0 else "negative"
        return {"display": f"{delta_pct:+.1f}%", "icon": icon, "class": cls}

    def _point_delta_meta(delta_points):
        if delta_points is None:
            return {"display": None, "icon": "trending_flat", "class": ""}
        icon = "trending_up" if delta_points >= 0 else "trending_down"
        cls = "positive" if delta_points >= 0 else "negative"
        return {"display": f"{delta_points:+.1f} pts", "icon": icon, "class": cls}

    def _bucket_series(items, bucket_count=7):
        if not items:
            return []
        if len(items) <= bucket_count:
            return [
                {
                    "start": entry["date"],
                    "end": entry["date"],
                    "spend": float(entry.get("spend") or 0),
                    "qty": float(entry.get("qty") or 0),
                }
                for entry in items
            ]
        size = len(items)
        base = size // bucket_count
        remainder = size % bucket_count
        buckets = []
        idx = 0
        for bucket_index in range(bucket_count):
            take = base + (1 if bucket_index < remainder else 0)
            segment = items[idx:idx + take]
            idx += take
            if not segment:
                continue
            buckets.append(
                {
                    "start": segment[0]["date"],
                    "end": segment[-1]["date"],
                    "spend": sum(float(item.get("spend") or 0) for item in segment),
                    "qty": sum(float(item.get("qty") or 0) for item in segment),
                }
            )
        return buckets

    def _axis_label(value_date):
        if not value_date:
            return ""
        if value_date == today:
            return "Today"
        return value_date.strftime("%b %d")

    def _fetch_period_totals(connection, plants, start_value, end_value):
        if not plants:
            return {"loads": 0, "avg_util": 0.0, "spend": 0.0, "qty": 0.0}
        placeholders = ", ".join("?" for _ in plants)
        params = list(plants) + [start_value, end_value]
        loads_row = connection.execute(
            f"""
            SELECT
                COUNT(*) AS load_count,
                AVG(utilization_pct) AS avg_util,
                SUM(COALESCE(estimated_cost, 0)) AS total_spend
            FROM loads
            WHERE status = 'APPROVED'
              AND origin_plant IN ({placeholders})
              AND DATE(created_at) BETWEEN DATE(?) AND DATE(?)
            """,
            params,
        ).fetchone()
        qty_row = connection.execute(
            f"""
            SELECT
                SUM(COALESCE(ol.qty, 0)) AS total_qty
            FROM loads l
            JOIN load_lines ll ON ll.load_id = l.id
            JOIN order_lines ol ON ol.id = ll.order_line_id
            WHERE l.status = 'APPROVED'
              AND l.origin_plant IN ({placeholders})
              AND DATE(l.created_at) BETWEEN DATE(?) AND DATE(?)
              AND COALESCE(ol.is_excluded, 0) = 0
            """,
            params,
        ).fetchone()
        return {
            "loads": int(loads_row["load_count"] or 0) if loads_row else 0,
            "avg_util": float(loads_row["avg_util"] or 0) if loads_row else 0.0,
            "spend": float(loads_row["total_spend"] or 0) if loads_row else 0.0,
            "qty": float(qty_row["total_qty"] or 0) if qty_row else 0.0,
        }

    with db.get_connection() as connection:
        current_totals = _fetch_period_totals(connection, plant_scope, start_iso, end_iso)
        prev_totals = _fetch_period_totals(connection, plant_scope, prev_start_iso, prev_end_iso)

        current_cpu = (
            (current_totals["spend"] / current_totals["qty"]) if current_totals["qty"] else None
        )
        prev_cpu = (prev_totals["spend"] / prev_totals["qty"]) if prev_totals["qty"] else None

        loads_trend = _trend_meta(_pct_change(current_totals["loads"], prev_totals["loads"]))
        spend_trend = _trend_meta(_pct_change(current_totals["spend"], prev_totals["spend"]))
        cpu_trend = _trend_meta(_pct_change(current_cpu, prev_cpu))
        util_trend = _point_delta_meta(
            (current_totals["avg_util"] - prev_totals["avg_util"]) if prev_totals["loads"] else None
        )

        avg_util = float(current_totals["avg_util"] or 0)
        health_label = "NO DATA"
        health_class = "neutral"
        if current_totals["loads"]:
            if avg_util >= 80:
                health_label = "OPTIMAL"
                health_class = "success"
            elif avg_util >= 70:
                health_label = "MONITOR"
                health_class = "warning"
            else:
                health_label = "AT RISK"
                health_class = "danger"

        kpis = [
            {
                "label": "Total Loads Batched",
                "icon": "inventory_2",
                "value": _format_int(current_totals["loads"]),
                "unit": "",
                "delta_display": loads_trend["display"],
                "delta_icon": loads_trend["icon"],
                "delta_class": loads_trend["class"],
                "footer": "vs previous period",
            },
            {
                "label": "Average Utilization",
                "icon": "speed",
                "value": f"{avg_util:.1f}%",
                "unit": "",
                "delta_display": util_trend["display"],
                "delta_icon": util_trend["icon"],
                "delta_class": util_trend["class"],
                "footer": "vs previous period",
            },
            {
                "label": "Total Logistics Spend",
                "icon": "payments",
                "value": _format_compact_currency(current_totals["spend"]),
                "unit": "",
                "delta_display": spend_trend["display"],
                "delta_icon": spend_trend["icon"],
                "delta_class": spend_trend["class"],
                "footer": "vs previous period",
            },
            {
                "label": "Cost Per Unit",
                "icon": "straighten",
                "value": _format_currency(current_cpu, decimals=2) if current_cpu is not None else "—",
                "unit": "/ unit",
                "delta_display": cpu_trend["display"],
                "delta_icon": cpu_trend["icon"],
                "delta_class": cpu_trend["class"],
                "footer": "vs previous period",
            },
        ]

        # Plant cards.
        open_orders_by_plant = {plant: 0 for plant in allowed_plants}
        planned_loads_by_plant = {plant: 0 for plant in allowed_plants}
        plant_spend = {plant: 0.0 for plant in allowed_plants}
        plant_avg_util = {plant: None for plant in allowed_plants}
        plant_qty = {plant: 0.0 for plant in allowed_plants}

        allowed_placeholders = ", ".join("?" for _ in allowed_plants) if allowed_plants else "''"

        assigned_clause = """
            EXISTS (
                SELECT 1
                FROM order_lines ol
                JOIN load_lines ll ON ll.order_line_id = ol.id
                WHERE ol.so_num = orders.so_num
                LIMIT 1
            )
        """

        for row in connection.execute(
            f"""
            SELECT plant, COUNT(*) AS open_orders
            FROM orders
            WHERE is_excluded = 0
              AND plant IN ({allowed_placeholders})
              AND NOT {assigned_clause}
            GROUP BY plant
            """,
            list(allowed_plants),
        ).fetchall():
            open_orders_by_plant[row["plant"]] = int(row["open_orders"] or 0)

        for row in connection.execute(
            f"""
            SELECT origin_plant, COUNT(*) AS planned_loads
            FROM loads
            WHERE UPPER(status) IN ('PROPOSED', 'DRAFT')
              AND origin_plant IN ({allowed_placeholders})
            GROUP BY origin_plant
            """,
            list(allowed_plants),
        ).fetchall():
            planned_loads_by_plant[row["origin_plant"]] = int(row["planned_loads"] or 0)

        for row in connection.execute(
            f"""
            SELECT
                origin_plant,
                AVG(utilization_pct) AS avg_util,
                SUM(COALESCE(estimated_cost, 0)) AS spend
            FROM loads
            WHERE status = 'APPROVED'
              AND origin_plant IN ({allowed_placeholders})
              AND DATE(created_at) BETWEEN DATE(?) AND DATE(?)
            GROUP BY origin_plant
            """,
            list(allowed_plants) + [start_iso, end_iso],
        ).fetchall():
            plant = row["origin_plant"]
            plant_spend[plant] = float(row["spend"] or 0)
            plant_avg_util[plant] = float(row["avg_util"]) if row["avg_util"] is not None else None

        for row in connection.execute(
            f"""
            SELECT
                l.origin_plant AS origin_plant,
                SUM(COALESCE(ol.qty, 0)) AS qty
            FROM loads l
            JOIN load_lines ll ON ll.load_id = l.id
            JOIN order_lines ol ON ol.id = ll.order_line_id
            WHERE l.status = 'APPROVED'
              AND l.origin_plant IN ({allowed_placeholders})
              AND DATE(l.created_at) BETWEEN DATE(?) AND DATE(?)
              AND COALESCE(ol.is_excluded, 0) = 0
            GROUP BY l.origin_plant
            """,
            list(allowed_plants) + [start_iso, end_iso],
        ).fetchall():
            plant_qty[row["origin_plant"]] = float(row["qty"] or 0)

        selected_set = set(plant_filters or [])
        plant_cards = []
        for plant in allowed_plants:
            avg_util_value = plant_avg_util.get(plant)
            status = "neutral"
            if avg_util_value is not None:
                if avg_util_value >= 80:
                    status = "success"
                elif avg_util_value >= 70:
                    status = "warning"
                else:
                    status = "error"

            qty_value = float(plant_qty.get(plant) or 0)
            spend_value = float(plant_spend.get(plant) or 0)
            cost_unit_value = (spend_value / qty_value) if qty_value else None
            plant_cards.append(
                {
                    "code": plant,
                    "name": PLANT_NAMES.get(plant, plant),
                    "open_orders": open_orders_by_plant.get(plant, 0),
                    "planned_loads": planned_loads_by_plant.get(plant, 0),
                    "cost_per_unit_display": _format_currency(cost_unit_value, decimals=2)
                    if cost_unit_value is not None
                    else "—",
                    "status": status,
                    "selected": plant in selected_set,
                }
            )

        # Spend vs Volume trend.
        spend_by_day = {}
        qty_by_day = {}
        if plant_scope:
            scope_placeholders = ", ".join("?" for _ in plant_scope)
            for row in connection.execute(
                f"""
                SELECT DATE(created_at) AS day, SUM(COALESCE(estimated_cost, 0)) AS spend
                FROM loads
                WHERE status = 'APPROVED'
                  AND origin_plant IN ({scope_placeholders})
                  AND DATE(created_at) BETWEEN DATE(?) AND DATE(?)
                GROUP BY day
                ORDER BY day
                """,
                list(plant_scope) + [start_iso, end_iso],
            ).fetchall():
                if row["day"]:
                    spend_by_day[row["day"]] = float(row["spend"] or 0)

            for row in connection.execute(
                f"""
                SELECT DATE(l.created_at) AS day, SUM(COALESCE(ol.qty, 0)) AS qty
                FROM loads l
                JOIN load_lines ll ON ll.load_id = l.id
                JOIN order_lines ol ON ol.id = ll.order_line_id
                WHERE l.status = 'APPROVED'
                  AND l.origin_plant IN ({scope_placeholders})
                  AND DATE(l.created_at) BETWEEN DATE(?) AND DATE(?)
                  AND COALESCE(ol.is_excluded, 0) = 0
                GROUP BY day
                ORDER BY day
                """,
                list(plant_scope) + [start_iso, end_iso],
            ).fetchall():
                if row["day"]:
                    qty_by_day[row["day"]] = float(row["qty"] or 0)

        daily_items = []
        for offset in range(period_len_days):
            day = start_date + timedelta(days=offset)
            day_key = _iso(day)
            daily_items.append(
                {
                    "date": day,
                    "spend": float(spend_by_day.get(day_key) or 0),
                    "qty": float(qty_by_day.get(day_key) or 0),
                }
            )

        raw_buckets = _bucket_series(daily_items, bucket_count=7)
        max_spend = max((bucket["spend"] for bucket in raw_buckets), default=0.0)
        max_qty = max((bucket["qty"] for bucket in raw_buckets), default=0.0)
        trend_buckets = []
        for bucket in raw_buckets:
            spend_height = 0
            volume_height = 0
            if max_spend and bucket["spend"]:
                spend_height = max(4, round((bucket["spend"] / max_spend) * 100))
            if max_qty and bucket["qty"]:
                volume_height = max(4, round((bucket["qty"] / max_qty) * 100))
            trend_buckets.append(
                {
                    "start": bucket["start"],
                    "end": bucket["end"],
                    "spend": bucket["spend"],
                    "qty": bucket["qty"],
                    "spend_height": spend_height,
                    "volume_height": volume_height,
                }
            )

        trend_axis = {"start": "", "mid1": "", "mid2": "", "end": ""}
        if trend_buckets:
            if len(trend_buckets) == 1:
                trend_axis["start"] = _axis_label(trend_buckets[0]["start"])
                trend_axis["end"] = _axis_label(trend_buckets[0]["end"])
            elif len(trend_buckets) == 2:
                trend_axis["start"] = _axis_label(trend_buckets[0]["start"])
                trend_axis["end"] = _axis_label(trend_buckets[1]["end"])
            elif len(trend_buckets) == 3:
                trend_axis["start"] = _axis_label(trend_buckets[0]["start"])
                trend_axis["mid1"] = _axis_label(trend_buckets[1]["start"])
                trend_axis["end"] = _axis_label(trend_buckets[2]["end"])
            else:
                idx1 = len(trend_buckets) // 3
                idx2 = (len(trend_buckets) * 2) // 3
                idx1 = min(max(idx1, 1), len(trend_buckets) - 2)
                idx2 = min(max(idx2, idx1 + 1), len(trend_buckets) - 2)
                trend_axis["start"] = _axis_label(trend_buckets[0]["start"])
                trend_axis["mid1"] = _axis_label(trend_buckets[idx1]["start"])
                trend_axis["mid2"] = _axis_label(trend_buckets[idx2]["start"])
                trend_axis["end"] = _axis_label(trend_buckets[-1]["end"])

        # Efficiency distribution bins.
        efficiency = {"a": 0, "b": 0, "c": 0, "df": 0, "total": 0}
        if plant_scope:
            scope_placeholders = ", ".join("?" for _ in plant_scope)
            row = connection.execute(
                f"""
                SELECT
                    SUM(CASE WHEN utilization_pct >= 95 THEN 1 ELSE 0 END) AS grade_a,
                    SUM(CASE WHEN utilization_pct >= 85 AND utilization_pct < 95 THEN 1 ELSE 0 END) AS grade_b,
                    SUM(CASE WHEN utilization_pct >= 70 AND utilization_pct < 85 THEN 1 ELSE 0 END) AS grade_c,
                    SUM(CASE WHEN utilization_pct < 70 THEN 1 ELSE 0 END) AS grade_df,
                    COUNT(*) AS total
                FROM loads
                WHERE status = 'APPROVED'
                  AND origin_plant IN ({scope_placeholders})
                  AND DATE(created_at) BETWEEN DATE(?) AND DATE(?)
                """,
                list(plant_scope) + [start_iso, end_iso],
            ).fetchone()
            if row:
                efficiency = {
                    "a": int(row["grade_a"] or 0),
                    "b": int(row["grade_b"] or 0),
                    "c": int(row["grade_c"] or 0),
                    "df": int(row["grade_df"] or 0),
                    "total": int(row["total"] or 0),
                }

        eff_total = efficiency["total"] or 0
        efficiency_rows = [
            {
                "label": "Grade A (95%+)",
                "count": efficiency["a"],
                "width": round((efficiency["a"] / eff_total) * 100) if eff_total else 0,
                "class": "success",
            },
            {
                "label": "Grade B (85-94%)",
                "count": efficiency["b"],
                "width": round((efficiency["b"] / eff_total) * 100) if eff_total else 0,
                "class": "info",
            },
            {
                "label": "Grade C (70-84%)",
                "count": efficiency["c"],
                "width": round((efficiency["c"] / eff_total) * 100) if eff_total else 0,
                "class": "neutral",
            },
            {
                "label": "Grade D/F (<70%)",
                "count": efficiency["df"],
                "width": round((efficiency["df"] / eff_total) * 100) if eff_total else 0,
                "class": "danger",
            },
        ]

        # Load review widgets.
        sku_specs = {spec["sku"]: spec for spec in db.list_sku_specs()}
        color_palette = [
            "#137fec",
            "#10b981",
            "#f59e0b",
            "#ef4444",
            "#8b5cf6",
            "#22d3ee",
            "#f472b6",
            "#f97316",
        ]

        def _fetch_loads(limit, ascending, max_util=None):
            if not plant_scope:
                return []
            scope_placeholders = ", ".join("?" for _ in plant_scope)
            util_clause = ""
            params = list(plant_scope)
            if max_util is not None:
                util_clause = " AND utilization_pct < ?"
                params.append(max_util)
            params.extend([start_iso, end_iso, limit])
            order_dir = "ASC" if ascending else "DESC"
            rows = connection.execute(
                f"""
                SELECT id, load_number, origin_plant, utilization_pct, created_at, trailer_type
                FROM loads
                WHERE status = 'APPROVED'
                  AND origin_plant IN ({scope_placeholders})
                  AND DATE(created_at) BETWEEN DATE(?) AND DATE(?)
                  {util_clause}
                ORDER BY utilization_pct {order_dir}, created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [dict(row) for row in rows]

        def _format_load_rows(loads):
            formatted = []
            for load in loads:
                created_at = load.get("created_at") or ""
                ship_date = ""
                try:
                    ship_date = datetime.fromisoformat(created_at).strftime("%m/%d")
                except (TypeError, ValueError):
                    parsed = _parse_date(created_at)
                    ship_date = parsed.strftime("%m/%d") if parsed else ""
                formatted.append(
                    {
                        "id": load["id"],
                        "load_number": load.get("load_number") or f"Load #{load['id']}",
                        "origin_plant": load.get("origin_plant"),
                        "utilization_pct": round(load.get("utilization_pct") or 0, 1),
                        "ship_date": ship_date,
                        "thumbnail": _build_load_thumbnail(load, sku_specs, color_palette),
                    }
                )
            return formatted

        top_loads = _format_load_rows(_fetch_loads(limit=5, ascending=False))
        problem_loads = _format_load_rows(_fetch_loads(limit=5, ascending=True, max_util=70))

    return {
        "period": period,
        "period_label": period_label,
        "period_options": period_options,
        "date_range_label": date_range_label,
        "start_date": start_date,
        "end_date": end_date,
        "plant_scope_label": plant_scope_label,
        "plant_filter_param": plant_filter_param,
        "network_health": {"label": health_label, "class": health_class},
        "kpis": kpis,
        "plant_cards": plant_cards,
        "trend_buckets": trend_buckets,
        "trend_axis": trend_axis,
        "efficiency_rows": efficiency_rows,
        "top_loads": top_loads,
        "problem_loads": problem_loads,
    }


@app.route("/")
@app.route("/dashboard")
def dashboard():
    _require_session()
    context = _build_command_center_dashboard_context()
    return render_template("dashboard.html", **context)


@app.route("/upload", methods=["GET", "POST"])
def upload():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    if request.method == "GET":
        return redirect(url_for("orders"))
    if request.method == "POST":
        file = request.files.get("file")
        if not file:
            return render_template(
                "upload.html", error="Please choose a CSV file to upload.", summary=None
            )
        try:
            importer = OrderImporter()
            summary = importer.parse_csv(file)
            db.upsert_order_lines(summary["order_lines"])
            db.upsert_orders(summary["orders"])
            upload_id = db.add_upload_history(
                {
                    "filename": file.filename,
                    "total_rows": summary["total_rows"],
                    "total_orders": len(summary["orders"]),
                    "mapping_rate": summary["mapping_rate"],
                    "unmapped_count": len(summary["unmapped_items"]),
                }
            )
            db.add_upload_unmapped_items(upload_id, summary["unmapped_items"])
        except Exception as exc:
            return render_template(
                "upload.html",
                error=f"Upload failed: {exc}",
                summary=None,
            )

        return render_template(
            "upload.html",
            error="",
            summary=summary,
        )
    return redirect(url_for("orders"))


@app.route("/orders/upload", methods=["POST"])
def orders_upload():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    file = request.files.get("file")
    if not file:
        return redirect(url_for("orders"))
    try:
        importer = OrderImporter()
        summary = importer.parse_csv(file)
        db.upsert_order_lines(summary["order_lines"])
        db.upsert_orders(summary["orders"])
        upload_id = db.add_upload_history(
            {
                "filename": file.filename,
                "total_rows": summary["total_rows"],
                "total_orders": len(summary["orders"]),
                "mapping_rate": summary["mapping_rate"],
                "unmapped_count": len(summary["unmapped_items"]),
            }
        )
        db.add_upload_unmapped_items(upload_id, summary["unmapped_items"])
    except Exception:
        return redirect(url_for("orders"))

    return redirect(url_for("orders"))


@app.route("/orders")
def orders():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    role = _get_session_role()
    profile_default_plants = session.get(SESSION_PROFILE_DEFAULT_PLANTS_KEY) or []
    allowed_plants = _get_allowed_plants()
    plant_filters = _resolve_plant_filters(request.args.get("plants") or request.args.get("plant"))
    plant_scope = plant_filters or allowed_plants
    today_override = _resolve_today_override(request.args.get("today"))
    today = today_override or date.today()

    due_filter = (request.args.get("due") or "").upper()
    due_start = request.args.get("due_start", "")
    due_end = request.args.get("due_end", "")
    if due_filter == "PAST_DUE":
        due_start = ""
        due_end = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    elif due_filter == "THIS_WEEK":
        start_week, end_week, _, _ = _week_bounds(today)
        due_start = start_week.strftime("%Y-%m-%d")
        due_end = end_week.strftime("%Y-%m-%d")
    elif due_filter == "NEXT_WEEK":
        _, _, next_start, next_end = _week_bounds(today)
        due_start = next_start.strftime("%Y-%m-%d")
        due_end = next_end.strftime("%Y-%m-%d")
    elif not due_filter and (due_start or due_end):
        due_filter = "CUSTOM"

    assignment_filter = (request.args.get("assigned") or "").upper()
    filters = {
        "plants": plant_scope,
        "state": request.args.get("state", ""),
        "cust_name": request.args.get("customer", ""),
        "due_start": due_start or None,
        "due_end": due_end or None,
        "assignment_status": assignment_filter,
    }
    sort_key = request.args.get("sort", "due_date")
    data = order_service.list_orders(filters=filters, sort_key=sort_key)
    orders_list = data["orders"]
    _annotate_orders_due_status(orders_list, today=today)
    orders_snapshot = _build_orders_snapshot(orders_list, today=today)

    plants = allowed_plants
    states = _distinct([order.get("state") for order in orders_list])
    customers = _distinct([order.get("cust_name") for order in orders_list])

    card_filters = {
        "plants": allowed_plants,
        "state": filters.get("state", ""),
        "cust_name": filters.get("cust_name", ""),
    }
    orders_for_cards = order_service.list_orders(filters=card_filters, sort_key=sort_key)["orders"]
    orders_by_plant = {plant: 0 for plant in plants}
    for order in orders_for_cards:
        plant = order.get("plant")
        if plant in orders_by_plant and not order.get("is_excluded"):
            orders_by_plant[plant] += 1
    plant_cards = []
    for plant in plants:
        plant_cards.append(
            {
                "code": plant,
                "name": PLANT_NAMES.get(plant, plant),
                "orders": orders_by_plant.get(plant, 0),
            }
        )

    optimize_defaults = dict(load_builder.DEFAULT_BUILD_PARAMS)
    if plant_filters:
        optimize_defaults["origin_plant"] = plant_filters[0]
    elif not optimize_defaults["origin_plant"] and plants:
        optimize_defaults["origin_plant"] = plants[0]
    optimize_defaults["state_filters"] = []
    optimize_defaults["customer_filters"] = []
    optimize_defaults["enforce_time_window"] = True
    optimize_defaults["batch_horizon_enabled"] = True
    optimize_defaults["batch_end_date"] = _default_batch_end_date().strftime("%Y-%m-%d")

    last_upload = db.get_last_upload()
    last_upload_unmapped_items = []
    if last_upload:
        last_upload_unmapped_items = db.list_upload_unmapped_items(
            last_upload.get("id"), limit=8
        )
    upload_history = db.list_upload_history()
    rejected_orders = sum(1 for order in orders_list if order.get("is_excluded"))
    due_dates = [
        _parse_date(order.get("due_date"))
        for order in orders_list
        if order.get("due_date")
    ]
    due_dates = [value for value in due_dates if value]
    ship_date_range = None
    if due_dates:
        ship_date_range = {
            "start": min(due_dates).strftime("%Y-%m-%d"),
            "end": max(due_dates).strftime("%Y-%m-%d"),
        }

    strategic_setting = db.get_planning_setting("strategic_customers") or {}
    strategic_customers_raw = strategic_setting.get("value_text") or ""
    strategic_customers = _parse_strategic_customers(strategic_customers_raw)
    strategic_customer_groups = []
    for entry in strategic_customers:
        matching_customers = [
            cust
            for cust in customers
            if customer_rules.matches_any_customer_pattern(cust, entry.get("patterns"))
        ]
        if matching_customers:
            strategic_customer_groups.append(
                {
                    "key": entry["key"],
                    "label": entry["label"],
                    "customers": matching_customers,
                }
            )
    strategic_orders = {entry["key"]: [] for entry in strategic_customers}
    other_orders = []
    for order in orders_list:
        cust_name = order.get("cust_name") or ""
        matched_key = None
        for entry in strategic_customers:
            if customer_rules.matches_any_customer_pattern(cust_name, entry.get("patterns")):
                matched_key = entry["key"]
                break
        if matched_key:
            strategic_orders[matched_key].append(order)
        else:
            other_orders.append(order)

    order_sections = []
    for entry in strategic_customers:
        section_orders = strategic_orders.get(entry["key"]) or []
        if not section_orders:
            continue
        limit = 15
        order_sections.append(
            {
                "key": entry["key"],
                "label": entry["label"],
                "orders": section_orders,
                "limit": limit,
                "hidden_count": max(len(section_orders) - limit, 0),
            }
        )
    order_sections.append(
        {
            "key": "other",
            "label": "Other Customers",
            "orders": other_orders,
            "limit": None,
            "hidden_count": 0,
        }
    )

    show_more_plants = False
    if profile_default_plants and role != ROLE_ADMIN:
        if not plant_filters:
            show_more_plants = True
        else:
            show_more_plants = any(code not in profile_default_plants for code in plant_filters)

    today_override_value = today_override.strftime("%Y-%m-%d") if today_override else ""
    today_override_label = today_override.strftime("%b %d, %Y") if today_override else ""

    return render_template(
        "orders.html",
        orders=orders_list,
        order_sections=order_sections,
        strategic_customers=strategic_customers,
        strategic_customer_groups=strategic_customer_groups,
        summary=data["summary"],
        filters=filters,
        plants=plants,
        states=states,
        customers=customers,
        optimize_defaults=optimize_defaults,
        optimize_errors={},
        optimize_summary=None,
        last_upload=last_upload,
        last_upload_unmapped_items=last_upload_unmapped_items,
        upload_history=upload_history,
        rejected_orders=rejected_orders,
        ship_date_range=ship_date_range,
        plant_filters=plant_filters,
        plant_filter_param=",".join(plant_filters) if plant_filters else "",
        due_filter=due_filter,
        due_start=due_start,
        due_end=due_end,
        assignment_filter=assignment_filter,
        plant_cards=plant_cards,
        orders_snapshot=orders_snapshot,
        today_override=today_override,
        today_override_value=today_override_value,
        today_override_label=today_override_label,
        profile_default_plants=profile_default_plants,
        show_more_plants=show_more_plants,
        is_admin=role == ROLE_ADMIN,
    )


@app.route("/orders/clear", methods=["POST"])
def clear_orders():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    _require_admin()
    db.clear_loads()
    db.clear_orders()
    return redirect(url_for("orders"))


@app.route("/orders/exclude", methods=["POST"])
def exclude_orders():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    ids = request.form.getlist("order_ids")
    order_service.exclude_orders([int(order_id) for order_id in ids])
    return redirect(url_for("orders"))


@app.route("/orders/include", methods=["POST"])
def include_orders():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    ids = request.form.getlist("order_ids")
    order_service.include_orders([int(order_id) for order_id in ids])
    return redirect(url_for("orders"))


@app.route("/orders/optimize", methods=["POST"])
def orders_optimize():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    role = _get_session_role()
    profile_default_plants = session.get(SESSION_PROFILE_DEFAULT_PLANTS_KEY) or []
    allowed_plants = _get_allowed_plants()
    origin_plant = _normalize_plant_code(request.form.get("origin_plant"))
    if origin_plant and origin_plant not in allowed_plants:
        form_data = dict(load_builder.DEFAULT_BUILD_PARAMS)
        form_data["origin_plant"] = origin_plant
        form_data["trailer_type"] = request.form.get("trailer_type", "STEP_DECK")
        form_data["time_window_days"] = request.form.get("time_window_days", form_data.get("time_window_days", "7"))
        form_data["geo_radius"] = request.form.get("geo_radius", form_data.get("geo_radius", "100"))
        form_data["max_detour_pct"] = request.form.get("max_detour_pct", form_data.get("max_detour_pct", "15"))
        form_data["capacity_feet"] = request.form.get("capacity_feet", form_data.get("capacity_feet", "53"))
        form_data["state_filters"] = [
            value.strip().upper()
            for value in request.form.getlist("opt_states")
            if value and value.strip()
        ]
        form_data["customer_filters"] = [
            value.strip()
            for value in request.form.getlist("opt_customers")
            if value and value.strip()
        ]
        ui_toggles = "opt_toggles" in request.form
        form_data["enforce_time_window"] = bool(request.form.get("enforce_time_window")) if ui_toggles else True
        form_data["batch_horizon_enabled"] = bool(request.form.get("batch_horizon_enabled")) if ui_toggles else False
        form_data["batch_end_date"] = request.form.get("batch_end_date") or _default_batch_end_date().strftime("%Y-%m-%d")
        result = {
            "errors": {"origin_plant": "Select a plant within your scope."},
            "form_data": form_data,
            "success_message": "",
            "summary": None,
        }
    else:
        result = load_builder.build_loads(request.form)

    if not result["errors"]:
        return redirect(url_for("loads", plants=origin_plant))

    plant_filters = _resolve_plant_filters(request.args.get("plants") or request.args.get("plant"))
    plant_scope = plant_filters or allowed_plants

    due_filter = (request.args.get("due") or "").upper()
    due_start = request.args.get("due_start", "")
    due_end = request.args.get("due_end", "")
    today_override = _resolve_today_override(request.values.get("today"))
    today = today_override or date.today()
    if due_filter == "PAST_DUE":
        due_start = ""
        due_end = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    elif due_filter == "THIS_WEEK":
        start_week, end_week, _, _ = _week_bounds(today)
        due_start = start_week.strftime("%Y-%m-%d")
        due_end = end_week.strftime("%Y-%m-%d")
    elif due_filter == "NEXT_WEEK":
        _, _, next_start, next_end = _week_bounds(today)
        due_start = next_start.strftime("%Y-%m-%d")
        due_end = next_end.strftime("%Y-%m-%d")
    elif not due_filter and (due_start or due_end):
        due_filter = "CUSTOM"

    assignment_filter = (request.args.get("assigned") or "").upper()
    filters = {
        "plants": plant_scope,
        "state": request.args.get("state", ""),
        "cust_name": request.args.get("customer", ""),
        "due_start": due_start or None,
        "due_end": due_end or None,
        "assignment_status": assignment_filter,
    }
    data = order_service.list_orders(filters=filters, sort_key="due_date")
    orders_list = data["orders"]
    _annotate_orders_due_status(orders_list, today=today)
    orders_snapshot = _build_orders_snapshot(orders_list, today=today)
    plants = allowed_plants
    states = _distinct([order.get("state") for order in orders_list])
    customers = _distinct([order.get("cust_name") for order in orders_list])
    last_upload = db.get_last_upload()
    upload_history = db.list_upload_history()
    last_upload_unmapped_items = []
    if last_upload:
        last_upload_unmapped_items = db.list_upload_unmapped_items(
            last_upload.get("id"), limit=8
        )

    card_filters = {
        "plants": allowed_plants,
        "state": filters.get("state", ""),
        "cust_name": filters.get("cust_name", ""),
    }
    orders_for_cards = order_service.list_orders(filters=card_filters, sort_key="due_date")["orders"]
    orders_by_plant = {plant: 0 for plant in plants}
    for order in orders_for_cards:
        plant = order.get("plant")
        if plant in orders_by_plant and not order.get("is_excluded"):
            orders_by_plant[plant] += 1
    plant_cards = [
        {
            "code": plant,
            "name": PLANT_NAMES.get(plant, plant),
            "orders": orders_by_plant.get(plant, 0),
        }
        for plant in plants
    ]

    rejected_orders = sum(1 for order in orders_list if order.get("is_excluded"))
    due_dates = [
        _parse_date(order.get("due_date"))
        for order in orders_list
        if order.get("due_date")
    ]
    due_dates = [value for value in due_dates if value]
    ship_date_range = None
    if due_dates:
        ship_date_range = {
            "start": min(due_dates).strftime("%Y-%m-%d"),
            "end": max(due_dates).strftime("%Y-%m-%d"),
        }

    strategic_setting = db.get_planning_setting("strategic_customers") or {}
    strategic_customers_raw = strategic_setting.get("value_text") or ""
    strategic_customers = _parse_strategic_customers(strategic_customers_raw)
    strategic_customer_groups = []
    for entry in strategic_customers:
        matching_customers = [
            cust
            for cust in customers
            if customer_rules.matches_any_customer_pattern(cust, entry.get("patterns"))
        ]
        if matching_customers:
            strategic_customer_groups.append(
                {
                    "key": entry["key"],
                    "label": entry["label"],
                    "customers": matching_customers,
                }
            )
    strategic_orders = {entry["key"]: [] for entry in strategic_customers}
    other_orders = []
    for order in orders_list:
        cust_name = order.get("cust_name") or ""
        matched_key = None
        for entry in strategic_customers:
            if customer_rules.matches_any_customer_pattern(cust_name, entry.get("patterns")):
                matched_key = entry["key"]
                break
        if matched_key:
            strategic_orders[matched_key].append(order)
        else:
            other_orders.append(order)

    order_sections = []
    for entry in strategic_customers:
        section_orders = strategic_orders.get(entry["key"]) or []
        if not section_orders:
            continue
        limit = 15
        order_sections.append(
            {
                "key": entry["key"],
                "label": entry["label"],
                "orders": section_orders,
                "limit": limit,
                "hidden_count": max(len(section_orders) - limit, 0),
            }
        )
    order_sections.append(
        {
            "key": "other",
            "label": "Other Customers",
            "orders": other_orders,
            "limit": None,
            "hidden_count": 0,
        }
    )

    show_more_plants = False
    if profile_default_plants and role != ROLE_ADMIN:
        if not plant_filters:
            show_more_plants = True
        else:
            show_more_plants = any(code not in profile_default_plants for code in plant_filters)

    today_override_value = today_override.strftime("%Y-%m-%d") if today_override else ""
    today_override_label = today_override.strftime("%b %d, %Y") if today_override else ""

    return render_template(
        "orders.html",
        orders=orders_list,
        order_sections=order_sections,
        strategic_customers=strategic_customers,
        strategic_customer_groups=strategic_customer_groups,
        summary=data["summary"],
        filters=filters,
        plants=plants,
        states=states,
        customers=customers,
        optimize_defaults=result["form_data"],
        optimize_errors=result["errors"],
        optimize_summary=result["summary"],
        last_upload=last_upload,
        last_upload_unmapped_items=last_upload_unmapped_items,
        upload_history=upload_history,
        rejected_orders=rejected_orders,
        ship_date_range=ship_date_range,
        plant_filters=plant_filters,
        plant_filter_param=",".join(plant_filters) if plant_filters else "",
        due_filter=due_filter,
        due_start=due_start,
        due_end=due_end,
        assignment_filter=assignment_filter,
        plant_cards=plant_cards,
        orders_snapshot=orders_snapshot,
        today_override=today_override,
        today_override_value=today_override_value,
        today_override_label=today_override_label,
        profile_default_plants=profile_default_plants,
        show_more_plants=show_more_plants,
        is_admin=role == ROLE_ADMIN,
    )


@app.route("/orders/export")
def export_orders():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    plant_filters = _resolve_plant_filters(request.args.get("plants") or request.args.get("plant"))
    plant_scope = plant_filters or _get_allowed_plants()
    filters = {"plants": plant_scope} if plant_scope else {}
    data = order_service.list_orders(filters=filters)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(data["orders"][0].keys() if data["orders"] else [])
    for order in data["orders"]:
        writer.writerow(order.values())
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=orders_export.csv"},
    )


@app.route("/optimize", methods=["GET"])
def optimize():
    return redirect(url_for("orders"))


@app.route("/optimize/build", methods=["POST"])
def build_optimize():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    allowed_plants = _get_allowed_plants()
    origin_plant = _normalize_plant_code(request.form.get("origin_plant"))
    if origin_plant and origin_plant not in allowed_plants:
        return redirect(url_for("orders"))

    result = load_builder.build_loads(request.form)
    if result["errors"]:
        return redirect(url_for("orders"))
    return redirect(url_for("loads"))


@app.route("/loads")
def loads():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    allowed_plants = _get_allowed_plants()
    plant_filters = _resolve_plant_filters(request.args.get("plants") or request.args.get("plant"))
    plant_scope = plant_filters or allowed_plants
    tab = (request.args.get("tab") or "").strip().lower()
    status_filter = (request.args.get("status") or "").strip().upper()
    sort_mode = (request.args.get("sort") or "flow").strip().lower()
    if sort_mode not in {"flow", "util"}:
        sort_mode = "flow"
    today_override = _resolve_today_override(request.args.get("today"))
    today = today_override or date.today()
    reopt_status = request.args.get("reopt", "")
    feedback_error = request.args.get("feedback_error") or ""
    feedback_target = request.args.get("feedback_target") or ""
    manual_error = request.args.get("manual_error") or ""
    all_loads = load_builder.list_loads(None)
    all_loads = [load for load in all_loads if load.get("origin_plant") in allowed_plants]
    loads_data = [load for load in all_loads if load.get("origin_plant") in plant_scope]
    zip_coords = geo_utils.load_zip_coordinates()
    plant_names = {row["plant_code"]: row["name"] for row in db.list_plants()}
    sku_specs = {spec["sku"]: spec for spec in db.list_sku_specs()}
    color_palette = [
        "#137fec",
        "#10b981",
        "#f59e0b",
        "#ef4444",
        "#8b5cf6",
        "#22d3ee",
        "#f472b6",
        "#f97316",
    ]
    route_palette = [
        "#38bdf8",
        "#22c55e",
        "#f59e0b",
        "#f97316",
        "#a855f7",
        "#ef4444",
    ]

    for load in loads_data:
        lines = load.get("lines", [])
        trailer_type = (load.get("trailer_type") or "STEP_DECK").strip().upper()
        load["trailer_type"] = trailer_type
        load["total_units"] = sum((line.get("qty") or 0) for line in lines)
        load["total_sales"] = sum((line.get("sales") or 0) for line in lines)
        order_colors = {}
        for line in lines:
            so_num = line.get("so_num")
            if so_num and so_num not in order_colors:
                order_colors[so_num] = color_palette[len(order_colors) % len(color_palette)]
        load["order_colors"] = order_colors
        load["order_count"] = len(order_colors)
        line_items = []
        stops = []
        stop_map = {}
        due_dates = []
        for line in lines:
            due_date = _parse_date(line.get("due_date"))
            if due_date:
                due_dates.append(due_date)
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
            zip_code = geo_utils.normalize_zip(line.get("zip"))
            state = line.get("state") or ""
            city = line.get("city") or ""
            key = f"{zip_code}|{state}"
            if key not in stop_map:
                coords = zip_coords.get(zip_code) if zip_code else None
                stop_map[key] = {
                    "zip": zip_code,
                    "state": state,
                    "city": city,
                    "lat": coords[0] if coords else None,
                    "lng": coords[1] if coords else None,
                    "customers": set(),
                }
            elif city and not stop_map[key].get("city"):
                stop_map[key]["city"] = city
            if line.get("cust_name"):
                stop_map[key]["customers"].add(line.get("cust_name"))

        anchor_date = min(due_dates) if due_dates else None
        load["ship_date"] = anchor_date.strftime("%Y-%m-%d") if anchor_date else ""
        load["ship_date_status"] = _due_status(anchor_date, today=today)
        for line in lines:
            status_label = ""
            due_date = _parse_date(line.get("due_date"))
            if due_date and due_date < today:
                status_label = "Past Due"
            elif due_date and anchor_date:
                delta_days = (due_date - anchor_date).days
                if delta_days > 0:
                    status_label = f"Early {delta_days}d"
            line["status_label"] = status_label

        manifest_groups = []
        group_map = {}
        for line in lines:
            order_id = line.get("so_num") or "UNKNOWN"
            group = group_map.get(order_id)
            if not group:
                group = {
                    "order_id": order_id,
                    "due_date": line.get("due_date") or "",
                    "cust_name": line.get("cust_name") or "",
                    "city": line.get("city") or "",
                    "state": line.get("state") or "",
                    "zip": line.get("zip") or "",
                    "total_qty": 0,
                    "status_label": "",
                    "sku_set": set(),
                    "lines": [],
                }
                group_map[order_id] = group
                manifest_groups.append(group)

            group["total_qty"] += line.get("qty") or 0
            group["lines"].append(line)
            sku_value = line.get("sku")
            if sku_value:
                group["sku_set"].add(sku_value)

            group_due = _parse_date(group.get("due_date"))
            line_due = _parse_date(line.get("due_date"))
            if line_due and (not group_due or line_due < group_due):
                group["due_date"] = line.get("due_date")

            if not group["city"] and line.get("city"):
                group["city"] = line.get("city")
            if not group["state"] and line.get("state"):
                group["state"] = line.get("state")
            if not group["zip"] and line.get("zip"):
                group["zip"] = line.get("zip")

            line_status = line.get("status_label") or ""
            if "Past Due" in line_status:
                group["status_label"] = "Past Due"
            elif line_status and not group["status_label"]:
                group["status_label"] = line_status

        for group in manifest_groups:
            group["color"] = load["order_colors"].get(group["order_id"], "#64748b")
            group["sku_list"] = sorted(group.get("sku_set") or [])
            group_due = _parse_date(group.get("due_date"))
            group["due_status"] = _due_status(group_due, today=today)
            early_days = (group_due - anchor_date).days if group_due and anchor_date else 0
            group["early_days"] = early_days if early_days > 0 else 0
        load["manifest_groups"] = manifest_groups

        for stop in stop_map.values():
            stops.append(
                {
                    "zip": stop["zip"],
                    "state": stop["state"],
                    "city": stop.get("city") or "",
                    "city_abbr": _city_abbr(stop.get("city")),
                    "lat": stop.get("lat"),
                    "lng": stop.get("lng"),
                    "customers": sorted(stop.get("customers") or []),
                }
            )

        origin_code = load.get("origin_plant")
        origin_coords = geo_utils.plant_coords_for_code(origin_code)
        requires_return_to_origin = any(
            customer_rules.is_lowes_customer(line.get("cust_name") or "")
            for line in lines
        )
        ordered_stops = tsp_solver.solve_route(origin_coords, stops) if origin_coords else list(stops)

        origin_name = plant_names.get(origin_code, PLANT_NAMES.get(origin_code, origin_code))
        route_nodes = [
            {
                "type": "origin",
                "label": origin_code or "",
                "subtitle": origin_name or "",
                "icon": "home",
                "coords": origin_coords,
                "sequence": 0,
            }
        ]
        for idx, stop in enumerate(ordered_stops, start=1):
            coords = None
            if stop.get("lat") is not None and stop.get("lng") is not None:
                coords = (stop.get("lat"), stop.get("lng"))
            city = stop.get("city") or ""
            state = stop.get("state") or ""
            subtitle = ", ".join([part for part in [city, state] if part]).strip()
            route_nodes.append(
                {
                    "type": "stop",
                    "label": stop.get("city_abbr") or state or stop.get("zip") or "",
                    "subtitle": subtitle,
                    "icon": "place",
                    "coords": coords,
                    "sequence": idx,
                }
            )
        if requires_return_to_origin and origin_coords and len(route_nodes) > 1:
            route_nodes.append(
                {
                    "type": "final",
                    "label": origin_code or "",
                    "subtitle": origin_name or "",
                    "icon": "flag",
                    "coords": origin_coords,
                    "sequence": len(route_nodes),
                }
            )
        elif len(route_nodes) > 1:
            route_nodes[-1]["type"] = "final"
            route_nodes[-1]["icon"] = "flag"

        for idx, node in enumerate(route_nodes):
            color = route_palette[idx % len(route_palette)]
            node["color"] = color
            node["bg"] = f"{color}22"

        route_legs = []
        for idx in range(len(route_nodes) - 1):
            origin_leg = route_nodes[idx].get("coords")
            dest_leg = route_nodes[idx + 1].get("coords")
            if origin_leg and dest_leg:
                miles = geo_utils.haversine_distance_coords(origin_leg, dest_leg)
                route_legs.append(round(miles))
            else:
                route_legs.append(None)
        total_route_distance = sum(leg for leg in route_legs if leg is not None)

        schematic = stack_calculator.calculate_stack_configuration(
            line_items,
            trailer_type=trailer_type,
        )
        load["auto_trailer_label"] = ""
        load["auto_trailer_reason"] = ""
        if trailer_type == "FLATBED" and not schematic.get("exceeds_capacity"):
            step_items = []
            for line in lines:
                sku = line.get("sku")
                spec = sku_specs.get(sku) if sku else None
                max_stack = (spec or {}).get("max_stack_step_deck") or (spec or {}).get("max_stack_flat_bed") or 1
                step_items.append(
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
            step_schematic = stack_calculator.calculate_stack_configuration(
                step_items,
                trailer_type="STEP_DECK",
            )
            if step_schematic.get("exceeds_capacity"):
                load["auto_trailer_label"] = "Auto Flatbed"
                load["auto_trailer_reason"] = "Assigned a flatbed because the load does not fit on the 43' / 10' step deck split."
        sku_colors = {}
        for idx, item in enumerate(line_items):
            sku = item.get("sku") or f"item-{idx}"
            if sku not in sku_colors:
                sku_colors[sku] = color_palette[len(sku_colors) % len(color_palette)]

        utilization_pct = schematic.get("utilization_pct", load.get("utilization_pct", 0)) or 0
        load["utilization_pct"] = utilization_pct
        order_numbers = {line.get("so_num") for line in lines if line.get("so_num")}
        exceeds_capacity = schematic.get("exceeds_capacity", False)
        load["over_capacity"] = (exceeds_capacity or utilization_pct > 100) and len(order_numbers) <= 1
        load["schematic"] = schematic
        load["stops"] = ordered_stops
        load["stop_count"] = len(ordered_stops)
        load["sku_colors"] = sku_colors
        load["route_nodes"] = route_nodes
        load["route_legs"] = route_legs
        load["route_distance"] = round(total_route_distance) if total_route_distance else 0
        map_stops = []
        if origin_coords:
            origin_color = route_nodes[0]["color"] if route_nodes else "#38bdf8"
            map_stops.append(
                {
                    "type": "origin",
                    "lat": origin_coords[0],
                    "lng": origin_coords[1],
                    "label": origin_name or origin_code,
                    "sequence": 0,
                    "color": origin_color,
                }
            )
        for idx, stop in enumerate(ordered_stops, start=1):
            stop_color = route_nodes[idx]["color"] if idx < len(route_nodes) else "#f59e0b"
            map_stops.append(
                {
                    "type": "stop",
                    "lat": stop.get("lat"),
                    "lng": stop.get("lng"),
                    "label": stop.get("city") or stop.get("state") or stop.get("zip") or "",
                    "sequence": idx,
                    "color": stop_color,
                }
            )
        if requires_return_to_origin and origin_coords and map_stops:
            final_color = route_nodes[-1]["color"] if route_nodes else "#f59e0b"
            map_stops.append(
                {
                    "type": "final",
                    "lat": origin_coords[0],
                    "lng": origin_coords[1],
                    "label": origin_name or origin_code,
                    "sequence": len(map_stops),
                    "color": final_color,
                }
            )
        elif map_stops:
            map_stops[-1]["type"] = "final"
        load["map_stops"] = map_stops

    optimized_loads = []
    for load in loads_data:
        status = (load.get("status") or STATUS_PROPOSED).upper()
        build_source = (load.get("build_source") or "OPTIMIZED").upper()
        if status not in {STATUS_PROPOSED, STATUS_DRAFT}:
            continue
        if build_source == "MANUAL":
            continue
        optimized_loads.append(load)
    optimized_order_ids = {
        line.get("so_num")
        for load in optimized_loads
        for line in load.get("lines", [])
        if line.get("so_num")
    }
    optimized_total_spend = sum((load.get("estimated_cost") or 0) for load in optimized_loads)
    optimized_util_values = [load.get("utilization_pct") or 0 for load in optimized_loads]
    optimized_avg_util = (
        round(sum(optimized_util_values) / len(optimized_util_values), 1)
        if optimized_util_values
        else 0.0
    )
    grade_counts = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
    for load in optimized_loads:
        grade = (load.get("schematic") or {}).get("utilization_grade")
        if not grade:
            grade = _utilization_grade(load.get("utilization_pct") or 0)
        grade = (grade or "F").upper()
        if grade not in grade_counts:
            grade_counts[grade] = 0
        grade_counts[grade] += 1

    baseline_cost = None
    baseline_set_at = None
    baseline_delta = None
    baseline_direction = ""
    if len(plant_scope) == 1:
        baseline_info = db.get_optimizer_baseline(plant_scope[0])
        baseline_cost = baseline_info.get("baseline_cost")
        baseline_set_at = baseline_info.get("baseline_set_at")
        if baseline_cost is None and optimized_total_spend:
            db.set_optimizer_baseline(plant_scope[0], optimized_total_spend)
            baseline_cost = optimized_total_spend
            baseline_set_at = datetime.utcnow().isoformat(timespec="seconds")
        if baseline_cost is not None:
            baseline_delta = optimized_total_spend - baseline_cost
            if baseline_delta < 0:
                baseline_direction = "below"
            elif baseline_delta > 0:
                baseline_direction = "above"

    optimization_summary = {
        "total_orders": len(optimized_order_ids),
        "total_loads": len(optimized_loads),
        "total_spend": optimized_total_spend,
        "avg_utilization": optimized_avg_util,
        "grade_counts": grade_counts,
        "baseline_cost": baseline_cost,
        "baseline_set_at": baseline_set_at,
        "baseline_delta": baseline_delta,
        "baseline_direction": baseline_direction,
    }

    metrics_loads = list(loads_data)
    util_values = [load.get("utilization_pct") for load in metrics_loads if load.get("utilization_pct") is not None]
    avg_utilization = round(sum(util_values) / len(util_values), 1) if util_values else 0.0
    total_planned_cost = sum((load.get("estimated_cost") or 0) for load in metrics_loads)
    pending_exceptions = sum(
        1
        for load in metrics_loads
        if load.get("over_capacity") or (load.get("schematic") or {}).get("exceeds_capacity")
    )

    all_statuses = sorted({(load.get("status") or STATUS_PROPOSED).upper() for load in loads_data})
    if tab not in {"draft", "final"}:
        if status_filter == STATUS_APPROVED:
            tab = "final"
        elif status_filter in {STATUS_DRAFT, STATUS_PROPOSED}:
            tab = "draft"
        else:
            tab = "draft"

    if tab == "draft":
        loads_data = [
            load
            for load in loads_data
            if (load.get("status") or STATUS_PROPOSED).upper() in {STATUS_PROPOSED, STATUS_DRAFT}
        ]
    elif tab == "final":
        loads_data = [
            load
            for load in loads_data
            if (load.get("status") or STATUS_PROPOSED).upper() == STATUS_APPROVED
        ]
    elif status_filter:
        loads_data = [
            load
            for load in loads_data
            if (load.get("status") or STATUS_PROPOSED).upper() == status_filter
        ]

    if tab == "draft":
        for load in loads_data:
            standalone_cost = load.get("standalone_cost")
            consolidation_savings = load.get("consolidation_savings")
            if not standalone_cost:
                if consolidation_savings is not None:
                    standalone_cost = (load.get("estimated_cost") or 0) + consolidation_savings
                else:
                    standalone_cost = load.get("estimated_cost") or 0
            if consolidation_savings is None:
                consolidation_savings = (standalone_cost or 0) - (load.get("estimated_cost") or 0)

            fragility_score = (consolidation_savings / standalone_cost) if standalone_cost else 0.0
            load["standalone_cost"] = standalone_cost
            load["consolidation_savings"] = consolidation_savings
            load["fragility_score"] = fragility_score
            load["stop_count"] = load.get("stop_count") or len(load.get("stops") or [])

        def _approval_sort_key(load):
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
                -(load.get("id") or 0),
            )

        if sort_mode == "util":
            loads_data.sort(
                key=lambda load: (
                    -(load.get("utilization_pct") or 0),
                    -(load.get("estimated_cost") or 0),
                    -(load.get("id") or 0),
                )
            )
        else:
            loads_data.sort(key=_approval_sort_key)
    elif sort_mode == "util":
        loads_data.sort(
            key=lambda load: (
                -(load.get("utilization_pct") or 0),
                -(load.get("estimated_cost") or 0),
                -(load.get("id") or 0),
            )
        )

    full_truckloads = []
    manual_loads = []
    other_loads = list(loads_data)
    if tab == "draft":
        manual_loads = [
            load
            for load in loads_data
            if (load.get("build_source") or "OPTIMIZED").upper() == "MANUAL"
        ]
        other_loads = [
            load
            for load in loads_data
            if (load.get("build_source") or "OPTIMIZED").upper() != "MANUAL"
        ]
        full_ids = set()
        for load in other_loads:
            if _is_full_truckload(load):
                full_truckloads.append(load)
                if load.get("id") is not None:
                    full_ids.add(load.get("id"))
        other_loads = [load for load in other_loads if load.get("id") not in full_ids]

    load_sections = []
    if tab == "draft":
        if manual_loads:
            load_sections.append(
                {"title": "Manually Built Loads", "loads": manual_loads, "kind": "manual"}
            )
        if full_truckloads:
            load_sections.append(
                {"title": "Full Truckload", "loads": full_truckloads, "kind": "full"}
            )
        load_sections.append({"title": "Draft Loads", "loads": other_loads, "kind": "draft"})
    else:
        load_sections.append({"title": "Loads", "loads": loads_data, "kind": "all"})

    plant_cards = []
    for plant in allowed_plants:
        plant_loads = [load for load in all_loads if load.get("origin_plant") == plant]
        load_count = len(plant_loads)
        order_count = len(
            {
                line.get("so_num")
                for load in plant_loads
                for line in load.get("lines", [])
                if line.get("so_num")
            }
        )
        plant_cards.append(
            {
                "code": plant,
                "name": PLANT_NAMES.get(plant, plant),
                "orders": order_count,
                "loads": load_count,
            }
        )

    plant_scope = plant_scope or allowed_plants
    progress_snapshot = _compute_load_progress_snapshot(
        plant_scope=plant_scope,
        all_loads=all_loads,
        allowed_plants=allowed_plants,
    )
    order_status_counts = progress_snapshot["order_status_counts"]
    load_status_counts = progress_snapshot["load_status_counts"]
    total_orders = progress_snapshot["total_orders"]
    approved_orders = progress_snapshot["approved_orders"]
    remaining_orders = max(total_orders - approved_orders, 0)
    progress_pct = progress_snapshot["progress_pct"]
    draft_tab_count = progress_snapshot["draft_tab_count"]
    final_tab_count = progress_snapshot["final_tab_count"]

    today_override_value = today_override.strftime("%Y-%m-%d") if today_override else ""
    today_override_label = today_override.strftime("%b %d, %Y") if today_override else ""

    return render_template(
        "loads.html",
        loads=loads_data,
        plants=allowed_plants,
        plant_filters=plant_filters,
        plant_filter_param=",".join(plant_filters) if plant_filters else "",
        plant_cards=plant_cards,
        statuses=all_statuses,
        status_filter=status_filter,
        tab=tab,
        sort_mode=sort_mode,
        draft_tab_count=draft_tab_count,
        final_tab_count=final_tab_count,
        reopt_status=reopt_status,
        load_sections=load_sections,
        order_status_counts=order_status_counts,
        load_status_counts=load_status_counts,
        progress_pct=progress_pct,
        total_orders=total_orders,
        approved_orders=approved_orders,
        remaining_orders=remaining_orders,
        avg_utilization=avg_utilization,
        total_planned_cost=total_planned_cost,
        pending_exceptions=pending_exceptions,
        optimization_summary=optimization_summary,
        feedback_error=feedback_error,
        feedback_target=feedback_target,
        manual_error=manual_error,
        today_override=today_override,
        today_override_value=today_override_value,
        today_override_label=today_override_label,
        order_removal_reasons=ORDER_REMOVAL_REASONS,
        load_rejection_reasons=LOAD_REJECTION_REASONS,
        is_admin=_get_session_role() == ROLE_ADMIN,
    )


@app.route("/loads/manual/search")
def manual_load_search():
    session_redirect = _require_session()
    if session_redirect:
        return jsonify({"error": "Session expired"}), 401

    allowed_plants = _get_allowed_plants()
    plant_code = (request.args.get("plant") or "").strip().upper()
    if not plant_code or plant_code not in allowed_plants:
        return jsonify({"error": "Invalid plant"}), 400

    q = (request.args.get("q") or "").strip()
    orders = db.list_eligible_manual_orders(plant_code, search=q, limit=25)
    return jsonify({"orders": orders})


@app.route("/loads/manual/suggest")
def manual_load_suggest():
    session_redirect = _require_session()
    if session_redirect:
        return jsonify({"error": "Session expired"}), 401

    allowed_plants = _get_allowed_plants()
    plant_code = (request.args.get("plant") or "").strip().upper()
    if not plant_code or plant_code not in allowed_plants:
        return jsonify({"error": "Invalid plant"}), 400

    seed_so_num = (request.args.get("seed") or "").strip()
    if not seed_so_num:
        return jsonify({"error": "Missing seed order"}), 400

    settings = db.get_optimizer_settings(plant_code) or {}
    try:
        geo_radius = float(settings.get("geo_radius") or load_builder.DEFAULT_BUILD_PARAMS.get("geo_radius") or 0)
    except (TypeError, ValueError):
        geo_radius = float(load_builder.DEFAULT_BUILD_PARAMS.get("geo_radius") or 0)
    try:
        time_window_days = int(settings.get("time_window_days") or load_builder.DEFAULT_BUILD_PARAMS.get("time_window_days") or 0)
    except (TypeError, ValueError):
        time_window_days = int(load_builder.DEFAULT_BUILD_PARAMS.get("time_window_days") or 0)

    candidates = db.list_eligible_manual_orders(plant_code, search=None, limit=None)
    candidate_map = {str(order.get("so_num") or "").strip(): order for order in candidates if order.get("so_num")}
    seed = candidate_map.get(seed_so_num)
    if not seed:
        return jsonify({"error": "Seed order not available in draft scope"}), 404

    zip_coords = geo_utils.load_zip_coordinates()
    seed_zip = geo_utils.normalize_zip(seed.get("zip"))
    seed_coords = zip_coords.get(seed_zip) if seed_zip else None
    seed_due = _parse_date(seed.get("due_date"))

    suggestions = []
    for order in candidates:
        so_num = str(order.get("so_num") or "").strip()
        if not so_num or so_num == seed_so_num:
            continue

        order_due = _parse_date(order.get("due_date"))
        if seed_due and order_due and time_window_days and time_window_days > 0:
            if abs((order_due - seed_due).days) > time_window_days:
                continue

        dist = None
        order_zip = geo_utils.normalize_zip(order.get("zip"))
        order_coords = zip_coords.get(order_zip) if order_zip else None
        if seed_coords and order_coords:
            dist = geo_utils.haversine_distance_coords(seed_coords, order_coords)
            if geo_radius and geo_radius > 0 and dist > geo_radius:
                continue

        suggestions.append(
            {
                "so_num": so_num,
                "cust_name": order.get("cust_name") or "",
                "due_date": order.get("due_date") or "",
                "city": order.get("city") or "",
                "state": order.get("state") or "",
                "zip": order.get("zip") or "",
                "total_length_ft": order.get("total_length_ft") or 0,
                "total_qty": order.get("total_qty") or 0,
                "utilization_pct": order.get("utilization_pct") or 0,
                "distance_miles": round(dist, 1) if dist is not None else None,
            }
        )

    suggestions.sort(
        key=lambda item: (
            item["distance_miles"] is None,
            item["distance_miles"] if item["distance_miles"] is not None else 0,
            item.get("due_date") or "",
        )
    )

    return jsonify(
        {
            "seed": {
                "so_num": seed_so_num,
                "cust_name": seed.get("cust_name") or "",
                "due_date": seed.get("due_date") or "",
                "city": seed.get("city") or "",
                "state": seed.get("state") or "",
                "zip": seed.get("zip") or "",
                "total_length_ft": seed.get("total_length_ft") or 0,
                "total_qty": seed.get("total_qty") or 0,
                "utilization_pct": seed.get("utilization_pct") or 0,
            },
            "suggestions": suggestions[:25],
            "params": {
                "geo_radius": geo_radius,
                "time_window_days": time_window_days,
            },
        }
    )


@app.route("/loads/manual/create", methods=["POST"])
def manual_load_create():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    allowed_plants = _get_allowed_plants()
    plant_code = (request.form.get("plant") or "").strip().upper()
    if not plant_code or plant_code not in allowed_plants:
        return redirect(url_for("loads", manual_error="Select a valid plant."))

    so_nums = list(
        dict.fromkeys(
            [value.strip() for value in request.form.getlist("so_nums") if (value or "").strip()]
        )
    )
    if not so_nums:
        return redirect(url_for("loads", plants=plant_code, manual_error="Select at least one order."))

    eligible = db.filter_eligible_manual_so_nums(plant_code, so_nums)
    if eligible != set(so_nums) or len(eligible) != len(so_nums):
        return redirect(
            url_for(
                "loads",
                plants=plant_code,
                manual_error="Some selected orders are no longer available in Draft Loads.",
            )
        )

    trailer_type = (request.form.get("trailer_type") or "").strip().upper() or None

    # Clear any non-manual draft loads so selected orders can be reassigned.
    db.clear_unapproved_loads(plant_code)

    result = load_builder.create_manual_load(
        plant_code,
        so_nums,
        trailer_type=trailer_type,
        created_by=_get_session_profile_name() or _get_session_role(),
    )
    if result.get("errors"):
        _reoptimize_for_plant(plant_code)
        message = next(iter(result["errors"].values()))
        return redirect(url_for("loads", plants=plant_code, manual_error=message))

    _reoptimize_for_plant(plant_code)
    return redirect(url_for("loads", plants=plant_code, tab="draft", reopt="done"))


def _capacity_for_trailer(trailer_type):
    trailer_key = (trailer_type or "STEP_DECK").strip().upper()
    config = stack_calculator.TRAILER_CONFIGS.get(trailer_key, stack_calculator.TRAILER_CONFIGS["STEP_DECK"])
    try:
        return float(config.get("capacity") or 53)
    except (TypeError, ValueError):
        return 53.0


@app.route("/loads/<int:load_id>/manual_add/suggestions")
def manual_add_suggestions(load_id):
    session_redirect = _require_session()
    if session_redirect:
        return jsonify({"error": "Session expired"}), 401

    load = db.get_load(load_id)
    if not load:
        return jsonify({"error": "Load not found"}), 404

    allowed_plants = _get_allowed_plants()
    if load.get("origin_plant") not in allowed_plants:
        return jsonify({"error": "Not authorized for this plant"}), 403

    status = (load.get("status") or STATUS_PROPOSED).upper()
    if status == STATUS_APPROVED:
        return jsonify({"error": "Approved loads cannot be modified."}), 400

    plant_code = load.get("origin_plant")
    trailer_type = (load.get("trailer_type") or "STEP_DECK").strip().upper()
    capacity_ft = _capacity_for_trailer(trailer_type)

    lines = db.list_load_lines(load_id)
    existing_so_nums = {
        line.get("so_num")
        for line in lines
        if line.get("so_num")
    }
    line_totals = {}
    for line in lines:
        so_num = line.get("so_num")
        if not so_num:
            continue
        line_totals[so_num] = line_totals.get(so_num, 0) + float(line.get("total_length_ft") or 0)

    order_rows = db.list_orders_by_so_nums(plant_code, list(existing_so_nums)) if existing_so_nums else []
    order_map = {row.get("so_num"): row for row in order_rows if row.get("so_num")}

    used_ft = 0.0
    for so_num in existing_so_nums:
        order = order_map.get(so_num)
        if order and order.get("total_length_ft") is not None:
            used_ft += float(order.get("total_length_ft") or 0)
        else:
            used_ft += float(line_totals.get(so_num) or 0)

    remaining_ft = max(capacity_ft - used_ft, 0)
    util_pct = round((used_ft / capacity_ft) * 100, 1) if capacity_ft else 0.0

    anchor_due = None
    for line in lines:
        due_date = _parse_date(line.get("due_date"))
        if due_date and (anchor_due is None or due_date < anchor_due):
            anchor_due = due_date

    settings = db.get_optimizer_settings(plant_code) or {}
    try:
        geo_radius = float(settings.get("geo_radius") or load_builder.DEFAULT_BUILD_PARAMS.get("geo_radius") or 0)
    except (TypeError, ValueError):
        geo_radius = float(load_builder.DEFAULT_BUILD_PARAMS.get("geo_radius") or 0)
    try:
        time_window_days = int(settings.get("time_window_days") or load_builder.DEFAULT_BUILD_PARAMS.get("time_window_days") or 0)
    except (TypeError, ValueError):
        time_window_days = int(load_builder.DEFAULT_BUILD_PARAMS.get("time_window_days") or 0)

    zip_coords = geo_utils.load_zip_coordinates()
    stop_coords = []
    for line in lines:
        zip_code = geo_utils.normalize_zip(line.get("zip"))
        coords = zip_coords.get(zip_code) if zip_code else None
        if coords and coords not in stop_coords:
            stop_coords.append(coords)

    candidates = db.list_eligible_manual_orders(plant_code, search=None, limit=None)
    suggestions = []
    for order in candidates:
        so_num = str(order.get("so_num") or "").strip()
        if not so_num or so_num in existing_so_nums:
            continue

        order_due = _parse_date(order.get("due_date"))
        if anchor_due and order_due and time_window_days and time_window_days > 0:
            if abs((order_due - anchor_due).days) > time_window_days:
                continue

        dist = None
        order_zip = geo_utils.normalize_zip(order.get("zip"))
        order_coords = zip_coords.get(order_zip) if order_zip else None
        if order_coords and stop_coords:
            dist = min(
                geo_utils.haversine_distance_coords(order_coords, stop)
                for stop in stop_coords
            )
            if geo_radius and geo_radius > 0 and dist > geo_radius:
                continue

        total_length_ft = float(order.get("total_length_ft") or 0)
        if total_length_ft > remaining_ft:
            continue

        suggestions.append(
            {
                "so_num": so_num,
                "cust_name": order.get("cust_name") or "",
                "due_date": order.get("due_date") or "",
                "city": order.get("city") or "",
                "state": order.get("state") or "",
                "zip": order.get("zip") or "",
                "total_length_ft": total_length_ft,
                "utilization_pct": order.get("utilization_pct") or 0,
                "distance_miles": round(dist, 1) if dist is not None else None,
            }
        )

    suggestions.sort(
        key=lambda item: (
            item["distance_miles"] is None,
            item["distance_miles"] if item["distance_miles"] is not None else 0,
            item.get("due_date") or "",
        )
    )

    return jsonify(
        {
            "load": {
                "id": load_id,
                "origin_plant": plant_code,
                "trailer_type": trailer_type,
            },
            "space": {
                "capacity_ft": round(capacity_ft, 1),
                "used_ft": round(used_ft, 1),
                "remaining_ft": round(remaining_ft, 1),
                "util_pct": util_pct,
            },
            "params": {
                "geo_radius": geo_radius,
                "time_window_days": time_window_days,
            },
            "suggestions": suggestions[:25],
        }
    )


@app.route("/loads/<int:load_id>/manual_add", methods=["POST"])
def manual_add_orders(load_id):
    session_redirect = _require_session()
    if session_redirect:
        return jsonify({"error": "Session expired"}), 401

    load = db.get_load(load_id)
    if not load:
        return jsonify({"error": "Load not found"}), 404

    allowed_plants = _get_allowed_plants()
    if load.get("origin_plant") not in allowed_plants:
        return jsonify({"error": "Not authorized for this plant"}), 403

    status = (load.get("status") or STATUS_PROPOSED).upper()
    if status == STATUS_APPROVED:
        return jsonify({"error": "Approved loads cannot be modified."}), 400

    selected = list(
        dict.fromkeys(
            [value.strip() for value in request.form.getlist("so_nums") if (value or "").strip()]
        )
    )
    if not selected:
        return jsonify({"error": "Select at least one order."}), 400

    existing_lines = db.list_load_lines(load_id)
    existing_so_nums = {line.get("so_num") for line in existing_lines if line.get("so_num")}
    if any(so_num in existing_so_nums for so_num in selected):
        return jsonify({"error": "Some selected orders are already in this load."}), 400

    plant_code = load.get("origin_plant")
    eligible = db.filter_eligible_manual_so_nums(plant_code, selected)
    if eligible != set(selected) or len(eligible) != len(selected):
        return jsonify({"error": "Some selected orders are no longer available in Draft Loads."}), 400

    order_lines = db.list_order_lines_for_so_nums(plant_code, selected)
    if not order_lines:
        return jsonify({"error": "No eligible order lines found."}), 400

    db.update_load_build_source(load_id, "MANUAL")
    for line in order_lines:
        db.create_load_line(load_id, line["id"], line.get("total_length_ft") or 0)

    _reoptimize_for_plant(plant_code)
    return jsonify(
        {
            "redirect_url": url_for("loads", plants=plant_code, tab="draft", reopt="done")
        }
    )


@app.route("/feedback")
def feedback_log():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    filters = {
        "start_date": request.args.get("start_date") or "",
        "end_date": request.args.get("end_date") or "",
        "planner_id": request.args.get("planner_id") or "",
        "action_type": request.args.get("action_type") or "",
        "reason_category": request.args.get("reason_category") or "",
        "search": request.args.get("search") or "",
        "sort": request.args.get("sort") or "timestamp_desc",
    }
    entries = db.list_load_feedback(filters)
    options = db.list_feedback_filter_options()
    return render_template(
        "feedback_log.html",
        entries=entries,
        filters=filters,
        options=options,
    )


@app.route("/feedback/app")
def app_feedback_log():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    filters = {
        "start_date": request.args.get("start_date") or "",
        "end_date": request.args.get("end_date") or "",
        "planner_id": request.args.get("planner_id") or "",
        "category": request.args.get("category") or "",
        "status": request.args.get("status") or "",
        "search": request.args.get("search") or "",
        "sort": request.args.get("sort") or "timestamp_desc",
    }
    entries = db.list_app_feedback(filters)
    options = db.list_app_feedback_filter_options()
    return render_template(
        "app_feedback_log.html",
        entries=entries,
        filters=filters,
        options=options,
    )


@app.route("/feedback/app", methods=["POST"])
def submit_app_feedback():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    category = (request.form.get("category") or "").strip()
    title = (request.form.get("title") or "").strip()
    message = (request.form.get("message") or "").strip()
    page = (request.form.get("page") or "").strip()
    planner_id = _get_session_profile_name() or _get_session_role()

    next_url = request.form.get("next") or request.referrer or url_for("app_feedback_log")
    if not category or not title or not message:
        return redirect(next_url)

    db.add_app_feedback(
        category=category,
        title=title,
        message=message,
        page=page,
        planner_id=planner_id,
    )
    return redirect(next_url)


@app.route("/feedback/app/<int:feedback_id>/resolve", methods=["POST"])
def resolve_app_feedback(feedback_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    resolved_by = _get_session_profile_name() or _get_session_role()
    db.resolve_app_feedback(feedback_id, resolved_by=resolved_by)
    return redirect(request.form.get("next") or request.referrer or url_for("app_feedback_log"))


@app.route("/loads/<int:load_id>")
def load_detail(load_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    with db.get_connection() as connection:
        load = connection.execute(
            "SELECT * FROM loads WHERE id = ?",
            (load_id,),
        ).fetchone()
    if not load:
        return redirect(url_for("loads"))

    allowed_plants = _get_allowed_plants()
    if load["origin_plant"] not in allowed_plants:
        return redirect(url_for("loads"))

    load_data = dict(load)
    trailer_type = (load_data.get("trailer_type") or "STEP_DECK").strip().upper()
    load_data["trailer_type"] = trailer_type
    lines = db.list_load_lines(load_id)
    plant_names = {row["plant_code"]: row["name"] for row in db.list_plants()}
    sku_specs = {spec["sku"]: spec for spec in db.list_sku_specs()}
    line_items = []
    stops = []
    stop_map = {}
    zip_coords = geo_utils.load_zip_coordinates()
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
            }
        )
        zip_code = geo_utils.normalize_zip(line.get("zip"))
        state = line.get("state") or ""
        key = f"{zip_code}|{state}"
        if key not in stop_map:
            coords = zip_coords.get(zip_code) if zip_code else None
            stop_map[key] = {
                "zip": zip_code,
                "state": state,
                "customers": set(),
                "lat": coords[0] if coords else None,
                "lng": coords[1] if coords else None,
            }
        if line.get("cust_name"):
            stop_map[key]["customers"].add(line.get("cust_name"))

    for stop in stop_map.values():
        stops.append(
            {
                "zip": stop["zip"],
                "state": stop["state"],
                "customers": sorted(stop["customers"]),
                "lat": stop.get("lat"),
                "lng": stop.get("lng"),
            }
        )

    origin_code = load_data.get("origin_plant")
    origin_coords = geo_utils.plant_coords_for_code(origin_code)
    requires_return_to_origin = any(
        customer_rules.is_lowes_customer(line.get("cust_name") or "")
        for line in lines
    )
    ordered_stops = tsp_solver.solve_route(origin_coords, stops) if origin_coords else list(stops)

    origin_name = plant_names.get(origin_code, PLANT_NAMES.get(origin_code, origin_code))
    route_nodes = [
        {
            "type": "origin",
            "label": origin_code or "",
            "subtitle": origin_name or "",
            "icon": "home",
            "coords": origin_coords,
        }
    ]
    for stop in ordered_stops:
        coords = None
        if stop.get("lat") is not None and stop.get("lng") is not None:
            coords = (stop.get("lat"), stop.get("lng"))
        route_nodes.append(
            {
                "type": "customer",
                "label": f"{stop.get('state') or ''} {stop.get('zip') or ''}".strip(),
                "subtitle": ", ".join(stop.get("customers") or []),
                "icon": "person_pin_circle",
                "coords": coords,
            }
        )
    if requires_return_to_origin and origin_coords and len(route_nodes) > 1:
        route_nodes.append(
            {
                "type": "final",
                "label": origin_code or "",
                "subtitle": origin_name or "",
                "icon": "home",
                "coords": origin_coords,
            }
        )

    route_palette = [
        "#38bdf8",
        "#22c55e",
        "#f59e0b",
        "#f97316",
        "#a855f7",
        "#ef4444",
    ]
    for idx, node in enumerate(route_nodes):
        color = route_palette[idx % len(route_palette)]
        node["color"] = color
        node["bg"] = f"{color}22"

    route_legs = []
    for idx in range(len(route_nodes) - 1):
        origin_leg = route_nodes[idx].get("coords")
        dest_leg = route_nodes[idx + 1].get("coords")
        if origin_leg and dest_leg:
            miles = geo_utils.haversine_distance_coords(origin_leg, dest_leg)
            route_legs.append(round(miles))
        else:
            route_legs.append(None)

    map_stops = []
    if origin_coords:
        origin_color = route_nodes[0]["color"] if route_nodes else "#38bdf8"
        map_stops.append(
            {
                "type": "origin",
                "lat": origin_coords[0],
                "lng": origin_coords[1],
                "label": origin_name or origin_code,
                "color": origin_color,
            }
        )
    for idx, stop in enumerate(ordered_stops, start=1):
        stop_color = route_nodes[idx]["color"] if idx < len(route_nodes) else "#f59e0b"
        map_stops.append(
            {
                "type": "customer",
                "lat": stop.get("lat"),
                "lng": stop.get("lng"),
                "label": f"{stop.get('state') or ''} {stop.get('zip') or ''}".strip(),
                "color": stop_color,
            }
        )
    if requires_return_to_origin and origin_coords and map_stops:
        final_color = route_nodes[-1]["color"] if route_nodes else "#f59e0b"
        map_stops.append(
            {
                "type": "final",
                "lat": origin_coords[0],
                "lng": origin_coords[1],
                "label": origin_name or origin_code,
                "color": final_color,
            }
        )

    schematic = stack_calculator.calculate_stack_configuration(
        line_items,
        trailer_type=trailer_type,
    )
    utilization_pct = schematic.get("utilization_pct", load_data.get("utilization_pct", 0)) or 0
    order_numbers = {line.get("so_num") for line in lines if line.get("so_num")}
    exceeds_capacity = schematic.get("exceeds_capacity", False)
    over_capacity = (exceeds_capacity or utilization_pct > 100) and len(order_numbers) <= 1
    color_palette = [
        "#137fec",
        "#10b981",
        "#f59e0b",
        "#ef4444",
        "#8b5cf6",
        "#22d3ee",
        "#f472b6",
        "#f97316",
    ]
    sku_colors = {}
    for idx, item in enumerate(line_items):
        sku = item.get("sku") or f"item-{idx}"
        if sku not in sku_colors:
            sku_colors[sku] = color_palette[len(sku_colors) % len(color_palette)]

    load_data["schematic"] = schematic
    load_data["sku_colors"] = sku_colors
    load_data["stops"] = ordered_stops
    load_data["route_nodes"] = route_nodes
    load_data["route_legs"] = route_legs
    load_data["map_stops"] = map_stops

    due_dates = [ _parse_date(line.get("due_date")) for line in lines ]
    due_dates = [value for value in due_dates if value]
    anchor_date = min(due_dates) if due_dates else None
    today = date.today()

    manifest_rows = []
    for line in lines:
        due_date = _parse_date(line.get("due_date"))
        max_stack = line.get("max_stack_height") or 1
        qty = line.get("qty") or 0
        positions_required = math.ceil(qty / max_stack) if max_stack else qty
        linear_feet = (line.get("unit_length_ft") or 0) * positions_required
        status = ""
        early_days = None
        if due_date and due_date < today:
            status = "Past Due"
        elif due_date and anchor_date:
            delta_days = (due_date - anchor_date).days
            if delta_days > 0:
                early_days = delta_days
                status = f"Early {delta_days}d"

        manifest_rows.append(
            {
                "so_num": line.get("so_num"),
                "cust_name": line.get("cust_name"),
                "destination": f"{line.get('city') or ''}, {line.get('state') or ''} {line.get('zip') or ''}".strip(", "),
                "due_date": line.get("due_date") or "",
                "sku": line.get("sku"),
                "qty": qty,
                "linear_feet": round(linear_feet, 1),
                "status": status,
                "early_days": early_days,
            }
        )

    utilization_grade = schematic.get("utilization_grade") or "F"

    return render_template(
        "load_detail.html",
        load=load_data,
        lines=lines,
        manifest_rows=manifest_rows,
        schematic=schematic,
        sku_colors=sku_colors,
        stops=ordered_stops,
        over_capacity=over_capacity,
        utilization_pct=utilization_pct,
        utilization_grade=utilization_grade,
        utilization_credit_ft=schematic.get("utilization_credit_ft", 0),
    )


@app.route("/loads/<int:load_id>/trailer", methods=["POST"])
def update_load_trailer(load_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    trailer_type = request.form.get("trailer_type", "").strip().upper()
    if trailer_type not in {"STEP_DECK", "FLATBED", "WEDGE"}:
        return jsonify({"error": "Invalid trailer type"}), 400

    with db.get_connection() as connection:
        load = connection.execute(
            "SELECT id, origin_plant FROM loads WHERE id = ?",
            (load_id,),
        ).fetchone()
    if not load:
        return jsonify({"error": "Load not found"}), 404

    allowed_plants = _get_allowed_plants()
    if load["origin_plant"] not in allowed_plants:
        return jsonify({"error": "Not authorized for this plant"}), 403

    db.update_load_trailer_type(load_id, trailer_type)
    return ("", 204)


def _build_load_schematic_payload(load_id):
    load = db.get_load(load_id)
    if not load:
        return None

    trailer_type = (load.get("trailer_type") or "STEP_DECK").strip().upper()
    load["trailer_type"] = trailer_type
    lines = db.list_load_lines(load_id)
    sku_specs = {spec["sku"]: spec for spec in db.list_sku_specs()}
    color_palette = [
        "#137fec",
        "#10b981",
        "#f59e0b",
        "#ef4444",
        "#8b5cf6",
        "#22d3ee",
        "#f472b6",
        "#f97316",
    ]
    order_colors = {}
    line_items = []
    for line in lines:
        so_num = line.get("so_num")
        if so_num and so_num not in order_colors:
            order_colors[so_num] = color_palette[len(order_colors) % len(color_palette)]
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
                "order_id": so_num,
            }
        )

    schematic = stack_calculator.calculate_stack_configuration(
        line_items,
        trailer_type=trailer_type,
    )
    load["auto_trailer_label"] = ""
    load["auto_trailer_reason"] = ""
    if trailer_type == "FLATBED" and not schematic.get("exceeds_capacity"):
        step_items = []
        for line in lines:
            sku = line.get("sku")
            spec = sku_specs.get(sku) if sku else None
            max_stack = (spec or {}).get("max_stack_step_deck") or (spec or {}).get("max_stack_flat_bed") or 1
            step_items.append(
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
        step_schematic = stack_calculator.calculate_stack_configuration(
            step_items,
            trailer_type="STEP_DECK",
        )
        if step_schematic.get("exceeds_capacity"):
            load["auto_trailer_label"] = "Auto Flatbed"
            load["auto_trailer_reason"] = "Assigned a flatbed because the load does not fit on the 43' / 10' step deck split."
    utilization_pct = schematic.get("utilization_pct", load.get("utilization_pct", 0)) or 0
    order_numbers = {line.get("so_num") for line in lines if line.get("so_num")}
    exceeds_capacity = schematic.get("exceeds_capacity", False)
    over_capacity = (exceeds_capacity or utilization_pct > 100) and len(order_numbers) <= 1

    load["schematic"] = schematic
    load["order_colors"] = order_colors
    load["over_capacity"] = over_capacity
    load["utilization_pct"] = utilization_pct
    return load


@app.route("/loads/<int:load_id>/schematic")
def load_schematic_fragment(load_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    load_data = _build_load_schematic_payload(load_id)
    if not load_data:
        return jsonify({"error": "Load not found"}), 404

    allowed_plants = _get_allowed_plants()
    if load_data.get("origin_plant") not in allowed_plants:
        return jsonify({"error": "Not authorized for this plant"}), 403

    status = (load_data.get("status") or STATUS_PROPOSED).upper()
    tab = (request.args.get("tab") or "").strip().lower()
    schematic_html = render_template(
        "partials/load_schematic_card.html",
        load=load_data,
        status=status,
        tab=tab,
    )
    return jsonify(
        {
            "schematic_html": schematic_html,
            "utilization_pct": round(load_data.get("utilization_pct") or 0),
            "utilization_grade": (load_data.get("schematic") or {}).get("utilization_grade") or "F",
            "over_capacity": bool(load_data.get("over_capacity")),
            "exceeds_capacity": bool((load_data.get("schematic") or {}).get("exceeds_capacity")),
        }
    )


@app.route("/loads/<int:load_id>/status", methods=["POST"], strict_slashes=False)
def update_load_status(load_id):
    is_async = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    session_redirect = _require_session()
    if session_redirect:
        if is_async:
            return jsonify({"error": "Session expired"}), 401
        return session_redirect

    action = (request.form.get("action") or "").strip().lower()
    if action not in {"approve_draft", "approve_lock", "propose"}:
        return jsonify({"error": "Invalid action"}), 400

    load = db.get_load(load_id)
    if not load:
        return jsonify({"error": "Load not found"}), 404

    allowed_plants = _get_allowed_plants()
    if load["origin_plant"] not in allowed_plants:
        return jsonify({"error": "Not authorized for this plant"}), 403

    plant_filters = _parse_plant_filters(request.form.get("plants"))
    if plant_filters is None:
        plant_filters = []
    plant_scope = [code for code in plant_filters if code in allowed_plants] if plant_filters else []
    if not plant_scope:
        plant_scope = allowed_plants

    current_status = (load.get("status") or STATUS_PROPOSED).upper()
    load_number = load.get("load_number")
    plant_code = load.get("origin_plant")
    year_suffix = _year_suffix()

    redirect_target = request.referrer or url_for("loads")

    if action == "propose":
        if current_status != STATUS_PROPOSED:
            db.update_load_status(load_id, STATUS_PROPOSED, load_number)
        if is_async:
            snapshot = _compute_load_progress_snapshot(plant_scope=plant_scope)
            return jsonify(
                {
                    "status": STATUS_PROPOSED,
                    "load_id": load_id,
                    "progress": {
                        "approved_orders": snapshot["approved_orders"],
                        "total_orders": snapshot["total_orders"],
                        "progress_pct": snapshot["progress_pct"],
                    },
                    "tab_counts": {
                        "draft": snapshot["draft_tab_count"],
                        "final": snapshot["final_tab_count"],
                    },
                }
            )
        return redirect(redirect_target)

    if action == "approve_draft":
        if current_status == STATUS_APPROVED:
            return redirect(url_for("loads"))
        if not load_number:
            seq = db.get_next_load_sequence(plant_code, year_suffix)
            load_number = _format_load_number(plant_code, year_suffix, seq, draft=True)
        else:
            normalized, suffix = _normalize_load_number(load_number)
            if suffix != "D":
                load_number = f"{normalized}-D"
        db.update_load_status(load_id, STATUS_DRAFT, load_number)
        if is_async:
            snapshot = _compute_load_progress_snapshot(plant_scope=plant_scope)
            return jsonify(
                {
                    "status": STATUS_DRAFT,
                    "load_id": load_id,
                    "load_number": load_number,
                    "progress": {
                        "approved_orders": snapshot["approved_orders"],
                        "total_orders": snapshot["total_orders"],
                        "progress_pct": snapshot["progress_pct"],
                    },
                    "tab_counts": {
                        "draft": snapshot["draft_tab_count"],
                        "final": snapshot["final_tab_count"],
                    },
                }
            )
        return redirect(redirect_target)

    if action == "approve_lock":
        if not load_number:
            seq = db.get_next_load_sequence(plant_code, year_suffix)
            load_number = _format_load_number(plant_code, year_suffix, seq, draft=False)
        else:
            normalized, suffix = _normalize_load_number(load_number)
            if suffix == "D":
                load_number = normalized[:-2] if normalized.endswith("-D") else normalized
        db.update_load_status(load_id, STATUS_APPROVED, load_number)
        if is_async:
            snapshot = _compute_load_progress_snapshot(plant_scope=plant_scope)
            return jsonify(
                {
                    "status": STATUS_APPROVED,
                    "load_id": load_id,
                    "load_number": load_number,
                    "progress": {
                        "approved_orders": snapshot["approved_orders"],
                        "total_orders": snapshot["total_orders"],
                        "progress_pct": snapshot["progress_pct"],
                    },
                    "tab_counts": {
                        "draft": snapshot["draft_tab_count"],
                        "final": snapshot["final_tab_count"],
                    },
                }
            )
        return redirect(redirect_target)

    return redirect(redirect_target)


@app.route("/loads/<int:load_id>/remove_order", methods=["GET", "POST"])
def remove_order_from_load(load_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    load = db.get_load(load_id)
    if not load:
        return redirect(url_for("loads"))

    allowed_plants = _get_allowed_plants()
    if load["origin_plant"] not in allowed_plants:
        return redirect(url_for("loads"))

    order_id = (request.values.get("order_id") or "").strip()
    load_data = dict(load)
    plant_code = load_data.get("origin_plant")
    load_label = load_data.get("load_number") or f"Load #{load_id}"
    load_status = (load_data.get("status") or STATUS_PROPOSED).upper()

    def build_order_summary(order_lines):
        if not order_lines:
            return {}
        due_dates = [_parse_date(line.get("due_date")) for line in order_lines if line.get("due_date")]
        due_date = min(due_dates).strftime("%Y-%m-%d") if due_dates else (order_lines[0].get("due_date") or "")
        city = order_lines[0].get("city") or ""
        state = order_lines[0].get("state") or ""
        zip_code = order_lines[0].get("zip") or ""
        location = ", ".join([part for part in [city, state] if part]).strip()
        if zip_code:
            location = f"{location} {zip_code}".strip()
        return {
            "customer": order_lines[0].get("cust_name") or "",
            "due_date": due_date,
            "location": location,
            "total_qty": sum((line.get("qty") or 0) for line in order_lines),
        }

    reasons_options = ORDER_REMOVAL_REASONS

    lines = db.list_load_lines(load_id)
    order_lines = [line for line in lines if line.get("so_num") == order_id]

    if request.method == "GET":
        if not order_id or not order_lines:
            return redirect(url_for("loads", plants=plant_code))
        order_summary = build_order_summary(order_lines)
        return render_template(
            "remove_feedback.html",
            load=load_data,
            load_label=load_label,
            load_status=load_status,
            order_id=order_id,
            order_summary=order_summary,
            reasons_options=reasons_options,
            selected_reasons=[],
            notes="",
            error=None,
            plant_filters=[plant_code],
            plant_filter_param=plant_code,
        )

    if not order_id or not order_lines:
        return redirect(url_for("loads", plants=plant_code))

    reason_category = (request.form.get("reason_category") or "").strip()
    details = (request.form.get("details") or "").strip()
    selected_reasons = request.form.getlist("reasons")
    notes = (request.form.get("notes") or "").strip()
    if not reason_category and selected_reasons:
        reason_category = ", ".join([reason for reason in selected_reasons if reason])
    if not details:
        details = notes

    if not reason_category or len(details or "") < 10:
        error_message = "Select a reason and add at least 10 characters before removing this order."
        return redirect(
            url_for(
                "loads",
                plants=plant_code,
                feedback_error=error_message,
                feedback_target=f"order-{load_id}-{order_id}",
            )
        )

    db.add_load_feedback(
        load_id,
        order_id=order_id,
        action_type="order_removed",
        reason_category=reason_category,
        details=details,
        planner_id=_get_session_profile_name() or _get_session_role(),
    )
    db.remove_order_from_load(load_id, order_id)
    if db.count_load_lines(load_id) == 0:
        db.delete_load(load_id)

    _reoptimize_for_plant(plant_code)
    return redirect(url_for("loads", plants=plant_code, reopt="done"))


@app.route("/loads/<int:load_id>/reject", methods=["POST"])
def reject_load(load_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    load = db.get_load(load_id)
    if not load:
        return jsonify({"error": "Load not found"}), 404

    allowed_plants = _get_allowed_plants()
    if load["origin_plant"] not in allowed_plants:
        return jsonify({"error": "Not authorized for this plant"}), 403

    reason_category = (request.form.get("reason_category") or "").strip()
    details = (request.form.get("details") or "").strip()
    if not reason_category or len(details or "") < 10:
        error_message = "Select a reason and add at least 10 characters before rejecting this load."
        return redirect(
            url_for(
                "loads",
                plants=load["origin_plant"],
                feedback_error=error_message,
                feedback_target=f"load-{load_id}",
            )
        )

    db.add_load_feedback(
        load_id,
        order_id=None,
        action_type="load_rejected",
        reason_category=reason_category,
        details=details,
        planner_id=_get_session_profile_name() or _get_session_role(),
    )
    db.delete_load(load_id)
    _reoptimize_for_plant(load["origin_plant"])
    return redirect(url_for("loads", plants=load["origin_plant"], reopt="done"))


@app.route("/loads/clear", methods=["POST"])
def clear_loads():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    plant_filters = _resolve_plant_filters(request.form.get("plants") or request.form.get("plant"))
    plant_scope = plant_filters or _get_allowed_plants()
    tab = (request.form.get("tab") or "").strip().lower()
    sort_mode = (request.form.get("sort") or "").strip().lower()
    today_param = request.form.get("today")
    sort_mode = (request.form.get("sort") or "").strip().lower()
    today_param = request.form.get("today")
    if plant_filters:
        for plant in plant_scope:
            db.clear_loads_for_plant(plant)
    else:
        db.clear_loads_for_plant(None)
    return redirect(
        url_for(
            "loads",
            plants=",".join(plant_filters) if plant_filters else None,
            tab=tab or None,
            sort=sort_mode or None,
            today=today_param or None,
        )
    )


@app.route("/loads/approve_all", methods=["POST"])
def approve_all_loads():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    plant_filters = _resolve_plant_filters(request.form.get("plants") or request.form.get("plant"))
    plant_scope = plant_filters or _get_allowed_plants()
    status_filter = (request.form.get("status") or "").strip().upper()
    tab = (request.form.get("tab") or "").strip().lower()
    sort_mode = (request.form.get("sort") or "").strip().lower()
    today_param = request.form.get("today")

    if not plant_scope:
        return redirect(url_for("loads"))

    placeholders = ", ".join("?" for _ in plant_scope)
    params = list(plant_scope)
    status_clause = ""
    if status_filter:
        status_clause = " AND UPPER(status) = ?"
        params.append(status_filter)

    with db.get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT id, load_number, origin_plant, status
            FROM loads
            WHERE origin_plant IN ({placeholders})
            {status_clause}
            """,
            params,
        ).fetchall()

    year_suffix = _year_suffix()
    for row in rows:
        current_status = (row["status"] or STATUS_PROPOSED).upper()
        if current_status == STATUS_APPROVED:
            continue
        plant_code = row["origin_plant"]
        load_number = row["load_number"]
        if not load_number:
            seq = db.get_next_load_sequence(plant_code, year_suffix)
            load_number = _format_load_number(plant_code, year_suffix, seq, draft=False)
        else:
            normalized, suffix = _normalize_load_number(load_number)
            if suffix == "D":
                load_number = normalized[:-2] if normalized.endswith("-D") else normalized
        db.update_load_status(row["id"], STATUS_APPROVED, load_number)

    return redirect(
        url_for(
            "loads",
            plants=",".join(plant_filters) if plant_filters else None,
            status=status_filter or None,
            tab=tab or None,
            sort=sort_mode or None,
            today=today_param or None,
        )
    )


@app.route("/loads/approve_full", methods=["POST"])
def approve_full_truckloads():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    plant_filters = _resolve_plant_filters(request.form.get("plants") or request.form.get("plant"))
    plant_scope = plant_filters or _get_allowed_plants()
    tab = (request.form.get("tab") or "").strip().lower()

    if not plant_scope:
        return redirect(url_for("loads"))

    all_loads = load_builder.list_loads(None)
    candidates = [
        load
        for load in all_loads
        if load.get("origin_plant") in plant_scope
        and (load.get("status") or STATUS_PROPOSED).upper() in {STATUS_PROPOSED, STATUS_DRAFT}
        and (load.get("build_source") or "OPTIMIZED").upper() != "MANUAL"
        and _is_full_truckload(load)
    ]

    year_suffix = _year_suffix()
    for load in candidates:
        current_status = (load.get("status") or STATUS_PROPOSED).upper()
        if current_status == STATUS_APPROVED:
            continue
        plant_code = load.get("origin_plant")
        load_number = load.get("load_number")
        if not load_number:
            seq = db.get_next_load_sequence(plant_code, year_suffix)
            load_number = _format_load_number(plant_code, year_suffix, seq, draft=False)
        else:
            normalized, suffix = _normalize_load_number(load_number)
            if suffix == "D":
                load_number = normalized[:-2] if normalized.endswith("-D") else normalized
        db.update_load_status(load["id"], STATUS_APPROVED, load_number)

    return redirect(
        url_for(
            "loads",
            plants=",".join(plant_filters) if plant_filters else None,
            tab=tab or None,
            sort=sort_mode or None,
            today=today_param or None,
        )
    )


@app.route("/loads/reject_all", methods=["POST"])
def reject_all_loads():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    plant_filters = _resolve_plant_filters(request.form.get("plants") or request.form.get("plant"))
    plant_scope = plant_filters or _get_allowed_plants()
    status_filter = (request.form.get("status") or "").strip().upper()
    tab = (request.form.get("tab") or "").strip().lower()

    if not plant_scope:
        return redirect(url_for("loads"))

    if tab == "final":
        return redirect(
            url_for(
                "loads",
                plants=",".join(plant_filters) if plant_filters else None,
                status=status_filter or None,
                tab=tab,
            )
        )

    placeholders = ", ".join("?" for _ in plant_scope)
    params = list(plant_scope)
    status_clause = ""
    if status_filter:
        status_clause = " AND UPPER(status) = ?"
        params.append(status_filter)
    else:
        status_clause = " AND UPPER(status) != ?"
        params.append(STATUS_APPROVED)

    with db.get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT id, origin_plant
            FROM loads
            WHERE origin_plant IN ({placeholders})
            {status_clause}
            """,
            params,
        ).fetchall()

        for row in rows:
            connection.execute(
                "DELETE FROM load_lines WHERE load_id = ?",
                (row["id"],),
            )
            connection.execute(
                "DELETE FROM loads WHERE id = ?",
                (row["id"],),
            )
        connection.commit()

    plants_to_reopt = sorted({row["origin_plant"] for row in rows})
    for plant in plants_to_reopt:
        _reoptimize_for_plant(plant)

    return redirect(
        url_for(
            "loads",
            plants=",".join(plant_filters) if plant_filters else None,
            status=status_filter or None,
            tab=tab or None,
            reopt="done" if plants_to_reopt else None,
        )
    )


@app.route("/rates")
def rates():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    if _get_session_role() != ROLE_ADMIN:
        return redirect(url_for("settings", tab="rates"))
    rates_data = db.list_rate_matrix()
    plants, states, matrix = _build_rate_matrix(rates_data)
    return render_template(
        "rates.html",
        rates=rates_data,
        plants=plants,
        states=states,
        matrix=matrix,
    )


@app.route("/settings")
def settings():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    tab = request.args.get("tab", "rates")

    rates_data = []
    rate_plants = []
    rate_states = []
    rate_matrix = {}
    specs = []
    lookups_data = []
    plants_data = []
    strategic_customers_raw = ""
    strategic_customers = []
    optimizer_defaults = {}
    util_grade_thresholds = []

    if tab == "rates":
        rates_data = db.list_rate_matrix()
        rate_plants, rate_states, rate_matrix = _build_rate_matrix_records(rates_data)
    elif tab == "skus":
        specs = db.list_sku_specs()
    elif tab == "lookups":
        specs = db.list_sku_specs()
        lookups_data = db.list_item_lookups()
    elif tab == "plants":
        plants_data = db.list_plants()
    elif tab == "planning_tools":
        setting = db.get_planning_setting("strategic_customers") or {}
        strategic_customers_raw = setting.get("value_text") or ""
        strategic_customers = _parse_strategic_customers(strategic_customers_raw)
        optimizer_defaults = dict(load_builder.DEFAULT_BUILD_PARAMS)
        util_grade_thresholds = [
            {"grade": "A", "label": ">= 85%"},
            {"grade": "B", "label": ">= 70%"},
            {"grade": "C", "label": ">= 55%"},
            {"grade": "D", "label": ">= 40%"},
            {"grade": "F", "label": "< 40%"},
        ]

    return render_template(
        "settings.html",
        tab=tab,
        rates=rates_data,
        rate_plants=rate_plants,
        rate_states=rate_states,
        rate_matrix=rate_matrix,
        specs=specs,
        lookups=lookups_data,
        plants_data=plants_data,
        strategic_customers_raw=strategic_customers_raw,
        strategic_customers=strategic_customers,
        optimizer_defaults=optimizer_defaults,
        util_grade_thresholds=util_grade_thresholds,
        is_admin=_get_session_role() == ROLE_ADMIN,
    )


@app.route("/planning-tools/save", methods=["POST"])
def save_planning_tools():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    _require_admin()

    strategic_customers = request.form.get("strategic_customers") or ""
    db.upsert_planning_setting("strategic_customers", strategic_customers)
    return redirect(url_for("settings", tab="planning_tools"))


@app.route("/rates/save", methods=["POST"])
def save_rate():
    _require_admin()
    payload = request.get_json(silent=True) or request.form

    rate_id = payload.get("id") or payload.get("rate_id")
    origin_plant = (payload.get("origin_plant") or "").strip().upper()
    destination_state = (payload.get("destination_state") or "").strip().upper()
    rate_per_mile = float(payload.get("rate_per_mile") or 0)
    effective_year = int(payload.get("effective_year") or 2026)
    notes = (payload.get("notes") or "").strip()

    rate_payload = {
        "origin_plant": origin_plant,
        "destination_state": destination_state,
        "rate_per_mile": rate_per_mile,
        "effective_year": effective_year,
        "notes": notes,
    }

    if rate_id:
        db.update_rate(int(rate_id), rate_payload)
        saved_rate = db.get_rate_by_id(int(rate_id))
    else:
        db.upsert_rate(rate_payload)
        saved_rate = db.get_rate_by_lane(origin_plant, destination_state, effective_year)

    if request.is_json:
        return jsonify({"rate": saved_rate})

    return redirect(request.referrer or url_for("settings", tab="rates"))


@app.route("/rates/add", methods=["POST"])
def add_rate():
    _require_admin()
    rate = {
        "origin_plant": request.form.get("origin_plant", "").strip().upper(),
        "destination_state": request.form.get("destination_state", "").strip().upper(),
        "rate_per_mile": float(request.form.get("rate_per_mile", 0) or 0),
        "effective_year": int(request.form.get("effective_year", 2026) or 2026),
        "notes": request.form.get("notes", "").strip(),
    }
    db.upsert_rate(rate)
    return redirect(request.referrer or url_for("rates"))


@app.route("/rates/delete/<int:rate_id>", methods=["POST"])
def delete_rate(rate_id):
    _require_admin()
    db.delete_rate(rate_id)
    return redirect(url_for("rates"))


@app.route("/skus/save", methods=["POST"])
def save_sku():
    _require_admin()
    payload = request.get_json(silent=True) or {}
    spec_id = payload.get("id")
    if not spec_id:
        return jsonify({"error": "Missing SKU id"}), 400

    spec = {
        "sku": (payload.get("sku") or "").strip(),
        "category": (payload.get("category") or "").strip(),
        "length_with_tongue_ft": float(payload.get("length_with_tongue_ft") or 0),
        "max_stack_step_deck": int(payload.get("max_stack_step_deck") or 1),
        "max_stack_flat_bed": int(payload.get("max_stack_flat_bed") or 1),
        "notes": (payload.get("notes") or "").strip(),
    }
    db.update_sku_spec(int(spec_id), spec)
    return jsonify({"status": "ok"})


@app.route("/lookups/save", methods=["POST"])
def save_lookup():
    _require_admin()
    payload = request.get_json(silent=True) or {}
    entry_id = payload.get("id")
    if not entry_id:
        return jsonify({"error": "Missing lookup id"}), 400

    entry = {
        "plant": (payload.get("plant") or "").strip().upper(),
        "bin": (payload.get("bin") or "").strip().upper(),
        "item_pattern": (payload.get("item_pattern") or "").strip(),
        "sku": (payload.get("sku") or "").strip(),
    }
    db.update_item_lookup(int(entry_id), entry)
    return jsonify({"status": "ok"})


@app.route("/plants/save", methods=["POST"])
def save_plant():
    _require_admin()
    payload = request.get_json(silent=True) or {}
    plant_id = payload.get("id")
    if not plant_id:
        return jsonify({"error": "Missing plant id"}), 400

    plant = {
        "plant_code": (payload.get("plant_code") or "").strip().upper(),
        "name": (payload.get("name") or "").strip(),
        "lat": float(payload.get("lat") or 0),
        "lng": float(payload.get("lng") or 0),
        "address": (payload.get("address") or "").strip(),
    }
    db.update_plant(int(plant_id), plant)
    return jsonify({"status": "ok"})


@app.route("/skus")
def skus():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    if _get_session_role() != ROLE_ADMIN:
        return redirect(url_for("settings", tab="skus"))
    specs = db.list_sku_specs()
    return render_template("skus.html", specs=specs)


@app.route("/skus/add", methods=["POST"])
def add_sku():
    _require_admin()
    spec = {
        "sku": request.form.get("sku", "").strip(),
        "category": request.form.get("category", "").strip(),
        "length_with_tongue_ft": float(request.form.get("length_with_tongue_ft", 0) or 0),
        "max_stack_step_deck": int(request.form.get("max_stack_step_deck", 1) or 1),
        "max_stack_flat_bed": int(request.form.get("max_stack_flat_bed", 1) or 1),
        "notes": request.form.get("notes", "").strip(),
    }
    db.upsert_sku_spec(spec)
    return redirect(url_for("skus"))


@app.route("/skus/delete/<int:spec_id>", methods=["POST"])
def delete_sku(spec_id):
    _require_admin()
    db.delete_sku_spec(spec_id)
    return redirect(url_for("skus"))


@app.route("/lookups")
def lookups():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    if _get_session_role() != ROLE_ADMIN:
        return redirect(url_for("settings", tab="lookups"))
    lookups_data = db.list_item_lookups()
    specs = db.list_sku_specs()
    return render_template("lookups.html", lookups=lookups_data, specs=specs)


@app.route("/lookups/add", methods=["POST"])
def add_lookup():
    _require_admin()
    entry = {
        "plant": request.form.get("plant", "").strip().upper(),
        "bin": request.form.get("bin", "").strip().upper(),
        "item_pattern": request.form.get("item_pattern", "").strip().upper(),
        "sku": request.form.get("sku", "").strip(),
    }
    db.add_item_lookup(entry)
    return redirect(url_for("lookups"))


@app.route("/lookups/delete/<int:entry_id>", methods=["POST"])
def delete_lookup(entry_id):
    _require_admin()
    db.delete_item_lookup(entry_id)
    return redirect(url_for("lookups"))


@app.route("/api/orders/<so_num>/stack-config")
def order_stack_config(so_num):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    with db.get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                ol.plant,
                ol.item,
                ol.sku,
                ol.qty,
                ol.unit_length_ft,
                ol.max_stack_height,
                ss.category
            FROM order_lines ol
            JOIN sku_specifications ss ON ol.sku = ss.sku
            WHERE ol.so_num = ?
            ORDER BY ol.id ASC
            """,
            (so_num,),
        ).fetchall()
        order_row = connection.execute(
            """
            SELECT
                orders.so_num,
                orders.plant,
                orders.cust_name,
                orders.due_date,
                orders.state,
                orders.zip,
                orders.total_qty,
                orders.total_length_ft,
                orders.utilization_pct,
                orders.line_count,
                orders.is_excluded,
                (
                    SELECT city
                    FROM order_lines
                    WHERE order_lines.so_num = orders.so_num
                      AND city IS NOT NULL
                      AND city != ''
                    LIMIT 1
                ) AS city
            FROM orders
            WHERE orders.so_num = ?
            LIMIT 1
            """,
            (so_num,),
        ).fetchone()

    if not rows:
        return jsonify({"error": "Order not found"}), 404

    allowed_plants = _get_allowed_plants()
    if rows[0]["plant"] not in allowed_plants:
        return jsonify({"error": "Not authorized for this plant."}), 403

    line_items = []
    for row in rows:
        max_stack = row["max_stack_height"] or 1
        qty = row["qty"] or 0
        positions_required = math.ceil(qty / max_stack) if max_stack else qty
        linear_feet = (row["unit_length_ft"] or 0) * positions_required
        line_items.append(
            {
                "item": row["item"],
                "sku": row["sku"],
                "qty": qty,
                "unit_length_ft": row["unit_length_ft"] or 0,
                "max_stack_height": max_stack,
                "positions_required": positions_required,
                "linear_feet": linear_feet,
                "category": row["category"] or "",
            }
        )

    config = stack_calculator.calculate_stack_configuration(line_items)
    config["order_id"] = so_num
    config["line_items"] = line_items
    config["positions_count"] = len(config["positions"])

    plant_code = (order_row["plant"] if order_row else None) or rows[0]["plant"]
    dest_zip = order_row["zip"] if order_row else None
    dest_state = order_row["state"] if order_row else None
    dest_city = order_row["city"] if order_row else None

    zip_coords = geo_utils.load_zip_coordinates()
    origin_coords = geo_utils.plant_coords_for_code(plant_code) if plant_code else None
    dest_coords = (
        zip_coords.get(geo_utils.normalize_zip(dest_zip)) if dest_zip else None
    )

    destination_label_parts = []
    if dest_city:
        destination_label_parts.append(str(dest_city))
    if dest_state:
        destination_label_parts.append(str(dest_state))
    if dest_zip:
        destination_label_parts.append(str(dest_zip))
    destination_label = " ".join(part for part in destination_label_parts if part).strip()

    map_stops = []
    map_stops.append(
        {
            "label": f"{plant_code} Plant" if plant_code else "Origin Plant",
            "lat": origin_coords[0] if origin_coords else None,
            "lng": origin_coords[1] if origin_coords else None,
            "type": "origin",
            "sequence": 1,
        }
    )
    map_stops.append(
        {
            "label": destination_label or "Destination",
            "lat": dest_coords[0] if dest_coords else None,
            "lng": dest_coords[1] if dest_coords else None,
            "type": "final",
            "sequence": 2,
        }
    )
    config["map_stops"] = map_stops

    if order_row:
        config["order_meta"] = {
            "so_num": order_row["so_num"],
            "plant": order_row["plant"],
            "cust_name": order_row["cust_name"],
            "due_date": order_row["due_date"],
            "city": order_row["city"],
            "state": order_row["state"],
            "zip": order_row["zip"],
            "total_qty": order_row["total_qty"],
            "total_length_ft": order_row["total_length_ft"],
            "utilization_pct": order_row["utilization_pct"],
            "line_count": order_row["line_count"],
            "is_excluded": order_row["is_excluded"],
        }

    return jsonify(config)


@app.route("/api/optimize", methods=["POST"])
def run_optimization():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    data = request.get_json(silent=True) or {}
    plant_code = data.get("plant_code") or data.get("origin_plant")
    plant_code = _normalize_plant_code(plant_code)
    if plant_code and plant_code not in _get_allowed_plants():
        return jsonify({"error": "Plant not in scope"}), 403
    flexibility_days = data.get("flexibility_days", 7)
    proximity_miles = data.get("proximity_miles", data.get("geo_radius", 200))
    capacity_feet = data.get("capacity_feet", 53)
    trailer_type = data.get("trailer_type", "STEP_DECK")

    engine = OptimizerEngine()
    result = engine.run_optimization(
        plant_code,
        flexibility_days=flexibility_days,
        proximity_miles=proximity_miles,
        capacity_feet=capacity_feet,
        trailer_type=trailer_type,
    )
    return jsonify(result)


@app.route("/api/optimize/<int:run_id>/loads")
def get_optimization_loads(run_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    with db.get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                l.id,
                l.load_number,
                l.plant_code,
                l.total_util,
                l.total_miles,
                l.total_cost,
                l.num_orders,
                l.status,
                GROUP_CONCAT(a.order_so_num) as order_nums
            FROM optimized_loads l
            LEFT JOIN load_order_assignments a ON l.id = a.load_id
            WHERE l.run_id = ?
            GROUP BY l.id
            ORDER BY l.load_number
            """,
            (run_id,),
        ).fetchall()

    if rows:
        allowed_plants = _get_allowed_plants()
        if rows[0]["plant_code"] not in allowed_plants:
            return jsonify({"error": "Not authorized for this plant."}), 403

    loads = []
    for row in rows:
        order_numbers = row["order_nums"].split(",") if row["order_nums"] else []
        loads.append(
            {
                "id": row["id"],
                "load_number": row["load_number"],
                "total_util": (row["total_util"] or 0) * 100,
                "total_miles": row["total_miles"],
                "total_cost": row["total_cost"],
                "num_orders": row["num_orders"],
                "status": row["status"],
                "order_numbers": order_numbers,
            }
        )

    return jsonify({"loads": loads})


if __name__ == "__main__":
    app.run(debug=True)
