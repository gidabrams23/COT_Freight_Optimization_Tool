import uuid
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, current_app

from . import db
from . import brand_config
from .services.load_constraint_checker import check_load
from .services import pj_measurement
from .services.pj_rules import compute_column_heights

prograde_bp = Blueprint("prograde", __name__, url_prefix="/prograde", template_folder="templates", static_folder="static")


def _json_error(message, status=400):
    return jsonify(ok=False, error=message), status


def _session_or_404(session_id):
    row = db.get_session(session_id)
    if not row:
        return None, _json_error("Session not found", 404)
    return row, None


def _build_session_api_state(session_id):
    session = db.get_session(session_id)
    if not session:
        return None
    session_dict = dict(session)
    brand = session_dict["brand"]
    carrier = db.get_carrier_config(session_dict["carrier_type"])
    zones = brand_config.DECK_ZONES.get(brand, [])
    raw_positions = db.get_positions(session_id)
    canvas = _build_canvas_data(session_id, session_dict, carrier, zones, raw_positions, brand)
    return {
        "total_footprint": canvas["total_footprint"],
        "pct_used": canvas["pct_used"],
        "violations": canvas["violations"],
        "violations_error": canvas["violations_error"],
        "violations_warning": canvas["violations_warning"],
        "violations_info": canvas["violations_info"],
    }


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _build_bt_sku_map():
    return {r["item_number"]: dict(r) for r in db.get_bigtex_skus()}


def _build_position_view(pos, brand, bt_sku_map=None, height_ref=None):
    """Enrich a position row with display fields: footprint, height, sku metadata."""
    p = dict(pos)
    if brand == "pj":
        raw = db.get_pj_sku(p["item_number"])
        sku = dict(raw) if raw else {}
    else:
        sku = (bt_sku_map or {}).get(p["item_number"]) or {}

    p["footprint"] = round(sku.get("total_footprint") or 0, 2)
    p["model"] = sku.get("model", "")
    p["description"] = sku.get("description", "")
    p["pj_category"] = sku.get("pj_category", "")
    p["mcat"] = sku.get("mcat", "")
    p["tier"] = sku.get("tier")
    p["gn_axle_droppable"] = bool(sku.get("gn_axle_droppable"))
    p["can_nest_inside_dump"] = bool(sku.get("can_nest_inside_dump"))
    if brand == "pj":
        p["bed_length"] = round((sku.get("bed_length_measured") or sku.get("bed_length_stated") or 0), 2)
        p["tongue_length"] = round((sku.get("tongue_feet") or 0), 2)
    elif brand == "bigtex":
        p["bed_length"] = round((sku.get("bed_length") or 0), 2)
        p["tongue_length"] = round((sku.get("tongue") or 0), 2)
    else:
        p["bed_length"] = 0
        p["tongue_length"] = 0

    # Height for display (top height value; axle drop override for GNs)
    if brand == "pj" and height_ref:
        cat = sku.get("pj_category", "")
        ref = (height_ref or {}).get(cat, {})
        if p["gn_axle_dropped"] and ref.get("gn_axle_dropped_ft") is not None:
            p["height"] = ref["gn_axle_dropped_ft"]
        else:
            p["height"] = ref.get("height_top_ft", 0)
    elif brand == "bigtex":
        p["height"] = sku.get("stack_height") or 0
    else:
        p["height"] = 0
    return p


def _zone_caps(carrier, zones):
    caps = {}
    for z in zones:
        if z == "lower_deck":
            caps[z] = carrier["lower_deck_length_ft"] if carrier else 41.0
        elif z == "upper_deck":
            caps[z] = carrier["upper_deck_length_ft"] if carrier else 12.0
        else:
            caps[z] = None   # BT: from stack configs
    return caps


def _zone_clearances(carrier):
    if not carrier:
        return {"lower_deck": 10.0, "upper_deck": 8.5}
    return {
        "lower_deck": round(carrier["max_height_ft"] - carrier["lower_deck_ground_height_ft"], 2),
        "upper_deck": round(carrier["max_height_ft"] - carrier["upper_deck_ground_height_ft"], 2),
    }


