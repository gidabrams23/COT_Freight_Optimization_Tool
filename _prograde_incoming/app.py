import uuid
from flask import Flask, render_template, request, redirect, url_for, jsonify

import db
import brand_config
from services.load_constraint_checker import check_load
from services.models import Violation
from services import pj_measurement
from services.pj_rules import compute_column_heights

app = Flask(__name__)
app.secret_key = "prograde-dev-key"


# ── Helpers ───────────────────────────────────────────────────────────────────

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


# ── Page Routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    sessions = db.get_all_sessions()
    return render_template("index.html", sessions=sessions)


@app.route("/session/new", methods=["GET", "POST"])
def session_new():
    if request.method == "POST":
        brand         = request.form["brand"]
        carrier_type  = request.form["carrier_type"]
        planner_name  = request.form.get("planner_name", "").strip()
        session_label = request.form.get("session_label", "").strip()
        session_id    = str(uuid.uuid4())
        db.create_session(session_id, brand, carrier_type, planner_name, session_label)
        return redirect(url_for("load_builder", session_id=session_id))
    return render_template("session_start.html")


@app.route("/session/<session_id>/load")
def load_builder(session_id):
    session = db.get_session(session_id)
    if not session:
        return "Session not found", 404

    brand        = session["brand"]
    carrier_type = session["carrier_type"]
    carrier      = db.get_carrier_config(carrier_type)
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
        "load_builder.html",
        session=session,
        carrier=carrier,
        zones=zones,
        zone_labels=zone_labels,
        skus=skus,
        **canvas,
    )


