import uuid
from datetime import datetime
from pathlib import Path
import tempfile
import re
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, current_app, session
from jinja2 import ChoiceLoader, DictLoader

from . import db
from . import brand_config
from .services.load_constraint_checker import check_load
from .services import pj_measurement
from .services.pj_rules import (
    compute_column_heights,
    compute_pj_length_metrics,
    pj_dump_stacked_height_ft,
    pj_non_dump_stacking_height_ft,
)
from .services.bt_rules import compute_bt_length_metrics
from .services.inventory_gap_finder import build_inventory_gap_data

prograde_bp = Blueprint("prograde", __name__, url_prefix="/prograde", template_folder="templates", static_folder="static")
_TRAILER_SHAPE_TEMPLATE_NAME = "prograde/macros/trailer_shapes.html"
_TRAILER_SHAPE_SOURCE_PATH = Path(__file__).resolve().parents[2] / "trailer_shapes.html"
_ALLOWED_ORDER_UPLOAD_EXTENSIONS = {".xlsx", ".xlsm"}
_VALID_BRANDS = {"pj", "bigtex"}
_PJ_DUMP_CATEGORIES = {
    "dump_lowside",
    "dump_highside_3ft",
    "dump_highside_4ft",
    "dump_small",
    "dump_gn",
    "dump_variants",
}
_PJ_PICKER_COLLAPSE_MAP = {
    "car_hauler_deckover": "car_hauler",
    "tilt_deckover": "tilt",
}
_PJ_PICKER_CATEGORY_LABELS = {
    "car_hauler": "Car Hauler",
    "deck_over": "Deck Over",
    "dump": "Dump",
    "gooseneck": "Gooseneck",
    "pintle": "Pintle",
    "tilt": "Tilt",
    "utility": "Utility",
    "uncategorized": "Uncategorized",
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
_REAR_POCKET_LEN_FT = 5.0
_REAR_POCKET_HEIGHT_FT = 0.5
_DUMP_DOOR_MIN_EXPOSED_TONGUE_FT = 1.0
_GOOSENECK_WALL_CLEARANCE_FT = 0.08
SESSION_PROFILE_ID_KEY = "prograde_profile_id"
SESSION_PROFILE_NAME_KEY = "prograde_profile_name"
SESSION_PROFILE_IS_ADMIN_KEY = "prograde_profile_is_admin"
SESSION_ACCOUNT_NOTICE_KEY = "prograde_account_notice"


def _json_error(message, status=400):
    return jsonify(ok=False, error=message), status


def _normalize_pj_tongue_profile(raw_value, *, default=None):
    value = str(raw_value or "").strip().lower()
    if value in {"gooseneck", "gn"}:
        return "gooseneck"
    if value in {"standard", "std"}:
        return "standard"
    return default


def _normalize_pj_dump_height_ft(raw_value, *, default=None):
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return default
    if abs(value - 3.0) <= 0.05:
        return 3.0
    if abs(value - 4.0) <= 0.05:
        return 4.0
    return default


def _normalize_optional_bool(raw_value, *, default=False):
    if isinstance(raw_value, bool):
        return raw_value
    if raw_value is None:
        return bool(default)
    value = str(raw_value).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _row_to_dict(value):
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    try:
        return dict(value)
    except Exception:
        return {}


def _parse_override_reason_tokens(override_reason):
    tokens = {}
    for raw in str(override_reason or "").split(";"):
        token = raw.strip()
        if not token or ":" not in token:
            continue
        key, value = token.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key:
            tokens[key] = value
    return tokens


def _compose_override_reason_tokens(tokens):
    if not tokens:
        return None
    ordered = []
    for key in sorted(tokens.keys()):
        value = str(tokens[key]).strip()
        if value:
            ordered.append(f"{key}:{value}")
    return ";".join(ordered) if ordered else None


def _set_override_reason_token(override_reason, key, value):
    tokens = _parse_override_reason_tokens(override_reason)
    key_norm = str(key or "").strip().lower()
    if not key_norm:
        return _compose_override_reason_tokens(tokens)
    if value is None or str(value).strip() == "":
        tokens.pop(key_norm, None)
    else:
        tokens[key_norm] = str(value).strip()
    return _compose_override_reason_tokens(tokens)


def _get_override_reason_token(override_reason, key):
    tokens = _parse_override_reason_tokens(override_reason)
    return tokens.get(str(key or "").strip().lower())


def _build_tongue_override_reason(tongue_profile, base_override_reason=None):
    mode = _normalize_pj_tongue_profile(tongue_profile)
    if not mode:
        return base_override_reason
    return _set_override_reason_token(base_override_reason, "tongue_profile", mode)


def _extract_tongue_override_reason(override_reason):
    return _normalize_pj_tongue_profile(_get_override_reason_token(override_reason, "tongue_profile"), default=None)


def _extract_dump_door_removed_reason(override_reason):
    token = str(_get_override_reason_token(override_reason, "dump_door_removed") or "").strip().lower()
    return token in {"1", "true", "yes", "on"}


def _build_dump_height_override_reason(dump_height_ft, base_override_reason=None):
    normalized = _normalize_pj_dump_height_ft(dump_height_ft, default=None)
    if normalized is None:
        return base_override_reason
    return _set_override_reason_token(base_override_reason, "dump_height_ft", f"{normalized:.1f}")


def _extract_dump_height_override_reason(override_reason):
    return _normalize_pj_dump_height_ft(
        _get_override_reason_token(override_reason, "dump_height_ft"),
        default=None,
    )


def _build_gn_crisscross_override_reason(enabled, base_override_reason=None):
    return _set_override_reason_token(
        base_override_reason,
        "gn_crisscross",
        "1" if bool(enabled) else None,
    )


def _extract_gn_crisscross_override_reason(override_reason):
    return _normalize_optional_bool(
        _get_override_reason_token(override_reason, "gn_crisscross"),
        default=False,
    )


def _ensure_trailer_shape_template_alias():
    """Expose the root trailer macro file at the import path required by the canvas template.

    This refreshes the alias whenever the source file mtime changes so live PJ
    rendering edits are reflected without relying on process restarts.
    """
    app = current_app._get_current_object()
    if not _TRAILER_SHAPE_SOURCE_PATH.exists():
        return
    mtime_ns = _TRAILER_SHAPE_SOURCE_PATH.stat().st_mtime_ns
    if (
        getattr(app, "_prograde_trailer_shape_alias_ready", False)
        and getattr(app, "_prograde_trailer_shape_alias_mtime_ns", None) == mtime_ns
    ):
        return
    if not hasattr(app, "_prograde_trailer_shape_base_loader"):
        app._prograde_trailer_shape_base_loader = app.jinja_env.loader

    base_loader = app._prograde_trailer_shape_base_loader
    source = _TRAILER_SHAPE_SOURCE_PATH.read_text(encoding="utf-8")
    alias_loader = DictLoader({_TRAILER_SHAPE_TEMPLATE_NAME: source})
    app.jinja_env.loader = ChoiceLoader([alias_loader, base_loader] if base_loader is not None else [alias_loader])
    app.jinja_env.cache.clear()
    app._prograde_trailer_shape_alias_ready = True
    app._prograde_trailer_shape_alias_mtime_ns = mtime_ns


def _session_or_404(session_id):
    row = db.get_session(session_id)
    if not row:
        return None, _json_error("Session not found", 404)
    active_profile = _get_active_profile()
    if not active_profile:
        return None, _json_error("Select a ProGrade account to continue.", 401)
    if not _can_access_session(row, active_profile):
        return None, _json_error("You do not have access to this session.", 403)
    return row, None


def _selected_brand(default="bigtex"):
    query_brand = (request.args.get("brand") or "").strip().lower()
    if query_brand in _VALID_BRANDS:
        return query_brand
    fallback_brand = (default or "").strip().lower()
    if fallback_brand in _VALID_BRANDS:
        return fallback_brand
    return "bigtex"


def _safe_next_url(value):
    text = (value or "").strip()
    if text.startswith("/prograde/"):
        return text
    return None


def _set_account_notice(message, level="info"):
    session[SESSION_ACCOUNT_NOTICE_KEY] = {
        "message": str(message or "").strip(),
        "level": str(level or "info").strip().lower(),
    }


def _consume_account_notice():
    payload = session.pop(SESSION_ACCOUNT_NOTICE_KEY, None)
    if not isinstance(payload, dict):
        return None
    message = str(payload.get("message") or "").strip()
    if not message:
        return None
    level = str(payload.get("level") or "info").strip().lower()
    if level not in {"info", "success", "warning", "error"}:
        level = "info"
    return {"message": message, "level": level}


def _profile_to_view(profile):
    profile_map = dict(profile or {})
    is_admin = bool(profile_map.get("is_admin"))
    return {
        "id": profile_map.get("id"),
        "name": (profile_map.get("name") or "Unnamed").strip() or "Unnamed",
        "is_admin": is_admin,
        "role_label": "Administrator Account" if is_admin else "Planner Account",
    }


def _get_active_profile():
    profile_id = session.get(SESSION_PROFILE_ID_KEY)
    if not profile_id:
        return None
    profile = db.get_access_profile(profile_id)
    if not profile:
        session.pop(SESSION_PROFILE_ID_KEY, None)
        session.pop(SESSION_PROFILE_NAME_KEY, None)
        session.pop(SESSION_PROFILE_IS_ADMIN_KEY, None)
        return None
    view = _profile_to_view(profile)
    session[SESSION_PROFILE_ID_KEY] = int(view["id"])
    session[SESSION_PROFILE_NAME_KEY] = view["name"]
    session[SESSION_PROFILE_IS_ADMIN_KEY] = 1 if view["is_admin"] else 0
    return view


def _set_active_profile(profile):
    view = _profile_to_view(profile)
    session[SESSION_PROFILE_ID_KEY] = int(view["id"])
    session[SESSION_PROFILE_NAME_KEY] = view["name"]
    session[SESSION_PROFILE_IS_ADMIN_KEY] = 1 if view["is_admin"] else 0
    return view


def _resolve_session_builder_name(session_row):
    payload = dict(session_row or {})
    created_by_name = (payload.get("created_by_name") or "").strip()
    planner_name = (payload.get("planner_name") or "").strip()
    return created_by_name or planner_name or "Unassigned"


def _can_access_session(session_row, active_profile):
    if not session_row or not active_profile:
        return False
    if bool(active_profile.get("is_admin")):
        return True

    profile_id = active_profile.get("id")
    session_profile_id = dict(session_row).get("created_by_profile_id")
    try:
        if session_profile_id is not None and profile_id is not None:
            return int(session_profile_id) == int(profile_id)
    except (TypeError, ValueError):
        pass

    profile_name = (active_profile.get("name") or "").strip().lower()
    builder_name = _resolve_session_builder_name(session_row).strip().lower()
    return bool(profile_name and builder_name and profile_name == builder_name)


def _session_page_or_redirect(session_id):
    row = db.get_session(session_id)
    if not row:
        return None, ("Session not found", 404)
    brand = (dict(row).get("brand") or "bigtex").strip().lower() or "bigtex"
    active_profile = _get_active_profile()
    if not active_profile:
        _set_account_notice("Select an account to continue.", level="warning")
        next_url = request.full_path if request.query_string else request.path
        return None, redirect(url_for("prograde.account_landing", brand=brand, next=next_url))
    if not _can_access_session(row, active_profile):
        _set_account_notice("Planner accounts can only open their own sessions.", level="error")
        return None, redirect(url_for("prograde.sessions", brand=brand))
    return row, None


def _default_carrier_type_for_brand(_brand: str) -> str:
    # Both PJ and Big Tex currently run as 53' step deck in this workflow.
    step_deck = db.get_carrier_config("53_step_deck")
    if step_deck:
        return "53_step_deck"
    # Fallback for safety if seed data is incomplete.
    carriers = db.get_carrier_configs()
    if carriers:
        return carriers[0]["carrier_type"]
    return "53_step_deck"


def _normalize_zone_for_brand(brand: str, zone: str) -> str:
    """Map incoming/legacy deck zone values to current per-brand zone names."""
    zone_value = (zone or "").strip()
    if not zone_value:
        return zone_value
    brand_key = (brand or "").strip().lower()
    if brand_key != "bigtex":
        return zone_value
    legacy_map = {
        "stack_1": "lower_deck",
        "stack_2": "lower_deck",
        "stack_3": "upper_deck",
    }
    return legacy_map.get(zone_value, zone_value)


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


# -- Helpers ---------------------------------------------------------------

def _build_bt_sku_map():
    return {r["item_number"]: dict(r) for r in db.get_bigtex_skus()}


def _build_position_view(pos, brand, bt_sku_map=None, height_ref=None):
    """Enrich a position row with display fields: footprint, height, sku metadata."""
    p = dict(pos)
    p["gn_axle_dropped"] = bool(p.get("gn_axle_dropped"))
    p["is_rotated"] = bool(p.get("is_rotated"))
    p["violation"] = False
    p["is_top_layer"] = False
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
    p["item_code"] = (
        _pj_picker_short_item_code(sku)
        if brand == "pj"
        else str(p.get("item_number") or "").strip()
    )
    p["gn_axle_droppable"] = bool(sku.get("gn_axle_droppable"))
    p["can_nest_inside_dump"] = bool(sku.get("can_nest_inside_dump"))
    if brand == "pj":
        bed_measured = round((sku.get("bed_length_measured") or sku.get("bed_length_stated") or 0), 2)
        tongue_feet = round((sku.get("tongue_feet") or 0), 2)
        footprint_total = round(float(sku.get("total_footprint") or 0.0), 2)
        default_tongue_profile = _normalize_pj_tongue_profile(_pj_picker_tongue_profile(sku), default="standard")
        override_tongue_profile = _extract_tongue_override_reason(p.get("override_reason"))
        render_tongue_profile = override_tongue_profile or default_tongue_profile
        render_tongue_ft = _pj_render_tongue_length_ft(render_tongue_profile, tongue_feet)
        deck_profile = _pj_render_deck_profile(sku)
        default_dump_height_ft = _normalize_pj_dump_height_ft(sku.get("dump_side_height_ft"), default=None)
        override_dump_height_ft = _extract_dump_height_override_reason(p.get("override_reason"))
        selected_dump_height_ft = (
            override_dump_height_ft
            if override_dump_height_ft is not None
            else default_dump_height_ft
        ) if deck_profile == "dump" else None
        if bed_measured > 0:
            deck_length_ft = max(bed_measured, 1.0)
        elif footprint_total > 0:
            deck_length_ft = max(round(footprint_total - render_tongue_ft, 2), 1.0)
        else:
            deck_length_ft = 1.0
        render_footprint_ft = round(deck_length_ft + render_tongue_ft, 2)
        p["bed_length_measured"] = bed_measured
        p["tongue_feet"] = tongue_feet
        p["dump_side_height_ft"] = selected_dump_height_ft
        p["selected_dump_height_ft"] = selected_dump_height_ft
        p["bed_length"] = bed_measured
        p["tongue_length"] = tongue_feet
        p["tongue_length_actual"] = tongue_feet
        p["render_tongue_profile"] = render_tongue_profile
        p["render_tongue_length_ft"] = round(render_tongue_ft, 2)
        p["deck_profile"] = deck_profile
        p["deck_length_ft"] = round(deck_length_ft, 2)
        p["render_footprint_ft"] = render_footprint_ft
        p["dump_door_removed"] = _extract_dump_door_removed_reason(p.get("override_reason"))
        p["gn_crisscross"] = _extract_gn_crisscross_override_reason(p.get("override_reason"))
    elif brand == "bigtex":
        p["bed_length"] = round((sku.get("bed_length") or 0), 2)
        p["tongue_length"] = round((sku.get("tongue") or 0), 2)
        p["bed_length_measured"] = p["bed_length"]
        p["tongue_feet"] = p["tongue_length"]
        p["dump_side_height_ft"] = None
        p["selected_dump_height_ft"] = None
        p["render_tongue_profile"] = "standard"
        p["render_tongue_length_ft"] = p["tongue_length"]
        p["tongue_length_actual"] = p["tongue_length"]
        p["deck_profile"] = "flat"
        p["deck_length_ft"] = p["bed_length"]
        p["render_footprint_ft"] = p["footprint"]
        p["dump_door_removed"] = False
        p["gn_crisscross"] = False
    else:
        p["bed_length"] = 0
        p["tongue_length"] = 0
        p["bed_length_measured"] = 0
        p["tongue_feet"] = 0
        p["dump_side_height_ft"] = None
        p["selected_dump_height_ft"] = None
        p["render_tongue_profile"] = "standard"
        p["render_tongue_length_ft"] = 0
        p["tongue_length_actual"] = 0
        p["deck_profile"] = "flat"
        p["deck_length_ft"] = 0
        p["render_footprint_ft"] = 0
        p["dump_door_removed"] = False
        p["gn_crisscross"] = False

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
    if brand == "pj" and p.get("deck_profile") == "dump" and p.get("selected_dump_height_ft") is not None:
        stacked_dump_height_ft = pj_dump_stacked_height_ft(p.get("selected_dump_height_ft"))
        if stacked_dump_height_ft is not None:
            p["height"] = stacked_dump_height_ft
            p["stacking_height_ft"] = stacked_dump_height_ft
        else:
            p["height"] = p["selected_dump_height_ft"]
    base_component_height_ft = round(float(p.get("height") or 0.0), 2)
    if (
        brand == "pj"
        and p.get("render_tongue_profile") == "gooseneck"
        and p.get("deck_profile") != "dump"
    ):
        # GN neck profile is modeled as a fixed 6.0' vertical envelope.
        p["height"] = 6.0
    p["deck_component_height_ft"] = (
        base_component_height_ft
        if base_component_height_ft > 0
        else round(float(p.get("height") or 0.0), 2)
    )
    p["deck_height_ft"] = round(float(p.get("height") or 0.0), 2)
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


def _as_float(value, default=0.0):
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def compute_column_x_positions(zone_columns, zone_caps=None, right_anchor_zones=None):
    """
    For each zone, walk columns in sequence order.
    x_ft is local to the zone origin and advances by the column render footprint.
    Returns dict[(zone, sequence)] -> local_x_ft.
    """
    x_positions = {}
    right_anchor = set(right_anchor_zones or [])
    for zone in ("lower_deck", "upper_deck"):
        cols = (zone_columns or {}).get(zone) or {}
        seqs = sorted(cols.keys())
        col_width_by_seq = {}
        total_used = 0.0
        for seq in seqs:
            col = cols.get(seq) or []
            col_footprint = 0.0
            for unit in col:
                footprint = _as_float(
                    unit.get("render_footprint_ft")
                    if unit.get("render_footprint_ft") is not None
                    else unit.get("footprint"),
                    0.0,
                )
                if footprint > col_footprint:
                    col_footprint = footprint
            col_footprint = max(col_footprint, 0.0)
            col_width_by_seq[int(seq)] = col_footprint
            total_used += col_footprint

        zone_cap = _as_float((zone_caps or {}).get(zone), total_used)
        if zone in right_anchor:
            # Keep upper-deck usage pinned to the trailer-end boundary.
            # Any overflow pushes left across the step (never right past trailer end).
            cursor = zone_cap - total_used
        else:
            cursor = 0.0
        for seq in seqs:
            x_positions[(zone, int(seq))] = round(cursor, 3)
            cursor += col_width_by_seq.get(int(seq), 0.0)
    return x_positions


def _column_base_dims(col, rear_pocket_len_ft=_REAR_POCKET_LEN_FT):
    """Return base-unit envelope/pocket geometry for a stacked column."""
    if not col:
        return {
            "deck_len_ft": 0.0,
            "left_tongue_ft": 0.0,
            "right_tongue_ft": 0.0,
            "rear_pocket_left_ft": 0.0,
            "rear_pocket_right_ft": 0.0,
            "full_span_ft": 0.0,
        }
    base = next((p for p in col if int(p.get("layer") or 0) == 1), col[0])
    deck_len_ft = _as_float(base.get("deck_length_ft"), _as_float(base.get("bed_length"), 0.0))
    tongue_len_ft = _as_float(
        base.get("occupied_tongue_length_ft"),
        _as_float(
            base.get("render_tongue_length_ft"),
            _as_float(base.get("tongue_length"), 0.0),
        ),
    )
    is_rotated = bool(base.get("is_rotated"))
    left_tongue_ft = tongue_len_ft if is_rotated else 0.0
    right_tongue_ft = 0.0 if is_rotated else tongue_len_ft
    rear_pocket_ft = min(max(deck_len_ft, 0.0), _as_float(rear_pocket_len_ft, _REAR_POCKET_LEN_FT))
    return {
        "deck_len_ft": max(deck_len_ft, 0.0),
        "left_tongue_ft": max(left_tongue_ft, 0.0),
        "right_tongue_ft": max(right_tongue_ft, 0.0),
        "rear_pocket_left_ft": 0.0 if is_rotated else rear_pocket_ft,
        "rear_pocket_right_ft": rear_pocket_ft if is_rotated else 0.0,
        "full_span_ft": max(deck_len_ft + left_tongue_ft + right_tongue_ft, 0.0),
    }


def _unit_tongue_length_ft(unit, prefer_occupied=True):
    if prefer_occupied:
        return _as_float(
            unit.get("occupied_tongue_length_ft"),
            _as_float(
                unit.get("render_tongue_length_ft"),
                _as_float(unit.get("tongue_length"), 0.0),
            ),
        )
    return _as_float(
        unit.get("render_tongue_length_ft"),
        _as_float(unit.get("tongue_length"), 0.0),
    )


def _is_gooseneck_render_profile(unit):
    return str(unit.get("render_tongue_profile") or "").strip().lower() == "gooseneck"


def _lower_column_layer_start_offsets(sorted_col, prefer_occupied_tongue=True):
    """
    Return per-layer local deck starts (ft) for lower-deck stacked rendering.

    Utilities stacked above a gooseneck host are shifted so their tongue tip sits
    on the host deck edge on the tongue side (mirrors with rotation).
    """
    offsets = [0.0 for _ in sorted_col]
    if len(sorted_col) <= 1:
        return offsets

    for idx, unit in enumerate(sorted_col):
        if idx == 0:
            continue
        if str(unit.get("pj_category") or "").strip().lower() != "utility":
            continue
        if _is_gooseneck_render_profile(unit):
            continue

        host_idx = None
        for j in range(idx - 1, -1, -1):
            if _is_gooseneck_render_profile(sorted_col[j]):
                host_idx = j
                break
        if host_idx is None:
            continue

        host = sorted_col[host_idx]
        host_start_ft = offsets[host_idx]
        host_deck_len_ft = _as_float(host.get("deck_length_ft"), _as_float(host.get("bed_length"), 0.0))
        host_left_ft = host_start_ft
        host_right_ft = host_start_ft + host_deck_len_ft

        unit_tongue_ft = max(_unit_tongue_length_ft(unit, prefer_occupied=prefer_occupied_tongue), 0.0)
        unit_deck_len_ft = max(_as_float(unit.get("deck_length_ft"), _as_float(unit.get("bed_length"), 0.0)), 0.0)
        # Keep stacked utility tongues entirely on the non-gooseneck side of the
        # host GN wall plane (mirrors with rotation).
        wall_clearance_ft = max(_as_float(_GOOSENECK_WALL_CLEARANCE_FT, 0.0), 0.0)
        if bool(unit.get("is_rotated")):
            offsets[idx] = host_left_ft + wall_clearance_ft + unit_tongue_ft
        else:
            offsets[idx] = host_right_ft - wall_clearance_ft - (unit_deck_len_ft + unit_tongue_ft)

    return offsets


def _column_gooseneck_wall_x_local(col, start_local_ft):
    """
    Return the tongue-side GN wall plane (local x ft) for a lower-deck column.

    For rotated GN columns, the wall plane is deck start.
    For non-rotated GN columns, the wall plane is deck end.
    """
    if not col:
        return None
    base = next((p for p in col if int(p.get("layer") or 0) == 1), col[0])
    if not _is_gooseneck_render_profile(base):
        return None
    deck_len_ft = max(_as_float(base.get("deck_length_ft"), _as_float(base.get("bed_length"), 0.0)), 0.0)
    if bool(base.get("is_rotated")):
        return _as_float(start_local_ft, 0.0)
    return _as_float(start_local_ft, 0.0) + deck_len_ft


def _column_render_envelope_dims(col, zone=None):
    """
    Return rendered envelope dims across all units in a stacked column.

    `zone='lower_deck'` applies intra-stack utility-on-gooseneck tongue anchoring.
    """
    if not col:
        return {
            "left_tongue_ft": 0.0,
            "right_reach_ft": 0.0,
            "full_span_ft": 0.0,
        }

    sorted_col = sorted(col, key=lambda p: int(p.get("layer") or 0))
    local_starts = [0.0 for _ in sorted_col]
    if zone == "lower_deck":
        local_starts = _lower_column_layer_start_offsets(
            sorted_col,
            prefer_occupied_tongue=True,
        )

    min_left_ft = 0.0
    max_right_ft = 0.0
    for idx, unit in enumerate(sorted_col):
        start_ft = _as_float(local_starts[idx], 0.0)
        deck_len_ft = max(_as_float(unit.get("deck_length_ft"), _as_float(unit.get("bed_length"), 0.0)), 0.0)
        tongue_len_ft = max(_unit_tongue_length_ft(unit, prefer_occupied=True), 0.0)
        is_rotated = bool(unit.get("is_rotated"))

        left_edge_ft = start_ft - (tongue_len_ft if is_rotated else 0.0)
        right_edge_ft = start_ft + deck_len_ft + (0.0 if is_rotated else tongue_len_ft)

        min_left_ft = min(min_left_ft, left_edge_ft)
        max_right_ft = max(max_right_ft, right_edge_ft)

    left_tongue_ft = max(-min_left_ft, 0.0)
    right_reach_ft = max(max_right_ft, 0.0)
    full_span_ft = max(max_right_ft - min_left_ft, 0.0)
    return {
        "left_tongue_ft": round(left_tongue_ft, 3),
        "right_reach_ft": round(right_reach_ft, 3),
        "full_span_ft": round(full_span_ft, 3),
    }


def _column_has_stuffed_forward_tongue(col):
    """Return True when any non-rotated unit has occupied tongue shorter than rendered tongue."""
    for unit in (col or []):
        if bool(unit.get("is_rotated")):
            continue
        rendered_tongue_ft = _as_float(
            unit.get("render_tongue_length_ft"),
            _as_float(unit.get("tongue_length"), 0.0),
        )
        occupied_raw = unit.get("occupied_tongue_length_ft")
        if occupied_raw is None:
            continue
        occupied_tongue_ft = _as_float(occupied_raw, rendered_tongue_ft)
        if occupied_tongue_ft + 1e-9 < rendered_tongue_ft:
            return True
    return False


def _upper_column_intrusion_interval_on_lower(col, upper_start_local_ft, zone_origin_upper_ft, step_x_ft):
    """
    Project one upper-deck column into lower-deck x-space as a blocked interval.

    For seam-facing gooseneck columns (rotated upper units), use the gooseneck
    wall plane (deck start) as the left blocked edge instead of the tongue tip.
    """
    if not col:
        return None
    base_dims = _column_base_dims(col)
    render_dims = _column_render_envelope_dims(col, zone="upper_deck")
    col_right_global = (
        _as_float(zone_origin_upper_ft, 0.0)
        + _as_float(upper_start_local_ft, 0.0)
        + max(_as_float(base_dims.get("deck_len_ft"), 0.0), 0.0)
        + max(_as_float(base_dims.get("right_tongue_ft"), 0.0), 0.0)
    )
    upper_left_global = col_right_global - max(_as_float(render_dims.get("full_span_ft"), 0.0), 0.0)
    upper_right_global = col_right_global

    projected_left = max(min(upper_left_global, upper_right_global), 0.0)
    projected_right = min(max(upper_left_global, upper_right_global), _as_float(step_x_ft, 0.0))
    if projected_right - projected_left <= 1e-9:
        return None

    base = next((p for p in col if int(p.get("layer") or 0) == 1), col[0])
    if _is_gooseneck_render_profile(base) and bool(base.get("is_rotated")):
        # Seam-facing GN wall plane for rotated upper units is deck start.
        deck_start_global = (
            _as_float(zone_origin_upper_ft, 0.0)
            + _as_float(upper_start_local_ft, 0.0)
            + max(_as_float(base_dims.get("right_tongue_ft"), 0.0), 0.0)
        )
        projected_left = max(projected_left, min(deck_start_global, projected_right))
        if projected_right - projected_left <= 1e-9:
            return None

    return (projected_left, projected_right)


def _merge_intervals(intervals, eps=1e-9):
    normalized = []
    for raw_left, raw_right in (intervals or []):
        left = _as_float(raw_left, 0.0)
        right = _as_float(raw_right, 0.0)
        if right < left:
            left, right = right, left
        if right - left <= eps:
            continue
        normalized.append((left, right))
    if not normalized:
        return []
    normalized.sort(key=lambda pair: (pair[0], pair[1]))
    merged = [normalized[0]]
    for left, right in normalized[1:]:
        last_left, last_right = merged[-1]
        if left <= last_right + eps:
            merged[-1] = (last_left, max(last_right, right))
        else:
            merged.append((left, right))
    return merged


def _subtract_intervals(base_intervals, carve_intervals, eps=1e-9):
    base = _merge_intervals(base_intervals, eps=eps)
    carve = _merge_intervals(carve_intervals, eps=eps)
    if not base or not carve:
        return base

    result = []
    carve_idx = 0
    for base_left, base_right in base:
        cursor = base_left
        while carve_idx < len(carve) and carve[carve_idx][1] <= cursor + eps:
            carve_idx += 1

        idx = carve_idx
        while idx < len(carve):
            carve_left, carve_right = carve[idx]
            if carve_left >= base_right - eps:
                break
            if carve_right <= cursor + eps:
                idx += 1
                continue
            if carve_left > cursor + eps:
                result.append((cursor, min(carve_left, base_right)))
            cursor = max(cursor, carve_right)
            if cursor >= base_right - eps:
                break
            idx += 1

        if cursor < base_right - eps:
            result.append((cursor, base_right))

    return _merge_intervals(result, eps=eps)


def _cross_deck_dump_door_allowance_intervals(
    zone_cols,
    x_positions,
    zone_origin_x_ft,
    step_x_ft,
    lower_zone="lower_deck",
    upper_zone="upper_deck",
):
    """
    Return allowed lower/upper overlap windows (global x ft) for open dump-door insertion.

    This is intentionally narrow:
    - Only seam-interface columns (rightmost lower, leftmost upper).
    - Only when the upper interface base is a dump with door removed and rear facing seam.
    - Only allows lower tongue insertion up to rear-pocket length.
    """
    lower_cols = (zone_cols or {}).get(lower_zone) or {}
    upper_cols = (zone_cols or {}).get(upper_zone) or {}
    if not lower_cols or not upper_cols:
        return []

    lower_seq = max(lower_cols.keys())
    upper_seq = min(upper_cols.keys())
    lower_col = lower_cols.get(lower_seq) or []
    upper_col = upper_cols.get(upper_seq) or []
    if not lower_col or not upper_col:
        return []

    lower_base = next((p for p in lower_col if int(p.get("layer") or 0) == 1), lower_col[0])
    upper_base = next((p for p in upper_col if int(p.get("layer") or 0) == 1), upper_col[0])

    upper_is_dump = str(upper_base.get("deck_profile") or "").strip().lower() == "dump"
    upper_door_removed = bool(upper_base.get("dump_door_removed"))
    upper_rear_faces_seam = not bool(upper_base.get("is_rotated"))
    if not (upper_is_dump and upper_door_removed and upper_rear_faces_seam):
        return []

    lower_tongue_toward_seam_ft = 0.0
    if not bool(lower_base.get("is_rotated")):
        lower_tongue_toward_seam_ft = _as_float(
            lower_base.get("render_tongue_length_ft"),
            _as_float(lower_base.get("tongue_length"), 0.0),
        )

    upper_deck_len_ft = _as_float(
        upper_base.get("deck_length_ft"),
        _as_float(upper_base.get("bed_length"), 0.0),
    )
    insertion_window_ft = min(max(upper_deck_len_ft, 0.0), _REAR_POCKET_LEN_FT)
    insertable_lower_tongue_ft = max(
        max(lower_tongue_toward_seam_ft, 0.0) - _DUMP_DOOR_MIN_EXPOSED_TONGUE_FT,
        0.0,
    )
    allowance_ft = min(insertable_lower_tongue_ft, max(insertion_window_ft, 0.0))
    if allowance_ft <= 1e-9:
        return []

    upper_base_dims = _column_base_dims(upper_col)
    upper_render_dims = _column_render_envelope_dims(upper_col, zone=upper_zone)
    upper_start_local = _as_float((x_positions or {}).get(upper_zone, {}).get(int(upper_seq), 0.0), 0.0)
    col_right_global = (
        _as_float((zone_origin_x_ft or {}).get(upper_zone), step_x_ft)
        + upper_start_local
        + max(_as_float(upper_base_dims.get("deck_len_ft"), 0.0), 0.0)
        + max(_as_float(upper_base_dims.get("right_tongue_ft"), 0.0), 0.0)
    )
    upper_left_global = col_right_global - max(_as_float(upper_render_dims.get("full_span_ft"), 0.0), 0.0)
    projected_left = max(min(upper_left_global, col_right_global), 0.0)
    projected_right = min(max(upper_left_global, col_right_global), step_x_ft)
    if projected_right - projected_left <= 1e-9:
        return []

    allowance_right = min(projected_left + allowance_ft, projected_right)
    if allowance_right - projected_left <= 1e-9:
        return []
    return [(projected_left, allowance_right)]


def _apply_dump_door_tongue_stuffing(
    zone_cols,
    lower_zone="lower_deck",
    upper_zone="upper_deck",
):
    """
    For the seam-interface lower stack, cap forward tongue occupancy to the
    minimum exposed value when upper interface dump door is off.

    Rendering keeps full tongue length; this only changes packing/counted length.
    """
    lower_cols = (zone_cols or {}).get(lower_zone) or {}
    upper_cols = (zone_cols or {}).get(upper_zone) or {}
    if not lower_cols or not upper_cols:
        return False

    lower_seq = max(lower_cols.keys())
    upper_seq = min(upper_cols.keys())
    lower_col = lower_cols.get(lower_seq) or []
    upper_col = upper_cols.get(upper_seq) or []
    if not lower_col or not upper_col:
        return False

    upper_base = next((p for p in upper_col if int(p.get("layer") or 0) == 1), upper_col[0])
    upper_is_dump = str(upper_base.get("deck_profile") or "").strip().lower() == "dump"
    upper_door_removed = bool(upper_base.get("dump_door_removed"))
    upper_rear_faces_seam = not bool(upper_base.get("is_rotated"))
    if not (upper_is_dump and upper_door_removed and upper_rear_faces_seam):
        return False

    applied = False
    for unit in lower_col:
        if bool(unit.get("is_rotated")):
            continue
        original_tongue_ft = _as_float(
            unit.get("render_tongue_length_ft"),
            _as_float(unit.get("tongue_length"), 0.0),
        )
        stuffed_tongue_ft = min(original_tongue_ft, _DUMP_DOOR_MIN_EXPOSED_TONGUE_FT)
        deck_len_ft = _as_float(unit.get("deck_length_ft"), _as_float(unit.get("bed_length"), 0.0))
        prev_occupied_tongue = _as_float(unit.get("occupied_tongue_length_ft"), original_tongue_ft)
        prev_occupied_footprint = _as_float(unit.get("occupied_footprint_ft"), deck_len_ft + original_tongue_ft)
        unit["occupied_tongue_length_ft"] = round(stuffed_tongue_ft, 2)
        unit["occupied_footprint_ft"] = round(deck_len_ft + stuffed_tongue_ft, 2)
        if (
            abs(prev_occupied_tongue - stuffed_tongue_ft) > 1e-9
            or abs(prev_occupied_footprint - (deck_len_ft + stuffed_tongue_ft)) > 1e-9
        ):
            applied = True
    return applied


def _category_key_for_position(pos):
    raw = (pos.get("pj_category") or pos.get("mcat") or "unknown")
    if pos.get("mcat"):
        raw = db.normalize_bigtex_mcat(raw)
    key = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
    return key or "unknown"


def _category_label_for_key(key):
    if not key:
        return "Unknown"
    return str(key).replace("_", " ").strip().title()


def _build_category_visuals(enriched_positions):
    color_cycle = [
        "#22c55e",
        "#3b82f6",
        "#06b6d4",
        "#a78bfa",
        "#f59e0b",
        "#ef4444",
        "#f43f5e",
        "#14b8a6",
        "#38bdf8",
        "#f97316",
        "#84cc16",
        "#eab308",
    ]
    seen = set()
    ordered_keys = []
    for p in enriched_positions or []:
        key = _category_key_for_position(p)
        if key in seen:
            continue
        seen.add(key)
        ordered_keys.append(key)

    if not ordered_keys:
        ordered_keys = ["unknown"]

    palette = {}
    for idx, key in enumerate(ordered_keys):
        palette[key] = color_cycle[idx % len(color_cycle)]
    palette.setdefault("unknown", "#64748b")

    legend = [(key, _category_label_for_key(key)) for key in ordered_keys]
    return palette, legend


def _build_manifest_rows(enriched_positions):
    manifest_rows = []
    for pos in (enriched_positions or []):
        sku = (pos.get("item_number") or "").strip()
        if not sku:
            continue
        zone = str(pos.get("deck_zone") or "").strip().replace("_", " ").title()
        row = {
            "unit_number": int(pos.get("unit_sequence_num") or 0),
            "item_number": sku,
            "item_code": str(pos.get("item_code") or sku).strip(),
            "description": (pos.get("description") or "").strip(),
            "bed_length": round(float(pos.get("bed_length") or 0), 2),
            "tongue_length": round(float(pos.get("tongue_length") or 0), 2),
            "height_each": round(float(pos.get("stacking_height_ft", pos.get("height") or 0)), 2),
            "total_footprint": round(float(pos.get("render_footprint_ft") or pos.get("footprint") or 0), 2),
            "zones": zone,
        }
        manifest_rows.append(row)

    manifest_rows.sort(
        key=lambda r: (
            int(r.get("unit_number") or 0) if int(r.get("unit_number") or 0) > 0 else 999999,
            r.get("item_code") or r.get("item_number") or "",
        )
    )
    for idx, row in enumerate(manifest_rows, start=1):
        if int(row.get("unit_number") or 0) <= 0:
            row["unit_number"] = idx
    return manifest_rows


def _assign_unit_sequence_numbers(enriched_positions):
    ordered = sorted(
        (enriched_positions or []),
        key=lambda p: (
            0 if str((p or {}).get("added_at") or "").strip() else 1,
            str((p or {}).get("added_at") or ""),
            str((p or {}).get("position_id") or ""),
        ),
    )
    sequence_by_position = {}
    for idx, pos in enumerate(ordered, start=1):
        pid = str((pos or {}).get("position_id") or "")
        if pid:
            sequence_by_position[pid] = idx
    for pos in (enriched_positions or []):
        pid = str((pos or {}).get("position_id") or "")
        pos["unit_sequence_num"] = int(sequence_by_position.get(pid, 0))


def _format_session_display_id(session):
    session_dict = dict(session or {})
    brand_key = (session_dict.get("brand") or "").strip().lower()
    prefix = "PJ" if brand_key == "pj" else "BT"
    created_at_raw = (session_dict.get("created_at") or "").strip()
    date_label = "00-00-00"
    if created_at_raw:
        try:
            dt = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
            date_label = dt.strftime("%m-%d-%y")
        except ValueError:
            pass
    sequence = db.get_session_daily_sequence(
        session_dict.get("session_id"),
        brand_key,
        created_at_raw,
    )
    return f"{prefix} {date_label} #{sequence}"


def _normalize_pj_picker_category(raw_category):
    category = str(raw_category or "").strip().lower()
    if not category:
        return "uncategorized"
    if category in _PJ_DUMP_CATEGORIES:
        return "dump"
    return _PJ_PICKER_COLLAPSE_MAP.get(category, category)


def _pj_picker_category_label(category):
    key = str(category or "").strip().lower()
    if not key:
        return "Uncategorized"
    return _PJ_PICKER_CATEGORY_LABELS.get(key, _category_label_for_key(key))


def _pj_picker_tongue_profile(sku):
    sku_map = _row_to_dict(sku)
    category = str((sku_map or {}).get("pj_category") or "").strip().lower()
    model = str((sku_map or {}).get("model") or "").strip().upper()
    model_prefix = "".join(ch for ch in model if ch.isalnum())[:2]
    if category in _PJ_GOOSENECK_CATEGORIES or model_prefix in _PJ_GOOSENECK_MODEL_PREFIXES:
        return "gooseneck"
    return "standard"


def _position_uses_pj_gooseneck(position, sku=None):
    pos = _row_to_dict(position)
    override_mode = _extract_tongue_override_reason(pos.get("override_reason"))
    if override_mode == "gooseneck":
        return True
    if override_mode == "standard":
        return False
    return _pj_picker_tongue_profile(sku or {}) == "gooseneck"


def _apply_pj_gn_crisscross_for_column(session_id, deck_zone, sequence, preferred_position_id=None):
    if not session_id or not deck_zone or sequence is None:
        return False
    try:
        target_sequence = int(sequence)
    except (TypeError, ValueError):
        return False

    rows = []
    for row in db.get_positions(session_id):
        pos = _row_to_dict(row)
        if pos.get("deck_zone") != deck_zone:
            continue
        if int(pos.get("sequence") or 0) != target_sequence:
            continue
        rows.append(pos)
    if len(rows) < 2:
        return False
    rows.sort(key=lambda row: int(row.get("layer") or 0))

    sku_cache = {}

    def _sku_for(position):
        item_number = str(position.get("item_number") or "").strip()
        if not item_number:
            return {}
        if item_number not in sku_cache:
            sku_cache[item_number] = dict(db.get_pj_sku(item_number) or {})
        return sku_cache[item_number]

    def _is_gn(position):
        return _position_uses_pj_gooseneck(position, _sku_for(position))

    gooseneck_rows = [row for row in rows if _is_gn(row)]
    if len(gooseneck_rows) < 2:
        return False

    preferred = None
    if preferred_position_id:
        preferred = next(
            (row for row in rows if str(row.get("position_id") or "") == str(preferred_position_id)),
            None,
        )
    if preferred is None or not _is_gn(preferred):
        preferred = gooseneck_rows[-1]

    host = None
    for row in reversed(rows):
        if row.get("position_id") == preferred.get("position_id"):
            continue
        if _is_gn(row):
            host = row
            break
    if host is None:
        return False

    applied = False
    preferred_rotated = bool(preferred.get("is_rotated"))
    host_rotated = bool(host.get("is_rotated"))
    if preferred_rotated != host_rotated:
        return False

    for row in (preferred, host):
        existing_override = row.get("override_reason")
        updated_override = _build_gn_crisscross_override_reason(True, existing_override)
        if updated_override != existing_override:
            db.update_position_field(row["position_id"], "override_reason", updated_override)
            applied = True

    return applied


def _pj_render_deck_profile(sku):
    sku_map = _row_to_dict(sku)
    category = str((sku_map or {}).get("pj_category") or "").strip().lower()
    return "dump" if category in _PJ_DUMP_CATEGORIES else "flat"


def _pj_render_tongue_length_ft(tongue_profile, actual_tongue_ft=0.0):
    mode = _normalize_pj_tongue_profile(tongue_profile, default="standard")
    if mode == "gooseneck":
        return 9.0
    return round(max(_as_float(actual_tongue_ft, 0.0), 0.0), 2)


def _pj_picker_model_code(sku):
    sku_map = _row_to_dict(sku)
    model = "".join(ch for ch in str((sku_map or {}).get("model") or "").strip().upper() if ch.isalnum())
    if model:
        return model[:2]
    item = "".join(ch for ch in str((sku_map or {}).get("item_number") or "").strip().upper() if ch.isalnum())
    return item[:2]


def _pj_picker_short_item_code(sku):
    sku_map = _row_to_dict(sku)
    model = str((sku_map or {}).get("model") or "").strip().upper()
    model_prefix = "".join(ch for ch in model if ch.isalnum())[:2]
    item = "".join(ch for ch in str((sku_map or {}).get("item_number") or "").strip().upper() if ch.isalnum())
    if not item:
        return ""
    if not model_prefix:
        model_prefix = item[:2]
    tail = item
    if model_prefix and item.startswith(model_prefix):
        tail = item[len(model_prefix):]
    tail = re.sub(r"^[A-Z]+", "", tail)
    digits = "".join(ch for ch in tail if ch.isdigit())
    if not digits:
        digits = "".join(ch for ch in item if ch.isdigit())
    model_code = _pj_picker_model_code(sku_map)
    category = str((sku_map or {}).get("pj_category") or "").strip().lower()
    if (
        len(digits) >= 3
        and digits.startswith("2")
        and (model_code in {"C4", "C5"} or category == "utility" or model_code.startswith("U"))
    ):
        digits = digits[1:]
    if len(digits) < 2:
        return item
    return f"{model_prefix}{digits[:2]}"


def _build_pj_picker_skus():
    height_ref = db.get_pj_height_ref_dict()
    picker_rows = []
    for row in db.get_pj_skus():
        sku = dict(row)
        picker_category = _normalize_pj_picker_category(sku.get("pj_category"))
        cat_ref = height_ref.get(sku.get("pj_category"), {}) if height_ref else {}
        deck_height = float(cat_ref.get("height_top_ft") or cat_ref.get("height_mid_ft") or 0.0)
        deck_length = float(sku.get("bed_length_measured") or sku.get("bed_length_stated") or 0.0)
        deck_profile = _pj_render_deck_profile(sku)
        dump_default_height_ft = _normalize_pj_dump_height_ft(sku.get("dump_side_height_ft"), default=None)
        if deck_profile == "dump" and dump_default_height_ft is not None:
            deck_height = float(dump_default_height_ft)
        sku["picker_category"] = picker_category
        sku["picker_category_label"] = _pj_picker_category_label(picker_category)
        sku["picker_tongue_profile"] = _normalize_pj_tongue_profile(_pj_picker_tongue_profile(sku), default="standard")
        sku["picker_model_code"] = _pj_picker_model_code(sku)
        sku["picker_item_code"] = _pj_picker_short_item_code(sku)
        sku["picker_deck_profile"] = deck_profile
        sku["picker_is_dump"] = deck_profile == "dump"
        sku["picker_dump_height_ft"] = dump_default_height_ft
        sku["deck_length_ft"] = round(deck_length, 2)
        sku["deck_height_ft"] = round(deck_height, 2)
        picker_rows.append(sku)
    return picker_rows


def _build_bt_inventory_gap_data(total_footprint, carrier_total_length):
    remaining_ft_raw = round(float(carrier_total_length or 0) - float(total_footprint or 0), 2)
    remaining_ft = max(remaining_ft_raw, 0.0)
    upload_meta = db.get_bt_inventory_upload_meta()
    snapshot_rows = db.get_bt_inventory_snapshot_rows(limit=500)
    rows = []

    for row in snapshot_rows:
        available = int(row["available_count"] or 0)
        if available <= 0:
            continue

        footprint_each = round(float(row["sku_total_footprint"] or 0.0), 2)
        fits_gap = footprint_each > 0 and footprint_each <= remaining_ft + 1e-9
        max_fit_qty = int(remaining_ft // footprint_each) if footprint_each > 0 else 0
        suggested_qty = min(available, max_fit_qty) if fits_gap else 0
        suggested_fill_ft = round(suggested_qty * footprint_each, 2)
        gap_after_fill_ft = round(max(remaining_ft - suggested_fill_ft, 0.0), 2)

        rows.append(
            {
                "item_number": row["item_number"],
                "model": row["sku_model"] or "",
                "mcat": db.normalize_bigtex_mcat(row["sku_mcat"] or ""),
                "footprint_each": footprint_each,
                "total_count": int(row["total_count"] or 0),
                "available_count": available,
                "assigned_count": int(row["assigned_count"] or 0),
                "built_count": int(row["built_count"] or 0),
                "future_build_count": int(row["future_build_count"] or 0),
                "available_built_count": int(row["available_built_count"] or 0),
                "available_future_count": int(row["available_future_count"] or 0),
                "fits_gap": fits_gap,
                "suggested_qty": suggested_qty,
                "suggested_fill_ft": suggested_fill_ft,
                "gap_after_fill_ft": gap_after_fill_ft,
                "is_unmapped": not bool(row["sku_model"] or row["sku_mcat"] or row["sku_total_footprint"]),
            }
        )

    rows.sort(
        key=lambda r: (
            0 if r["fits_gap"] else 1,
            -int(r["suggested_qty"]),
            -int(r["available_count"]),
            r["item_number"],
        )
    )

    total_available_units = sum(r["available_count"] for r in rows)
    return {
        "remaining_ft": remaining_ft,
        "remaining_ft_raw": remaining_ft_raw,
        "rows": rows,
        "total_available_units": total_available_units,
        "upload_meta": dict(upload_meta) if upload_meta else None,
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
    carrier_map = dict(carrier) if carrier else {}
    height_ref  = db.get_pj_height_ref_dict() if brand == "pj" else {}
    bt_sku_map  = _build_bt_sku_map() if brand == "bigtex" else None
    bt_configs  = {r["config_id"]: dict(r) for r in db.get_bt_stack_configs()} if brand == "bigtex" else {}

    enriched = []
    for p in positions:
        pv = _build_position_view(p, brand, bt_sku_map, height_ref)
        pv["deck_zone"] = _normalize_zone_for_brand(brand, pv.get("deck_zone"))
        enriched.append(pv)
    pj_sku_map = {}
    if brand == "pj":
        for p in enriched:
            item_number = str(p.get("item_number") or "").strip()
            if not item_number or item_number in pj_sku_map:
                continue
            pj_sku_map[item_number] = dict(db.get_pj_sku(item_number) or {})

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
            if zone_cols[z][seq]:
                col_rows = zone_cols[z][seq]
                top_layer = max(int(p["layer"]) for p in col_rows)
                gooseneck_flags = []
                for p in col_rows:
                    p["is_top_layer"] = int(p["layer"]) == top_layer
                    p["gn_crisscross"] = False
                    if brand != "pj":
                        continue
                    p["true_height_ft"] = round(
                        _as_float(
                            p.get("true_height_ft"),
                            _as_float(
                                p.get("deck_component_height_ft"),
                                _as_float(p.get("height"), 0.0),
                            ),
                        ),
                        2,
                    )
                    gooseneck_flags.append(_is_gooseneck_render_profile(p))

                if brand == "pj":
                    for idx, p in enumerate(col_rows):
                        sku = pj_sku_map.get(p.get("item_number")) or {}
                        stacked_height_ft = None

                        is_gooseneck = gooseneck_flags[idx] if idx < len(gooseneck_flags) else False
                        has_gooseneck_above = (
                            any(gooseneck_flags[idx + 1:])
                            if idx + 1 < len(gooseneck_flags)
                            else False
                        )
                        if is_gooseneck:
                            if has_gooseneck_above:
                                stacked_height_ft = _as_float(
                                    p.get("true_height_ft"),
                                    _as_float(p.get("deck_component_height_ft"), _as_float(p.get("height"), 0.0)),
                                )
                            else:
                                stacked_height_ft = 6.0
                        else:
                            non_dump_stack_height_ft = pj_non_dump_stacking_height_ft(
                                p,
                                sku,
                                p["is_top_layer"],
                            )
                            if non_dump_stack_height_ft is not None:
                                stacked_height_ft = _as_float(non_dump_stack_height_ft, 0.0)

                        if stacked_height_ft is None:
                            continue
                        stacked_height_ft = round(_as_float(stacked_height_ft, 0.0), 2)
                        visual_height_ft = round(
                            _as_float(
                                p.get("true_height_ft"),
                                _as_float(
                                    p.get("deck_component_height_ft"),
                                    _as_float(p.get("height"), 0.0),
                                ),
                            ),
                            2,
                        )
                        p["stacking_height_ft"] = stacked_height_ft
                        p["height"] = stacked_height_ft
                        if is_gooseneck:
                            # GN height is recorded as a 6' envelope for stacking/clearance,
                            # but deck body rendering stays at native unit height.
                            p["deck_component_height_ft"] = visual_height_ft
                            p["deck_height_ft"] = visual_height_ft
                        else:
                            p["deck_component_height_ft"] = stacked_height_ft
                            p["deck_height_ft"] = stacked_height_ft
            if brand == "pj" and zone_cols[z][seq]:
                col_rows = zone_cols[z][seq]
                for idx in range(1, len(col_rows)):
                    lower = col_rows[idx - 1]
                    upper = col_rows[idx]
                    lower_is_gn = (lower.get("render_tongue_profile") or "standard") == "gooseneck"
                    upper_is_gn = (upper.get("render_tongue_profile") or "standard") == "gooseneck"
                    if not (lower_is_gn and upper_is_gn):
                        continue
                    if bool(lower.get("is_rotated")) != bool(upper.get("is_rotated")):
                        continue
                    lower["gn_crisscross"] = True
                    upper["gn_crisscross"] = True
    if brand == "pj":
        _apply_dump_door_tongue_stuffing(zone_cols, lower_zone="lower_deck", upper_zone="upper_deck")

    lower_left_overhang_ft = 0.0

    # Per-zone length (sum of base-unit footprints by column, before cross-deck adjustments)
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
                    total += _as_float(base.get("render_footprint_ft"), _as_float(base.get("footprint"), 0.0))
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
    zone_blocked_ft = {z: 0.0 for z in zones}
    length_metrics = {}

    if brand == "pj":
        pj_skus = dict(pj_sku_map)
        offsets = db.get_pj_offsets_dict()
        length_metrics = compute_pj_length_metrics(
            positions,
            skus=pj_skus,
            offsets=offsets,
            lower_cap_ft=float(z_caps.get("lower_deck") or 41.0),
            upper_cap_ft=float(z_caps.get("upper_deck") or 12.0),
            height_ref=height_ref,
        )
    elif brand == "bigtex":
        bt_skus = {p["item_number"]: dict(db.get_bigtex_sku(p["item_number"]) or {}) for p in positions}
        length_metrics = compute_bt_length_metrics(
            positions,
            sku_map=bt_skus,
            lower_cap_ft=float(z_caps.get("lower_deck") or 41.0),
            upper_cap_ft=float(z_caps.get("upper_deck") or 12.0),
        )

    if length_metrics:
        zone_blocked_ft["lower_deck"] = float(length_metrics.get("blocked_lower_ft") or 0.0)
        zone_blocked_ft["upper_deck"] = float(length_metrics.get("blocked_upper_ft") or 0.0)
        # Keep rendered/labelled deck usage tied to physical occupied footprint only.
        # Step-seam blocked distance is retained separately in zone_blocked_ft for rule diagnostics.

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

    trailer_total_len_ft = _as_float(carrier_map.get("total_length_ft"), 53.0)
    lower_deck_len_ft = _as_float(carrier_map.get("lower_deck_length_ft"), _as_float(z_caps.get("lower_deck"), 41.5))
    upper_deck_len_ft = _as_float(carrier_map.get("upper_deck_length_ft"), _as_float(z_caps.get("upper_deck"), 11.5))
    lower_surface_ft = _as_float(carrier_map.get("lower_deck_ground_height_ft"), 3.5)
    upper_surface_ft = _as_float(carrier_map.get("upper_deck_ground_height_ft"), 5.0)
    max_height_ft = _as_float(carrier_map.get("max_height_ft"), 13.5)
    step_x_ft = lower_deck_len_ft
    zone_origin_x_ft = {
        "lower_deck": 0.0,
        "upper_deck": step_x_ft,
    }
    zone_surface_ft = {
        "lower_deck": lower_surface_ft,
        "upper_deck": upper_surface_ft,
    }

    anchor_caps = dict(z_caps or {})
    anchor_caps["upper_deck"] = max(trailer_total_len_ft - zone_origin_x_ft.get("upper_deck", step_x_ft), 0.0)
    x_positions_raw = compute_column_x_positions(
        zone_cols,
        zone_caps=anchor_caps,
        right_anchor_zones={"upper_deck"},
    )
    x_positions = {z: {} for z in zones}
    for (zone, seq), x_ft in x_positions_raw.items():
        if zone in x_positions:
            x_positions[zone][int(seq)] = round(_as_float(x_ft, 0.0), 3)

    # Same-zone tongue-under-rear overlap: when adjacent columns face each
    # other's rear pocket, reduce required horizontal spacing.
    lower_zone = "lower_deck"
    upper_zone = "upper_deck"
    if lower_zone in zone_cols and lower_zone in x_positions:
        seqs = sorted((zone_cols.get(lower_zone) or {}).keys())
        prev_dims = None
        prev_right_edge = None
        for idx, seq in enumerate(seqs):
            col = (zone_cols.get(lower_zone) or {}).get(seq) or []
            dims = _column_base_dims(col)
            current_start = _as_float(x_positions[lower_zone].get(int(seq), 0.0), 0.0)
            if idx > 0 and prev_dims is not None and prev_right_edge is not None:
                overlap_left_tongue = min(
                    max(dims.get("left_tongue_ft", 0.0), 0.0),
                    max(prev_dims.get("rear_pocket_right_ft", 0.0), 0.0),
                )
                overlap_prev_tongue = min(
                    max(prev_dims.get("right_tongue_ft", 0.0), 0.0),
                    max(dims.get("rear_pocket_left_ft", 0.0), 0.0),
                )
                current_start = prev_right_edge + max(dims.get("left_tongue_ft", 0.0), 0.0)
                current_start -= (overlap_left_tongue + overlap_prev_tongue)
                x_positions[lower_zone][int(seq)] = round(current_start, 3)

            render_dims = _column_render_envelope_dims(col, zone=lower_zone)
            prev_right_edge = current_start + max(_as_float(render_dims.get("right_reach_ft"), 0.0), 0.0)
            prev_dims = dims

    # Lower deck hard right barrier at the usable seam boundary: no deck or tongue may
    # extend past step_x on the lower deck. Resolve from right->left so
    # overflow shifts left without disturbing earlier stacks unless necessary.
    if lower_zone in zone_cols and lower_zone in x_positions:
        lower_blocked_by_upper_ft = max(_as_float(zone_blocked_ft.get("lower_deck"), 0.0), 0.0)
        lower_cap_local = max(
            (step_x_ft - lower_blocked_by_upper_ft) - _as_float(zone_origin_x_ft.get(lower_zone), 0.0),
            0.0,
        )
        # If upper-deck gooseneck walls allow deeper lower-deck placement than
        # coarse blocked-length math, honor the geometry-driven seam boundary.
        if upper_zone in zone_cols and upper_zone in x_positions:
            upper_intrusion_cap_intervals = []
            for seq in sorted((zone_cols.get(upper_zone) or {}).keys()):
                col = (zone_cols.get(upper_zone) or {}).get(seq) or []
                interval = _upper_column_intrusion_interval_on_lower(
                    col,
                    _as_float(x_positions[upper_zone].get(int(seq), 0.0), 0.0),
                    _as_float(zone_origin_x_ft.get(upper_zone), step_x_ft),
                    step_x_ft,
                )
                if interval is not None:
                    upper_intrusion_cap_intervals.append(interval)
            merged_cap_intrusion = _merge_intervals(upper_intrusion_cap_intervals)
            if merged_cap_intrusion:
                lower_cap_local = max(
                    lower_cap_local,
                    min(_as_float(left, step_x_ft) for left, _ in merged_cap_intrusion),
                )
        # Keep a tiny visual buffer so tongue strokes do not appear to cross the step wall.
        next_start_limit = max(lower_cap_local - 0.08, 0.0)
        for seq in sorted((zone_cols.get(lower_zone) or {}).keys(), reverse=True):
            col = (zone_cols.get(lower_zone) or {}).get(seq) or []
            render_dims = _column_render_envelope_dims(col, zone=lower_zone)
            col_right_extent = max(_as_float(render_dims.get("right_reach_ft"), 0.0), 0.0)

            current_start = _as_float(x_positions[lower_zone].get(int(seq), 0.0), 0.0)
            max_start = next_start_limit - col_right_extent
            adjusted_start = min(current_start, max_start)
            x_positions[lower_zone][int(seq)] = round(adjusted_start, 3)
            next_start_limit = adjusted_start

        # Re-pack left->right after seam clamping so rear-pocket overlap can be
        # fully realized while still respecting the hard right boundary.
        seqs = sorted((zone_cols.get(lower_zone) or {}).keys())
        prev_dims = None
        prev_right_edge = None
        for idx, seq in enumerate(seqs):
            col = (zone_cols.get(lower_zone) or {}).get(seq) or []
            dims = _column_base_dims(col)
            current_start = _as_float(x_positions[lower_zone].get(int(seq), 0.0), 0.0)
            if idx > 0 and prev_dims is not None and prev_right_edge is not None:
                overlap_left_tongue = min(
                    max(dims.get("left_tongue_ft", 0.0), 0.0),
                    max(prev_dims.get("rear_pocket_right_ft", 0.0), 0.0),
                )
                overlap_prev_tongue = min(
                    max(prev_dims.get("right_tongue_ft", 0.0), 0.0),
                    max(dims.get("rear_pocket_left_ft", 0.0), 0.0),
                )
                desired_start = prev_right_edge + max(dims.get("left_tongue_ft", 0.0), 0.0)
                desired_start -= (overlap_left_tongue + overlap_prev_tongue)

                render_dims = _column_render_envelope_dims(col, zone=lower_zone)
                col_right_extent = max(_as_float(render_dims.get("right_reach_ft"), 0.0), 0.0)
                max_start = max((lower_cap_local - 0.08) - col_right_extent, 0.0)
                current_start = min(desired_start, max_start)
                x_positions[lower_zone][int(seq)] = round(current_start, 3)

            render_dims = _column_render_envelope_dims(col, zone=lower_zone)
            prev_right_edge = current_start + max(_as_float(render_dims.get("right_reach_ft"), 0.0), 0.0)
            prev_dims = dims

        # Final lower-deck right snap: shift the whole lower cluster right so
        # the rightmost stack sits at the usable lower boundary.
        lower_max_right = 0.0
        for seq in sorted((zone_cols.get(lower_zone) or {}).keys()):
            col = (zone_cols.get(lower_zone) or {}).get(seq) or []
            render_dims = _column_render_envelope_dims(col, zone=lower_zone)
            start = _as_float(x_positions[lower_zone].get(int(seq), 0.0), 0.0)
            right = start + max(_as_float(render_dims.get("right_reach_ft"), 0.0), 0.0)
            if right > lower_max_right:
                lower_max_right = right
        lower_shift_delta = max((lower_cap_local - 0.08) - lower_max_right, 0.0)
        if lower_shift_delta > 1e-9:
            for seq in sorted((zone_cols.get(lower_zone) or {}).keys()):
                cur = _as_float(x_positions[lower_zone].get(int(seq), 0.0), 0.0)
                x_positions[lower_zone][int(seq)] = round(cur + lower_shift_delta, 3)

        # Minimize lower-deck overhang by pulling each left stack right until it
        # reaches the adjacent right stack's gooseneck wall plane (when present).
        # This allows tongue-region contact while still preventing deck overlap.
        seqs = sorted((zone_cols.get(lower_zone) or {}).keys())
        for idx in range(len(seqs) - 2, -1, -1):
            left_seq = int(seqs[idx])
            right_seq = int(seqs[idx + 1])
            left_col = (zone_cols.get(lower_zone) or {}).get(left_seq) or []
            right_col = (zone_cols.get(lower_zone) or {}).get(right_seq) or []
            if not left_col or not right_col:
                continue

            right_start = _as_float(x_positions[lower_zone].get(right_seq), 0.0)
            right_gn_wall_x = _column_gooseneck_wall_x_local(right_col, right_start)
            if right_gn_wall_x is None:
                continue

            left_start = _as_float(x_positions[lower_zone].get(left_seq), 0.0)
            left_dims = _column_render_envelope_dims(left_col, zone=lower_zone)
            left_right_reach_ft = max(_as_float(left_dims.get("right_reach_ft"), 0.0), 0.0)
            left_right_x = left_start + left_right_reach_ft
            target_left_right_x = right_gn_wall_x - _GOOSENECK_WALL_CLEARANCE_FT
            if left_right_x + 1e-9 >= target_left_right_x:
                continue

            delta = target_left_right_x - left_right_x
            x_positions[lower_zone][left_seq] = round(left_start + delta, 3)

    # Upper deck explicit right alignment to the usable right boundary.
    upper_zone = "upper_deck"
    if upper_zone in zone_cols and upper_zone in x_positions:
        upper_cap_local = max(
            trailer_total_len_ft - _as_float(zone_origin_x_ft.get(upper_zone), step_x_ft),
            0.0,
        )
        upper_max_right = 0.0
        for seq in sorted((zone_cols.get(upper_zone) or {}).keys()):
            col = (zone_cols.get(upper_zone) or {}).get(seq) or []
            dims = _column_base_dims(col)
            start = _as_float(x_positions[upper_zone].get(int(seq), 0.0), 0.0)
            right = start + max(dims.get("deck_len_ft", 0.0), 0.0) + max(dims.get("right_tongue_ft", 0.0), 0.0)
            if right > upper_max_right:
                upper_max_right = right
        upper_shift_delta = (upper_cap_local - 0.08) - upper_max_right
        if abs(upper_shift_delta) > 1e-9:
            for seq in sorted((zone_cols.get(upper_zone) or {}).keys()):
                cur = _as_float(x_positions[upper_zone].get(int(seq), 0.0), 0.0)
                x_positions[upper_zone][int(seq)] = round(cur + upper_shift_delta, 3)

    # Post-upper-alignment right pack:
    # After upper deck is snapped to trailer end, pull the lower cluster right
    # to the tightest allowable seam boundary from (a) blocked-length math and
    # (b) projected upper intrusion geometry (including GN wall planes).
    if (
        lower_zone in zone_cols
        and upper_zone in zone_cols
        and lower_zone in x_positions
        and upper_zone in x_positions
    ):
        post_upper_intrusion = []
        for seq in sorted((zone_cols.get(upper_zone) or {}).keys()):
            col = (zone_cols.get(upper_zone) or {}).get(seq) or []
            interval = _upper_column_intrusion_interval_on_lower(
                col,
                _as_float(x_positions[upper_zone].get(int(seq), 0.0), 0.0),
                _as_float(zone_origin_x_ft.get(upper_zone), step_x_ft),
                step_x_ft,
            )
            if interval is not None:
                post_upper_intrusion.append(interval)
        merged_post_upper_intrusion = _merge_intervals(post_upper_intrusion)
        if merged_post_upper_intrusion:
            lower_blocked_by_upper_ft = max(_as_float(zone_blocked_ft.get("lower_deck"), 0.0), 0.0)
            blocked_cap_local = max(
                (step_x_ft - lower_blocked_by_upper_ft) - _as_float(zone_origin_x_ft.get(lower_zone), 0.0),
                0.0,
            )
            intrusion_cap_local = min(_as_float(left, step_x_ft) for left, _ in merged_post_upper_intrusion)
            final_cap_local = max(blocked_cap_local, intrusion_cap_local)

            lower_max_right = 0.0
            for seq in sorted((zone_cols.get(lower_zone) or {}).keys()):
                col = (zone_cols.get(lower_zone) or {}).get(seq) or []
                dims = _column_render_envelope_dims(col, zone=lower_zone)
                start = _as_float(x_positions[lower_zone].get(int(seq), 0.0), 0.0)
                right = start + max(_as_float(dims.get("right_reach_ft"), 0.0), 0.0)
                if right > lower_max_right:
                    lower_max_right = right
            lower_shift_delta = max((final_cap_local - 0.08) - lower_max_right, 0.0)
            if lower_shift_delta > 1e-9:
                for seq in sorted((zone_cols.get(lower_zone) or {}).keys()):
                    cur = _as_float(x_positions[lower_zone].get(int(seq), 0.0), 0.0)
                    x_positions[lower_zone][int(seq)] = round(cur + lower_shift_delta, 3)

    # Cross-deck collision guard:
    # Lower-deck horizontal occupancy cannot intersect upper-deck occupied span
    # projected onto the lower deck when stack height exceeds step clearance.
    # Keep low-profile stacks (that fit under the step) eligible to remain under
    # upper overhang, and shift only interfering tall stacks left.
    if (
        lower_zone in zone_cols
        and upper_zone in zone_cols
        and lower_zone in x_positions
        and upper_zone in x_positions
    ):
        upper_intrusion_intervals = []
        for seq in sorted((zone_cols.get(upper_zone) or {}).keys()):
            col = (zone_cols.get(upper_zone) or {}).get(seq) or []
            interval = _upper_column_intrusion_interval_on_lower(
                col,
                _as_float(x_positions[upper_zone].get(int(seq), 0.0), 0.0),
                _as_float(zone_origin_x_ft.get(upper_zone), step_x_ft),
                step_x_ft,
            )
            if interval is not None:
                upper_intrusion_intervals.append(interval)

        merged_upper_intrusion = _merge_intervals(upper_intrusion_intervals)
        if merged_upper_intrusion:
            step_clearance_ft = max(upper_surface_ft - lower_surface_ft, 0.0)
            lower_height_map = col_heights.get(lower_zone, {}) or {}
            next_start_limit = None
            for seq in sorted((zone_cols.get(lower_zone) or {}).keys(), reverse=True):
                col = (zone_cols.get(lower_zone) or {}).get(seq) or []
                render_dims = _column_render_envelope_dims(col, zone=lower_zone)
                col_right_reach_ft = max(_as_float(render_dims.get("right_reach_ft"), 0.0), 0.0)
                col_left_tongue_ft = max(_as_float(render_dims.get("left_tongue_ft"), 0.0), 0.0)
                cur_start = _as_float(x_positions[lower_zone].get(int(seq), 0.0), 0.0)
                if next_start_limit is not None:
                    cur_start = min(cur_start, next_start_limit - col_right_reach_ft)

                col_height_ft = _as_float(lower_height_map.get(int(seq), 0.0), 0.0)
                if col_height_ft > step_clearance_ft + 1e-9:
                    required_gap_ft = 0.08
                    if _column_has_stuffed_forward_tongue(col):
                        # Keep at least the non-nested tongue exposure at the seam.
                        required_gap_ft = max(required_gap_ft, _DUMP_DOOR_MIN_EXPOSED_TONGUE_FT)
                    for _ in range(max(len(merged_upper_intrusion) * 2, 2)):
                        col_left = cur_start - col_left_tongue_ft
                        col_right = cur_start + col_right_reach_ft
                        overlap_found = False
                        for blocked_left, blocked_right in merged_upper_intrusion:
                            if col_right <= (blocked_left - required_gap_ft) + 1e-9:
                                continue
                            if col_left >= blocked_right - 1e-9:
                                continue
                            # Push this stack just left of the blocked interval.
                            cur_start = min(
                                cur_start,
                                blocked_left - required_gap_ft - col_right_reach_ft,
                            )
                            overlap_found = True
                            break
                        if not overlap_found:
                            break

                x_positions[lower_zone][int(seq)] = round(cur_start, 3)
                next_start_limit = cur_start

    # Compute true lower-deck left overhang from resolved spatial positions.
    # This captures deck and tongue geometry (including rotated units) after all
    # right-edge barrier shifts are applied.
    lower_cols = zone_cols.get("lower_deck", {})
    for seq, col in lower_cols.items():
        local_x_ft = _as_float(x_positions.get("lower_deck", {}).get(int(seq), 0.0), 0.0)
        sorted_col = sorted((col or []), key=lambda p: int(p.get("layer") or 0))
        local_offsets = _lower_column_layer_start_offsets(
            sorted_col,
            prefer_occupied_tongue=False,
        )
        for idx, unit in enumerate(sorted_col):
            deck_len_ft = _as_float(unit.get("deck_length_ft"), _as_float(unit.get("bed_length"), 0.0))
            tongue_len_ft = _as_float(
                unit.get("render_tongue_length_ft"),
                _as_float(unit.get("tongue_length"), 0.0),
            )
            is_rotated = bool(unit.get("is_rotated"))
            unit_local_start_ft = local_x_ft + _as_float(local_offsets[idx], 0.0)
            left_edge_ft = unit_local_start_ft - (tongue_len_ft if is_rotated else 0.0)
            # Include non-rotated deck-only overflow if local_x_ft is negative.
            left_edge_ft = min(left_edge_ft, unit_local_start_ft)
            if left_edge_ft < 0.0:
                lower_left_overhang_ft = max(lower_left_overhang_ft, -left_edge_ft)

    spatial_columns = {z: [] for z in zones}
    measure_segments_by_zone = {z: [] for z in zones}
    for zone in zones:
        cols = zone_cols.get(zone, {})
        zone_cap = _as_float(z_caps.get(zone), 0.0)
        zone_origin = _as_float(zone_origin_x_ft.get(zone), 0.0)
        zone_surface = _as_float(zone_surface_ft.get(zone), 0.0)
        zone_used = 0.0
        zone_prev_right_edge = None

        for seq in sorted(cols.keys()):
            col = cols.get(seq) or []
            local_x_ft = _as_float(x_positions.get(zone, {}).get(int(seq), 0.0), 0.0)
            global_x_ft = zone_origin + local_x_ft
            base_dims = _column_base_dims(col)
            left_edge_ft = local_x_ft - _as_float(base_dims.get("left_tongue_ft"), 0.0)
            right_edge_ft = (
                local_x_ft
                + _as_float(base_dims.get("deck_len_ft"), 0.0)
                + _as_float(base_dims.get("right_tongue_ft"), 0.0)
            )
            if zone_prev_right_edge is None:
                col_footprint_ft = max(right_edge_ft - left_edge_ft, 0.0)
            else:
                col_footprint_ft = max(right_edge_ft - max(zone_prev_right_edge, left_edge_ft), 0.0)
            zone_prev_right_edge = right_edge_ft if zone_prev_right_edge is None else max(zone_prev_right_edge, right_edge_ft)
            measure_x_local_ft = left_edge_ft
            col_right_edge_global_ft = zone_origin + right_edge_ft

            y_cursor_ft = zone_surface
            sorted_col = sorted(col, key=lambda p: int(p.get("layer") or 0))
            lower_local_offsets = []
            if zone == "lower_deck":
                lower_local_offsets = _lower_column_layer_start_offsets(
                    sorted_col,
                    prefer_occupied_tongue=False,
                )
            for idx, unit in enumerate(sorted_col):
                deck_len_ft = _as_float(unit.get("deck_length_ft"), _as_float(unit.get("bed_length"), 0.0))
                tongue_len_ft = _as_float(
                    unit.get("render_tongue_length_ft"),
                    _as_float(unit.get("tongue_length"), 0.0),
                )
                deck_component_h_ft = _as_float(
                    unit.get("deck_component_height_ft"),
                    _as_float(unit.get("height"), 0.0),
                )
                is_unit_rotated = bool(unit.get("is_rotated"))
                unit_left_tongue_ft = tongue_len_ft if is_unit_rotated else 0.0
                unit_right_tongue_ft = 0.0 if is_unit_rotated else tongue_len_ft
                if zone == "upper_deck":
                    unit_left_edge_ft = col_right_edge_global_ft - (
                        unit_left_tongue_ft + deck_len_ft + unit_right_tongue_ft
                    )
                    unit_deck_start_ft = unit_left_edge_ft + unit_left_tongue_ft
                elif zone == "lower_deck":
                    unit_deck_start_ft = global_x_ft + _as_float(lower_local_offsets[idx], 0.0)
                else:
                    unit_deck_start_ft = global_x_ft

                unit["x_ft"] = round(unit_deck_start_ft, 3)
                unit["x_local_ft"] = round(unit_deck_start_ft - zone_origin, 3)
                unit["deck_x_start_ft"] = round(unit_deck_start_ft, 3)
                unit["deck_x_end_ft"] = round(unit_deck_start_ft + deck_len_ft, 3)
                unit["y_surface_ft"] = round(y_cursor_ft, 3)
                unit["y_body_top_ft"] = round(y_cursor_ft + deck_component_h_ft, 3)
                unit["zone_surface_ft"] = round(zone_surface, 3)

                if is_unit_rotated:
                    tongue_attach_x_ft = unit_deck_start_ft
                    tongue_tip_x_ft = unit_deck_start_ft - tongue_len_ft
                else:
                    tongue_attach_x_ft = unit_deck_start_ft + deck_len_ft
                    tongue_tip_x_ft = tongue_attach_x_ft + tongue_len_ft

                zone_cap_x_ft = step_x_ft if zone == "lower_deck" else trailer_total_len_ft
                unit["tongue_x_start_ft"] = round(tongue_attach_x_ft, 3)
                unit["tongue_x_end_ft"] = round(tongue_tip_x_ft, 3)
                unit["zone_cap_x_ft"] = round(zone_cap_x_ft, 3)
                unit["neck_overhangs_step"] = bool(
                    zone == "lower_deck"
                    and (unit.get("render_tongue_profile") or "standard") == "gooseneck"
                    and tongue_tip_x_ft > step_x_ft
                )
                unit["neck_overhangs_cab"] = bool(tongue_tip_x_ft > trailer_total_len_ft)
                unit["overhang_ft"] = round(max(0.0, tongue_tip_x_ft - trailer_total_len_ft), 3)

                y_cursor_ft += deck_component_h_ft
            rendered_col_height_ft = max(y_cursor_ft - zone_surface, 0.0)
            logical_col_height_ft = rendered_col_height_ft
            if brand == "pj":
                logical_col_height_ft = _as_float(
                    (col_heights.get(zone) or {}).get(int(seq), rendered_col_height_ft),
                    rendered_col_height_ft,
                )
            col_heights.setdefault(zone, {})[int(seq)] = round(logical_col_height_ft, 3)

            col_entry = {
                "zone": zone,
                "sequence": int(seq),
                "x_local_ft": round(local_x_ft, 3),
                "x_ft": round(global_x_ft, 3),
                "footprint_ft": round(col_footprint_ft, 3),
                "height_ft": round(logical_col_height_ft, 3),
                "height_cap_ft": round(_as_float(clearances.get(zone), 0.0), 3),
            }
            spatial_columns[zone].append(col_entry)
            measure_segments_by_zone[zone].append(
                {
                    "kind": "stack",
                    "sequence": int(seq),
                    "length_ft": round(col_footprint_ft, 3),
                    "x_local_ft": round(measure_x_local_ft, 3),
                }
            )
            zone_used += col_footprint_ft

        gap_ft = max(zone_cap - zone_used, 0.0)
        if gap_ft > 0:
            measure_segments_by_zone[zone].append(
                {
                    "kind": "gap",
                    "sequence": None,
                    "length_ft": round(gap_ft, 3),
                    "x_local_ft": round(max(zone_used, 0.0), 3),
                }
            )

    # True max rendered height above each zone surface, derived from y_body_top_ft.
    # Used by the canvas template for accurate scale + gauge display.
    max_stacked_ft_by_zone: dict = {}
    for zone in zones:
        zs_ft = _as_float(zone_surface_ft.get(zone), 0.0)
        zone_max = 0.0
        for seq_col in (zone_cols.get(zone) or {}).values():
            for unit in seq_col:
                top = _as_float(unit.get("y_body_top_ft"), 0.0)
                used = max(top - zs_ft, 0.0)
                if used > zone_max:
                    zone_max = used
        max_stacked_ft_by_zone[zone] = round(zone_max, 3)

    # Global horizontal occupancy span from rendered geometry.
    spatial_min_x_ft = 0.0
    spatial_max_x_ft = 0.0
    spatial_span_ft = 0.0
    in_trailer_left_ft = 0.0
    in_trailer_right_ft = 0.0
    in_trailer_span_ft = 0.0
    left_overhang_total_ft = 0.0
    right_overhang_total_ft = 0.0
    if enriched:
        min_edge = None
        max_edge = None
        for unit in enriched:
            deck_x0 = _as_float(unit.get("deck_x_start_ft"), _as_float(unit.get("x_ft"), 0.0))
            deck_x1 = _as_float(unit.get("deck_x_end_ft"), deck_x0)
            tongue_x0 = _as_float(unit.get("tongue_x_start_ft"), deck_x1)
            tongue_x1 = _as_float(unit.get("tongue_x_end_ft"), tongue_x0)
            unit_left = min(deck_x0, deck_x1, tongue_x0, tongue_x1)
            unit_right = max(deck_x0, deck_x1, tongue_x0, tongue_x1)
            min_edge = unit_left if min_edge is None else min(min_edge, unit_left)
            max_edge = unit_right if max_edge is None else max(max_edge, unit_right)

        spatial_min_x_ft = _as_float(min_edge, 0.0)
        spatial_max_x_ft = _as_float(max_edge, 0.0)
        spatial_span_ft = max(spatial_max_x_ft - spatial_min_x_ft, 0.0)
        in_trailer_left_ft = max(spatial_min_x_ft, 0.0)
        in_trailer_right_ft = min(spatial_max_x_ft, trailer_total_len_ft)
        in_trailer_span_ft = max(in_trailer_right_ft - in_trailer_left_ft, 0.0)
        left_overhang_total_ft = max(0.0, -spatial_min_x_ft)
        right_overhang_total_ft = max(0.0, spatial_max_x_ft - trailer_total_len_ft)

    # Assign global stack index by absolute x-position so lower/upper measure
    # rows can keep aligned stack numbering.
    stack_segments = []
    for zone in zones:
        zone_origin = _as_float(zone_origin_x_ft.get(zone), 0.0)
        for seg in (measure_segments_by_zone.get(zone) or []):
            if (seg.get("kind") or "") != "stack":
                continue
            seq = int(seg.get("sequence") or 0)
            x_abs = zone_origin + _as_float(seg.get("x_local_ft"), 0.0)
            stack_segments.append((x_abs, zone, seq))
    stack_segments.sort(key=lambda item: (item[0], item[1], item[2]))
    stack_index_map = {}
    for idx, (_x_abs, zone, seq) in enumerate(stack_segments, start=1):
        stack_index_map[(zone, seq)] = idx
    for zone in zones:
        for seg in (measure_segments_by_zone.get(zone) or []):
            if (seg.get("kind") or "") != "stack":
                continue
            seq = int(seg.get("sequence") or 0)
            seg["stack_index"] = int(stack_index_map.get((zone, seq), 0))

    trailer_geometry = {
        "total_length_ft": round(trailer_total_len_ft, 3),
        "lower_deck_length_ft": round(lower_deck_len_ft, 3),
        "upper_deck_length_ft": round(upper_deck_len_ft, 3),
        "lower_deck_surface_ft": round(lower_surface_ft, 3),
        "upper_deck_surface_ft": round(upper_surface_ft, 3),
        "step_height_ft": round(max(upper_surface_ft - lower_surface_ft, 0.0), 3),
        "step_x_ft": round(step_x_ft, 3),
        "max_height_ft": round(max_height_ft, 3),
        "lower_clearance_ft": round(_as_float(clearances.get("lower_deck"), max_height_ft - lower_surface_ft), 3),
        "upper_clearance_ft": round(_as_float(clearances.get("upper_deck"), max_height_ft - upper_surface_ft), 3),
    }

    if brand in {"pj", "bigtex"} and length_metrics:
        total_footprint = round(float(length_metrics.get("effective_total_ft") or 0.0), 2)
    else:
        total_footprint = sum(zone_lengths.values())
    pct_used = round(total_footprint / (carrier["total_length_ft"] if carrier else 53.0) * 100, 1)

    # Violations (with acknowledgment overlay)
    violations_raw = check_load(session_id)
    acked = set(db.get_acknowledged_violations(session_id))
    violations = []
    violated_position_ids = set()
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
        for pid in (v.position_ids or []):
            if pid:
                violated_position_ids.add(str(pid))

    for p in enriched:
        p["violation"] = str(p.get("position_id") or "") in violated_position_ids

    _assign_unit_sequence_numbers(enriched)

    # After running check, mark stale session as active
    db.mark_session_active(session_id)

    violations_error   = sum(1 for v in violations if v["severity"] == "error" and not v["acknowledged"])
    violations_warning = sum(1 for v in violations if v["severity"] == "warning" and not v["acknowledged"])
    violations_info    = sum(1 for v in violations if v["severity"] == "info")
    manifest_rows = _build_manifest_rows(enriched)
    manifest_total_units = len(manifest_rows)
    category_palette, category_legend = _build_category_visuals(enriched)

    return dict(
        enriched_positions=enriched,
        zone_cols=zone_cols,
        zone_lengths=zone_lengths,
        zone_blocked_ft=zone_blocked_ft,
        length_metrics=length_metrics,
        col_heights=col_heights,
        z_caps=z_caps,
        clearances=clearances,
        total_footprint=total_footprint,
        pct_used=pct_used,
        violations=violations,
        violations_error=violations_error,
        violations_warning=violations_warning,
        violations_info=violations_info,
        manifest_rows=manifest_rows,
        manifest_total_units=manifest_total_units,
        category_palette=category_palette,
        category_legend=category_legend,
        lower_left_overhang_ft=round(lower_left_overhang_ft, 3),
        x_positions=x_positions,
        spatial_columns=spatial_columns,
        measure_segments_by_zone=measure_segments_by_zone,
        zone_origin_x_ft=zone_origin_x_ft,
        max_stacked_ft_by_zone=max_stacked_ft_by_zone,
        trailer_geometry=trailer_geometry,
        rear_clearance_len_ft=_REAR_POCKET_LEN_FT,
        rear_clearance_height_ft=_REAR_POCKET_HEIGHT_FT,
        spatial_usage={
            "min_x_ft": round(spatial_min_x_ft, 3),
            "max_x_ft": round(spatial_max_x_ft, 3),
            "span_ft": round(spatial_span_ft, 3),
            "in_trailer_left_ft": round(in_trailer_left_ft, 3),
            "in_trailer_right_ft": round(in_trailer_right_ft, 3),
            "in_trailer_span_ft": round(in_trailer_span_ft, 3),
            "left_overhang_ft": round(left_overhang_total_ft, 3),
            "right_overhang_ft": round(right_overhang_total_ft, 3),
            "total_overhang_ft": round(left_overhang_total_ft + right_overhang_total_ft, 3),
        },
    )


# -- Page Routes -----------------------------------------------------------

@prograde_bp.route("/")
def index():
    return account_landing()


@prograde_bp.route("/account")
def account_landing():
    selected_brand = _selected_brand(default="bigtex")
    active_profile = _get_active_profile()
    account_notice = _consume_account_notice()
    accounts = [_profile_to_view(row) for row in db.list_access_profiles()]
    next_url = _safe_next_url(request.args.get("next")) or url_for("prograde.sessions", brand=selected_brand)
    return render_template(
        "prograde/account.html",
        accounts=accounts,
        active_profile=active_profile,
        account_notice=account_notice,
        selected_brand=selected_brand,
        next_url=next_url,
    )


@prograde_bp.route("/sessions")
def sessions():
    selected_brand = _selected_brand(default="bigtex")
    active_profile = _get_active_profile()
    if not active_profile:
        _set_account_notice("Select or create an account to continue.", level="warning")
        next_url = request.full_path if request.query_string else request.path
        return redirect(url_for("prograde.account_landing", brand=selected_brand, next=next_url))
    account_notice = _consume_account_notice()
    can_manage_sessions = bool(active_profile.get("is_admin"))
    sessions = []
    for row in db.get_all_sessions(brand=selected_brand, saved_only=True):
        if not _can_access_session(row, active_profile):
            continue
        session_dict = dict(row)
        session_dict["display_id"] = _format_session_display_id(row)
        session_dict["builder_name"] = _resolve_session_builder_name(row)
        sessions.append(session_dict)
    return render_template(
        "prograde/index.html",
        sessions=sessions,
        active_profile=active_profile,
        can_manage_sessions=can_manage_sessions,
        account_notice=account_notice,
        selected_brand=selected_brand,
        has_seed_data=db.has_seed_data(),
    )


@prograde_bp.route("/account/select", methods=["POST"])
def account_select():
    selected_brand = (request.form.get("brand") or "").strip().lower()
    if selected_brand not in _VALID_BRANDS:
        selected_brand = _selected_brand(default="bigtex")
    profile_id_raw = request.form.get("profile_id")
    next_url = _safe_next_url(request.form.get("next"))
    try:
        profile_id = int(profile_id_raw)
    except (TypeError, ValueError):
        profile_id = None
    profile = db.get_access_profile(profile_id) if profile_id else None
    if not profile:
        _set_account_notice("Select a valid ProGrade account.", level="error")
        return redirect(url_for("prograde.account_landing", brand=selected_brand))
    selected = _set_active_profile(profile)
    _set_account_notice(f"Using account: {selected['name']}", level="success")
    return redirect(next_url or url_for("prograde.sessions", brand=selected_brand))


@prograde_bp.route("/account/create", methods=["POST"])
def account_create():
    selected_brand = (request.form.get("brand") or "").strip().lower()
    if selected_brand not in _VALID_BRANDS:
        selected_brand = _selected_brand(default="bigtex")
    next_url = _safe_next_url(request.form.get("next"))
    name = (request.form.get("name") or "").strip()
    try:
        profile_id = db.create_access_profile(name=name, is_admin=False)
    except ValueError as exc:
        _set_account_notice(str(exc), level="error")
        return redirect(url_for("prograde.account_landing", brand=selected_brand))
    profile = db.get_access_profile(profile_id)
    if profile:
        _set_active_profile(profile)
    _set_account_notice(f"Account created: {name}", level="success")
    return redirect(next_url or url_for("prograde.sessions", brand=selected_brand))


@prograde_bp.route("/session/new", methods=["GET", "POST"])
def session_new():
    brand = _selected_brand(default="bigtex")
    active_profile = _get_active_profile()
    if not active_profile:
        _set_account_notice("Select an account before creating a load.", level="warning")
        next_url = request.full_path if request.query_string else request.path
        return redirect(url_for("prograde.account_landing", brand=brand, next=next_url))
    if not db.has_seed_data():
        return redirect(url_for("prograde.settings", brand=brand))

    if request.method == "GET":
        session_id = str(uuid.uuid4())
        session_label = (request.args.get("label") or "").strip()
        planner_name = active_profile["name"]
        carrier_type = _default_carrier_type_for_brand(brand)
        db.create_session(
            session_id,
            brand,
            carrier_type,
            planner_name,
            session_label,
            created_by_profile_id=active_profile["id"],
            created_by_name=planner_name,
        )
        return redirect(url_for("prograde.load_builder", session_id=session_id, brand=brand))

    if request.method == "POST":
        brand = (request.form.get("brand") or "").strip().lower()
        if brand not in _VALID_BRANDS:
            brand = _selected_brand(default="bigtex")
        carrier_type = _default_carrier_type_for_brand(brand)
        planner_name = active_profile["name"]
        session_label = request.form.get("session_label", "").strip()

        # Enforce 53' step deck for both brands.
        step_deck = db.get_carrier_config("53_step_deck")
        if step_deck:
            carrier_type = "53_step_deck"

        carrier = db.get_carrier_config(carrier_type)
        if not carrier:
            return _json_error("Carrier type not found", 400)

        session_id    = str(uuid.uuid4())
        db.create_session(
            session_id,
            brand,
            carrier_type,
            planner_name,
            session_label,
            created_by_profile_id=active_profile["id"],
            created_by_name=planner_name,
        )
        return redirect(url_for("prograde.load_builder", session_id=session_id, brand=brand))
    return redirect(url_for("prograde.sessions", brand=brand))


@prograde_bp.route("/session/<session_id>/load")
def load_builder(session_id):
    _ensure_trailer_shape_template_alias()
    session_row, err = _session_page_or_redirect(session_id)
    if err:
        return err
    session = dict(session_row)
    session["builder_name"] = _resolve_session_builder_name(session_row)

    brand = session["brand"]
    carrier_type = session["carrier_type"]
    carrier      = db.get_carrier_config(carrier_type)
    if not carrier:
        return "Carrier configuration not found", 400
    zones        = brand_config.DECK_ZONES[brand]
    zone_labels  = brand_config.ZONE_LABELS

    raw_positions = db.get_positions(session_id)
    canvas = _build_canvas_data(session_id, session, carrier, zones, raw_positions, brand)
    inventory_gap = build_inventory_gap_data(
        session_id=session_id,
        brand=brand,
        carrier=carrier,
        canvas=canvas,
    )

    # SKU list for picker
    if brand == "pj":
        skus = _build_pj_picker_skus()
    else:
        skus = [dict(s) for s in db.get_bigtex_skus()]
    pj_offsets = db.get_pj_offsets_dict() if brand == "pj" else {}
    pj_crisscross_assumptions = {
        "length_save_ft": _as_float(pj_offsets.get("gn_crisscross_length_save_ft"), 2.0),
        "height_save_ft": _as_float(pj_offsets.get("gn_crisscross_height_save_ft"), 1.0),
        "width_save_ft": _as_float(pj_offsets.get("gn_crisscross_width_save_ft"), 0.6),
    }
    session_display_id = _format_session_display_id(session_row)
    selected_brand = _selected_brand(default=brand)
    active_profile = _get_active_profile()

    return render_template(
        "prograde/load_builder.html",
        session=session,
        session_display_id=session_display_id,
        active_profile=active_profile,
        selected_brand=selected_brand,
        carrier=carrier,
        zones=zones,
        zone_labels=zone_labels,
        skus=skus,
        inventory_gap=inventory_gap,
        pj_crisscross_assumptions=pj_crisscross_assumptions,
        **canvas,
    )


@prograde_bp.route("/session/<session_id>/export")
def export_load(session_id):
    session_row, err = _session_page_or_redirect(session_id)
    if err:
        return err
    session = dict(session_row)
    session["builder_name"] = _resolve_session_builder_name(session_row)

    brand = session["brand"]
    carrier_type = session["carrier_type"]
    carrier      = db.get_carrier_config(carrier_type)
    if not carrier:
        return "Carrier configuration not found", 400
    zones        = brand_config.DECK_ZONES[brand]
    zone_labels  = brand_config.ZONE_LABELS

    raw_positions = db.get_positions(session_id)
    canvas = _build_canvas_data(session_id, session, carrier, zones, raw_positions, brand)
    selected_brand = _selected_brand(default=brand)
    active_profile = _get_active_profile()

    return render_template(
        "prograde/export.html",
        session=session,
        active_profile=active_profile,
        selected_brand=selected_brand,
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
    selected_brand = _selected_brand(default="bigtex")
    active_profile = _get_active_profile()
    bt_workbook_path = db.get_bigtex_workbook_path()
    pj_workbook_path = db.get_pj_workbook_path()
    carrier_configs = db.get_carrier_configs()
    carrier_geometry = next(
        (dict(row) for row in carrier_configs if str(row["carrier_type"]) == "53_step_deck"),
        {},
    )
    pj_measurement_offsets = db.get_pj_measurement_offsets()
    pj_offset_map = {
        str(row["rule_key"]): _as_float(row["offset_ft"], 0.0)
        for row in pj_measurement_offsets
    }
    gn_neck_geometry = {
        "gn_neck_total_ft": _as_float(pj_offset_map.get("gn_neck_total_ft"), 9.0),
        "gn_neck_rise_ft": _as_float(pj_offset_map.get("gn_neck_rise_ft"), 6.0),
        "gn_neck_base_ft": _as_float(pj_offset_map.get("gn_neck_base_ft"), 0.5),
        "gn_neck_crown_ft": _as_float(pj_offset_map.get("gn_neck_crown_ft"), 5.0),
        "gn_neck_descent_ft": _as_float(pj_offset_map.get("gn_neck_descent_ft"), 3.5),
        "gn_coupler_drop_ft": _as_float(pj_offset_map.get("gn_coupler_drop_ft"), 5.0),
    }
    pj_height_reference = db.get_pj_height_reference()
    pj_height_map = {row["category"]: dict(row) for row in pj_height_reference}
    pj_skus = [dict(row) for row in db.get_pj_skus()]
    for sku in pj_skus:
        sku["item_code"] = _pj_picker_short_item_code(sku)
    advanced_schematic_links = db.get_advanced_schematic_links()
    if not db.has_seed_data():
        return render_template(
            "prograde/settings.html",
            carrier_configs=[],
            carrier_geometry={},
            gn_neck_geometry={},
            pj_offset_map={},
            advanced_schematic_links=[],
            pj_tongue_groups=[],
            pj_height_reference=[],
            pj_height_map={},
            pj_measurement_offsets=[],
            pj_skus=[],
            bt_skus=[],
            bt_stack_configs=[],
            bt_workbook_path=str(bt_workbook_path) if bt_workbook_path else "",
            pj_workbook_path=str(pj_workbook_path) if pj_workbook_path else "",
            pj_categories=brand_config.PJ_CATEGORIES,
            selected_brand=selected_brand,
            active_profile=active_profile,
            error_message="ProGrade seed data not loaded. Settings are unavailable until data is seeded.",
        ), 503
    return render_template(
        "prograde/settings.html",
        carrier_configs        = carrier_configs,
        carrier_geometry       = carrier_geometry,
        gn_neck_geometry       = gn_neck_geometry,
        pj_offset_map          = pj_offset_map,
        advanced_schematic_links = advanced_schematic_links,
        pj_tongue_groups       = db.get_pj_tongue_groups(),
        pj_height_reference    = pj_height_reference,
        pj_height_map          = pj_height_map,
        pj_measurement_offsets = pj_measurement_offsets,
        pj_skus                = pj_skus,
        bt_skus                = db.get_bigtex_skus(),
        bt_stack_configs       = db.get_bt_stack_configs(),
        bt_workbook_path       = str(bt_workbook_path) if bt_workbook_path else "",
        pj_workbook_path       = str(pj_workbook_path) if pj_workbook_path else "",
        pj_categories          = brand_config.PJ_CATEGORIES,
        selected_brand         = selected_brand,
        active_profile         = active_profile,
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


@prograde_bp.route("/api/session/<session_id>/save", methods=["POST"])
def api_save_session(session_id):
    _session, err = _session_or_404(session_id)
    if err:
        return err
    saved_row = db.save_session(session_id)
    if not saved_row:
        return _json_error("Session not found", 404)
    return jsonify(
        ok=True,
        session_id=saved_row["session_id"],
        status=saved_row["status"],
        is_saved=bool(saved_row["is_saved"]),
        updated_at=saved_row["updated_at"],
    )


@prograde_bp.route("/session/<session_id>/delete", methods=["POST"])
def session_delete(session_id):
    row, err = _session_page_or_redirect(session_id)
    if err:
        return err
    db.delete_session(session_id)
    selected_brand = _selected_brand(default=(row["brand"] if row else "bigtex"))
    return redirect(url_for("prograde.sessions", brand=selected_brand))


@prograde_bp.route("/api/session/<session_id>/inventory/upload", methods=["POST"])
def api_upload_bt_inventory(session_id):
    session, err = _session_or_404(session_id)
    if err:
        return err
    if (session["brand"] or "").strip().lower() != "bigtex":
        return _json_error("Inventory upload is currently available for Big Tex sessions only.", 400)

    orders_file = request.files.get("orders_file")
    if orders_file is None or not (orders_file.filename or "").strip():
        return _json_error("orders_file is required")

    filename = Path(orders_file.filename).name
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_ORDER_UPLOAD_EXTENSIONS:
        allowed = ", ".join(sorted(_ALLOWED_ORDER_UPLOAD_EXTENSIONS))
        return _json_error(f"Unsupported file type. Upload one of: {allowed}")

    sheet_name = (request.form.get("sheet_name") or "All.Orders.Quick").strip() or "All.Orders.Quick"
    temp_path = Path(tempfile.gettempdir()) / f"prograde_bt_orders_{uuid.uuid4().hex}{ext}"
    orders_file.save(temp_path)
    try:
        result = db.import_bigtex_inventory_orders_workbook(workbook_path=temp_path, sheet_name=sheet_name)
        return jsonify(ok=True, import_result=result)
    except FileNotFoundError as exc:
        return _json_error(str(exc), 404)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    except Exception:
        current_app.logger.exception("Failed to import Big Tex inventory workbook")
        return _json_error("Failed to import Big Tex inventory workbook", 500)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


# -- Settings Save API -----------------------------------------------------

ALLOWED_FIELDS = {
    "carrier_configs": {
        "total_length_ft", "max_height_ft", "lower_deck_length_ft", "upper_deck_length_ft",
        "lower_deck_ground_height_ft", "upper_deck_ground_height_ft", "gn_max_lower_deck_ft", "notes",
    },
    "advanced_schematic_links": {"drawing_label", "render_mode", "applies_to_categories", "notes", "display_order"},
    "pj_tongue_groups":    {"group_label", "tongue_feet", "notes"},
    "pj_height_reference": {"height_mid_ft", "height_top_ft", "gn_axle_dropped_ft", "notes"},
    "pj_measurement_offsets": {"offset_ft", "notes"},
    "bt_stack_configs":    {"max_length_ft", "max_height_ft", "notes"},
    "bigtex_skus": {"mcat", "tier", "model", "gvwr", "floor_type", "bed_length", "width", "tongue", "stack_height"},
    "pj_skus": {
        "pj_category", "bed_length_measured", "tongue_feet", "dump_side_height_ft",
        "can_nest_inside_dump", "gn_axle_droppable", "tongue_overlap_allowed", "pairing_rule", "notes",
    },
}

NUMERIC_FIELDS = {
    "total_length_ft", "max_height_ft", "lower_deck_length_ft", "upper_deck_length_ft",
    "lower_deck_ground_height_ft", "upper_deck_ground_height_ft", "gn_max_lower_deck_ft",
    "tongue_feet", "height_mid_ft", "height_top_ft", "gn_axle_dropped_ft",
    "offset_ft", "max_length_ft", "max_height_ft", "dump_side_height_ft",
    "can_nest_inside_dump", "gn_axle_droppable", "tongue_overlap_allowed",
    "tier", "gvwr", "bed_length", "width", "tongue", "stack_height",
    "bed_length_measured", "display_order",
}

PJ_OFFSETS_REQUIRE_SKU_RECOMPUTE = {
    "car_hauler_spare_mount_offset",
    "dump_tarp_kit_offset",
    "dtj_cylinder_extra_offset",
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
        elif table == "advanced_schematic_links":
            db.update_advanced_schematic_link(pk, field, value)
        elif table == "pj_tongue_groups":
            db.update_pj_tongue_group(pk, field, value)
        elif table == "pj_height_reference":
            db.update_pj_height_reference(pk, field, value)
            if field == "height_top_ft":
                # ProGrade settings now use a single height field; keep mid/top synchronized.
                db.update_pj_height_reference(pk, "height_mid_ft", value)
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
        needs_pj_recompute = False
        if recompute == "pj_skus":
            if table == "pj_measurement_offsets":
                needs_pj_recompute = str(pk or "") in PJ_OFFSETS_REQUIRE_SKU_RECOMPUTE
            else:
                needs_pj_recompute = True
        elif (
            table == "pj_measurement_offsets"
            and field == "offset_ft"
            and str(pk or "") in PJ_OFFSETS_REQUIRE_SKU_RECOMPUTE
        ):
            needs_pj_recompute = True
        if needs_pj_recompute:
            recomputed = _recompute_all_pj_skus()
        if table == "pj_tongue_groups" and field == "tongue_feet" and value is not None:
            recomputed = _recompute_pj_skus_for_tongue_group(pk, float(value))
        if table == "pj_skus" and field in {"bed_length_measured", "tongue_feet"}:
            refreshed = db.recompute_pj_footprint(pk)
            if refreshed:
                recomputed = [refreshed]
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


@prograde_bp.route("/api/settings/pj/import", methods=["POST"])
def api_pj_import():
    data = request.get_json(silent=True) or {}
    workbook_path = (data.get("workbook_path") or "").strip() or None
    toc_sheet_name = (data.get("toc_sheet_name") or "ToC").strip() or "ToC"
    try:
        result = db.import_pj_skus_from_workbook(workbook_path=workbook_path, toc_sheet_name=toc_sheet_name)
        db.flag_all_draft_sessions_stale()
        return jsonify(ok=True, sessions_flagged=True, import_result=result)
    except FileNotFoundError as exc:
        return _json_error(str(exc), 404)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    except Exception:
        current_app.logger.exception("Failed to import PJ workbook")
        return _json_error("Failed to import PJ workbook", 500)


@prograde_bp.route("/api/session/<session_id>/add", methods=["POST"])
def api_add_unit(session_id):
    session, err = _session_or_404(session_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    item_number = (data.get("item_number") or "").strip()
    deck_zone = _normalize_zone_for_brand(session["brand"], data.get("deck_zone"))
    stack_on = data.get("stack_on")
    insert_index = data.get("insert_index")
    requested_tongue_profile = _normalize_pj_tongue_profile(data.get("pj_tongue_profile"), default=None)
    requested_dump_height_ft = _normalize_pj_dump_height_ft(data.get("pj_dump_height_ft"), default=None)

    if data.get("pj_dump_height_ft") not in (None, "") and requested_dump_height_ft is None:
        return _json_error("pj_dump_height_ft must be 3 or 4")

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
        normalized_positions = []
        for p in positions:
            pd = dict(p)
            pd["deck_zone"] = _normalize_zone_for_brand(brand, pd.get("deck_zone"))
            normalized_positions.append(pd)
        if stack_on:
            target = next((p for p in normalized_positions if p["position_id"] == stack_on), None)
            if not target:
                return _json_error("Target position not found")
            seq = int(target["sequence"])
            layer = max(
                (
                    int(p["layer"])
                    for p in normalized_positions
                    if p["deck_zone"] == target["deck_zone"] and int(p["sequence"]) == seq
                ),
                default=0,
            ) + 1
            deck_zone = target["deck_zone"]
        else:
            zone_positions = [p for p in normalized_positions if p["deck_zone"] == deck_zone]
            seq = max((int(p["sequence"]) for p in zone_positions), default=0) + 1
            layer = 1

        position_id = str(uuid.uuid4())
        override_reason = None
        if brand == "pj":
            sku_map = dict(sku)
            default_tongue_profile = _normalize_pj_tongue_profile(
                _pj_picker_tongue_profile(sku_map),
                default="standard",
            )
            selected_tongue_profile = requested_tongue_profile or default_tongue_profile
            override_reason = _build_tongue_override_reason(selected_tongue_profile)
            if _pj_render_deck_profile(sku_map) == "dump":
                default_dump_height_ft = _normalize_pj_dump_height_ft(
                    sku_map.get("dump_side_height_ft"),
                    default=None,
                )
                selected_dump_height_ft = (
                    requested_dump_height_ft
                    if requested_dump_height_ft is not None
                    else default_dump_height_ft
                )
                override_reason = _build_dump_height_override_reason(selected_dump_height_ft, override_reason)

        # Big Tex rule-of-thumb: add new units with tongues facing left by default.
        default_is_rotated = 1 if brand == "bigtex" else 0
        db.add_position(
            position_id,
            session_id,
            brand,
            item_number,
            deck_zone,
            layer,
            seq,
            override_reason=override_reason,
            is_rotated=default_is_rotated,
        )
        if insert_idx is not None:
            db.move_position(session_id, position_id, deck_zone, to_sequence=None, insert_index=insert_idx)
        gn_crisscross_applied = False
        if brand == "pj" and stack_on:
            gn_crisscross_applied = _apply_pj_gn_crisscross_for_column(
                session_id,
                deck_zone,
                seq,
                preferred_position_id=position_id,
            )
        return jsonify(
            ok=True,
            position_id=position_id,
            gn_crisscross_applied=bool(gn_crisscross_applied),
            state=_build_session_api_state(session_id),
        )
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


@prograde_bp.route("/api/session/<session_id>/rotate", methods=["POST"])
def api_rotate_unit(session_id):
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
        new_val = 0 if int(pos["is_rotated"] or 0) else 1
        db.update_position_field(position_id, "is_rotated", new_val)
        return jsonify(ok=True, is_rotated=new_val, state=_build_session_api_state(session_id))
    except Exception:
        current_app.logger.exception("Failed to rotate unit for ProGrade session %s", session_id)
        return _json_error("Failed to rotate unit", 500)


@prograde_bp.route("/api/session/<session_id>/toggle_dump_door", methods=["POST"])
def api_toggle_dump_door(session_id):
    session_row, err = _session_or_404(session_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    position_id = data.get("position_id")
    if not position_id:
        return _json_error("position_id required")

    pos = db.get_position(position_id)
    if not pos or pos["session_id"] != session_id:
        return _json_error("Position not found", 404)
    pos_map = _row_to_dict(pos)
    if (session_row["brand"] or "").strip().lower() != "pj":
        return _json_error("Dump door toggle is PJ-only", 400)

    sku = db.get_pj_sku(pos_map.get("item_number")) or {}
    if _pj_render_deck_profile(sku) != "dump":
        return _json_error("Selected unit is not a dump profile", 400)

    current_removed = _extract_dump_door_removed_reason(pos_map.get("override_reason"))
    next_removed = not current_removed
    new_override = _set_override_reason_token(
        pos_map.get("override_reason"),
        "dump_door_removed",
        "1" if next_removed else None,
    )
    try:
        db.update_position_field(position_id, "override_reason", new_override)
        return jsonify(ok=True, dump_door_removed=next_removed, state=_build_session_api_state(session_id))
    except Exception:
        current_app.logger.exception("Failed to toggle dump door for ProGrade session %s", session_id)
        return _json_error("Failed to toggle dump door", 500)


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
    to_zone = _normalize_zone_for_brand(session["brand"], data.get("to_zone"))
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
        gn_crisscross_applied = False
        if (
            session["brand"] == "pj"
            and result.get("layer")
            and int(result.get("layer") or 0) > 1
        ):
            gn_crisscross_applied = _apply_pj_gn_crisscross_for_column(
                session_id,
                to_zone,
                result.get("sequence"),
                preferred_position_id=position_id,
            )
        return jsonify(
            ok=True,
            result=result,
            gn_crisscross_applied=bool(gn_crisscross_applied),
            state=_build_session_api_state(session_id),
        )
    except Exception:
        current_app.logger.exception("Failed to move position for ProGrade session %s", session_id)
        return _json_error("Failed to move unit", 500)


@prograde_bp.route("/api/session/<session_id>/column/move", methods=["POST"])
def api_move_column(session_id):
    session, err = _session_or_404(session_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    from_zone = _normalize_zone_for_brand(session["brand"], data.get("from_zone"))
    to_zone = _normalize_zone_for_brand(session["brand"], data.get("to_zone"))
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
    session_row, err = _session_or_404(session_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    brand = session_row["brand"]
    deck_zone = _normalize_zone_for_brand(brand, data.get("deck_zone"))
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
    session_row, err = _session_or_404(session_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    brand = session_row["brand"]
    from_zone = _normalize_zone_for_brand(brand, data.get("from_zone"))
    to_zone = _normalize_zone_for_brand(brand, data.get("to_zone"))
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