def _recompute_all_pj_skus():
    offsets = db.get_pj_offsets_dict()
    updated = []
    for sku in db.get_pj_skus():
        sku_d = dict(sku)
        result = pj_measurement.recompute_sku(sku_d, offsets)
        db.update_pj_sku_field(sku_d["item_number"], "bed_length_measured", result["bed_length_measured"])
        db.update_pj_sku_field(sku_d["item_number"], "total_footprint",     result["total_footprint"])
        updated.append({"item_number": sku_d["item_number"], **result})
    return updated


def _recompute_pj_skus_for_tongue_group(group_id, new_tongue_ft):
    """When tongue_feet changes for a group, recompute total_footprint for affected SKUs."""
    offsets = db.get_pj_offsets_dict()
    updated = []
    for sku in db.get_pj_skus_for_tongue_group(group_id):
        sku_d = dict(sku)
        sku_d["tongue_feet"] = new_tongue_ft
        db.update_pj_sku_field(sku_d["item_number"], "tongue_feet", new_tongue_ft)
        result = pj_measurement.recompute_sku(sku_d, offsets)
        db.update_pj_sku_field(sku_d["item_number"], "bed_length_measured", result["bed_length_measured"])
        db.update_pj_sku_field(sku_d["item_number"], "total_footprint",     result["total_footprint"])
        updated.append({"item_number": sku_d["item_number"], **result})
    return updated


def _build_canvas_data(session_id, session, carrier, zones, positions, brand):
    """Build all zone/column data needed for the load canvas."""
    height_ref  = db.get_pj_height_ref_dict() if brand == "pj" else {}
    bt_sku_map  = _build_bt_sku_map() if brand == "bigtex" else None
    bt_configs  = {r["config_id"]: dict(r) for r in db.get_bt_stack_configs()} if brand == "bigtex" else {}

    enriched = [_build_position_view(p, brand, bt_sku_map, height_ref) for p in positions]

    # Group positions: {zone: {seq: [positions sorted by layer]}}
    zone_cols: dict = {z: {} for z in zones}
    for p in enriched:
        z = p["deck_zone"]
        if z in zone_cols:
            seq = p["sequence"]
            zone_cols[z].setdefault(seq, []).append(p)
    # Sort each column by layer
    for z in zone_cols:
        for seq in zone_cols[z]:
            zone_cols[z][seq] = sorted(zone_cols[z][seq], key=lambda p: p["layer"])

    # Per-zone length (sum of footprints of bottom-layer units in each column)
    zone_lengths: dict = {}
    for z in zones:
        total = 0.0
        for seq, col in zone_cols[z].items():
            # Count only the base unit (layer 1) footprint for length
            base = next((p for p in col if p["layer"] == 1), col[0] if col else None)
            if base:
                if base.get("is_nested"):
                    total += 0  # nested unit's footprint not counted
                else:
                    total += base["footprint"]
        zone_lengths[z] = round(total, 2)

    # Per-zone, per-column heights (PJ only)
    col_heights: dict = {z: {} for z in zones}
    if brand == "pj":
        raw_col_h = compute_column_heights(positions, brand, height_ref)
        for z, cols in raw_col_h.items():
            if z in col_heights:
                col_heights[z] = cols

    # For BT: sum stack_height per stack column
    elif brand == "bigtex":
        for z in zones:
            for seq, col in zone_cols[z].items():
                col_heights[z][seq] = round(sum(p["height"] for p in col), 2)

    # Zone caps
    z_caps = _zone_caps(carrier, zones)

    # BT: fill in caps from stack configs
    if brand == "bigtex":
        for z in zones:
            # Use utility_3stack caps as default display cap
            cfg_key = f"utility_3stack_{z}"
            if cfg_key in bt_configs and bt_configs[cfg_key].get("max_length_ft"):
                z_caps[z] = bt_configs[cfg_key]["max_length_ft"]

    # Zone clearances (PJ height caps)
    clearances = _zone_clearances(carrier)

    # BT height caps
    if brand == "bigtex":
        for z in zones:
            cfg_key = f"utility_3stack_{z}"
            if cfg_key in bt_configs and bt_configs[cfg_key].get("max_height_ft"):
                clearances[z] = bt_configs[cfg_key]["max_height_ft"]

    total_footprint = sum(zone_lengths.values())
    pct_used = round(total_footprint / (carrier["total_length_ft"] if carrier else 53.0) * 100, 1)

    # Violations (with acknowledgment overlay)
    violations_raw = check_load(session_id)
    acked = set(db.get_acknowledged_violations(session_id))
    violations = []
    for v in violations_raw:
        vd = {
            "severity": v.severity,
            "rule_code": v.rule_code,
            "message": v.message,
            "suggested_fix": v.suggested_fix,
            "position_ids": v.position_ids,
            "acknowledged": v.rule_code in acked,
        }
        violations.append(vd)

    # After running check, mark stale session as active
    db.mark_session_active(session_id)

    violations_error   = sum(1 for v in violations if v["severity"] == "error" and not v["acknowledged"])
    violations_warning = sum(1 for v in violations if v["severity"] == "warning" and not v["acknowledged"])
    violations_info    = sum(1 for v in violations if v["severity"] == "info")

    return dict(
        enriched_positions=enriched,
        zone_cols=zone_cols,
        zone_lengths=zone_lengths,
        col_heights=col_heights,
        z_caps=z_caps,
        clearances=clearances,
        total_footprint=total_footprint,
        pct_used=pct_used,
        violations=violations,
        violations_error=violations_error,
        violations_warning=violations_warning,
        violations_info=violations_info,
    )