@app.route("/session/<session_id>/export")
def export_load(session_id):
    session = db.get_session(session_id)
    if not session:
        return "Session not found", 404

    brand        = session["brand"]
    carrier_type = session["carrier_type"]
    carrier      = db.get_carrier_config(carrier_type)
    zones        = brand_config.DECK_ZONES[brand]
    zone_labels  = brand_config.ZONE_LABELS

    raw_positions = db.get_positions(session_id)
    canvas = _build_canvas_data(session_id, session, carrier, zones, raw_positions, brand)

    return render_template(
        "export.html",
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


@app.route("/settings")
def settings():
    return render_template(
        "settings.html",
        carrier_configs        = db.get_carrier_configs(),
        pj_tongue_groups       = db.get_pj_tongue_groups(),
        pj_height_reference    = db.get_pj_height_reference(),
        pj_measurement_offsets = db.get_pj_measurement_offsets(),
        pj_skus                = db.get_pj_skus(),
        bt_stack_configs       = db.get_bt_stack_configs(),
        pj_categories          = brand_config.PJ_CATEGORIES,
    )


# ── Settings Save API ─────────────────────────────────────────────────────────

ALLOWED_FIELDS = {
    "carrier_configs": {
        "total_length_ft", "max_height_ft", "lower_deck_length_ft", "upper_deck_length_ft",
        "lower_deck_ground_height_ft", "upper_deck_ground_height_ft", "gn_max_lower_deck_ft", "notes",
    },
    "pj_tongue_groups":    {"group_label", "tongue_feet", "notes"},
    "pj_height_reference": {"height_mid_ft", "height_top_ft", "gn_axle_dropped_ft", "notes"},
    "pj_measurement_offsets": {"offset_ft", "notes"},
    "bt_stack_configs":    {"max_length_ft", "max_height_ft", "notes"},
    "pj_skus": {
        "pj_category", "dump_side_height_ft", "can_nest_inside_dump",
        "gn_axle_droppable", "tongue_overlap_allowed", "pairing_rule", "notes",
    },
}

NUMERIC_FIELDS = {
    "total_length_ft", "max_height_ft", "lower_deck_length_ft", "upper_deck_length_ft",
    "lower_deck_ground_height_ft", "upper_deck_ground_height_ft", "gn_max_lower_deck_ft",
    "tongue_feet", "height_mid_ft", "height_top_ft", "gn_axle_dropped_ft",
    "offset_ft", "max_length_ft", "dump_side_height_ft",
    "can_nest_inside_dump", "gn_axle_droppable", "tongue_overlap_allowed",
}


@app.route("/api/settings/save", methods=["POST"])
def api_settings_save():
    data      = request.get_json()
    table     = data.get("table")
    pk        = data.get("pk")
    field     = data.get("field")
    value     = data.get("value")
    recompute = data.get("recompute")

    if table not in ALLOWED_FIELDS:
        return jsonify(ok=False, error="Unknown table")
    if field not in ALLOWED_FIELDS[table]:
        return jsonify(ok=False, error="Field not allowed")

    try:
        if field in NUMERIC_FIELDS and value not in (None, ""):
            value = float(value)
        elif value == "":
            value = None

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
        elif table == "pj_skus":
            db.update_pj_sku_field(pk, field, value)

        db.flag_all_draft_sessions_stale()

        recomputed = None
        # Offset change → recompute all PJ SKU footprints
        if recompute == "pj_skus" or (table == "pj_measurement_offsets" and field == "offset_ft"):
            recomputed = _recompute_all_pj_skus()

        # Tongue feet change → recompute SKUs for that tongue group
        if table == "pj_tongue_groups" and field == "tongue_feet" and value is not None:
            recomputed = _recompute_pj_skus_for_tongue_group(pk, float(value))

        return jsonify(ok=True, sessions_flagged=True, recomputed=recomputed)

    except Exception as e:
        return jsonify(ok=False, error=str(e))


# ── Session / Load Canvas APIs ────────────────────────────────────────────────

@app.route("/api/session/<session_id>/add", methods=["POST"])
def api_add_unit(session_id):
    session = db.get_session(session_id)
    if not session:
        return jsonify(ok=False, error="Session not found")

    data        = request.get_json()
    item_number = data.get("item_number")
    deck_zone   = data.get("deck_zone")
    stack_on    = data.get("stack_on")  # optional: position_id to stack ON TOP of

    if not item_number or not deck_zone:
        return jsonify(ok=False, error="item_number and deck_zone required")

    positions = db.get_positions(session_id)

    if stack_on:
        # Stacking: same sequence as target, layer = target_layer + 1
        target = next((p for p in positions if p["position_id"] == stack_on), None)
        if not target:
            return jsonify(ok=False, error="Target position not found")
        seq   = target["sequence"]
        layer = target["layer"] + 1
    else:
        # New column: next sequence number in the zone
        zone_positions = [p for p in positions if p["deck_zone"] == deck_zone]
        seq   = max((p["sequence"] for p in zone_positions), default=0) + 1
        layer = 1

    position_id = str(uuid.uuid4())
    db.add_position(position_id, session_id, session["brand"], item_number, deck_zone, layer, seq)
    return jsonify(ok=True, position_id=position_id)


@app.route("/api/session/<session_id>/remove", methods=["POST"])
def api_remove_unit(session_id):
    data        = request.get_json()
    position_id = data.get("position_id")
    if not position_id:
        return jsonify(ok=False, error="position_id required")
    db.remove_position(position_id)
    return jsonify(ok=True)


@app.route("/api/session/<session_id>/toggle_axle_drop", methods=["POST"])
def api_toggle_axle_drop(session_id):
    data        = request.get_json()
    position_id = data.get("position_id")
    if not position_id:
        return jsonify(ok=False, error="position_id required")
    positions = db.get_positions(session_id)
    pos = next((p for p in positions if p["position_id"] == position_id), None)
    if not pos:
        return jsonify(ok=False, error="Position not found")
    new_val = 0 if pos["gn_axle_dropped"] else 1
    db.update_position_field(position_id, "gn_axle_dropped", new_val)
    return jsonify(ok=True, gn_axle_dropped=new_val)


@app.route("/api/session/<session_id>/nest", methods=["POST"])
def api_nest_unit(session_id):
    """Mark a position as nested inside another (e.g. D5 inside a dump)."""
    data             = request.get_json()
    position_id      = data.get("position_id")
    nested_inside_id = data.get("nested_inside")
    if not position_id or not nested_inside_id:
        return jsonify(ok=False, error="position_id and nested_inside required")
    db.update_position_field(position_id, "is_nested", 1)
    db.update_position_field(position_id, "nested_inside", nested_inside_id)
    return jsonify(ok=True)


@app.route("/api/session/<session_id>/acknowledge", methods=["POST"])
def api_acknowledge(session_id):
    """Acknowledge a warning-level violation so it no longer blocks progress."""
    data      = request.get_json()
    rule_code = data.get("rule_code")
    action    = data.get("action", "add")  # 'add' | 'remove'
    if not rule_code:
        return jsonify(ok=False, error="rule_code required")

    acked = db.get_acknowledged_violations(session_id)
    if action == "add" and rule_code not in acked:
        acked.append(rule_code)
    elif action == "remove" and rule_code in acked:
        acked.remove(rule_code)
    db.set_acknowledged_violations(session_id, acked)
    return jsonify(ok=True, acknowledged=acked)


@app.route("/api/session/<session_id>/check")
def api_check(session_id):
    violations = check_load(session_id)
    acked = set(db.get_acknowledged_violations(session_id))
    return jsonify(violations=[
        {
            "severity": v.severity,
            "rule_code": v.rule_code,
            "message": v.message,
            "suggested_fix": v.suggested_fix,
            "position_ids": v.position_ids,
            "acknowledged": v.rule_code in acked,
        }
        for v in violations
    ])


if __name__ == "__main__":
    db.init_db()
    app.run(debug=True, port=5050)