# â”€â”€ Page Routes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@prograde_bp.route("/")
def index():
    sessions = db.get_all_sessions()
    return render_template(
        "prograde/index.html",
        sessions=sessions,
        has_seed_data=db.has_seed_data(),
    )


@prograde_bp.route("/session/new", methods=["GET", "POST"])
def session_new():
    if not db.has_seed_data():
        return render_template(
            "prograde/session_start.html",
            error_message="ProGrade seed data is not loaded yet. Contact support or load seed data first.",
        ), 503

    if request.method == "POST":
        brand = (request.form.get("brand") or "").strip().lower()
        carrier_type = (request.form.get("carrier_type") or "").strip()
        planner_name  = request.form.get("planner_name", "").strip()
        session_label = request.form.get("session_label", "").strip()

        if brand not in {"pj", "bigtex"}:
            return render_template(
                "prograde/session_start.html",
                error_message="Invalid brand selection.",
            ), 400
        carrier = db.get_carrier_config(carrier_type)
        if not carrier:
            return render_template(
                "prograde/session_start.html",
                error_message="Carrier type not found.",
            ), 400

        session_id    = str(uuid.uuid4())
        db.create_session(session_id, brand, carrier_type, planner_name, session_label)
        return redirect(url_for("prograde.load_builder", session_id=session_id))
    return render_template("prograde/session_start.html")


@prograde_bp.route("/session/<session_id>/load")
def load_builder(session_id):
    session = db.get_session(session_id)
    if not session:
        return "Session not found", 404

    brand = session["brand"]
    carrier_type = session["carrier_type"]
    carrier      = db.get_carrier_config(carrier_type)
    if not carrier:
        return "Carrier configuration not found", 400
    zones        = brand_config.DECK_ZONES[brand]
    zone_labels  = brand_config.ZONE_LABELS

    raw_positions = db.get_positions(session_id)
    canvas = _build_canvas_data(session_id, session, carrier, zones, raw_positions, brand)

    # SKU list for picker
    if brand == "pj":
        skus = [dict(s) for s in db.get_pj_skus()]
    else:
        skus = [dict(s) for s in db.get_bigtex_skus()]

    return render_template(
        "prograde/load_builder.html",
        session=session,
        carrier=carrier,
        zones=zones,
        zone_labels=zone_labels,
        skus=skus,
        **canvas,
    )


@prograde_bp.route("/session/<session_id>/export")
def export_load(session_id):
    session = db.get_session(session_id)
    if not session:
        return "Session not found", 404

    brand = session["brand"]
    carrier_type = session["carrier_type"]
    carrier      = db.get_carrier_config(carrier_type)
    if not carrier:
        return "Carrier configuration not found", 400
    zones        = brand_config.DECK_ZONES[brand]
    zone_labels  = brand_config.ZONE_LABELS

    raw_positions = db.get_positions(session_id)
    canvas = _build_canvas_data(session_id, session, carrier, zones, raw_positions, brand)

    return render_template(
        "prograde/export.html",
        session=session,
        carrier=carrier,
        zones=zones,
        zone_labels=zone_labels,
        positions=canvas["enriched_positions"],
        zone_lengths=canvas["zone_lengths"],
        zone_caps=canvas["z_caps"],
        total_footprint=canvas["total_footprint"],
        violations=canvas["violations"],
        violations_error=canvas["violations_error"],
        violations_warning=canvas["violations_warning"],
    )


@prograde_bp.route("/settings")
def settings():
    bt_workbook_path = db.get_bigtex_workbook_path()
    if not db.has_seed_data():
        return render_template(
            "prograde/settings.html",
            carrier_configs=[],
            pj_tongue_groups=[],
            pj_height_reference=[],
            pj_measurement_offsets=[],
            pj_skus=[],
            bt_skus=[],
            bt_stack_configs=[],
            bt_workbook_path=str(bt_workbook_path) if bt_workbook_path else "",
            pj_categories=brand_config.PJ_CATEGORIES,
            error_message="ProGrade seed data not loaded. Settings are unavailable until data is seeded.",
        ), 503
    return render_template(
        "prograde/settings.html",
        carrier_configs        = db.get_carrier_configs(),
        pj_tongue_groups       = db.get_pj_tongue_groups(),
        pj_height_reference    = db.get_pj_height_reference(),
        pj_measurement_offsets = db.get_pj_measurement_offsets(),
        pj_skus                = db.get_pj_skus(),
        bt_skus                = db.get_bigtex_skus(),
        bt_stack_configs       = db.get_bt_stack_configs(),
        bt_workbook_path       = str(bt_workbook_path) if bt_workbook_path else "",
        pj_categories          = brand_config.PJ_CATEGORIES,
    )


@prograde_bp.route("/api/session/<session_id>/state")
def api_session_state(session_id):
    _session, err = _session_or_404(session_id)
    if err:
        return err
    state = _build_session_api_state(session_id)
    if state is None:
        return _json_error("Session not found", 404)
    return jsonify(ok=True, state=state)


# â”€â”€ Settings Save API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

ALLOWED_FIELDS = {
    "carrier_configs": {
        "total_length_ft", "max_height_ft", "lower_deck_length_ft", "upper_deck_length_ft",
        "lower_deck_ground_height_ft", "upper_deck_ground_height_ft", "gn_max_lower_deck_ft", "notes",
    },
    "pj_tongue_groups":    {"group_label", "tongue_feet", "notes"},
    "pj_height_reference": {"height_mid_ft", "height_top_ft", "gn_axle_dropped_ft", "notes"},
    "pj_measurement_offsets": {"offset_ft", "notes"},
    "bt_stack_configs":    {"max_length_ft", "max_height_ft", "notes"},
    "bigtex_skus": {"mcat", "tier", "model", "gvwr", "floor_type", "bed_length", "width", "tongue", "stack_height"},
    "pj_skus": {
        "pj_category", "dump_side_height_ft", "can_nest_inside_dump",
        "gn_axle_droppable", "tongue_overlap_allowed", "pairing_rule", "notes",
    },
}

NUMERIC_FIELDS = {
    "total_length_ft", "max_height_ft", "lower_deck_length_ft", "upper_deck_length_ft",
    "lower_deck_ground_height_ft", "upper_deck_ground_height_ft", "gn_max_lower_deck_ft",
    "tongue_feet", "height_mid_ft", "height_top_ft", "gn_axle_dropped_ft",
    "offset_ft", "max_length_ft", "max_height_ft", "dump_side_height_ft",
    "can_nest_inside_dump", "gn_axle_droppable", "tongue_overlap_allowed",
    "tier", "gvwr", "bed_length", "width", "tongue", "stack_height",
}


@prograde_bp.route("/api/settings/save", methods=["POST"])
def api_settings_save():
    data = request.get_json(silent=True) or {}
    table = data.get("table")
    pk = data.get("pk")
    field = data.get("field")
    value = data.get("value")
    recompute = data.get("recompute")

    if table not in ALLOWED_FIELDS:
        return _json_error("Unknown table")
    if field not in ALLOWED_FIELDS[table]:
        return _json_error("Field not allowed")
    if pk in (None, ""):
        return _json_error("Missing primary key")

    try:
        if field in NUMERIC_FIELDS and value not in (None, ""):
            value = float(value)
        elif value == "":
            value = None
    except (TypeError, ValueError):
        return _json_error(f"Invalid numeric value for {field}")

    try:
        if table == "carrier_configs":
            db.update_carrier_config(pk, field, value)
        elif table == "pj_tongue_groups":
            db.update_pj_tongue_group(pk, field, value)
        elif table == "pj_height_reference":
            db.update_pj_height_reference(pk, field, value)
        elif table == "pj_measurement_offsets":
            db.update_pj_measurement_offset(pk, field, value)
        elif table == "bt_stack_configs":
            db.update_bt_stack_config(pk, field, value)
        elif table == "bigtex_skus":
            if field in {"tier", "gvwr"} and value is not None:
                value = int(value)
            db.update_bigtex_sku_field(pk, field, value)
        elif table == "pj_skus":
            db.update_pj_sku_field(pk, field, value)

        db.flag_all_draft_sessions_stale()

        recomputed = None
        recomputed_bigtex = None
        if recompute == "pj_skus" or (table == "pj_measurement_offsets" and field == "offset_ft"):
            recomputed = _recompute_all_pj_skus()
        if table == "pj_tongue_groups" and field == "tongue_feet" and value is not None:
            recomputed = _recompute_pj_skus_for_tongue_group(pk, float(value))
        if table == "bigtex_skus" and field in {"bed_length", "tongue"}:
            refreshed = db.recompute_bigtex_footprint(pk)
            if refreshed:
                recomputed_bigtex = [refreshed]

        return jsonify(ok=True, sessions_flagged=True, recomputed=recomputed, recomputed_bigtex=recomputed_bigtex)
    except Exception:
        current_app.logger.exception("Failed to save ProGrade settings")
        return _json_error("Failed to save settings", 500)


@prograde_bp.route("/api/settings/bigtex/import", methods=["POST"])
def api_bigtex_import():
    data = request.get_json(silent=True) or {}
    workbook_path = (data.get("workbook_path") or "").strip() or None
    sheet_name = (data.get("sheet_name") or "Data").strip() or "Data"
    try:
        result = db.import_bigtex_skus_from_workbook(workbook_path=workbook_path, sheet_name=sheet_name)
        db.flag_all_draft_sessions_stale()
        return jsonify(ok=True, sessions_flagged=True, import_result=result)
    except FileNotFoundError as exc:
        return _json_error(str(exc), 404)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    except Exception:
        current_app.logger.exception("Failed to import Big Tex workbook")
        return _json_error("Failed to import Big Tex workbook", 500)


@prograde_bp.route("/api/session/<session_id>/add", methods=["POST"])
def api_add_unit(session_id):
    session, err = _session_or_404(session_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    item_number = (data.get("item_number") or "").strip()
    deck_zone = (data.get("deck_zone") or "").strip()
    stack_on = data.get("stack_on")
    insert_index = data.get("insert_index")

    if not item_number or not deck_zone:
        return _json_error("item_number and deck_zone required")

    brand = session["brand"]
    valid_zones = brand_config.DECK_ZONES.get(brand, [])
    if deck_zone not in valid_zones:
        return _json_error("Invalid deck_zone")

    sku = db.get_pj_sku(item_number) if brand == "pj" else db.get_bigtex_sku(item_number)
    if not sku:
        return _json_error("Item number not found")

    insert_idx = None
    if not stack_on and insert_index is not None:
        try:
            insert_idx = int(insert_index)
        except (TypeError, ValueError):
            return _json_error("insert_index must be an integer")

    try:
        positions = db.get_positions(session_id)
        if stack_on:
            target = next((p for p in positions if p["position_id"] == stack_on), None)
            if not target:
                return _json_error("Target position not found")
            seq = int(target["sequence"])
            layer = int(target["layer"]) + 1
        else:
            zone_positions = [p for p in positions if p["deck_zone"] == deck_zone]
            seq = max((int(p["sequence"]) for p in zone_positions), default=0) + 1
            layer = 1

        position_id = str(uuid.uuid4())
        db.add_position(position_id, session_id, brand, item_number, deck_zone, layer, seq)
        if insert_idx is not None:
            db.move_position(session_id, position_id, deck_zone, to_sequence=None, insert_index=insert_idx)
        return jsonify(ok=True, position_id=position_id, state=_build_session_api_state(session_id))
    except Exception:
        current_app.logger.exception("Failed to add unit for ProGrade session %s", session_id)
        return _json_error("Failed to add unit", 500)


@prograde_bp.route("/api/session/<session_id>/remove", methods=["POST"])
def api_remove_unit(session_id):
    _session, err = _session_or_404(session_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    position_id = data.get("position_id")
    if not position_id:
        return _json_error("position_id required")

    pos = db.get_position(position_id)
    if not pos or pos["session_id"] != session_id:
        return _json_error("Position not found", 404)

    try:
        db.remove_position(position_id)
        return jsonify(ok=True, state=_build_session_api_state(session_id))
    except Exception:
        current_app.logger.exception("Failed to remove unit for ProGrade session %s", session_id)
        return _json_error("Failed to remove unit", 500)


@prograde_bp.route("/api/session/<session_id>/toggle_axle_drop", methods=["POST"])
def api_toggle_axle_drop(session_id):
    _session, err = _session_or_404(session_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    position_id = data.get("position_id")
    if not position_id:
        return _json_error("position_id required")

    pos = db.get_position(position_id)
    if not pos or pos["session_id"] != session_id:
        return _json_error("Position not found", 404)

    try:
        new_val = 0 if int(pos["gn_axle_dropped"] or 0) else 1
        db.update_position_field(position_id, "gn_axle_dropped", new_val)
        return jsonify(ok=True, gn_axle_dropped=new_val, state=_build_session_api_state(session_id))
    except Exception:
        current_app.logger.exception("Failed to toggle axle drop for ProGrade session %s", session_id)
        return _json_error("Failed to toggle axle drop", 500)


@prograde_bp.route("/api/session/<session_id>/nest", methods=["POST"])
def api_nest_unit(session_id):
    _session, err = _session_or_404(session_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    position_id = data.get("position_id")
    nested_inside_id = data.get("nested_inside")
    if not position_id or not nested_inside_id:
        return _json_error("position_id and nested_inside required")

    pos = db.get_position(position_id)
    host = db.get_position(nested_inside_id)
    if not pos or not host or pos["session_id"] != session_id or host["session_id"] != session_id:
        return _json_error("Position not found", 404)

    try:
        db.update_position_field(position_id, "is_nested", 1)
        db.update_position_field(position_id, "nested_inside", nested_inside_id)
        return jsonify(ok=True, state=_build_session_api_state(session_id))
    except Exception:
        current_app.logger.exception("Failed to nest unit for ProGrade session %s", session_id)
        return _json_error("Failed to nest unit", 500)


@prograde_bp.route("/api/session/<session_id>/acknowledge", methods=["POST"])
def api_acknowledge(session_id):
    _session, err = _session_or_404(session_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    rule_code = (data.get("rule_code") or "").strip()
    action = (data.get("action") or "add").strip().lower()
    if not rule_code:
        return _json_error("rule_code required")
    if action not in {"add", "remove"}:
        return _json_error("action must be add or remove")

    try:
        acked = db.get_acknowledged_violations(session_id)
        if action == "add" and rule_code not in acked:
            acked.append(rule_code)
        elif action == "remove" and rule_code in acked:
            acked.remove(rule_code)
        db.set_acknowledged_violations(session_id, acked)
        return jsonify(ok=True, acknowledged=acked, state=_build_session_api_state(session_id))
    except Exception:
        current_app.logger.exception("Failed to acknowledge violation for ProGrade session %s", session_id)
        return _json_error("Failed to update acknowledgement", 500)


@prograde_bp.route("/api/session/<session_id>/check")
def api_check(session_id):
    _session, err = _session_or_404(session_id)
    if err:
        return err

    try:
        violations = check_load(session_id)
        acked = set(db.get_acknowledged_violations(session_id))
        return jsonify(
            ok=True,
            violations=[
                {
                    "severity": v.severity,
                    "rule_code": v.rule_code,
                    "message": v.message,
                    "suggested_fix": v.suggested_fix,
                    "position_ids": v.position_ids,
                    "acknowledged": v.rule_code in acked,
                }
                for v in violations
            ],
            state=_build_session_api_state(session_id),
        )
    except Exception:
        current_app.logger.exception("Failed to run constraint check for ProGrade session %s", session_id)
        return _json_error("Failed to run constraint check", 500)


@prograde_bp.route("/api/session/<session_id>/position/move", methods=["POST"])
def api_move_position(session_id):
    session, err = _session_or_404(session_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    position_id = (data.get("position_id") or "").strip()
    to_zone = (data.get("to_zone") or "").strip()
    to_sequence = data.get("to_sequence")
    insert_index = data.get("insert_index")

    if not position_id or not to_zone:
        return _json_error("position_id and to_zone required")

    brand = session["brand"]
    valid_zones = brand_config.DECK_ZONES.get(brand, [])
    if to_zone not in valid_zones:
        return _json_error("Invalid to_zone")

    if to_sequence is not None:
        try:
            to_sequence = int(to_sequence)
        except (TypeError, ValueError):
            return _json_error("to_sequence must be an integer")

    if insert_index is not None:
        try:
            insert_index = int(insert_index)
        except (TypeError, ValueError):
            return _json_error("insert_index must be an integer")

    try:
        result = db.move_position(
            session_id,
            position_id=position_id,
            to_zone=to_zone,
            to_sequence=to_sequence,
            insert_index=insert_index,
        )
        if not result:
            return _json_error("Position not found", 404)
        return jsonify(ok=True, result=result, state=_build_session_api_state(session_id))
    except Exception:
        current_app.logger.exception("Failed to move position for ProGrade session %s", session_id)
        return _json_error("Failed to move unit", 500)


@prograde_bp.route("/api/session/<session_id>/column/move", methods=["POST"])
def api_move_column(session_id):
    session, err = _session_or_404(session_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    from_zone = (data.get("from_zone") or "").strip()
    to_zone = (data.get("to_zone") or "").strip()
    sequence = data.get("sequence")
    insert_index = data.get("insert_index")

    if not from_zone or not to_zone or sequence is None:
        return _json_error("from_zone, to_zone, and sequence required")

    brand = session["brand"]
    valid_zones = brand_config.DECK_ZONES.get(brand, [])
    if from_zone not in valid_zones or to_zone not in valid_zones:
        return _json_error("Invalid zone")

    try:
        sequence = int(sequence)
    except (TypeError, ValueError):
        return _json_error("sequence must be an integer")

    if insert_index is not None:
        try:
            insert_index = int(insert_index)
        except (TypeError, ValueError):
            return _json_error("insert_index must be an integer")

    try:
        result = db.move_column(
            session_id,
            from_zone=from_zone,
            sequence=sequence,
            to_zone=to_zone,
            insert_index=insert_index,
        )
        if not result:
            return _json_error("Column not found", 404)
        return jsonify(ok=True, result=result, state=_build_session_api_state(session_id))
    except Exception:
        current_app.logger.exception("Failed to move column for ProGrade session %s", session_id)
        return _json_error("Failed to move column", 500)


@prograde_bp.route("/api/session/<session_id>/column/duplicate", methods=["POST"])
def api_duplicate_column(session_id):
    _session, err = _session_or_404(session_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    deck_zone = (data.get("deck_zone") or "").strip()
    sequence = data.get("sequence")
    if not deck_zone or sequence is None:
        return _json_error("deck_zone and sequence required")

    try:
        sequence = int(sequence)
    except (TypeError, ValueError):
        return _json_error("sequence must be an integer")

    try:
        result = db.duplicate_column(session_id, deck_zone, sequence)
        if not result:
            return _json_error("Column not found", 404)
        return jsonify(ok=True, result=result, state=_build_session_api_state(session_id))
    except Exception:
        current_app.logger.exception("Failed to duplicate column for ProGrade session %s", session_id)
        return _json_error("Failed to duplicate column", 500)


@prograde_bp.route("/api/session/<session_id>/column/move-zone", methods=["POST"])
def api_move_column_zone(session_id):
    _session, err = _session_or_404(session_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    from_zone = (data.get("from_zone") or "").strip()
    to_zone = (data.get("to_zone") or "").strip()
    sequence = data.get("sequence")
    if not from_zone or not to_zone or sequence is None:
        return _json_error("from_zone, to_zone, and sequence required")

    try:
        sequence = int(sequence)
    except (TypeError, ValueError):
        return _json_error("sequence must be an integer")

    try:
        result = db.move_column_zone(session_id, from_zone, sequence, to_zone)
        if not result:
            return _json_error("Column not found", 404)
        return jsonify(ok=True, result=result, state=_build_session_api_state(session_id))
    except Exception:
        current_app.logger.exception("Failed to move column zone for ProGrade session %s", session_id)
        return _json_error("Failed to move column", 500)


@prograde_bp.route("/api/session/<session_id>/column/resequence", methods=["POST"])
def api_resequence_column(session_id):
    _session, err = _session_or_404(session_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    deck_zone = (data.get("deck_zone") or "").strip()
    direction = (data.get("direction") or "").strip().lower()
    sequence = data.get("sequence")
    if not deck_zone or sequence is None or direction not in {"left", "right"}:
        return _json_error("deck_zone, sequence, and direction (left/right) required")

    try:
        sequence = int(sequence)
    except (TypeError, ValueError):
        return _json_error("sequence must be an integer")

    try:
        result = db.resequence_column(session_id, deck_zone, sequence, direction)
        if result is None:
            return _json_error("Column not found", 404)
        return jsonify(ok=True, result=result, state=_build_session_api_state(session_id))
    except Exception:
        current_app.logger.exception("Failed to resequence column for ProGrade session %s", session_id)
        return _json_error("Failed to resequence column", 500)


@prograde_bp.route("/api/session/<session_id>/reset", methods=["POST"])
def api_reset_session(session_id):
    _session, err = _session_or_404(session_id)
    if err:
        return err

    try:
        db.clear_session_positions(session_id)
        db.set_acknowledged_violations(session_id, [])
        return jsonify(ok=True, state=_build_session_api_state(session_id))
    except Exception:
        current_app.logger.exception("Failed to reset ProGrade session %s", session_id)
        return _json_error("Failed to reset session", 500)


db.init_db()
