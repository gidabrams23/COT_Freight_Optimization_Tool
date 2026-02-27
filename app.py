import json
import logging
import math
import os
import re
import secrets
import subprocess
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
try:
    from openpyxl.drawing.image import Image as OpenPyxlImage
except Exception:  # pragma: no cover - optional dependency path
    OpenPyxlImage = None

try:
    from PIL import Image as PILImage, ImageDraw
except Exception:  # pragma: no cover - optional dependency path
    PILImage = None
    ImageDraw = None

import db
from services import (
    load_builder,
    orders as order_service,
    stack_calculator,
    geo_utils,
    tsp_solver,
    customer_rules,
    routing_service,
    replay_evaluator,
)
from services.cost_calculator import (
    FUEL_SURCHARGE_SETTING_KEY,
    DEFAULT_FUEL_SURCHARGE_PER_MILE,
    STOP_FEE_SETTING_KEY,
    DEFAULT_STOP_FEE,
    MIN_LOAD_COST_SETTING_KEY,
    DEFAULT_MIN_LOAD_COST,
)
from services.optimizer_engine import OptimizerEngine
from services.order_importer import OrderImporter

logger = logging.getLogger(__name__)
ROOT_DIR = Path(__file__).resolve().parent


def _coerce_iso_date(raw_value):
    text = (raw_value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _git_last_updated_date():
    try:
        output = subprocess.check_output(
            ["git", "log", "-1", "--format=%cs"],
            cwd=str(ROOT_DIR),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return None
    return _coerce_iso_date(output)


def _source_last_updated_date():
    latest_ts = 0.0
    tracked_paths = [
        ROOT_DIR / "app.py",
        ROOT_DIR / "db.py",
        ROOT_DIR / "templates" / "login.html",
        ROOT_DIR / "static" / "styles.css",
    ]
    for path in tracked_paths:
        try:
            if path.exists():
                latest_ts = max(latest_ts, path.stat().st_mtime)
        except OSError:
            continue
    if latest_ts <= 0:
        return None
    return datetime.fromtimestamp(latest_ts).date().isoformat()


def _resolve_app_updated_on():
    return (
        _coerce_iso_date(os.environ.get("APP_UPDATED_ON"))
        or _git_last_updated_date()
        or _source_last_updated_date()
        or date.today().isoformat()
    )


APP_UPDATED_ON = _resolve_app_updated_on()
APP_VERSION = (os.environ.get("APP_VERSION") or "").strip() or f"v{APP_UPDATED_ON.replace('-', '.')}"
APP_RELEASE_LABEL = f"{APP_VERSION} | Updated {APP_UPDATED_ON}"


class UploadValidationError(Exception):
    def __init__(self, message, summary=None):
        super().__init__(message)
        self.summary = summary or {}


def _is_local_dev_mode():
    env_hint = (os.environ.get("APP_ENV") or os.environ.get("FLASK_ENV") or "").strip().lower()
    if env_hint in {"dev", "development", "local"}:
        return True
    return os.environ.get("FLASK_DEBUG", "").strip() == "1"


def _env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "on", "y"}:
        return True
    if normalized in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


app = Flask(__name__)
_configured_secret = (os.environ.get("FLASK_SECRET_KEY") or "").strip()
if not _configured_secret and not _is_local_dev_mode():
    raise RuntimeError(
        "FLASK_SECRET_KEY must be set for non-development environments."
    )
if not _configured_secret:
    _configured_secret = "dev-session-key"
    logger.warning("Using development session secret key.")
app.secret_key = _configured_secret
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=_env_bool(
        "SESSION_COOKIE_SECURE",
        default=not _is_local_dev_mode(),
    ),
)
_raw_web_concurrency = (os.environ.get("WEB_CONCURRENCY") or "").strip()
try:
    _configured_web_concurrency = int(_raw_web_concurrency) if _raw_web_concurrency else 1
except ValueError:
    _configured_web_concurrency = 1
if _configured_web_concurrency > 1:
    logger.warning(
        "WEB_CONCURRENCY=%s detected. Re-optimization job status is process-local; "
        "set WEB_CONCURRENCY=1 to avoid cross-worker status loss.",
        _configured_web_concurrency,
    )

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


@app.template_filter("est_datetime")
def est_datetime(value):
    return _format_est_datetime_label(value)

PLANT_CODES = ["GA", "TX", "VA", "IA", "OR", "NV", "CL"]
PLANT_NAMES = {
    "GA": "Lavonia",
    "IA": "Missouri Valley",
    "TX": "Mexia",
    "VA": "Montross",
    "CL": "Callao",
    "OR": "Coburg",
    "NV": "Winnemucca",
}
STATUS_PROPOSED = "PROPOSED"
STATUS_DRAFT = "DRAFT"
STATUS_APPROVED = "APPROVED"
LOAD_NUMBER_START_PATTERN = re.compile(r"^\d{4}$")
OPTIMIZER_V2_ENABLED = (
    os.environ.get("OPTIMIZER_V2_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
)
ROLE_ADMIN = "admin"
ROLE_PLANNER = "planner"
APP_TIMEZONE = ZoneInfo("America/New_York")
SESSION_PROFILE_ID_KEY = "profile_id"
SESSION_PROFILE_NAME_KEY = "profile_name"
SESSION_PROFILE_DEFAULT_PLANTS_KEY = "profile_default_plants"
SESSION_ACTIVE_PLANNING_ID_KEY = "active_planning_session_id"
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
OPTIMIZER_DEFAULTS_SETTING_KEY = "optimizer_defaults"
UTILIZATION_GRADE_THRESHOLDS_SETTING_KEY = "utilization_grade_thresholds"
REPLAY_EVAL_PRESET_SETTING_KEY = "replay_eval_preset"
STOP_COLOR_PALETTE_SETTING_KEY = "stop_color_palette"
TRAILER_ASSIGNMENT_RULES_SETTING_KEY = "trailer_assignment_rules"
RATE_TABLE_CONTEXTS_SETTING_KEY = "rate_table_contexts"
DEFAULT_UTILIZATION_GRADE_THRESHOLDS = {"A": 85, "B": 70, "C": 55, "D": 40}
DEFAULT_TRAILER_ASSIGNMENT_RULES = {
    "livestock_wedge_enabled": True,
    "livestock_category_tokens": ["LIVESTOCK"],
    "auto_assign_hotshot_enabled": True,
    "auto_assign_hotshot_utilization_threshold_pct": 45.0,
}
DEFAULT_RATE_TABLE_CONTEXTS = {
    "default_rate_table_key": "DEFAULT",
    "carrier_dedicated_ryder_rate_table_key": "DEDICATED_RYDER_FLEET",
    "trailer_hotshot_rate_table_key": "HOTSHOT_TRAILER_TYPES",
}
RATE_TABLE_KEY_OPTIONS = [
    {"key": "DEFAULT", "label": "DEFAULT"},
    {"key": "DEDICATED_RYDER_FLEET", "label": "DEDICATED RYDER FLEET (Placeholder)"},
    {"key": "HOTSHOT_TRAILER_TYPES", "label": "HOTSHOT TRAILER TYPES (Placeholder)"},
]
DEFAULT_STACK_OVERFLOW_MAX_HEIGHT = 5
DEFAULT_MAX_BACK_OVERHANG_FT = 4.0
DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT = 7.0
DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT = 16.0
DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT = 6.0
DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES = ["USA", "UTA"]
DEFAULT_STOP_COLOR_PALETTE = [
    "#6FAD47",
    "#01B0F0",
    "#EC7D31",
    "#FFFFFF",
    "#FE0000",
    "#A56CD2",
    "#A5A6A6",
    "#FED966",
    "#FE6699",
    "#6CF8FB",
    "#5F87CC",
    "#FEFF00",
]
FALLBACK_STOP_COLOR = "#64748B"
HEX_COLOR_PATTERN = re.compile(r"^#[0-9A-F]{6}$")
TRAILER_PROFILE_OPTIONS = stack_calculator.trailer_profile_options()
TUTORIAL_MANIFEST_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "docs",
    "tutorial",
    "tutorial_manifest.json",
)
TUTORIAL_ALLOWED_MEDIA_TYPES = {"image", "video"}
TUTORIAL_ALLOWED_AUDIENCE = {"all", ROLE_ADMIN, ROLE_PLANNER}
TUTORIAL_NOTE_LABELS = {
    "tip": "Tip",
    "warning": "Warning",
    "required": "Required",
}
TUTORIAL_NAV_ENABLED = _env_bool("TUTORIAL_NAV_ENABLED", default=False)
REOPT_JOB_RETENTION_SEC = 60 * 60
REOPT_JOB_MAX_ENTRIES = 200
_REOPT_JOB_LOCK = threading.Lock()
_REOPT_JOBS = {}
ACCESS_PROFILES_SEED_PATH = Path(
    os.environ.get("ACCESS_PROFILES_SEED_PATH", str(ROOT_DIR / "data" / "seed" / "access_profiles.csv"))
)
ACCESS_PROFILES_SEED_COLUMNS = ["name", "is_admin", "allowed_plants", "default_plants", "created_at"]


def _sync_access_profiles_seed_snapshot():
    try:
        profiles = db.list_access_profiles()
        ACCESS_PROFILES_SEED_PATH.parent.mkdir(parents=True, exist_ok=True)
        with ACCESS_PROFILES_SEED_PATH.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=ACCESS_PROFILES_SEED_COLUMNS)
            writer.writeheader()
            for profile in profiles:
                writer.writerow(
                    {
                        "name": (profile.get("name") or "").strip(),
                        "is_admin": 1 if profile.get("is_admin") else 0,
                        "allowed_plants": profile.get("allowed_plants") or "ALL",
                        "default_plants": profile.get("default_plants") or "ALL",
                        "created_at": profile.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
    except Exception:
        logger.warning("Unable to sync access profile seed snapshot at %s", ACCESS_PROFILES_SEED_PATH)


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
        {
            "name": "Kissaryn",
            "is_admin": False,
            "allowed_plants": "ALL",
            "default_plants": "GA,VA,NV",
        },
        {
            "name": "Judy",
            "is_admin": False,
            "allowed_plants": "ALL",
            "default_plants": "GA,VA,NV",
        },
    ]
)
_sync_access_profiles_seed_snapshot()
db.ensure_default_planning_settings(
    {
        "strategic_customers": json.dumps(
            [
                {
                    "label": "Lowe's",
                    "patterns": ["LOWE'S", "LOWES"],
                    "include_in_optimizer_workbench": True,
                },
                {
                    "label": "Tractor Supply",
                    "patterns": ["TRACTOR SUPPLY", "TRACTORSUPPLY"],
                    "wedge_min_item_length_ft": 16,
                    "include_in_optimizer_workbench": True,
                },
                {
                    "label": "Ace",
                    "patterns": ["ACE HARDWARE CORPORATION", "ACE HARDWARE"],
                    "include_in_optimizer_workbench": True,
                },
                {
                    "label": "TrailersPlus",
                    "patterns": ["TRAILERSPLUS", "TRAILER'S PLUS"],
                    "include_in_optimizer_workbench": True,
                },
                {
                    "label": "COT Sample",
                    "patterns": ["COT SAMPLE", "COTSAMPLE"],
                    "ignore_for_optimization": True,
                    "include_in_optimizer_workbench": False,
                },
                {
                    "label": "Carolina Equipment",
                    "patterns": ["CAROLINA EQUIPMENT", "CAROLINAEQUIPMENT"],
                    "ignore_for_optimization": True,
                    "include_in_optimizer_workbench": False,
                },
            ],
            separators=(",", ":"),
        ),
        STOP_FEE_SETTING_KEY: DEFAULT_STOP_FEE,
        MIN_LOAD_COST_SETTING_KEY: DEFAULT_MIN_LOAD_COST,
        FUEL_SURCHARGE_SETTING_KEY: DEFAULT_FUEL_SURCHARGE_PER_MILE,
        OPTIMIZER_DEFAULTS_SETTING_KEY: json.dumps(
            {
                "trailer_type": load_builder.DEFAULT_BUILD_PARAMS.get("trailer_type", "STEP_DECK"),
                "capacity_feet": float(load_builder.DEFAULT_BUILD_PARAMS.get("capacity_feet", 53) or 53),
                "time_window_days": int(load_builder.DEFAULT_BUILD_PARAMS.get("time_window_days", 7) or 7),
                "geo_radius": float(load_builder.DEFAULT_BUILD_PARAMS.get("geo_radius", 100) or 100),
                "max_detour_pct": float(load_builder.DEFAULT_BUILD_PARAMS.get("max_detour_pct", 15) or 15),
                "stack_overflow_max_height": int(
                    load_builder.DEFAULT_BUILD_PARAMS.get(
                        "stack_overflow_max_height",
                        DEFAULT_STACK_OVERFLOW_MAX_HEIGHT,
                    )
                    or DEFAULT_STACK_OVERFLOW_MAX_HEIGHT
                ),
                "max_back_overhang_ft": float(
                    load_builder.DEFAULT_BUILD_PARAMS.get(
                        "max_back_overhang_ft",
                        DEFAULT_MAX_BACK_OVERHANG_FT,
                    )
                    or DEFAULT_MAX_BACK_OVERHANG_FT
                ),
                "upper_two_across_max_length_ft": float(
                    load_builder.DEFAULT_BUILD_PARAMS.get(
                        "upper_two_across_max_length_ft",
                        DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT,
                    )
                    or DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT
                ),
                "upper_deck_exception_max_length_ft": float(
                    load_builder.DEFAULT_BUILD_PARAMS.get(
                        "upper_deck_exception_max_length_ft",
                        DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT,
                    )
                    or DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT
                ),
                "upper_deck_exception_overhang_allowance_ft": float(
                    load_builder.DEFAULT_BUILD_PARAMS.get(
                        "upper_deck_exception_overhang_allowance_ft",
                        DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT,
                    )
                    or DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT
                ),
                "upper_deck_exception_categories": stack_calculator.normalize_upper_deck_exception_categories(
                    load_builder.DEFAULT_BUILD_PARAMS.get("upper_deck_exception_categories"),
                    default=DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES,
                ),
            }
        ),
        REPLAY_EVAL_PRESET_SETTING_KEY: json.dumps(replay_evaluator.DEFAULT_REPLAY_PRESET),
        UTILIZATION_GRADE_THRESHOLDS_SETTING_KEY: json.dumps(DEFAULT_UTILIZATION_GRADE_THRESHOLDS),
        STOP_COLOR_PALETTE_SETTING_KEY: json.dumps(DEFAULT_STOP_COLOR_PALETTE),
        TRAILER_ASSIGNMENT_RULES_SETTING_KEY: json.dumps(DEFAULT_TRAILER_ASSIGNMENT_RULES),
        RATE_TABLE_CONTEXTS_SETTING_KEY: json.dumps(DEFAULT_RATE_TABLE_CONTEXTS),
    }
)


def _backfill_legacy_sessions():
    orphan_loads = db.list_loads_without_session()
    if not orphan_loads:
        return
    grouped = {}
    for load in orphan_loads:
        plant = load.get("origin_plant") or "UNKNOWN"
        grouped.setdefault(plant, []).append(load)

    for plant, loads in grouped.items():
        session_code = f"LEGACY_{plant}"
        existing = db.get_planning_session_by_code(session_code)
        if existing:
            session_id = existing.get("id")
        else:
            created_at = min(
                (entry.get("created_at") for entry in loads if entry.get("created_at")), default=None
            )
            session_id = db.create_planning_session(
                session_code=session_code,
                plant_code=plant,
                created_by="Legacy",
                config_json=json.dumps({}),
                horizon_end=None,
                status="ARCHIVED",
                created_at=created_at,
            )
        load_ids = [entry.get("id") for entry in loads if entry.get("id")]
        db.assign_loads_to_session(session_id, load_ids)


_backfill_legacy_sessions()


@app.route("/session", methods=["GET", "POST"])
def session_setup():
    # Kept for backwards-compatibility with older links/bookmarks.
    # Access is now handled via /login.
    next_url = request.values.get("next") or request.args.get("next")
    return redirect(url_for("login", next=next_url))


@app.route("/session/reset")
def session_reset():
    session.clear()
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    next_url = _safe_next_url(request.values.get("next")) or ""
    profiles = db.list_access_profiles()
    login_profiles = []
    for profile in profiles:
        is_admin = bool(profile.get("is_admin"))
        login_profiles.append(
            {
                "id": profile.get("id"),
                "name": (profile.get("name") or "Unnamed").strip() or "Unnamed",
                "is_admin": is_admin,
                "role_label": "Administrator Account" if is_admin else "Planner Account",
                "focus_plants": _profile_focus_plants(profile),
            }
        )
    selected_profile_id = None
    error = None

    if request.method == "POST":
        profile_id = request.form.get("profile_id")
        try:
            profile_id = int(profile_id)
        except (TypeError, ValueError):
            profile_id = None
        selected_profile_id = profile_id

        profile = db.get_access_profile(profile_id) if profile_id else None
        if not profile:
            error = "Select a valid account."
        elif profile.get("is_admin"):
            password = request.form.get("password") or ""
            expected = (os.environ.get("ADMIN_PASSWORD") or "").strip()
            if not expected:
                if _is_local_dev_mode():
                    expected = "admin"
                elif password == "admin":
                    # Local fallback: allow one known default when env config is missing.
                    expected = "admin"
                else:
                    error = "Admin password is not configured."
            if not error and password != expected:
                error = "Invalid admin password."

        if not error and profile:
            _apply_profile_to_session(profile, reset_filters=True)
            return redirect(next_url or url_for("orders"))

    if selected_profile_id is None and login_profiles:
        selected_profile_id = login_profiles[0]["id"]
    selected_profile = next(
        (profile for profile in login_profiles if profile["id"] == selected_profile_id),
        None,
    )

    return render_template(
        "login.html",
        profiles=login_profiles,
        selected_profile=selected_profile,
        selected_profile_id=selected_profile_id,
        plant_names=PLANT_NAMES,
        error=error,
        next_url=next_url,
    )


def _safe_next_url(value):
    value = (value or "").strip()
    if not value:
        return None
    if value.startswith("/"):
        return value
    return None


def _json_session_expired_response():
    next_url = request.full_path if request else ""
    return jsonify(
        {
            "error": "Session expired",
            "redirect_url": url_for("login", next=next_url),
        }
    ), 401


def _normalize_order_value(value):
    if value is None:
        return ""
    return str(value).strip()


def _line_signature(lines):
    signature = []
    for line in lines or []:
        item = _normalize_order_value(line.get("item"))
        sku = _normalize_order_value(line.get("sku"))
        try:
            qty_value = int(float(line.get("qty") or 0))
        except (TypeError, ValueError):
            qty_value = 0
        signature.append((item, sku, str(qty_value)))
    signature.sort()
    return signature


def _diff_order(existing, incoming, existing_lines=None, incoming_lines=None):
    fields = [
        "cust_name",
        "address1",
        "address2",
        "city",
        "state",
        "zip",
        "line_count",
    ]
    changes = {}
    for field in fields:
        old = _normalize_order_value(existing.get(field))
        new = _normalize_order_value(incoming.get(field))
        if old != new:
            changes[field] = {"from": old, "to": new}
    if _line_signature(existing_lines) != _line_signature(incoming_lines):
        changes["line_items"] = {"from": "changed", "to": "changed"}
    return changes


_DIMENSION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[xX]\s*(\d+(?:\.\d+)?)")


def _category_from_bin(bin_code):
    if not bin_code:
        return None
    normalized = str(bin_code).strip().upper()
    return normalized or None


def _extract_dimensions(value):
    if not value:
        return None
    match = _DIMENSION_RE.search(str(value))
    if not match:
        return None
    try:
        first = float(match.group(1))
        second = float(match.group(2))
    except (TypeError, ValueError):
        return None
    return (first, second)


def _suggest_sku_for_item(item, desc, specs, category_hint=None):
    item_dim = _extract_dimensions(item) or _extract_dimensions(desc)
    if not item_dim:
        return None
    if category_hint:
        candidates = [
            spec for spec in specs if (spec.get("category") or "").upper() == category_hint
        ]
    else:
        candidates = specs
    if not candidates:
        return None
    best = None
    best_score = -1e9
    for spec in candidates:
        spec_dim = spec.get("_dim")
        if not spec_dim:
            continue
        diff = abs(spec_dim[0] - item_dim[0]) + abs(spec_dim[1] - item_dim[1])
        score = 100 - (diff * 10)
        if spec_dim == item_dim:
            score += 40
        if spec.get("category") and spec.get("category") != "UNKNOWN":
            score += 2
        if score > best_score:
            best = spec
            best_score = score
    return best


def _build_unmapped_suggestions(unmapped_items):
    if not unmapped_items:
        return []
    specs = db.list_sku_specs()
    prepared_specs = []
    for spec in specs:
        spec_copy = dict(spec)
        spec_copy["_dim"] = _extract_dimensions(spec.get("sku")) or _extract_dimensions(
            spec.get("description") or spec.get("notes")
        )
        prepared_specs.append(spec_copy)

    grouped = {}
    for entry in unmapped_items:
        item = (entry.get("item") or "").strip().upper()
        if not item:
            continue
        if item not in grouped:
            grouped[item] = {
                "item": item,
                "desc": (entry.get("desc") or "").strip(),
                "bin": (entry.get("bin") or "").strip(),
                "bin_counts": {},
                "count": 0,
            }
        grouped[item]["count"] += 1
        bin_code = (entry.get("bin") or "").strip()
        if bin_code:
            grouped[item]["bin_counts"][bin_code] = (
                grouped[item]["bin_counts"].get(bin_code, 0) + 1
            )

    suggestions = []
    for payload in grouped.values():
        if payload.get("bin_counts"):
            payload["bin"] = max(
                payload["bin_counts"].items(), key=lambda entry: entry[1]
            )[0]
        category_hint = _category_from_bin(payload.get("bin"))
        payload["category_hint"] = category_hint or ""
        suggestion = _suggest_sku_for_item(
            payload["item"],
            payload.get("desc"),
            prepared_specs,
            category_hint=category_hint,
        )
        if suggestion:
            payload["suggested"] = {
                "sku": suggestion.get("sku"),
                "description": suggestion.get("description") or suggestion.get("notes") or "",
                "category": category_hint or suggestion.get("category") or "UNKNOWN",
                "length_with_tongue_ft": suggestion.get("length_with_tongue_ft") or 0,
                "max_stack_step_deck": suggestion.get("max_stack_step_deck") or 1,
                "max_stack_flat_bed": suggestion.get("max_stack_flat_bed") or 1,
            }
        suggestions.append(payload)

    suggestions.sort(key=lambda entry: entry.get("item") or "")
    return suggestions


def _handle_order_upload(file):
    importer = OrderImporter()
    summary = importer.parse_csv(file)
    unmapped_items = summary.get("unmapped_items") or []
    if unmapped_items:
        raise UploadValidationError(
            (
                "Upload blocked: some SKUs are unmapped or missing required dimensions/stack limits. "
                "Add SKU specs, then re-upload."
            ),
            summary=summary,
        )

    orders = summary.get("orders") or []
    so_nums = []
    for order in orders:
        so_num = (order.get("so_num") or "").strip()
        if so_num:
            so_nums.append(so_num)

    existing_map = {}
    if so_nums:
        for entry in db.list_orders_by_so_nums_any(so_nums):
            key = (entry.get("so_num") or "").strip()
            if key:
                existing_map[key] = entry

    existing_lines_map = {}
    if existing_map:
        existing_lines = db.list_order_lines_by_so_nums(list(existing_map.keys()))
        for line in existing_lines:
            key = (line.get("so_num") or "").strip()
            existing_lines_map.setdefault(key, []).append(line)

    incoming_lines_map = {}
    for line in summary.get("order_lines") or []:
        key = (line.get("so_num") or "").strip()
        if key:
            incoming_lines_map.setdefault(key, []).append(line)

    new_orders = 0
    existing_orders = 0
    changed_orders = 0
    unchanged_orders = 0
    reopened_orders = 0
    changed_keys = set()
    changes_payload = []

    seen_at = datetime.utcnow().isoformat(timespec="seconds")
    dropped = []
    for order in orders:
        so_num = (order.get("so_num") or "").strip()
        if not so_num:
            continue
        existing = existing_map.get(so_num)
        if existing:
            existing_orders += 1
            was_closed = (existing.get("status") or "").strip().upper() == "CLOSED"
            if was_closed:
                reopened_orders += 1
            else:
                diff = _diff_order(
                    existing,
                    order,
                    existing_lines=existing_lines_map.get(so_num, []),
                    incoming_lines=incoming_lines_map.get(so_num, []),
                )
                if diff:
                    changed_orders += 1
                    changed_keys.add(so_num)
                    changes_payload.append(
                        {
                            "so_num": so_num,
                            "plant": (order.get("plant") or "").strip(),
                            "changes_json": json.dumps(diff),
                        }
                    )
                else:
                    unchanged_orders += 1
        else:
            new_orders += 1
        order["status"] = "OPEN"
        order["last_seen_at"] = seen_at
        order["closed_at"] = None

    db.upsert_order_lines(summary.get("order_lines") or [])
    db.upsert_orders(orders)

    if so_nums:
        db.mark_orders_seen(so_nums, seen_at=seen_at)
        prior_open = set(db.list_open_order_so_nums())
        current_set = set(so_nums)
        dropped = sorted(prior_open - current_set)
        if dropped:
            db.mark_orders_closed(dropped, closed_at=seen_at)
        db.purge_closed_orders(retention_days=30)

    upload_id = db.add_upload_history(
        {
            "filename": getattr(file, "filename", ""),
            "total_rows": summary.get("total_rows"),
            "total_orders": len(orders),
            "new_orders": new_orders,
            "duplicate_orders": existing_orders,
            "changed_orders": changed_orders,
            "unchanged_orders": unchanged_orders,
            "reopened_orders": reopened_orders,
            "dropped_orders": len(dropped) if so_nums else 0,
            "mapping_rate": summary.get("mapping_rate"),
            "unmapped_count": len(summary.get("unmapped_items") or []),
        }
    )
    if changes_payload:
        db.add_upload_order_changes(upload_id, changes_payload)
    if so_nums:
        db.update_orders_upload_meta(so_nums, upload_id, changed_keys)
    db.add_upload_unmapped_items(upload_id, summary.get("unmapped_items") or [])

    summary["upload_id"] = upload_id
    summary["duplicate_orders"] = existing_orders
    summary["new_orders"] = new_orders
    summary["changed_orders"] = changed_orders
    summary["unchanged_orders"] = unchanged_orders
    summary["reopened_orders"] = reopened_orders
    summary["dropped_orders"] = len(dropped) if so_nums else 0
    return summary


@app.route("/access/switch", methods=["POST"])
def access_switch():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    profile_id = request.form.get("profile_id")
    try:
        profile_id = int(profile_id)
    except (TypeError, ValueError):
        profile_id = None

    profile = db.get_access_profile(profile_id) if profile_id else None
    if not profile:
        return redirect(url_for("orders"))

    _apply_profile_to_session(profile, reset_filters=True)
    next_url = _safe_next_url(request.form.get("next")) or url_for("orders")
    return redirect(next_url)


@app.route("/access/manage", methods=["GET", "POST"])
def access_manage():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
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
                _sync_access_profiles_seed_snapshot()
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
                    _sync_access_profiles_seed_snapshot()
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
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
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
    _sync_access_profiles_seed_snapshot()
    return redirect(url_for("access_manage"))


def _default_optimize_form():
    form_data = dict(load_builder.DEFAULT_BUILD_PARAMS)
    optimizer_defaults = _get_optimizer_default_settings()
    form_data["trailer_type"] = optimizer_defaults["trailer_type"]
    form_data["capacity_feet"] = str(optimizer_defaults["capacity_feet"])
    form_data["max_detour_pct"] = str(optimizer_defaults["max_detour_pct"])
    form_data["time_window_days"] = str(optimizer_defaults["time_window_days"])
    form_data["geo_radius"] = str(optimizer_defaults["geo_radius"])
    form_data["stack_overflow_max_height"] = str(
        optimizer_defaults["stack_overflow_max_height"]
    )
    form_data["max_back_overhang_ft"] = str(
        optimizer_defaults["max_back_overhang_ft"]
    )
    form_data["upper_two_across_max_length_ft"] = str(
        optimizer_defaults.get("upper_two_across_max_length_ft", DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT)
    )
    form_data["upper_deck_exception_max_length_ft"] = str(
        optimizer_defaults.get(
            "upper_deck_exception_max_length_ft",
            DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT,
        )
    )
    form_data["upper_deck_exception_overhang_allowance_ft"] = str(
        optimizer_defaults.get(
            "upper_deck_exception_overhang_allowance_ft",
            DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT,
        )
    )
    form_data["upper_deck_exception_categories"] = stack_calculator.normalize_upper_deck_exception_categories(
        optimizer_defaults.get("upper_deck_exception_categories"),
        default=DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES,
    )
    form_data["ignore_due_date"] = _coerce_bool_value(form_data.get("ignore_due_date"))
    if not form_data.get("orders_start_date"):
        form_data["orders_start_date"] = date.today().strftime("%Y-%m-%d")
    if not form_data["origin_plant"] and PLANT_CODES:
        form_data["origin_plant"] = PLANT_CODES[0]
    return form_data


def _distinct(values):
    return sorted({value for value in values if value})


_HIDDEN_RATE_MATRIX_ROWS = {"DETENTION", "MIN", "NY ZIP 100", "TONU"}


def _is_rate_matrix_display_state(state):
    normalized = (state or "").strip().upper()
    return bool(normalized) and normalized not in _HIDDEN_RATE_MATRIX_ROWS


def _build_rate_matrix(rates):
    plants = sorted({rate["origin_plant"] for rate in rates if rate.get("origin_plant")})
    states = sorted(
        {
            rate["destination_state"]
            for rate in rates
            if _is_rate_matrix_display_state(rate.get("destination_state"))
        }
    )
    matrix = {state: {plant: None for plant in plants} for state in states}
    for rate in rates:
        state = rate.get("destination_state")
        plant = rate.get("origin_plant")
        if state and plant and state in matrix and matrix[state][plant] is None:
            matrix[state][plant] = rate.get("rate_per_mile")
    return plants, states, matrix


def _build_rate_matrix_records(rates):
    plants = sorted({rate["origin_plant"] for rate in rates if rate.get("origin_plant")})
    states = sorted(
        {
            rate["destination_state"]
            for rate in rates
            if _is_rate_matrix_display_state(rate.get("destination_state"))
        }
    )
    matrix = {state: {plant: None for plant in plants} for state in states}
    for rate in rates:
        state = rate.get("destination_state")
        plant = rate.get("origin_plant")
        if not state or not plant or state not in matrix:
            continue
        current = matrix[state][plant]
        if not current or (rate.get("effective_year") or 0) > (current.get("effective_year") or 0):
            matrix[state][plant] = rate
    return plants, states, matrix


def _coerce_non_negative_float(value, default):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    if parsed < 0:
        return 0.0
    return parsed


def _coerce_non_negative_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(parsed, 0)


def _coerce_utilization_grade_thresholds(raw_value):
    defaults = dict(DEFAULT_UTILIZATION_GRADE_THRESHOLDS)
    if not isinstance(raw_value, dict):
        return defaults
    a = _coerce_non_negative_int(raw_value.get("A"), defaults["A"])
    b = _coerce_non_negative_int(raw_value.get("B"), defaults["B"])
    c = _coerce_non_negative_int(raw_value.get("C"), defaults["C"])
    d = _coerce_non_negative_int(raw_value.get("D"), defaults["D"])

    a = min(a, 100)
    b = min(b, 99)
    c = min(c, 99)
    d = min(d, 99)

    if b >= a:
        b = max(a - 1, 0)
    if c >= b:
        c = max(b - 1, 0)
    if d >= c:
        d = max(c - 1, 0)
    return {"A": a, "B": b, "C": c, "D": d}


def _get_planning_float_setting(setting_key, default_value):
    setting = db.get_planning_setting(setting_key) or {}
    return round(_coerce_non_negative_float(setting.get("value_text"), default_value), 2)


def _get_stop_fee_amount():
    return _get_planning_float_setting(STOP_FEE_SETTING_KEY, DEFAULT_STOP_FEE)


def _get_load_minimum_amount():
    return _get_planning_float_setting(MIN_LOAD_COST_SETTING_KEY, DEFAULT_MIN_LOAD_COST)


def _get_fuel_surcharge_per_mile():
    return _get_planning_float_setting(
        FUEL_SURCHARGE_SETTING_KEY,
        DEFAULT_FUEL_SURCHARGE_PER_MILE,
    )


def _get_rates_overview_metrics():
    return {
        "stop_fee": _get_stop_fee_amount(),
        "load_minimum": _get_load_minimum_amount(),
        "fuel_surcharge": _get_fuel_surcharge_per_mile(),
    }


def _normalize_rate_table_key(value, default="DEFAULT"):
    key = str(value or "").strip().upper()
    valid_keys = {option["key"] for option in RATE_TABLE_KEY_OPTIONS}
    if key in valid_keys:
        return key
    return default if default in valid_keys else "DEFAULT"


def _get_rate_table_contexts():
    defaults = dict(DEFAULT_RATE_TABLE_CONTEXTS)
    setting = db.get_planning_setting(RATE_TABLE_CONTEXTS_SETTING_KEY) or {}
    raw_text = (setting.get("value_text") or "").strip()
    parsed = None
    if raw_text:
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            parsed = None
    if isinstance(parsed, dict):
        defaults["default_rate_table_key"] = _normalize_rate_table_key(
            parsed.get("default_rate_table_key"),
            defaults["default_rate_table_key"],
        )
        defaults["carrier_dedicated_ryder_rate_table_key"] = _normalize_rate_table_key(
            parsed.get("carrier_dedicated_ryder_rate_table_key"),
            defaults["carrier_dedicated_ryder_rate_table_key"],
        )
        defaults["trailer_hotshot_rate_table_key"] = _normalize_rate_table_key(
            parsed.get("trailer_hotshot_rate_table_key"),
            defaults["trailer_hotshot_rate_table_key"],
        )
    return defaults


def _get_trailer_assignment_rules():
    defaults = dict(DEFAULT_TRAILER_ASSIGNMENT_RULES)
    setting = db.get_planning_setting(TRAILER_ASSIGNMENT_RULES_SETTING_KEY) or {}
    raw_text = (setting.get("value_text") or "").strip()
    parsed = None
    if raw_text:
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            parsed = None
    if isinstance(parsed, dict):
        if "livestock_wedge_enabled" in parsed:
            defaults["livestock_wedge_enabled"] = _coerce_bool_value(
                parsed.get("livestock_wedge_enabled")
            )
        tokens = parsed.get("livestock_category_tokens")
        if isinstance(tokens, str):
            tokens = [tokens]
        if isinstance(tokens, (list, tuple, set)):
            normalized_tokens = []
            for token in tokens:
                text = str(token or "").strip().upper()
                if text and text not in normalized_tokens:
                    normalized_tokens.append(text)
            if normalized_tokens:
                defaults["livestock_category_tokens"] = normalized_tokens
        if "auto_assign_hotshot_enabled" in parsed:
            defaults["auto_assign_hotshot_enabled"] = _coerce_bool_value(
                parsed.get("auto_assign_hotshot_enabled")
            )
        if "auto_assign_hotshot_utilization_threshold_pct" in parsed:
            defaults["auto_assign_hotshot_utilization_threshold_pct"] = round(
                _coerce_non_negative_float(
                    parsed.get("auto_assign_hotshot_utilization_threshold_pct"),
                    defaults["auto_assign_hotshot_utilization_threshold_pct"],
                ),
                1,
            )
    return defaults


def _build_freight_breakdown(load, stop_fee_amount, fuel_surcharge_per_mile, load_minimum_amount):
    def _as_float(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    estimated_cost = max(_as_float(load.get("estimated_cost"), 0.0), 0.0)
    estimated_miles = max(
        _as_float(load.get("estimated_miles"), _as_float(load.get("route_distance"), 0.0)),
        0.0,
    )
    stop_count = max(int(_as_float(load.get("stop_count"), 0.0)), 0)
    stop_fee = max(_as_float(stop_fee_amount, DEFAULT_STOP_FEE), 0.0)
    fuel_surcharge = max(
        _as_float(fuel_surcharge_per_mile, DEFAULT_FUEL_SURCHARGE_PER_MILE),
        0.0,
    )
    load_minimum = max(_as_float(load_minimum_amount, DEFAULT_MIN_LOAD_COST), 0.0)

    stop_cost = stop_count * stop_fee
    avg_rate_per_mile = _as_float(load.get("rate_per_mile"), 0.0)
    if avg_rate_per_mile <= 0 and estimated_miles > 0:
        avg_rate_per_mile = max((estimated_cost - stop_cost) / estimated_miles, 0.0)

    linehaul_rate_per_mile = max(avg_rate_per_mile - fuel_surcharge, 0.0)
    linehaul_cost = estimated_miles * linehaul_rate_per_mile
    fuel_cost = estimated_miles * fuel_surcharge
    subtotal_before_floor = linehaul_cost + fuel_cost + stop_cost

    adjustment_cost = estimated_cost - subtotal_before_floor
    if abs(adjustment_cost) < 0.01:
        adjustment_cost = 0.0

    min_floor_applied = (
        load_minimum > 0
        and estimated_cost + 0.01 >= load_minimum
        and subtotal_before_floor < load_minimum
    )
    adjustment_label = "Minimum load floor adjustment" if min_floor_applied else "Adjustment"

    return {
        "estimated_miles": round(estimated_miles, 1),
        "stop_count": stop_count,
        "stop_fee": round(stop_fee, 2),
        "stop_cost": round(stop_cost, 2),
        "avg_rate_per_mile": round(avg_rate_per_mile, 4),
        "linehaul_rate_per_mile": round(linehaul_rate_per_mile, 4),
        "linehaul_cost": round(linehaul_cost, 2),
        "fuel_surcharge_per_mile": round(fuel_surcharge, 4),
        "fuel_cost": round(fuel_cost, 2),
        "subtotal_before_floor": round(subtotal_before_floor, 2),
        "adjustment_cost": round(adjustment_cost, 2),
        "adjustment_label": adjustment_label,
        "min_load_cost": round(load_minimum, 2),
        "total_cost": round(estimated_cost, 2),
    }


def _get_optimizer_default_settings():
    defaults = {
        "trailer_type": stack_calculator.normalize_trailer_type(
            load_builder.DEFAULT_BUILD_PARAMS.get("trailer_type"),
            default="STEP_DECK",
        ),
        "capacity_feet": _coerce_non_negative_float(load_builder.DEFAULT_BUILD_PARAMS.get("capacity_feet"), 53),
        "max_detour_pct": _coerce_non_negative_float(load_builder.DEFAULT_BUILD_PARAMS.get("max_detour_pct"), 15),
        "time_window_days": _coerce_non_negative_int(load_builder.DEFAULT_BUILD_PARAMS.get("time_window_days"), 7),
        "geo_radius": _coerce_non_negative_float(load_builder.DEFAULT_BUILD_PARAMS.get("geo_radius"), 100),
        "stack_overflow_max_height": _coerce_non_negative_int(
            load_builder.DEFAULT_BUILD_PARAMS.get("stack_overflow_max_height"),
            DEFAULT_STACK_OVERFLOW_MAX_HEIGHT,
        ),
        "max_back_overhang_ft": _coerce_non_negative_float(
            load_builder.DEFAULT_BUILD_PARAMS.get("max_back_overhang_ft"),
            DEFAULT_MAX_BACK_OVERHANG_FT,
        ),
        "upper_two_across_max_length_ft": _coerce_non_negative_float(
            load_builder.DEFAULT_BUILD_PARAMS.get("upper_two_across_max_length_ft"),
            DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT,
        ),
        "upper_deck_exception_max_length_ft": _coerce_non_negative_float(
            load_builder.DEFAULT_BUILD_PARAMS.get("upper_deck_exception_max_length_ft"),
            DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT,
        ),
        "upper_deck_exception_overhang_allowance_ft": _coerce_non_negative_float(
            load_builder.DEFAULT_BUILD_PARAMS.get("upper_deck_exception_overhang_allowance_ft"),
            DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT,
        ),
        "upper_deck_exception_categories": stack_calculator.normalize_upper_deck_exception_categories(
            load_builder.DEFAULT_BUILD_PARAMS.get("upper_deck_exception_categories"),
            default=DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES,
        ),
    }
    setting = db.get_planning_setting(OPTIMIZER_DEFAULTS_SETTING_KEY) or {}
    raw_text = (setting.get("value_text") or "").strip()
    if raw_text:
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            defaults["trailer_type"] = stack_calculator.normalize_trailer_type(
                parsed.get("trailer_type"),
                default=defaults["trailer_type"],
            )
            defaults["capacity_feet"] = _coerce_non_negative_float(parsed.get("capacity_feet"), defaults["capacity_feet"])
            defaults["max_detour_pct"] = _coerce_non_negative_float(parsed.get("max_detour_pct"), defaults["max_detour_pct"])
            defaults["time_window_days"] = _coerce_non_negative_int(
                parsed.get("time_window_days"),
                defaults["time_window_days"],
            )
            defaults["geo_radius"] = _coerce_non_negative_float(parsed.get("geo_radius"), defaults["geo_radius"])
            defaults["stack_overflow_max_height"] = _coerce_non_negative_int(
                parsed.get("stack_overflow_max_height"),
                defaults["stack_overflow_max_height"],
            )
            defaults["max_back_overhang_ft"] = _coerce_non_negative_float(
                parsed.get("max_back_overhang_ft"),
                defaults["max_back_overhang_ft"],
            )
            defaults["upper_two_across_max_length_ft"] = _coerce_non_negative_float(
                parsed.get("upper_two_across_max_length_ft"),
                defaults["upper_two_across_max_length_ft"],
            )
            defaults["upper_deck_exception_max_length_ft"] = _coerce_non_negative_float(
                parsed.get("upper_deck_exception_max_length_ft"),
                defaults["upper_deck_exception_max_length_ft"],
            )
            defaults["upper_deck_exception_overhang_allowance_ft"] = _coerce_non_negative_float(
                parsed.get("upper_deck_exception_overhang_allowance_ft"),
                defaults["upper_deck_exception_overhang_allowance_ft"],
            )
            defaults["upper_deck_exception_categories"] = stack_calculator.normalize_upper_deck_exception_categories(
                parsed.get("upper_deck_exception_categories"),
                default=defaults["upper_deck_exception_categories"],
            )
    defaults["max_back_overhang_ft"] = round(defaults["max_back_overhang_ft"], 2)
    defaults["upper_two_across_max_length_ft"] = round(defaults["upper_two_across_max_length_ft"], 2)
    defaults["upper_deck_exception_max_length_ft"] = round(
        defaults["upper_deck_exception_max_length_ft"],
        2,
    )
    defaults["upper_deck_exception_overhang_allowance_ft"] = round(
        defaults["upper_deck_exception_overhang_allowance_ft"],
        2,
    )
    defaults["upper_deck_exception_categories"] = stack_calculator.normalize_upper_deck_exception_categories(
        defaults.get("upper_deck_exception_categories"),
        default=DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES,
    )
    return defaults


def _get_stack_capacity_assumptions():
    defaults = _get_optimizer_default_settings()
    return {
        "stack_overflow_max_height": _coerce_non_negative_int(
            defaults.get("stack_overflow_max_height"),
            DEFAULT_STACK_OVERFLOW_MAX_HEIGHT,
        ),
        "max_back_overhang_ft": round(
            _coerce_non_negative_float(
                defaults.get("max_back_overhang_ft"),
                DEFAULT_MAX_BACK_OVERHANG_FT,
            ),
            2,
        ),
        "upper_two_across_max_length_ft": round(
            _coerce_non_negative_float(
                defaults.get("upper_two_across_max_length_ft"),
                DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT,
            ),
            2,
        ),
        "upper_deck_exception_max_length_ft": round(
            _coerce_non_negative_float(
                defaults.get("upper_deck_exception_max_length_ft"),
                DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT,
            ),
            2,
        ),
        "upper_deck_exception_overhang_allowance_ft": round(
            _coerce_non_negative_float(
                defaults.get("upper_deck_exception_overhang_allowance_ft"),
                DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT,
            ),
            2,
        ),
        "upper_deck_exception_categories": stack_calculator.normalize_upper_deck_exception_categories(
            defaults.get("upper_deck_exception_categories"),
            default=DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES,
        ),
    }


def _get_replay_eval_preset():
    defaults = dict(replay_evaluator.DEFAULT_REPLAY_PRESET)
    optimizer_defaults = _get_optimizer_default_settings()
    defaults["trailer_type"] = optimizer_defaults.get("trailer_type") or defaults.get("trailer_type")
    defaults["capacity_feet"] = float(optimizer_defaults.get("capacity_feet") or defaults.get("capacity_feet") or 53.0)
    defaults["max_detour_pct"] = float(optimizer_defaults.get("max_detour_pct") or defaults.get("max_detour_pct") or 15.0)
    defaults["time_window_days"] = int(optimizer_defaults.get("time_window_days") or defaults.get("time_window_days") or 7)
    defaults["geo_radius"] = float(optimizer_defaults.get("geo_radius") or defaults.get("geo_radius") or 100.0)
    defaults["stack_overflow_max_height"] = int(
        optimizer_defaults.get("stack_overflow_max_height")
        or defaults.get("stack_overflow_max_height")
        or DEFAULT_STACK_OVERFLOW_MAX_HEIGHT
    )
    defaults["max_back_overhang_ft"] = float(
        optimizer_defaults.get("max_back_overhang_ft")
        or defaults.get("max_back_overhang_ft")
        or DEFAULT_MAX_BACK_OVERHANG_FT
    )
    defaults["upper_two_across_max_length_ft"] = float(
        optimizer_defaults.get("upper_two_across_max_length_ft")
        or defaults.get("upper_two_across_max_length_ft")
        or DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT
    )
    defaults["upper_deck_exception_max_length_ft"] = float(
        optimizer_defaults.get("upper_deck_exception_max_length_ft")
        or defaults.get("upper_deck_exception_max_length_ft")
        or DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT
    )
    defaults["upper_deck_exception_overhang_allowance_ft"] = float(
        optimizer_defaults.get("upper_deck_exception_overhang_allowance_ft")
        or defaults.get("upper_deck_exception_overhang_allowance_ft")
        or DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT
    )
    defaults["upper_deck_exception_categories"] = stack_calculator.normalize_upper_deck_exception_categories(
        optimizer_defaults.get("upper_deck_exception_categories")
        or defaults.get("upper_deck_exception_categories")
        or DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES,
        default=DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES,
    )

    setting = db.get_planning_setting(REPLAY_EVAL_PRESET_SETTING_KEY) or {}
    raw_text = (setting.get("value_text") or "").strip()
    parsed = None
    if raw_text:
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            parsed = None
    if isinstance(parsed, dict):
        defaults.update(parsed)

    defaults["trailer_type"] = stack_calculator.normalize_trailer_type(
        defaults.get("trailer_type"),
        default="STEP_DECK",
    )
    defaults["capacity_feet"] = _coerce_non_negative_float(defaults.get("capacity_feet"), 53)
    defaults["max_detour_pct"] = _coerce_non_negative_float(defaults.get("max_detour_pct"), 15)
    defaults["time_window_days"] = _coerce_non_negative_int(defaults.get("time_window_days"), 7)
    defaults["geo_radius"] = _coerce_non_negative_float(defaults.get("geo_radius"), 100)
    defaults["stack_overflow_max_height"] = _coerce_non_negative_int(
        defaults.get("stack_overflow_max_height"),
        DEFAULT_STACK_OVERFLOW_MAX_HEIGHT,
    )
    defaults["max_back_overhang_ft"] = round(
        _coerce_non_negative_float(
            defaults.get("max_back_overhang_ft"),
            DEFAULT_MAX_BACK_OVERHANG_FT,
        ),
        2,
    )
    defaults["upper_two_across_max_length_ft"] = round(
        _coerce_non_negative_float(
            defaults.get("upper_two_across_max_length_ft"),
            DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT,
        ),
        2,
    )
    defaults["upper_deck_exception_max_length_ft"] = round(
        _coerce_non_negative_float(
            defaults.get("upper_deck_exception_max_length_ft"),
            DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT,
        ),
        2,
    )
    defaults["upper_deck_exception_overhang_allowance_ft"] = round(
        _coerce_non_negative_float(
            defaults.get("upper_deck_exception_overhang_allowance_ft"),
            DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT,
        ),
        2,
    )
    defaults["upper_deck_exception_categories"] = stack_calculator.normalize_upper_deck_exception_categories(
        defaults.get("upper_deck_exception_categories"),
        default=DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES,
    )
    defaults["ops_parity_enabled"] = _coerce_bool_value(defaults.get("ops_parity_enabled"))
    defaults["ops_parity_max_utilization_pct"] = _coerce_non_negative_float(
        defaults.get("ops_parity_max_utilization_pct"),
        replay_evaluator.DEFAULT_REPLAY_PRESET.get("ops_parity_max_utilization_pct", 120.0),
    )
    defaults["algorithm_version"] = "v2"
    defaults["enforce_time_window"] = bool(defaults.get("enforce_time_window", True))
    return defaults


def _parse_replay_summary(raw_json):
    if not raw_json:
        return {}
    if isinstance(raw_json, dict):
        return raw_json
    try:
        parsed = json.loads(raw_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _get_utilization_grade_thresholds():
    return stack_calculator.get_utilization_grade_thresholds()


def _build_utilization_grade_rows(thresholds):
    a = thresholds["A"]
    b = thresholds["B"]
    c = thresholds["C"]
    d = thresholds["D"]
    f_max = max(d - 1, 0)
    return [
        {"grade": "A", "min_pct": a, "label": f">= {a}%"},
        {"grade": "B", "min_pct": b, "label": f">= {b}% and < {a}%"},
        {"grade": "C", "min_pct": c, "label": f">= {c}% and < {b}%"},
        {"grade": "D", "min_pct": d, "label": f">= {d}% and < {c}%"},
        {"grade": "F", "min_pct": None, "f_max": f_max, "label": f"< {d}%"},
    ]


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


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except (TypeError, ValueError):
            continue
    return None


def _to_est_datetime(value):
    parsed = _parse_datetime(value)
    if not parsed:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(APP_TIMEZONE)


def _format_est_datetime_label(value):
    parsed = _to_est_datetime(value)
    if not parsed:
        return ""
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year} {parsed.strftime('%I:%M %p')} ET"


def _format_datetime_label(value):
    return _format_est_datetime_label(value)


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
        all_loads = load_builder.list_loads(None, include_stack_metrics=False)
    all_loads = [load for load in all_loads if load.get("origin_plant") in allowed_plants]
    loads_for_progress = [load for load in all_loads if load.get("origin_plant") in plant_scope]

    optimized_loads = []
    for load in loads_for_progress:
        build_source = (load.get("build_source") or "OPTIMIZED").upper()
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
    approved_orders = order_status_counts["approved"]
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
    thresholds = _get_utilization_grade_thresholds()
    if utilization_pct >= thresholds["A"]:
        return "A"
    if utilization_pct >= thresholds["B"]:
        return "B"
    if utilization_pct >= thresholds["C"]:
        return "C"
    if utilization_pct >= thresholds["D"]:
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
    return customer_rules.parse_strategic_customers(value_text)


def _line_category_for_trailer_rules(line, sku_specs):
    sku = (line or {}).get("sku")
    spec = (sku_specs or {}).get(sku) if sku else None
    for value in (
        (spec or {}).get("category"),
        (line or {}).get("category"),
        (line or {}).get("bin"),
    ):
        text = str(value or "").strip().upper()
        if text:
            return text
    return ""


def _line_max_unit_length_for_trailer_rules(line, sku_specs):
    sku = (line or {}).get("sku")
    spec = (sku_specs or {}).get(sku) if sku else None
    lengths = [
        (line or {}).get("unit_length_ft"),
        (line or {}).get("length_with_tongue_ft"),
        (spec or {}).get("length_with_tongue_ft"),
    ]
    max_length = 0.0
    for value in lengths:
        parsed = _coerce_non_negative_float(value, 0.0)
        max_length = max(max_length, parsed)
    return max_length


def _auto_trailer_rule_annotation(
    load,
    lines,
    trailer_type,
    schematic,
    sku_specs,
    stop_sequence_map=None,
    assumptions=None,
    trailer_assignment_rules=None,
    strategic_customers=None,
):
    trailer_key = stack_calculator.normalize_trailer_type(trailer_type, default="STEP_DECK")
    rules = trailer_assignment_rules or _get_trailer_assignment_rules()
    strategic_entries = strategic_customers if strategic_customers is not None else []
    exceeds_capacity = bool((schematic or {}).get("exceeds_capacity"))
    build_source = str((load or {}).get("build_source") or "").strip().upper()
    load_status = str((load or {}).get("status") or "").strip().upper()
    is_manual_build = build_source == "MANUAL"
    is_active_draft = load_status in {STATUS_PROPOSED, STATUS_DRAFT, ""}

    if trailer_key == "FLATBED":
        step_schematic, _, _ = _calculate_load_schematic(
            lines,
            sku_specs,
            "STEP_DECK",
            stop_sequence_map=stop_sequence_map,
            assumptions=assumptions,
        )
        if step_schematic.get("exceeds_capacity"):
            return (
                "Auto Trailer Rule",
                "Changed from 53' Step Deck to 53' Flatbed because the load does not fit on the 43' / 10' split.",
            )

    if trailer_key == "HOTSHOT" and not exceeds_capacity and bool(rules.get("auto_assign_hotshot_enabled")):
        return (
            "Auto Trailer Rule",
            "Changed from 53' Step Deck to 40' Hotshot because the load fits on a hotshot trailer.",
        )

    if (
        trailer_key in {"FLATBED", "HOTSHOT"}
        and is_active_draft
        and not is_manual_build
    ):
        return (
            "Auto Trailer Rule",
            "Trailer was auto-selected by draft fit/rule logic for this load.",
        )

    if trailer_key != "WEDGE":
        return "", ""

    categories = {
        _line_category_for_trailer_rules(line, sku_specs)
        for line in (lines or [])
    }
    categories.discard("")
    max_unit_length_ft = max(
        (_line_max_unit_length_for_trailer_rules(line, sku_specs) for line in (lines or [])),
        default=0.0,
    )
    customer_names = {
        str((line or {}).get("cust_name") or "").strip()
        for line in (lines or [])
        if str((line or {}).get("cust_name") or "").strip()
    }
    matching_rules = [
        customer_rules.find_matching_strategic_customer(name, strategic_entries)
        for name in customer_names
    ]
    matching_rules = [entry for entry in matching_rules if entry]

    if any(bool((entry or {}).get("default_wedge_51")) for entry in matching_rules):
        return (
            "Auto Trailer Rule",
            "Changed from 53' Step Deck to 51' Wedge due to strategic customer wedge default.",
        )

    for entry in matching_rules:
        threshold_ft = _coerce_non_negative_float((entry or {}).get("wedge_min_item_length_ft"), 0.0)
        if threshold_ft > 0 and max_unit_length_ft >= threshold_ft:
            return (
                "Auto Trailer Rule",
                f"Changed from 53' Step Deck to 51' Wedge due to customer length rule ({threshold_ft:.0f} ft+).",
            )

    livestock_tokens = {
        str(token or "").strip().upper()
        for token in (rules.get("livestock_category_tokens") or [])
        if str(token or "").strip()
    }
    if bool(rules.get("livestock_wedge_enabled")) and categories.intersection(livestock_tokens):
        return (
            "Auto Trailer Rule",
            "Changed from 53' Step Deck to 51' Wedge because load includes LIVESTOCK-category items.",
        )

    has_tractor_supply = any(customer_rules.is_tractor_supply_customer(name) for name in customer_names)
    has_cargo_category = any(
        category == "CARGO" or category.startswith("CARGO-")
        for category in categories
    )
    if has_tractor_supply and has_cargo_category and max_unit_length_ft >= 16.0:
        return (
            "Auto Trailer Rule",
            "Changed from 53' Step Deck to 51' Wedge for Tractor Supply cargo with 16+ ft units.",
        )

    return "", ""


def _parse_manual_so_nums(raw_text):
    text = str(raw_text or "").strip()
    if not text:
        return []
    parsed = []
    seen = set()
    for token in re.split(r"[\s,;]+", text):
        value = token.strip().strip("\"'")
        if not value:
            continue
        normalized = value.upper()
        if normalized in {"SO_NUM", "ORDER", "ORDERS", "ORDER#", "SO#"}:
            continue
        if value in seen:
            continue
        seen.add(value)
        parsed.append(value)
    return parsed


def _sku_is_planner_input(spec):
    source = (spec.get("source") or "").strip().lower()
    if source == "planner":
        return True
    if source == "system":
        return False
    return bool((spec.get("added_at") or "").strip())


def _sku_source_label(spec):
    return "Planner Input" if _sku_is_planner_input(spec) else "Cheat Sheet"


def _build_source_led_cheat_sheet_rows(specs):
    spec_by_sku = {}
    for spec in specs or []:
        sku_key = (spec.get("sku") or "").strip().upper()
        if sku_key and sku_key not in spec_by_sku:
            spec_by_sku[sku_key] = spec

    importer = OrderImporter()
    rows = []
    with db.get_connection() as connection:
        observed = connection.execute(
            """
            WITH totals AS (
                SELECT
                    UPPER(TRIM(COALESCE(plant, ''))) AS plant,
                    UPPER(TRIM(COALESCE(bin, ''))) AS bin_raw,
                    UPPER(TRIM(COALESCE(item, ''))) AS item_num,
                    COUNT(*) AS line_count,
                    COUNT(DISTINCT COALESCE(NULLIF(TRIM(so_num), ''), printf('ROW_%d', id))) AS order_count
                FROM order_lines
                WHERE TRIM(COALESCE(item, '')) <> ''
                  AND TRIM(COALESCE(plant, '')) <> ''
                GROUP BY 1, 2, 3
            ),
            ranked_desc AS (
                SELECT
                    UPPER(TRIM(COALESCE(plant, ''))) AS plant,
                    UPPER(TRIM(COALESCE(bin, ''))) AS bin_raw,
                    UPPER(TRIM(COALESCE(item, ''))) AS item_num,
                    TRIM(COALESCE(item_desc, '')) AS item_desc,
                    COUNT(*) AS desc_count,
                    ROW_NUMBER() OVER (
                        PARTITION BY
                            UPPER(TRIM(COALESCE(plant, ''))),
                            UPPER(TRIM(COALESCE(bin, ''))),
                            UPPER(TRIM(COALESCE(item, '')))
                        ORDER BY COUNT(*) DESC, TRIM(COALESCE(item_desc, '')) ASC
                    ) AS rn
                FROM order_lines
                WHERE TRIM(COALESCE(item, '')) <> ''
                  AND TRIM(COALESCE(plant, '')) <> ''
                GROUP BY 1, 2, 3, 4
            )
            SELECT
                t.plant,
                t.bin_raw,
                t.item_num,
                COALESCE(d.item_desc, '') AS item_desc,
                t.line_count,
                t.order_count
            FROM totals t
            LEFT JOIN ranked_desc d
                ON d.plant = t.plant
               AND d.bin_raw = t.bin_raw
               AND d.item_num = t.item_num
               AND d.rn = 1
            ORDER BY t.item_num ASC, t.plant ASC, t.bin_raw ASC
            """
        ).fetchall()

    for row in observed:
        plant = (row["plant"] or "").strip().upper()
        bin_raw = (row["bin_raw"] or "").strip().upper()
        item_num = (row["item_num"] or "").strip().upper()
        item_desc = (row["item_desc"] or "").strip()
        bin_code = importer._extract_code(bin_raw)
        mapped_sku = importer.lookup_sku(
            item_num,
            plant=plant,
            bin_code=bin_code,
            bin_raw=bin_raw,
        )

        spec = None
        if mapped_sku:
            spec = spec_by_sku.get(str(mapped_sku).strip().upper())

        category = (spec.get("category") or "").strip() if spec else ""
        mapped_description = ""
        length_with_tongue_ft = None
        max_stack_step_deck = None
        max_stack_flat_bed = None
        mapped_source = ""

        if spec:
            mapped_description = (spec.get("description") or spec.get("notes") or "").strip()
            length_with_tongue_ft = spec.get("length_with_tongue_ft")
            max_stack_step_deck = spec.get("max_stack_step_deck")
            max_stack_flat_bed = spec.get("max_stack_flat_bed")
            mapped_source = _sku_source_label(spec)
            mapping_status = "Mapped"
        elif mapped_sku:
            cargo_length = importer._cargo_length_from_item(item_num, mapped_sku)
            if cargo_length:
                mapped_description = "Derived from cargo length rule"
                category = bin_code or bin_raw or "CARGO"
                length_with_tongue_ft = cargo_length
                max_stack_step_deck = 1
                max_stack_flat_bed = 1
                mapping_status = "Mapped (Cargo Rule)"
            else:
                mapping_status = "Mapped SKU Missing Spec"
        else:
            mapping_status = "Unmapped"

        rows.append(
            {
                "plant": plant,
                "bin": bin_raw,
                "item_num": item_num,
                "item_desc": item_desc,
                "mapped_sku": mapped_sku or "",
                "mapped_description": mapped_description,
                "category": category,
                "length_with_tongue_ft": length_with_tongue_ft,
                "max_stack_step_deck": max_stack_step_deck,
                "max_stack_flat_bed": max_stack_flat_bed,
                "mapped_source": mapped_source,
                "mapping_status": mapping_status,
                "line_count": int(row["line_count"] or 0),
                "order_count": int(row["order_count"] or 0),
            }
        )

    collapsed = {}
    for row in rows:
        key = (row.get("item_num") or "", row.get("bin") or "")
        group = collapsed.setdefault(
            key,
            {
                "item_num": row.get("item_num") or "",
                "bin": row.get("bin") or "",
                "line_count": 0,
                "order_count": 0,
                "desc_weights": {},
                "mapped_variants": {},
            },
        )
        group["line_count"] += int(row.get("line_count") or 0)
        group["order_count"] += int(row.get("order_count") or 0)

        item_desc = (row.get("item_desc") or "").strip()
        if item_desc:
            group["desc_weights"][item_desc] = group["desc_weights"].get(item_desc, 0) + int(
                row.get("line_count") or 0
            )

        mapped_sku = (row.get("mapped_sku") or "").strip()
        variant_key = mapped_sku or "__UNMAPPED__"
        variant = group["mapped_variants"].get(variant_key)
        if not variant:
            variant = {
                "mapped_sku": mapped_sku,
                "mapped_description": row.get("mapped_description") or "",
                "category": row.get("category") or "",
                "length_with_tongue_ft": row.get("length_with_tongue_ft"),
                "max_stack_step_deck": row.get("max_stack_step_deck"),
                "max_stack_flat_bed": row.get("max_stack_flat_bed"),
                "mapped_source": row.get("mapped_source") or "",
                "mapping_status": row.get("mapping_status") or "Unmapped",
                "weight": 0,
            }
            group["mapped_variants"][variant_key] = variant
        variant["weight"] += int(row.get("line_count") or 0)

    deduped_rows = []
    for group in collapsed.values():
        item_desc = ""
        if group["desc_weights"]:
            item_desc = max(
                group["desc_weights"].items(),
                key=lambda pair: (pair[1], pair[0]),
            )[0]

        mapped_variants = [
            payload
            for key, payload in group["mapped_variants"].items()
            if key != "__UNMAPPED__"
        ]

        if not mapped_variants:
            row = {
                "item_num": group["item_num"],
                "item_desc": item_desc,
                "bin": group["bin"],
                "mapped_sku": "",
                "mapped_sku_list": [],
                "mapped_description": "",
                "category": "",
                "length_with_tongue_ft": None,
                "max_stack_step_deck": None,
                "max_stack_flat_bed": None,
                "mapped_source": "",
                "mapping_status": "Unmapped",
                "mapping_conflict": False,
                "line_count": group["line_count"],
                "order_count": group["order_count"],
            }
            deduped_rows.append(row)
            continue

        if len(mapped_variants) == 1:
            winner = mapped_variants[0]
            row = {
                "item_num": group["item_num"],
                "item_desc": item_desc,
                "bin": group["bin"],
                "mapped_sku": winner.get("mapped_sku") or "",
                "mapped_sku_list": [winner.get("mapped_sku")] if winner.get("mapped_sku") else [],
                "mapped_description": winner.get("mapped_description") or "",
                "category": winner.get("category") or "",
                "length_with_tongue_ft": winner.get("length_with_tongue_ft"),
                "max_stack_step_deck": winner.get("max_stack_step_deck"),
                "max_stack_flat_bed": winner.get("max_stack_flat_bed"),
                "mapped_source": winner.get("mapped_source") or "",
                "mapping_status": winner.get("mapping_status") or "Mapped",
                "mapping_conflict": False,
                "line_count": group["line_count"],
                "order_count": group["order_count"],
            }
            deduped_rows.append(row)
            continue

        mapped_skus = sorted(
            [
                variant.get("mapped_sku")
                for variant in mapped_variants
                if (variant.get("mapped_sku") or "").strip()
            ]
        )
        conflict_label = ", ".join(mapped_skus[:3])
        if len(mapped_skus) > 3:
            conflict_label += f" +{len(mapped_skus) - 3} more"
        deduped_rows.append(
            {
                "item_num": group["item_num"],
                "item_desc": item_desc,
                "bin": group["bin"],
                "mapped_sku": conflict_label,
                "mapped_sku_list": mapped_skus,
                "mapped_description": "Multiple mapped SKUs across plants. Review lookup scope rules.",
                "category": "",
                "length_with_tongue_ft": None,
                "max_stack_step_deck": None,
                "max_stack_flat_bed": None,
                "mapped_source": "",
                "mapping_status": "Plant Mapping Conflict",
                "mapping_conflict": True,
                "line_count": group["line_count"],
                "order_count": group["order_count"],
            }
        )

    deduped_rows.sort(key=lambda row: (row.get("item_num") or "", row.get("bin") or ""))
    return deduped_rows


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


def _profile_focus_plants(profile):
    allowed = _profile_allowed_plants(profile)
    defaults = _profile_default_plants(profile, allowed)
    return defaults or allowed or list(PLANT_CODES)


def _apply_profile_to_session(profile, *, reset_filters=False):
    allowed = _profile_allowed_plants(profile)
    default_plants = _profile_default_plants(profile, allowed)

    session[SESSION_PROFILE_ID_KEY] = profile["id"]
    session[SESSION_PROFILE_NAME_KEY] = profile["name"]
    session[SESSION_PROFILE_DEFAULT_PLANTS_KEY] = default_plants
    session.pop(SESSION_ACTIVE_PLANNING_ID_KEY, None)

    session["role"] = ROLE_ADMIN if profile.get("is_admin") else ROLE_PLANNER
    session["allowed_plants"] = allowed

    if reset_filters or session.get("plant_filters") is None:
        session["plant_filters"] = list(default_plants)
        _sync_legacy_plant_filter(session["plant_filters"], allowed)


def _ensure_active_profile():
    profile_id = session.get(SESSION_PROFILE_ID_KEY)
    profile = db.get_access_profile(profile_id) if profile_id else None
    if not profile:
        return None

    # Apply if missing/mismatched or if allowed plants were cleared.
    if session.get(SESSION_PROFILE_ID_KEY) != profile["id"] or not _get_allowed_plants():
        _apply_profile_to_session(profile, reset_filters=True)
    return profile


def _get_session_role():
    role = session.get("role")
    return role if role in {ROLE_ADMIN, ROLE_PLANNER} else None


def _get_active_planning_session_id():
    value = session.get(SESSION_ACTIVE_PLANNING_ID_KEY)
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def _set_active_planning_session_id(session_id):
    if session_id:
        session[SESSION_ACTIVE_PLANNING_ID_KEY] = int(session_id)
    else:
        session.pop(SESSION_ACTIVE_PLANNING_ID_KEY, None)


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


def _sync_planning_session_status(session_id, loads=None):
    if not session_id:
        return None
    planning_session = db.get_planning_session(session_id)
    if not planning_session:
        return None
    current_status = (planning_session.get("status") or "").upper()
    if current_status in {"ARCHIVED", "LEGACY"}:
        return current_status
    if loads is None:
        next_status = db.compute_planning_session_status(session_id) or "DRAFT"
    else:
        total = len(loads)
        approved = sum(
            1 for load in loads if (load.get("status") or "").upper() == STATUS_APPROVED
        )
        next_status = "COMPLETED" if total > 0 and total == approved else "DRAFT"
    if next_status and next_status != current_status:
        db.update_planning_session_status(session_id, next_status)
        planning_session["status"] = next_status
    return planning_session.get("status") or next_status


def _normalize_session_status(value):
    status = (value or "").upper()
    if not status or status == "ACTIVE":
        return "DRAFT"
    return status


def _slug_session_label(value):
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip())
    return cleaned.strip("_").upper() or "PLANNER"


def _serialize_session_config(form_data, params):
    return json.dumps(
        {
            "origin_plant": params.get("origin_plant"),
            "capacity_feet": params.get("capacity_feet"),
            "trailer_type": params.get("trailer_type"),
            "max_detour_pct": params.get("max_detour_pct"),
            "time_window_days": params.get("time_window_days"),
            "geo_radius": params.get("geo_radius"),
            "stack_overflow_max_height": params.get("stack_overflow_max_height"),
            "max_back_overhang_ft": params.get("max_back_overhang_ft"),
            "upper_two_across_max_length_ft": params.get("upper_two_across_max_length_ft"),
            "upper_deck_exception_max_length_ft": params.get("upper_deck_exception_max_length_ft"),
            "upper_deck_exception_overhang_allowance_ft": params.get("upper_deck_exception_overhang_allowance_ft"),
            "upper_deck_exception_categories": params.get("upper_deck_exception_categories") or [],
            "enforce_time_window": params.get("enforce_time_window"),
            "batch_horizon_enabled": params.get("batch_horizon_enabled"),
            "batch_end_date": form_data.get("batch_end_date") or "",
            "orders_start_date": form_data.get("orders_start_date") or "",
            "ignore_due_date": bool(params.get("ignore_due_date")),
            "state_filters": params.get("state_filters") or [],
            "customer_filters": params.get("customer_filters") or [],
            "ignore_past_due": params.get("ignore_past_due"),
            "algorithm_version": params.get("algorithm_version") or "v2",
            "compare_algorithms": bool(params.get("compare_algorithms")),
        }
    )


def _create_planning_session(created_by, plant_code, form_data, params):
    today_value = date.today()
    date_stamp = today_value.strftime("%m%d%Y")
    created_by = (created_by or "").strip() or "Planner"
    planner_slug = _slug_session_label(created_by)
    plant_code = (plant_code or "").strip().upper() or "ALL"
    sequence = db.count_planning_sessions_for_day(created_by, plant_code, today_value.strftime("%Y-%m-%d")) + 1
    session_code = f"{planner_slug}_{date_stamp}_{plant_code}_S{sequence}"
    while db.get_planning_session_by_code(session_code):
        sequence += 1
        session_code = f"{planner_slug}_{date_stamp}_{plant_code}_S{sequence}"
    horizon_end = form_data.get("batch_end_date") if form_data.get("batch_horizon_enabled") else None
    config_json = _serialize_session_config(form_data, params)
    return db.create_planning_session(
        session_code=session_code,
        plant_code=plant_code,
        created_by=created_by,
        config_json=config_json,
        horizon_end=horizon_end or None,
        status="DRAFT",
    )


def _find_resumable_planning_session(plant_code):
    if not plant_code:
        return None
    sessions = db.list_planning_sessions({"plant_code": plant_code})
    for entry in sessions:
        status = _normalize_session_status(entry.get("status"))
        if status == "DRAFT" and (entry.get("load_count") or 0) > 0:
            return entry
    return None


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


def _build_load_thumbnail(load, sku_specs, stop_color_palette=None, max_blocks=4):
    lines = db.list_load_lines(load["id"])
    if not lines:
        return []

    trailer_type = stack_calculator.normalize_trailer_type(load.get("trailer_type"), default="STEP_DECK")
    zip_coords = geo_utils.load_zip_coordinates()
    ordered_stops = _ordered_stops_for_lines(lines, load.get("origin_plant"), zip_coords)
    ordered_stops = _apply_load_route_direction(ordered_stops, load=load)
    stop_sequence_map = _stop_sequence_map_from_ordered_stops(ordered_stops)
    order_colors = _build_order_colors_for_lines(
        lines,
        stop_sequence_map=stop_sequence_map,
        stop_palette=stop_color_palette,
    )
    schematic, line_items, _ = _calculate_load_schematic(
        lines,
        sku_specs,
        trailer_type,
        stop_sequence_map=stop_sequence_map,
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


def _line_stop_key(state, zip_code):
    state_value = (state or "").strip().upper()
    raw_zip = (zip_code or "").strip()
    normalized_zip = geo_utils.normalize_zip(raw_zip)
    zip_value = normalized_zip or raw_zip
    return f"{state_value}|{zip_value}"


def _stop_sequence_map_from_ordered_stops(ordered_stops):
    sequence = {}
    for idx, stop in enumerate(ordered_stops or [], start=1):
        key = _line_stop_key(stop.get("state"), stop.get("zip"))
        if key and key not in sequence:
            sequence[key] = idx
    return sequence


def _normalize_hex_color(value, fallback):
    fallback_text = str(fallback or FALLBACK_STOP_COLOR).strip().upper()
    if not HEX_COLOR_PATTERN.fullmatch(fallback_text):
        fallback_text = FALLBACK_STOP_COLOR
    raw = str(value or "").strip().upper()
    if not raw:
        return fallback_text
    if not raw.startswith("#"):
        raw = f"#{raw}"
    if HEX_COLOR_PATTERN.fullmatch(raw):
        return raw
    return fallback_text


def _get_stop_color_palette():
    defaults = list(DEFAULT_STOP_COLOR_PALETTE)
    setting = db.get_planning_setting(STOP_COLOR_PALETTE_SETTING_KEY) or {}
    raw_value = (setting.get("value_text") or "").strip()
    if not raw_value:
        return defaults
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return defaults

    if isinstance(parsed, list):
        normalized = []
        for idx, default_color in enumerate(defaults):
            source = parsed[idx] if idx < len(parsed) else default_color
            normalized.append(_normalize_hex_color(source, default_color))
        return normalized

    if isinstance(parsed, dict):
        normalized = []
        for idx, default_color in enumerate(defaults, start=1):
            source = parsed.get(str(idx))
            if source is None:
                source = parsed.get(idx)
            normalized.append(_normalize_hex_color(source, default_color))
        return normalized

    return defaults


def _color_for_stop_sequence(stop_sequence, stop_palette=None):
    palette = list(stop_palette or _get_stop_color_palette())
    if not palette:
        return FALLBACK_STOP_COLOR
    sequence = _coerce_int_value(stop_sequence, 0)
    if sequence <= 0:
        return FALLBACK_STOP_COLOR
    return palette[(sequence - 1) % len(palette)]


def _build_order_colors_for_lines(lines, stop_sequence_map=None, stop_palette=None):
    palette = list(stop_palette or _get_stop_color_palette())
    order_ids = []
    order_stop_map = {}
    for line in lines or []:
        order_id = (line.get("so_num") or "").strip()
        if not order_id:
            continue
        if order_id not in order_ids:
            order_ids.append(order_id)
        stop_sequence = None
        if stop_sequence_map:
            stop_sequence = stop_sequence_map.get(
                _line_stop_key(line.get("state"), line.get("zip"))
            )
        stop_value = _coerce_int_value(stop_sequence, 0)
        if stop_value <= 0:
            continue
        current = order_stop_map.get(order_id)
        if current is None or stop_value < current:
            order_stop_map[order_id] = stop_value

    order_colors = {}
    fallback_idx = 0
    for order_id in order_ids:
        stop_sequence = order_stop_map.get(order_id)
        if stop_sequence:
            order_colors[order_id] = _color_for_stop_sequence(stop_sequence, palette)
            continue
        if palette:
            order_colors[order_id] = palette[fallback_idx % len(palette)]
            fallback_idx += 1
        else:
            order_colors[order_id] = FALLBACK_STOP_COLOR
    return order_colors


def _requires_return_to_origin(lines):
    if not lines:
        return False
    strategic_setting = db.get_planning_setting("strategic_customers") or {}
    strategic_customers = _parse_strategic_customers(strategic_setting.get("value_text") or "")
    return any(
        bool(
            (
                customer_rules.find_matching_strategic_customer(
                    (line or {}).get("cust_name") or "",
                    strategic_customers,
                )
                or {}
            ).get("requires_return_to_origin")
        )
        for line in (lines or [])
    )


def _ordered_stops_for_lines(lines, origin_plant, zip_coords, return_to_origin=None):
    stop_map = {}
    for line in lines or []:
        state = (line.get("state") or "").strip().upper()
        zip_code = (line.get("zip") or "").strip()
        key = _line_stop_key(state, zip_code)
        if key in stop_map:
            continue
        normalized_zip = geo_utils.normalize_zip(zip_code)
        coords = zip_coords.get(normalized_zip) if normalized_zip else None
        stop_map[key] = {
            "state": state,
            "zip": zip_code,
            "coords": coords,
        }
    stops = list(stop_map.values())
    origin_coords = geo_utils.plant_coords_for_code(origin_plant)
    if return_to_origin is None:
        return_to_origin = _requires_return_to_origin(lines)
    if origin_coords:
        route_result = routing_service.get_routing_service().build_route(
            origin_coords,
            stops,
            return_to_origin=bool(return_to_origin),
            objective="distance",
            include_geometry=False,
        )
        ordered_stops = route_result.get("ordered_stops") or []
        if ordered_stops:
            return ordered_stops
        return tsp_solver.solve_route(
            origin_coords,
            stops,
            return_to_origin=bool(return_to_origin),
        )
    return stops


def _is_load_route_reversed(load):
    return bool(_coerce_int_value((load or {}).get("route_reversed"), 0))


def _apply_load_route_direction(ordered_stops, load=None, reverse_route=None):
    stops = list(ordered_stops or [])
    if reverse_route is None:
        reverse_route = _is_load_route_reversed(load)
    if reverse_route and len(stops) > 1:
        stops.reverse()
    return stops


def _load_route_display_metrics(load, route_nodes, use_cached_route=True):
    expected_leg_count = max(len(route_nodes) - 1, 0)
    stored_legs = load.get("route_legs") or []
    route_legs = []
    use_cached_route = bool(use_cached_route)

    if use_cached_route and isinstance(stored_legs, list) and len(stored_legs) == expected_leg_count:
        for value in stored_legs:
            try:
                route_legs.append(round(float(value)))
            except (TypeError, ValueError):
                route_legs.append(None)
    else:
        for idx in range(expected_leg_count):
            origin_leg = route_nodes[idx].get("coords")
            dest_leg = route_nodes[idx + 1].get("coords")
            if origin_leg and dest_leg:
                route_legs.append(round(geo_utils.haversine_distance_coords(origin_leg, dest_leg)))
            else:
                route_legs.append(None)

    stored_total = load.get("route_total_miles")
    total_route_distance = None
    if use_cached_route:
        if stored_total is None:
            stored_total = load.get("route_distance")
        try:
            total_route_distance = float(stored_total) if stored_total is not None else None
        except (TypeError, ValueError):
            total_route_distance = None
    if total_route_distance is None:
        total_route_distance = sum(leg for leg in route_legs if leg is not None)

    stored_geometry = load.get("route_geometry") or []
    route_geometry = []
    if use_cached_route and isinstance(stored_geometry, list):
        for point in stored_geometry:
            if (
                isinstance(point, (list, tuple))
                and len(point) >= 2
                and point[0] is not None
                and point[1] is not None
            ):
                try:
                    route_geometry.append([float(point[0]), float(point[1])])
                except (TypeError, ValueError):
                    continue
    if not route_geometry:
        for node in route_nodes:
            coords = node.get("coords")
            if coords:
                route_geometry.append([float(coords[0]), float(coords[1])])

    return {
        "route_legs": route_legs,
        "route_distance": round(total_route_distance) if total_route_distance else 0,
        "route_geometry": route_geometry,
    }


def _build_route_stops_for_lines(lines, zip_coords):
    stops = []
    stop_map = {}
    for line in lines or []:
        zip_code = geo_utils.normalize_zip(line.get("zip"))
        state = (line.get("state") or "").strip().upper()
        key = f"{zip_code}|{state}"
        if key not in stop_map:
            coords = zip_coords.get(zip_code) if zip_code else None
            stop_map[key] = {
                "zip": zip_code,
                "state": state,
                "lat": coords[0] if coords else None,
                "lng": coords[1] if coords else None,
                "coords": coords,
            }
        if line.get("cust_name"):
            stop_map[key].setdefault("customers", set()).add(line.get("cust_name"))
        if line.get("city") and not stop_map[key].get("city"):
            stop_map[key]["city"] = line.get("city")

    for stop in stop_map.values():
        stops.append(
            {
                "zip": stop.get("zip"),
                "state": stop.get("state"),
                "city": stop.get("city") or "",
                "customers": sorted(stop.get("customers") or []),
                "lat": stop.get("lat"),
                "lng": stop.get("lng"),
                "coords": stop.get("coords"),
            }
        )
    return stops


def _build_schematic_line_items(lines, sku_specs, trailer_type, stop_sequence_map=None):
    trailer_key = stack_calculator.normalize_trailer_type(trailer_type, default="STEP_DECK")
    is_step_deck = trailer_key.startswith("STEP_DECK")
    line_items = []
    for line in lines or []:
        sku = line.get("sku")
        spec = sku_specs.get(sku) if sku else None
        if is_step_deck:
            max_stack = (spec or {}).get("max_stack_step_deck") or (spec or {}).get("max_stack_flat_bed") or 1
            upper_max_stack = (spec or {}).get("max_stack_flat_bed") or max_stack or 1
        else:
            max_stack = (spec or {}).get("max_stack_flat_bed") or 1
            upper_max_stack = max_stack
        stop_sequence = None
        if stop_sequence_map:
            stop_sequence = stop_sequence_map.get(_line_stop_key(line.get("state"), line.get("zip")))
        line_items.append(
            {
                "item": line.get("item"),
                "item_desc": line.get("item_desc"),
                "sku": sku,
                "qty": line.get("qty") or 0,
                "unit_length_ft": line.get("unit_length_ft") or 0,
                "max_stack_height": max_stack,
                "upper_deck_max_stack_height": upper_max_stack,
                "category": (spec or {}).get("category", ""),
                "order_id": line.get("so_num"),
                "stop_sequence": stop_sequence,
            }
        )
    return line_items


def _calculate_load_schematic(
    lines,
    sku_specs,
    trailer_type,
    stop_sequence_map=None,
    assumptions=None,
):
    order_numbers = {
        (line.get("so_num") or "").strip()
        for line in (lines or [])
        if (line.get("so_num") or "").strip()
    }
    assumptions = assumptions or _get_stack_capacity_assumptions()
    line_items = _build_schematic_line_items(
        lines,
        sku_specs,
        trailer_type,
        stop_sequence_map=stop_sequence_map,
    )
    preserve_order_contiguity = len(order_numbers) <= 1
    schematic = stack_calculator.calculate_stack_configuration(
        line_items,
        trailer_type=trailer_type,
        preserve_order_contiguity=preserve_order_contiguity,
        stack_overflow_max_height=assumptions.get("stack_overflow_max_height"),
        max_back_overhang_ft=assumptions.get("max_back_overhang_ft"),
        upper_two_across_max_length_ft=assumptions.get("upper_two_across_max_length_ft"),
        upper_deck_exception_max_length_ft=assumptions.get("upper_deck_exception_max_length_ft"),
        upper_deck_exception_overhang_allowance_ft=assumptions.get("upper_deck_exception_overhang_allowance_ft"),
        upper_deck_exception_categories=assumptions.get("upper_deck_exception_categories"),
    )
    return schematic, line_items, order_numbers


def _coerce_float_value(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _coerce_int_value(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _coerce_bool_value(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on", "y"}


def _warning(code, message, position_id=None, deck=None):
    payload = {"code": code, "message": message, "severity": "warning"}
    if position_id:
        payload["position_id"] = position_id
    if deck:
        payload["deck"] = deck
    return payload


def _trailer_config_for_type(trailer_type):
    trailer_key = stack_calculator.normalize_trailer_type(trailer_type, default="STEP_DECK")
    base = stack_calculator.TRAILER_CONFIGS.get(
        trailer_key,
        stack_calculator.TRAILER_CONFIGS["STEP_DECK"],
    )
    return {
        "type": trailer_key,
        "capacity": _coerce_float_value(base.get("capacity"), 53.0),
        "lower": _coerce_float_value(base.get("lower"), 53.0),
        "upper": _coerce_float_value(base.get("upper"), 0.0),
    }


def _build_schematic_units(lines, sku_specs, trailer_type, stop_sequence_map=None, order_colors=None):
    trailer_key = stack_calculator.normalize_trailer_type(trailer_type, default="STEP_DECK")
    is_step_deck = trailer_key.startswith("STEP_DECK")
    order_colors = order_colors or {}
    units = []
    for line_idx, line in enumerate(lines or [], start=1):
        sku = line.get("sku")
        spec = sku_specs.get(sku) if sku else None
        if is_step_deck:
            max_stack = (
                (spec or {}).get("max_stack_step_deck")
                or (spec or {}).get("max_stack_flat_bed")
                or 1
            )
            upper_max_stack = (spec or {}).get("max_stack_flat_bed") or max_stack or 1
        else:
            max_stack = (spec or {}).get("max_stack_flat_bed") or 1
            upper_max_stack = max_stack
        max_stack = max(_coerce_int_value(max_stack, 1), 1)
        upper_max_stack = max(_coerce_int_value(upper_max_stack, max_stack), 1)
        qty = max(_coerce_int_value(line.get("qty"), 0), 0)
        if qty <= 0:
            continue
        unit_length_ft = _coerce_float_value(line.get("unit_length_ft"), 0.0)
        order_id = (line.get("so_num") or "").strip()
        stop_sequence = None
        if stop_sequence_map:
            stop_sequence = stop_sequence_map.get(_line_stop_key(line.get("state"), line.get("zip")))
        order_line_id = line.get("order_line_id") or line.get("id") or line_idx
        for unit_index in range(qty):
            unit_id = f"ol{order_line_id}-u{unit_index + 1}"
            units.append(
                {
                    "unit_id": unit_id,
                    "order_id": order_id,
                    "order_line_id": order_line_id,
                    "sku": sku,
                    "item": line.get("item"),
                    "item_desc": line.get("item_desc"),
                    "unit_length_ft": unit_length_ft,
                    "max_stack": max_stack,
                    "upper_max_stack": upper_max_stack,
                    "category": (spec or {}).get("category", ""),
                    "stop_sequence": stop_sequence,
                    "color": order_colors.get(order_id, "#334155"),
                }
            )
    return units


def _unit_item_key_from_unit(unit):
    return (
        unit.get("order_id") or "",
        unit.get("sku") or "",
        unit.get("item") or "",
        unit.get("item_desc") or "",
        round(_coerce_float_value(unit.get("unit_length_ft"), 0.0), 4),
        max(_coerce_int_value(unit.get("max_stack"), 1), 1),
        max(_coerce_int_value(unit.get("upper_max_stack"), unit.get("max_stack") or 1), 1),
        unit.get("category") or "",
    )


def _unit_item_key_from_schematic_item(item):
    return (
        item.get("order_id") or "",
        item.get("sku") or "",
        item.get("item") or "",
        item.get("item_desc") or "",
        round(_coerce_float_value(item.get("unit_length_ft"), 0.0), 4),
        max(_coerce_int_value(item.get("max_stack"), 1), 1),
        max(_coerce_int_value(item.get("upper_max_stack"), item.get("max_stack") or 1), 1),
        item.get("category") or "",
    )


def _layout_from_schematic(schematic, units):
    buckets = {}
    for unit in units:
        key = _unit_item_key_from_unit(unit)
        buckets.setdefault(key, []).append(unit["unit_id"])
    remaining_by_id = {unit["unit_id"]: unit for unit in units}
    positions = []

    def _pop_first_matching(order_id, sku):
        for unit_id, unit in list(remaining_by_id.items()):
            if (unit.get("order_id") or "") != (order_id or ""):
                continue
            if (unit.get("sku") or "") != (sku or ""):
                continue
            remaining_by_id.pop(unit_id, None)
            key = _unit_item_key_from_unit(unit)
            bucket = buckets.get(key) or []
            if unit_id in bucket:
                bucket.remove(unit_id)
            return unit_id
        return None

    for idx, pos in enumerate((schematic or {}).get("positions") or [], start=1):
        unit_ids = []
        for item in pos.get("items") or []:
            needed = max(_coerce_int_value(item.get("units"), 0), 0)
            if needed <= 0:
                continue
            item_key = _unit_item_key_from_schematic_item(item)
            bucket = buckets.get(item_key) or []
            while needed > 0 and bucket:
                unit_id = bucket.pop(0)
                if unit_id not in remaining_by_id:
                    continue
                remaining_by_id.pop(unit_id, None)
                unit_ids.append(unit_id)
                needed -= 1
            while needed > 0:
                fallback_id = _pop_first_matching(item.get("order_id"), item.get("sku"))
                if not fallback_id:
                    break
                unit_ids.append(fallback_id)
                needed -= 1
        if unit_ids:
            positions.append(
                {
                    "position_id": f"p{idx}",
                    "deck": (pos.get("deck") or "lower"),
                    "unit_ids": unit_ids,
                }
            )

    for unit_id in sorted(remaining_by_id.keys()):
        positions.append(
            {
                "position_id": f"p{len(positions) + 1}",
                "deck": "lower",
                "unit_ids": [unit_id],
            }
        )

    return {"positions": positions}


def _normalize_edit_layout(layout, units_by_id, trailer_type):
    config = _trailer_config_for_type(trailer_type)
    has_upper = config["upper"] > 0
    raw_positions = (layout or {}).get("positions") or []
    normalized_positions = []
    seen = set()

    for raw in raw_positions:
        deck = (raw.get("deck") or "lower").strip().lower()
        if deck not in {"lower", "upper"}:
            deck = "lower"
        if deck == "upper" and not has_upper:
            deck = "lower"
        raw_unit_ids = raw.get("unit_ids") or []
        unit_ids = []
        for value in raw_unit_ids:
            unit_id = str(value or "").strip()
            if not unit_id or unit_id not in units_by_id:
                continue
            if unit_id in seen:
                raise ValueError(f"Duplicate unit in layout: {unit_id}")
            seen.add(unit_id)
            unit_ids.append(unit_id)
        if unit_ids:
            normalized_positions.append(
                {
                    "position_id": "",
                    "deck": deck,
                    "unit_ids": unit_ids,
                }
            )

    expected_ids = set(units_by_id.keys())
    if expected_ids != seen:
        missing = sorted(expected_ids - seen)
        extra = sorted(seen - expected_ids)
        details = []
        if missing:
            details.append(f"missing units: {', '.join(missing[:5])}")
        if extra:
            details.append(f"unknown units: {', '.join(extra[:5])}")
        raise ValueError("Invalid layout payload (" + "; ".join(details) + ")")

    for idx, pos in enumerate(normalized_positions, start=1):
        pos["position_id"] = f"p{idx}"

    return {"positions": normalized_positions}


def _aggregate_position_items(units):
    grouped = []
    for unit in units:
        signature = (
            unit.get("item"),
            unit.get("sku"),
            unit.get("item_desc"),
            unit.get("category"),
            max(_coerce_int_value(unit.get("max_stack"), 1), 1),
            max(_coerce_int_value(unit.get("upper_max_stack"), unit.get("max_stack") or 1), 1),
            _coerce_float_value(unit.get("unit_length_ft"), 0.0),
            unit.get("order_id"),
            unit.get("stop_sequence"),
        )
        if grouped and grouped[-1]["signature"] == signature:
            grouped[-1]["units"] += 1
            continue
        grouped.append(
            {
                "signature": signature,
                "item": unit.get("item"),
                "sku": unit.get("sku"),
                "item_desc": unit.get("item_desc"),
                "category": unit.get("category"),
                "max_stack": max(_coerce_int_value(unit.get("max_stack"), 1), 1),
                "upper_max_stack": max(
                    _coerce_int_value(unit.get("upper_max_stack"), unit.get("max_stack") or 1),
                    1,
                ),
                "unit_length_ft": _coerce_float_value(unit.get("unit_length_ft"), 0.0),
                "order_id": unit.get("order_id"),
                "stop_sequence": unit.get("stop_sequence"),
                "units": 1,
            }
        )
    for group in grouped:
        group.pop("signature", None)
    return grouped


def _unit_capacity_fraction(max_stack):
    return 1.0 / max(_coerce_int_value(max_stack, 1), 1)


def _position_allows_single_overflow(
    units,
    stack_overflow_max_height,
    max_stack_utilization_multiplier,
    capacity_used=None,
):
    threshold = _coerce_int_value(stack_overflow_max_height, 0)
    if threshold <= 0:
        return False
    if capacity_used is None:
        capacity_used = sum(
            _unit_capacity_fraction(unit.get("max_stack"))
            for unit in (units or [])
        )
    if capacity_used <= (1.0 + 1e-6):
        return False
    if capacity_used > (max_stack_utilization_multiplier + 1e-6):
        return False

    for idx, unit in enumerate(units or []):
        unit_max_stack = max(_coerce_int_value(unit.get("max_stack"), 1), 1)
        if unit_max_stack < threshold:
            continue
        if (capacity_used - _unit_capacity_fraction(unit_max_stack)) > (1.0 + 1e-6):
            continue
        base_heights = {
            max(_coerce_int_value(base_unit.get("max_stack"), 1), 1)
            for base_idx, base_unit in enumerate(units or [])
            if base_idx != idx
        }
        # Must already be a mixed-height base stack before adding overflow.
        if len(base_heights) >= 2:
            return True
    return False


def _build_schematic_from_layout(layout, units_by_id, trailer_type, assumptions=None):
    assumptions = assumptions or _get_stack_capacity_assumptions()
    stack_overflow_max_height = _coerce_non_negative_int(
        assumptions.get("stack_overflow_max_height"),
        DEFAULT_STACK_OVERFLOW_MAX_HEIGHT,
    )
    max_back_overhang_ft = round(
        _coerce_non_negative_float(
            assumptions.get("max_back_overhang_ft"),
            DEFAULT_MAX_BACK_OVERHANG_FT,
        ),
        2,
    )
    upper_two_across_max_length_ft = round(
        _coerce_non_negative_float(
            assumptions.get("upper_two_across_max_length_ft"),
            DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT,
        ),
        2,
    )
    upper_deck_exception_max_length_ft = round(
        _coerce_non_negative_float(
            assumptions.get("upper_deck_exception_max_length_ft"),
            DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT,
        ),
        2,
    )
    upper_deck_exception_overhang_allowance_ft = round(
        _coerce_non_negative_float(
            assumptions.get("upper_deck_exception_overhang_allowance_ft"),
            DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT,
        ),
        2,
    )
    upper_deck_exception_categories = stack_calculator.normalize_upper_deck_exception_categories(
        assumptions.get("upper_deck_exception_categories"),
        default=DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES,
    )
    max_stack_utilization_multiplier = (
        1.0 + (1.0 / stack_overflow_max_height)
        if stack_overflow_max_height > 0
        else 1.0
    )
    trailer_config = _trailer_config_for_type(trailer_type)
    lower_length = trailer_config["lower"]
    upper_length = trailer_config["upper"]
    capacity = trailer_config["capacity"]
    has_upper = upper_length > 0

    positions = []
    warnings = []
    upper_deck_length_exceeds = []
    effective_units_by_position = {}

    def _effective_unit_max_stack(unit, deck_name):
        if deck_name == "upper" and trailer_config["type"].startswith("STEP_DECK"):
            return max(
                _coerce_int_value(
                    unit.get("upper_max_stack"),
                    unit.get("max_stack") or 1,
                ),
                1,
            )
        return max(_coerce_int_value(unit.get("max_stack"), 1), 1)

    for idx, raw_pos in enumerate((layout or {}).get("positions") or [], start=1):
        unit_ids = raw_pos.get("unit_ids") or []
        units = [units_by_id.get(unit_id) for unit_id in unit_ids if unit_id in units_by_id]
        units = [unit for unit in units if unit]
        if not units:
            continue
        length_ft = max(_coerce_float_value(unit.get("unit_length_ft"), 0.0) for unit in units)
        capacity_used = sum(
            1.0 / max(_coerce_int_value(unit.get("max_stack"), 1), 1)
            for unit in units
        )
        deck = (raw_pos.get("deck") or "lower").strip().lower()
        if deck not in {"lower", "upper"}:
            deck = "lower"
        if deck == "upper" and not has_upper:
            deck = "lower"
        position_id = raw_pos.get("position_id") or f"p{idx}"

        effective_units = []
        capacity_used = 0.0
        for unit in units:
            effective_max_stack = _effective_unit_max_stack(unit, deck)
            capacity_used += 1.0 / effective_max_stack
            adjusted = dict(unit)
            adjusted["max_stack"] = effective_max_stack
            effective_units.append(adjusted)
        effective_units_by_position[position_id] = effective_units

        if deck == "upper":
            position_probe = {
                "length_ft": length_ft,
                "items": [{"category": unit.get("category")} for unit in units],
            }
            upper_length_limit = stack_calculator.upper_deck_position_length_limit_ft(
                position_probe,
                trailer_config,
                upper_deck_exception_max_length_ft,
                upper_deck_exception_categories,
            )
            too_long_units = [
                unit
                for unit in units
                if _coerce_float_value(unit.get("unit_length_ft"), 0.0) > (upper_length_limit + 1e-6)
            ]
            if too_long_units:
                upper_deck_length_exceeds.append(
                    (position_id, upper_length_limit)
                )

        aggregated_items = _aggregate_position_items(units)
        if deck == "upper" and trailer_config["type"].startswith("STEP_DECK"):
            for item in aggregated_items:
                item["max_stack"] = max(
                    _coerce_int_value(
                        item.get("upper_max_stack"),
                        item.get("max_stack") or 1,
                    ),
                    1,
                )

        positions.append(
            {
                "position_id": position_id,
                "deck": deck,
                "length_ft": round(length_ft, 2),
                "items": aggregated_items,
                "capacity_used": round(capacity_used, 4),
                "overflow_applied": False,
                "overflow_note": None,
                "units_count": len(units),
                "top_stop_sequence": units[-1].get("stop_sequence"),
                "top_length_ft": _coerce_float_value(units[-1].get("unit_length_ft"), length_ft),
            }
        )

    upper_usage_meta = stack_calculator.apply_upper_usage_metadata(
        positions,
        trailer_config,
        upper_two_across_max_length_ft,
    )
    stack_index_by_position_id = stack_calculator.stack_display_index_map(
        positions,
        trailer_config=trailer_config,
    )

    for idx, (position_id, upper_length_limit) in enumerate(upper_deck_length_exceeds, start=1):
        stack_idx = int(stack_index_by_position_id.get(position_id, idx))
        warnings.append(
            _warning(
                "ITEM_TOO_BIG_FOR_UPPER_DECK",
                f"Stack {stack_idx}: one or more items exceed {upper_length_limit:.1f} ft upper deck limit.",
                position_id=position_id,
                deck="upper",
            )
        )

    for idx, pos in enumerate(positions, start=1):
        position_id = pos.get("position_id") or f"p{idx}"
        stack_idx = int(stack_index_by_position_id.get(position_id, idx))
        deck = (pos.get("deck") or "lower").strip().lower() or "lower"
        two_across_applied = bool(pos.get("two_across_applied")) and deck == "upper"
        capacity_used = _coerce_float_value(pos.get("capacity_used"), 0.0)
        effective_units = effective_units_by_position.get(position_id) or []
        overflow_applied = _position_allows_single_overflow(
            effective_units,
            stack_overflow_max_height=stack_overflow_max_height,
            max_stack_utilization_multiplier=max_stack_utilization_multiplier,
            capacity_used=capacity_used,
        )
        overflow_note = None
        if two_across_applied:
            pos["overflow_applied"] = False
            pos["overflow_note"] = None
            continue
        if capacity_used > (max_stack_utilization_multiplier + 1e-6):
            warnings.append(
                _warning(
                    "STACK_TOO_HIGH",
                    (
                        f"Stack {stack_idx} is {capacity_used * 100:.0f}% overfilled relative "
                        "to SKU-specific stacking maximums."
                    ),
                    position_id=position_id,
                    deck=deck,
                )
            )
        elif capacity_used > (1.0 + 1e-6):
            if overflow_applied:
                overflow_note = (
                    f"Stack {stack_idx} is {capacity_used * 100:.0f}% overfilled relative to "
                    "SKU-specific stacking maximums."
                )
                warnings.append(
                    _warning(
                        "STACK_OVERFLOW_ALLOWANCE_USED",
                        overflow_note,
                        position_id=position_id,
                        deck=deck,
                    )
                )
            else:
                warnings.append(
                    _warning(
                        "STACK_TOO_HIGH",
                        f"Stack {stack_idx} is {capacity_used * 100:.0f}% overfilled relative to SKU-specific stacking maximums.",
                        position_id=position_id,
                        deck=deck,
                    )
                )
        pos["overflow_applied"] = bool(overflow_applied)
        pos["overflow_note"] = overflow_note

    lower_total = sum(
        _coerce_float_value(pos.get("length_ft"), 0.0)
        for pos in positions
        if (pos.get("deck") or "lower") == "lower"
    )
    upper_total_raw = sum(
        _coerce_float_value(pos.get("length_ft"), 0.0)
        for pos in positions
        if (pos.get("deck") or "lower") == "upper"
    )
    upper_total_effective = sum(
        _coerce_float_value(pos.get("effective_length_ft"), _coerce_float_value(pos.get("length_ft"), 0.0))
        for pos in positions
        if (pos.get("deck") or "lower") == "upper"
    )

    def _append_overhang_warning(deck_name, overhang_ft, allowance_ft, deck_key):
        if overhang_ft <= 0.05:
            return
        if overhang_ft <= (allowance_ft + 1e-6):
            warnings.append(
                _warning(
                    "BACK_OVERHANG_IN_ALLOWANCE",
                    (
                        f"{deck_name} deck back overhang is {overhang_ft:.1f} ft "
                        f"(allowance {allowance_ft:.1f} ft)."
                    ),
                    deck=deck_key,
                )
            )
            return
        warnings.append(
            _warning(
                "ITEM_HANGS_OVER_DECK",
                (
                    f"{deck_name} deck back overhang is {overhang_ft:.1f} ft, "
                    f"exceeding allowance by {overhang_ft - allowance_ft:.1f} ft."
                ),
                deck=deck_key,
            )
        )

    _append_overhang_warning(
        "Lower",
        max(lower_total - lower_length, 0.0),
        max_back_overhang_ft,
        "lower",
    )
    if upper_length > 0:
        upper_eval = stack_calculator.evaluate_upper_deck_overhang(
            positions,
            trailer_config,
            max_back_overhang_ft=max_back_overhang_ft,
            upper_deck_exception_max_length_ft=upper_deck_exception_max_length_ft,
            upper_deck_exception_overhang_allowance_ft=upper_deck_exception_overhang_allowance_ft,
            upper_deck_exception_categories=upper_deck_exception_categories,
        )
        _append_overhang_warning(
            "Upper",
            upper_eval["upper_overhang_ft"],
            upper_eval["allowed_overhang_ft"],
            "upper",
        )

    for pos in positions:
        deck_length = upper_length if pos.get("deck") == "upper" else lower_length
        width_length = (
            _coerce_float_value(
                pos.get("effective_length_ft"),
                _coerce_float_value(pos.get("length_ft"), 0.0),
            )
            if (pos.get("deck") or "lower") == "upper"
            else _coerce_float_value(pos.get("length_ft"), 0.0)
        )
        if deck_length > 0:
            pos["width_pct"] = min(
                round((width_length / deck_length) * 100, 1),
                100,
            )
        else:
            pos["width_pct"] = 0

    total_linear_feet = lower_total + upper_total_effective
    lower_credit = 0.0
    upper_credit_raw = 0.0
    upper_length_used = 0.0
    for pos in positions:
        length_ft = _coerce_float_value(pos.get("length_ft"), 0.0)
        effective_length_ft = _coerce_float_value(pos.get("effective_length_ft"), length_ft)
        capacity_used = _coerce_float_value(pos.get("capacity_used"), 0.0)
        if pos.get("overflow_applied"):
            multiplier = min(capacity_used, max_stack_utilization_multiplier)
        else:
            multiplier = min(capacity_used, 1.0)
        multiplier = max(multiplier, 0.0)
        credit = (effective_length_ft if (pos.get("deck") or "lower") == "upper" else length_ft) * multiplier
        if (pos.get("deck") or "lower") == "upper":
            upper_credit_raw += credit
            upper_length_used += effective_length_ft
        else:
            lower_credit += credit

    upper_credit = upper_credit_raw
    if (
        trailer_config["type"].startswith("STEP_DECK")
        and upper_length > 0
        and upper_length_used > 0
        and upper_length_used < (upper_length - 1e-6)
    ):
        # Normalize occupied upper-deck stacks to the full 10' basis.
        upper_credit *= (upper_length / upper_length_used)

    total_credit_feet = lower_credit + upper_credit
    utilization_pct = (total_credit_feet / capacity) * 100 if capacity > 0 else 0.0
    max_stack_height = max((pos.get("units_count") or 0 for pos in positions), default=0)
    compatibility_issues = stack_calculator.check_stacking_compatibility(
        positions,
        trailer_config=trailer_config,
    )
    exceeds_capacity = stack_calculator.capacity_overflow_feet(
        {
            "positions": positions,
            "trailer_type": trailer_config["type"],
            "lower_deck_length": lower_length,
            "upper_deck_length": upper_length,
            "max_back_overhang_ft": max_back_overhang_ft,
            "upper_deck_exception_max_length_ft": upper_deck_exception_max_length_ft,
            "upper_deck_exception_overhang_allowance_ft": upper_deck_exception_overhang_allowance_ft,
            "upper_deck_exception_categories": upper_deck_exception_categories,
        }
    ) > 0.0
    utilization_grade = _utilization_grade(utilization_pct)

    for issue in compatibility_issues:
        warnings.append(_warning("COMPATIBILITY_ISSUE", issue))

    return (
        {
            "positions": positions,
            "total_linear_feet": round(total_linear_feet, 1),
            "utilization_pct": round(utilization_pct, 1),
            "max_stack_height": max_stack_height,
            "compatibility_issues": compatibility_issues,
            "exceeds_capacity": exceeds_capacity,
            "utilization_credit_ft": round(total_credit_feet, 1),
            "utilization_grade": utilization_grade,
            "trailer_type": trailer_config["type"],
            "capacity_feet": capacity,
            "lower_deck_length": lower_length,
            "upper_deck_length": upper_length,
            "lower_deck_used_length_ft": round(lower_total, 1),
            "upper_deck_raw_length_ft": round(upper_total_raw, 1),
            "upper_deck_effective_length_ft": round(upper_total_effective, 1),
            "upper_two_across_applied_count": int(upper_usage_meta.get("paired_positions") or 0),
            "upper_two_across_max_length_ft": round(
                _coerce_non_negative_float(
                    upper_usage_meta.get("threshold_ft"),
                    upper_two_across_max_length_ft,
                ),
                2,
            ),
            "stack_overflow_max_height": stack_overflow_max_height,
            "max_back_overhang_ft": max_back_overhang_ft,
            "upper_deck_exception_max_length_ft": upper_deck_exception_max_length_ft,
            "upper_deck_exception_overhang_allowance_ft": upper_deck_exception_overhang_allowance_ft,
            "upper_deck_exception_categories": list(upper_deck_exception_categories),
            "max_stack_utilization_multiplier": round(
                max_stack_utilization_multiplier,
                4,
            ),
        },
        warnings,
    )


def _remap_layout_for_trailer(layout, trailer_type):
    config = _trailer_config_for_type(trailer_type)
    has_upper = config["upper"] > 0
    remapped = {"positions": []}
    for pos in (layout or {}).get("positions") or []:
        deck = (pos.get("deck") or "lower").strip().lower()
        if deck not in {"lower", "upper"}:
            deck = "lower"
        if deck == "upper" and not has_upper:
            deck = "lower"
        remapped["positions"].append(
            {
                "position_id": pos.get("position_id"),
                "deck": deck,
                "unit_ids": list(pos.get("unit_ids") or []),
            }
        )
    return remapped


def _build_schematic_fragment_payload(load_data, status=None, tab=None):
    status = status or (load_data.get("status") or STATUS_PROPOSED).upper()
    tab = (tab or "").strip().lower()
    schematic_html = render_template(
        "partials/load_schematic_card.html",
        load=load_data,
        status=status,
        tab=tab,
    )
    return {
        "schematic_html": schematic_html,
        "utilization_pct": round(load_data.get("utilization_pct") or 0),
        "utilization_grade": (load_data.get("schematic") or {}).get("utilization_grade") or "F",
        "over_capacity": bool(load_data.get("over_capacity")),
        "exceeds_capacity": bool((load_data.get("schematic") or {}).get("exceeds_capacity")),
        "warnings": list(load_data.get("schematic_warnings") or []),
        "warning_count": int(load_data.get("schematic_warning_count") or 0),
        "assumptions": dict(load_data.get("stack_assumptions") or {}),
    }


def _build_load_schematic_edit_payload(load_id):
    load = db.get_load(load_id)
    if not load:
        return None

    trailer_type = stack_calculator.normalize_trailer_type(load.get("trailer_type"), default="STEP_DECK")
    assumptions = _get_stack_capacity_assumptions()
    lines = db.list_load_lines(load_id)
    sku_specs = {spec["sku"]: spec for spec in db.list_sku_specs()}
    stop_color_palette = _get_stop_color_palette()

    zip_coords = geo_utils.load_zip_coordinates()
    ordered_stops = _ordered_stops_for_lines(lines, load.get("origin_plant"), zip_coords)
    ordered_stops = _apply_load_route_direction(ordered_stops, load=load)
    stop_sequence_map = _stop_sequence_map_from_ordered_stops(ordered_stops)
    order_colors = _build_order_colors_for_lines(
        lines,
        stop_sequence_map=stop_sequence_map,
        stop_palette=stop_color_palette,
    )
    base_schematic, _, _ = _calculate_load_schematic(
        lines,
        sku_specs,
        trailer_type,
        stop_sequence_map=stop_sequence_map,
        assumptions=assumptions,
    )

    units = _build_schematic_units(
        lines,
        sku_specs,
        trailer_type,
        stop_sequence_map=stop_sequence_map,
        order_colors=order_colors,
    )
    units_by_id = {unit["unit_id"]: unit for unit in units}
    base_layout = _layout_from_schematic(base_schematic, units)
    layout = base_layout
    override = db.get_load_schematic_override(load_id)
    if override and (override.get("trailer_type") or "").strip().upper() == trailer_type:
        try:
            override_layout = json.loads(override.get("layout_json") or "{}")
            layout = _normalize_edit_layout(override_layout, units_by_id, trailer_type)
        except (json.JSONDecodeError, ValueError):
            layout = base_layout

    schematic, warnings = _build_schematic_from_layout(
        layout,
        units_by_id,
        trailer_type,
        assumptions=assumptions,
    )
    status = (load.get("status") or STATUS_PROPOSED).upper()

    return {
        "load_id": load_id,
        "status": status,
        "trailer_type": trailer_type,
        "can_edit": status != STATUS_APPROVED,
        "units": units,
        "layout": layout,
        "base_layout": base_layout,
        "metrics": {
            "utilization_pct": schematic.get("utilization_pct") or 0,
            "utilization_grade": schematic.get("utilization_grade") or "F",
            "total_linear_feet": schematic.get("total_linear_feet") or 0,
            "utilization_credit_ft": schematic.get("utilization_credit_ft") or 0,
            "exceeds_capacity": bool(schematic.get("exceeds_capacity")),
        },
        "warnings": warnings,
        "warning_count": len(warnings),
        "assumptions": assumptions,
        "schematic": schematic,
        "order_colors": order_colors,
        "load": load,
    }


def _build_orders_snapshot(orders, today=None):
    today = today or date.today()
    active_orders = [order for order in orders if not order.get("is_excluded")]
    next_14_end = today + timedelta(days=14)
    snapshot = {
        "total": len(active_orders),
        "past_due": 0,
        "due_next_14": 0,
        "due_14_plus": 0,
        "unassigned": 0,
    }
    for order in active_orders:
        due_date = _parse_date(order.get("due_date"))
        if due_date:
            if due_date < today:
                snapshot["past_due"] += 1
            elif due_date <= next_14_end:
                snapshot["due_next_14"] += 1
            else:
                snapshot["due_14_plus"] += 1
        else:
            snapshot["due_14_plus"] += 1
        if not order.get("is_assigned"):
            snapshot["unassigned"] += 1
    return snapshot


def _filter_out_past_due_orders(orders):
    filtered = []
    for order in orders or []:
        due_status = (order.get("due_status") or "").upper()
        if due_status == "PAST_DUE":
            continue
        filtered.append(order)
    return filtered


def _count_active_orders_by_plant_from_rows(orders, plants=None):
    counts = {plant: 0 for plant in (plants or [])}
    allowed = set(plants or [])
    for order in orders or []:
        if _coerce_bool_value(order.get("is_excluded")):
            continue
        plant_code = _normalize_plant_code(order.get("plant"))
        if not plant_code:
            continue
        if allowed and plant_code not in allowed:
            continue
        counts[plant_code] = counts.get(plant_code, 0) + 1
    return counts


def _clean_query_params(values):
    cleaned = {}
    for key, value in (values or {}).items():
        if value is None:
            continue
        normalized = str(value).strip()
        if not normalized:
            continue
        cleaned[key] = normalized
    return cleaned


def _require_session():
    profile = _ensure_active_profile()
    if not profile or not _get_allowed_plants():
        next_url = request.full_path if request else ""
        return redirect(url_for("login", next=next_url))
    return None


def _require_admin():
    if _get_session_role() != ROLE_ADMIN:
        abort(403)


def _can_access_planning_session(planning_session):
    if not planning_session:
        return False
    plant_code = _normalize_plant_code(planning_session.get("plant_code"))
    allowed_plants = set(_get_allowed_plants())
    if plant_code and plant_code not in allowed_plants:
        return False
    if _get_session_role() == ROLE_ADMIN:
        return True

    profile_name = (_get_session_profile_name() or "").strip()
    created_by = (planning_session.get("created_by") or "").strip()
    if not profile_name:
        return False
    return created_by.casefold() == profile_name.casefold()


def _get_scoped_planning_session_or_404(session_id):
    planning_session = db.get_planning_session(session_id)
    if not planning_session or not _can_access_planning_session(planning_session):
        abort(404)
    return planning_session


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


def _planning_session_year_suffix(planning_session):
    if not planning_session:
        return _year_suffix()
    created_dt = _parse_datetime(planning_session.get("created_at"))
    created_date = created_dt.date() if created_dt else _parse_date(planning_session.get("created_at"))
    return _year_suffix(created_date or date.today())


def _reserve_session_load_number(planning_session, fallback_plant_code, starting_sequence=None):
    if not planning_session:
        return {"error": "session_not_found"}
    session_id = planning_session.get("id")
    plant_code = (
        (planning_session.get("plant_code") or "").strip().upper()
        or (fallback_plant_code or "").strip().upper()
    )
    year_suffix = _planning_session_year_suffix(planning_session)
    return db.reserve_planning_session_load_number(
        session_id,
        plant_code,
        year_suffix,
        starting_sequence=starting_sequence,
    )


def _normalize_tutorial_relpath(value):
    if value is None:
        return ""
    normalized = str(value).strip().replace("\\", "/").lstrip("/")
    if not normalized:
        return ""
    clean = os.path.normpath(normalized).replace("\\", "/")
    if clean in {"..", "."} or clean.startswith("../"):
        return ""
    return clean


def _tutorial_static_file_exists(static_relpath):
    normalized = _normalize_tutorial_relpath(static_relpath)
    if not normalized:
        return False
    static_root = os.path.abspath(app.static_folder or "static")
    candidate = os.path.abspath(os.path.join(static_root, normalized))
    try:
        if os.path.commonpath([static_root, candidate]) != static_root:
            return False
    except ValueError:
        return False
    return os.path.isfile(candidate)


def _coerce_tutorial_audience(value):
    if not isinstance(value, list):
        return ["all"]
    audience = []
    for entry in value:
        token = str(entry).strip().lower()
        if token in TUTORIAL_ALLOWED_AUDIENCE and token not in audience:
            audience.append(token)
    return audience or ["all"]


def _parse_tutorial_step_note(raw_note):
    if isinstance(raw_note, str):
        note_text = raw_note.strip()
        return ("tip", note_text) if note_text else ("", "")
    if isinstance(raw_note, dict):
        note_text = str(raw_note.get("text") or "").strip()
        note_tone = str(raw_note.get("tone") or "tip").strip().lower()
        if note_tone not in TUTORIAL_NOTE_LABELS:
            note_tone = "tip"
        return (note_tone, note_text) if note_text else ("", "")
    return "", ""


def _load_tutorial_manifest(manifest_path=None):
    resolved_path = manifest_path or TUTORIAL_MANIFEST_PATH
    manifest = {
        "version": 1,
        "modules": [],
        "error": "",
        "manifest_path": resolved_path,
    }
    if not os.path.isfile(resolved_path):
        logger.warning("Tutorial manifest not found at %s", resolved_path)
        manifest["error"] = "Tutorial content is unavailable right now."
        return manifest

    try:
        with open(resolved_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load tutorial manifest at %s: %s", resolved_path, exc)
        manifest["error"] = "Tutorial content is unavailable right now."
        return manifest

    if not isinstance(payload, dict):
        logger.warning("Tutorial manifest must be a JSON object: %s", resolved_path)
        manifest["error"] = "Tutorial content is unavailable right now."
        return manifest

    version = payload.get("version")
    if isinstance(version, int) and version > 0:
        manifest["version"] = version

    raw_modules = payload.get("modules")
    if not isinstance(raw_modules, list):
        logger.warning("Tutorial manifest is missing a valid modules list: %s", resolved_path)
        manifest["error"] = "Tutorial content is unavailable right now."
        return manifest

    for module_idx, raw_module in enumerate(raw_modules, start=1):
        if not isinstance(raw_module, dict):
            logger.warning("Skipping tutorial module #%s: expected object.", module_idx)
            continue
        slug = str(raw_module.get("slug") or "").strip().lower()
        title = str(raw_module.get("title") or "").strip()
        route_hint = str(raw_module.get("route_hint") or "").strip()
        summary = str(raw_module.get("summary") or "").strip()
        raw_steps = raw_module.get("steps")
        if not slug or not title or not summary:
            logger.warning("Skipping tutorial module #%s: missing slug/title/summary.", module_idx)
            continue
        if not isinstance(raw_steps, list) or not raw_steps:
            logger.warning("Skipping tutorial module '%s': steps must be a non-empty list.", slug)
            continue

        module = {
            "slug": slug,
            "title": title,
            "route_hint": route_hint,
            "summary": summary,
            "audience": _coerce_tutorial_audience(raw_module.get("audience")),
            "steps": [],
        }

        for step_idx, raw_step in enumerate(raw_steps, start=1):
            if not isinstance(raw_step, dict):
                logger.warning("Skipping tutorial step #%s in module '%s': expected object.", step_idx, slug)
                continue
            step_id = str(raw_step.get("id") or "").strip().lower()
            step_title = str(raw_step.get("title") or "").strip()
            instruction = str(raw_step.get("instruction") or "").strip()
            media = raw_step.get("media") if isinstance(raw_step.get("media"), dict) else {}
            media_type = str(media.get("type") or "").strip().lower()
            media_src = _normalize_tutorial_relpath(media.get("src"))
            if (
                not step_id
                or not step_title
                or not instruction
                or media_type not in TUTORIAL_ALLOWED_MEDIA_TYPES
                or not media_src
            ):
                logger.warning(
                    "Skipping tutorial step #%s in module '%s': invalid id/content/media.",
                    step_idx,
                    slug,
                )
                continue

            poster_src = _normalize_tutorial_relpath(media.get("poster"))
            media_alt = str(media.get("alt") or "").strip()
            if media_type == "image" and not media_alt:
                media_alt = f"{title} - {step_title}"
            note_tone, note_text = _parse_tutorial_step_note(raw_step.get("note"))
            module["steps"].append(
                {
                    "id": step_id,
                    "title": step_title,
                    "instruction": instruction,
                    "note_tone": note_tone,
                    "note_label": TUTORIAL_NOTE_LABELS.get(note_tone, ""),
                    "note_text": note_text,
                    "media": {
                        "type": media_type,
                        "src": media_src,
                        "alt": media_alt,
                        "caption": str(media.get("caption") or "").strip(),
                        "poster": poster_src,
                        "exists": _tutorial_static_file_exists(media_src),
                        "poster_exists": bool(poster_src and _tutorial_static_file_exists(poster_src)),
                    },
                }
            )

        if not module["steps"]:
            logger.warning("Skipping tutorial module '%s': no valid steps were found.", slug)
            continue

        manifest["modules"].append(module)

    if not manifest["modules"] and not manifest["error"]:
        manifest["error"] = "Tutorial content is unavailable right now."
    return manifest


def _coerce_filter_list(values):
    if values is None:
        return []
    if isinstance(values, (list, tuple, set)):
        source = values
    else:
        source = [values]
    cleaned = []
    for value in source:
        text = str(value or "").strip()
        if text:
            cleaned.append(text)
    return cleaned


def _reoptimize_form_data(plant_code, session_id=None):
    form_data = _default_optimize_form()
    form_data["origin_plant"] = plant_code

    settings = db.get_optimizer_settings(plant_code) or {}
    if settings.get("capacity_feet") is not None:
        form_data["capacity_feet"] = str(settings.get("capacity_feet"))
    if settings.get("trailer_type"):
        form_data["trailer_type"] = stack_calculator.normalize_trailer_type(
            settings.get("trailer_type"),
            default=form_data.get("trailer_type"),
        )
    if settings.get("max_detour_pct") is not None:
        form_data["max_detour_pct"] = str(settings.get("max_detour_pct"))
    if settings.get("time_window_days") is not None:
        form_data["time_window_days"] = str(settings.get("time_window_days"))
    if settings.get("geo_radius") is not None:
        form_data["geo_radius"] = str(settings.get("geo_radius"))
    if settings.get("stack_overflow_max_height") is not None:
        form_data["stack_overflow_max_height"] = str(settings.get("stack_overflow_max_height"))
    if settings.get("max_back_overhang_ft") is not None:
        form_data["max_back_overhang_ft"] = str(settings.get("max_back_overhang_ft"))
    if settings.get("upper_two_across_max_length_ft") is not None:
        form_data["upper_two_across_max_length_ft"] = str(settings.get("upper_two_across_max_length_ft"))
    if settings.get("upper_deck_exception_max_length_ft") is not None:
        form_data["upper_deck_exception_max_length_ft"] = str(settings.get("upper_deck_exception_max_length_ft"))
    if settings.get("upper_deck_exception_overhang_allowance_ft") is not None:
        form_data["upper_deck_exception_overhang_allowance_ft"] = str(
            settings.get("upper_deck_exception_overhang_allowance_ft")
        )
    if settings.get("upper_deck_exception_categories") is not None:
        form_data["upper_deck_exception_categories"] = stack_calculator.normalize_upper_deck_exception_categories(
            settings.get("upper_deck_exception_categories"),
            default=DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES,
        )

    if not session_id:
        return form_data

    planning_session = db.get_planning_session(session_id)
    if not planning_session or not planning_session.get("config_json"):
        return form_data

    try:
        session_config = json.loads(planning_session.get("config_json") or "{}")
    except json.JSONDecodeError:
        return form_data

    if session_config.get("capacity_feet") is not None:
        form_data["capacity_feet"] = str(session_config.get("capacity_feet"))
    if session_config.get("trailer_type"):
        form_data["trailer_type"] = stack_calculator.normalize_trailer_type(
            session_config.get("trailer_type"),
            default=form_data.get("trailer_type"),
        )
    if session_config.get("max_detour_pct") is not None:
        form_data["max_detour_pct"] = str(session_config.get("max_detour_pct"))
    if session_config.get("time_window_days") is not None:
        form_data["time_window_days"] = str(session_config.get("time_window_days"))
    if session_config.get("geo_radius") is not None:
        form_data["geo_radius"] = str(session_config.get("geo_radius"))
    if session_config.get("stack_overflow_max_height") is not None:
        form_data["stack_overflow_max_height"] = str(session_config.get("stack_overflow_max_height"))
    if session_config.get("max_back_overhang_ft") is not None:
        form_data["max_back_overhang_ft"] = str(session_config.get("max_back_overhang_ft"))
    if session_config.get("upper_two_across_max_length_ft") is not None:
        form_data["upper_two_across_max_length_ft"] = str(session_config.get("upper_two_across_max_length_ft"))
    if session_config.get("upper_deck_exception_max_length_ft") is not None:
        form_data["upper_deck_exception_max_length_ft"] = str(
            session_config.get("upper_deck_exception_max_length_ft")
        )
    if session_config.get("upper_deck_exception_overhang_allowance_ft") is not None:
        form_data["upper_deck_exception_overhang_allowance_ft"] = str(
            session_config.get("upper_deck_exception_overhang_allowance_ft")
        )
    if session_config.get("upper_deck_exception_categories") is not None:
        form_data["upper_deck_exception_categories"] = stack_calculator.normalize_upper_deck_exception_categories(
            session_config.get("upper_deck_exception_categories"),
            default=DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES,
        )

    form_data["opt_toggles"] = "1"
    if session_config.get("enforce_time_window", True):
        form_data["enforce_time_window"] = "1"
    else:
        form_data["enforce_time_window"] = "0"

    batch_end_date = (session_config.get("batch_end_date") or "").strip()
    if session_config.get("batch_horizon_enabled") and batch_end_date:
        form_data["batch_horizon_enabled"] = "1"
        form_data["batch_end_date"] = batch_end_date
    else:
        form_data["batch_horizon_enabled"] = "0"
        form_data["batch_end_date"] = ""

    form_data["opt_states"] = _coerce_filter_list(session_config.get("state_filters"))
    form_data["opt_customers"] = _coerce_filter_list(session_config.get("customer_filters"))
    ignore_due_date = bool(session_config.get("ignore_due_date", False))
    form_data["ignore_due_date"] = ignore_due_date
    orders_start_date = (session_config.get("orders_start_date") or "").strip()
    if not orders_start_date and session_config.get("ignore_past_due", True):
        orders_start_date = date.today().strftime("%Y-%m-%d")
    form_data["orders_start_date"] = orders_start_date or date.today().strftime("%Y-%m-%d")
    return form_data


def _reoptimize_for_plant(plant_code, session_id=None, created_by=None, speed_profile="standard"):
    if not plant_code:
        return {"errors": {"origin_plant": "Missing plant code."}}
    if session_id:
        db.clear_unapproved_loads(session_id=session_id)
    else:
        db.clear_unapproved_loads(plant_code)
    form_data = _reoptimize_form_data(plant_code, session_id=session_id)
    speed_mode = (speed_profile or "").strip().lower()
    if speed_mode == "fast":
        form_data["__reopt_speed"] = "fast"
    return load_builder.build_loads(
        form_data,
        reset_proposed=False,
        store_settings=False,
        session_id=session_id,
        created_by=created_by,
        include_baseline=False,
    )


def _reopt_scope_key(plant_code, session_id=None):
    normalized_plant = (plant_code or "").strip().upper()
    if session_id:
        return f"{normalized_plant}:session:{int(session_id)}"
    return f"{normalized_plant}:plant"


def _trim_reopt_jobs(now_ts=None):
    current_ts = now_ts or datetime.utcnow().timestamp()
    active_ids = []
    finished_rows = []
    for job_id, job in _REOPT_JOBS.items():
        status = (job.get("status") or "").lower()
        if status == "running":
            active_ids.append(job_id)
            continue
        finished_at = job.get("finished_at")
        if finished_at:
            try:
                finished_ts = datetime.fromisoformat(finished_at).timestamp()
            except ValueError:
                finished_ts = current_ts
        else:
            finished_ts = current_ts
        age = current_ts - finished_ts
        if age > REOPT_JOB_RETENTION_SEC:
            finished_rows.append((job_id, finished_ts, True))
        else:
            finished_rows.append((job_id, finished_ts, False))

    for job_id, _, should_prune in finished_rows:
        if should_prune:
            _REOPT_JOBS.pop(job_id, None)

    active_count = len(active_ids)
    retained_finished = [
        (job_id, finished_ts)
        for job_id, finished_ts, should_prune in finished_rows
        if not should_prune
    ]
    if active_count + len(retained_finished) <= REOPT_JOB_MAX_ENTRIES:
        return

    retained_finished.sort(key=lambda item: item[1])
    while active_count + len(retained_finished) > REOPT_JOB_MAX_ENTRIES and retained_finished:
        stale_id, _ = retained_finished.pop(0)
        _REOPT_JOBS.pop(stale_id, None)


def _start_reopt_job(plant_code, session_id=None, created_by=None, speed_profile="standard"):
    normalized_plant = (plant_code or "").strip().upper()
    if not normalized_plant:
        raise ValueError("Missing plant code.")
    normalized_session = int(session_id) if session_id else None
    scope_key = _reopt_scope_key(normalized_plant, normalized_session)
    now_iso = datetime.utcnow().isoformat(timespec="seconds")

    with _REOPT_JOB_LOCK:
        _trim_reopt_jobs()
        for existing_id, existing in _REOPT_JOBS.items():
            if existing.get("scope_key") != scope_key:
                continue
            if (existing.get("status") or "").lower() == "running":
                return existing_id

        job_id = uuid.uuid4().hex
        _REOPT_JOBS[job_id] = {
            "id": job_id,
            "scope_key": scope_key,
            "plant_code": normalized_plant,
            "session_id": normalized_session,
            "speed_profile": (speed_profile or "standard").strip().lower() or "standard",
            "status": "running",
            "created_at": now_iso,
            "started_at": now_iso,
            "finished_at": "",
            "error": "",
            "success_message": "",
        }

    worker = threading.Thread(
        target=_run_reopt_job,
        args=(
            job_id,
            normalized_plant,
            normalized_session,
            created_by,
            (speed_profile or "standard").strip().lower() or "standard",
        ),
        daemon=True,
        name=f"reopt-{normalized_plant}-{job_id[:8]}",
    )
    worker.start()
    return job_id


def _run_reopt_job(job_id, plant_code, session_id=None, created_by=None, speed_profile="standard"):
    try:
        result = _reoptimize_for_plant(
            plant_code,
            session_id=session_id,
            created_by=created_by,
            speed_profile=speed_profile,
        ) or {}
        errors = result.get("errors") or {}
        success_message = (result.get("success_message") or "").strip()
        error_message = ""
        status = "done"
        if errors:
            status = "failed"
            try:
                first_error = next(iter(errors.values()))
            except StopIteration:
                first_error = "Re-optimization failed."
            error_message = str(first_error or "Re-optimization failed.").strip()

        finished_iso = datetime.utcnow().isoformat(timespec="seconds")
        with _REOPT_JOB_LOCK:
            job = _REOPT_JOBS.get(job_id)
            if job is not None:
                job.update(
                    {
                        "status": status,
                        "finished_at": finished_iso,
                        "error": error_message,
                        "success_message": success_message,
                    }
                )
                _trim_reopt_jobs()
    except Exception:  # pragma: no cover - defensive path
        logger.exception(
            "Background re-optimization failed for plant=%s session_id=%s",
            plant_code,
            session_id,
        )
        finished_iso = datetime.utcnow().isoformat(timespec="seconds")
        with _REOPT_JOB_LOCK:
            job = _REOPT_JOBS.get(job_id)
            if job is not None:
                job.update(
                    {
                        "status": "failed",
                        "finished_at": finished_iso,
                        "error": "Unexpected re-optimization error.",
                    }
                )
                _trim_reopt_jobs()


def _get_reopt_job(job_id):
    normalized = (job_id or "").strip()
    if not normalized:
        return None
    with _REOPT_JOB_LOCK:
        _trim_reopt_jobs()
        row = _REOPT_JOBS.get(normalized)
        if not row:
            return None
        return dict(row)


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
        "show_tutorial_nav": TUTORIAL_NAV_ENABLED,
        "trailer_profile_options": TRAILER_PROFILE_OPTIONS,
        "app_release_label": APP_RELEASE_LABEL,
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
              AND COALESCE(UPPER(status), 'OPEN') != 'CLOSED'
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
        stop_color_palette = _get_stop_color_palette()

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
                        "thumbnail": _build_load_thumbnail(load, sku_specs, stop_color_palette),
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
def home():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    return redirect(url_for("orders"))


@app.route("/dashboard")
def dashboard():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    context = _build_command_center_dashboard_context()
    if isinstance(context, Response):
        return context
    return render_template("dashboard.html", **context)


@app.route("/tutorial")
def tutorial():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    manifest = _load_tutorial_manifest()
    role = (_get_session_role() or "").strip().lower()
    modules = [
        module
        for module in manifest["modules"]
        if "all" in module.get("audience", []) or role in module.get("audience", [])
    ]
    selected_slug = (request.args.get("module") or "").strip().lower()
    selected_module = next((module for module in modules if module["slug"] == selected_slug), None)
    if not selected_module and modules:
        selected_module = modules[0]

    tutorial_error = manifest.get("error", "")
    if not tutorial_error and not modules:
        tutorial_error = "No tutorial modules are available for your current access profile."

    return render_template(
        "tutorial.html",
        tutorial_modules=modules,
        tutorial_selected_module=selected_module,
        tutorial_error=tutorial_error,
        tutorial_manifest_version=manifest.get("version", 1),
    )


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
            summary = _handle_order_upload(file)
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
        _handle_order_upload(file)
    except Exception:
        return redirect(url_for("orders"))

    return redirect(url_for("orders"))


@app.route("/api/orders/upload", methods=["POST"])
def api_orders_upload():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    file = request.files.get("file")
    if not file or not getattr(file, "filename", ""):
        return jsonify({"error": "Please choose a CSV file to upload."}), 400
    try:
        summary = _handle_order_upload(file)
    except UploadValidationError as exc:
        blocked_summary = exc.summary or {}
        unmapped_items = blocked_summary.get("unmapped_items") or []
        unmapped_suggestions = _build_unmapped_suggestions(unmapped_items)
        response = {
            "error": str(exc),
            "blocked": True,
            "total_rows": blocked_summary.get("total_rows") or 0,
            "total_orders": 0,
            "mapping_rate": round(blocked_summary.get("mapping_rate") or 0, 2),
            "unmapped_count": len(unmapped_items),
            "new_orders": 0,
            "changed_orders": 0,
            "unchanged_orders": 0,
            "reopened_orders": 0,
            "dropped_orders": 0,
            "unmapped_items": unmapped_suggestions,
        }
        return jsonify(response), 400
    except Exception as exc:
        return jsonify({"error": f"Upload failed: {exc}"}), 400
    unmapped_items = summary.get("unmapped_items") or []
    unmapped_suggestions = _build_unmapped_suggestions(unmapped_items)
    response = {
        "filename": getattr(file, "filename", ""),
        "total_rows": summary.get("total_rows"),
        "total_orders": len(summary.get("orders") or []),
        "mapping_rate": round(summary.get("mapping_rate") or 0, 2),
        "unmapped_count": len(summary.get("unmapped_items") or []),
        "new_orders": summary.get("new_orders"),
        "changed_orders": summary.get("changed_orders"),
        "unchanged_orders": summary.get("unchanged_orders"),
        "reopened_orders": summary.get("reopened_orders"),
        "dropped_orders": summary.get("dropped_orders"),
        "unmapped_items": unmapped_suggestions,
    }
    return jsonify(response)


@app.route("/api/skus/bulk-add", methods=["POST"])
def api_bulk_add_skus():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    payload = request.get_json(silent=True) or {}
    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        return jsonify({"error": "No SKU entries provided."}), 400

    cargo_pattern = re.compile(r"(\d+(?:\.\d+)?)\s*[xX]\s*(\d+(?:\.\d+)?)")

    def _cargo_length_from_text(value):
        if not value:
            return None
        match = cargo_pattern.search(str(value))
        if not match:
            return None
        try:
            length = float(match.group(2))
        except (TypeError, ValueError):
            return None
        return length + 4

    created = 0
    for entry in items:
        sku = (entry.get("sku") or "").strip().upper()
        if not sku:
            continue
        try:
            length = float(entry.get("length_with_tongue_ft") or 0)
        except (TypeError, ValueError):
            length = 0.0
        try:
            max_flat = int(entry.get("max_stack_flat_bed") or 1)
        except (TypeError, ValueError):
            max_flat = 1
        try:
            max_step = int(entry.get("max_stack_step_deck") or max_flat or 1)
        except (TypeError, ValueError):
            max_step = max_flat or 1

        category_value = (entry.get("bin") or entry.get("category") or "").strip()
        category_upper = category_value.upper() if category_value else ""
        if "CARGO" in category_upper:
            max_flat = 1
            max_step = 1
            if length <= 0:
                inferred = _cargo_length_from_text(sku) or _cargo_length_from_text(entry.get("description"))
                if inferred:
                    length = inferred

        if length <= 0:
            continue

        spec = {
            "sku": sku,
            "description": (entry.get("description") or "").strip(),
            "category": category_value or "UNKNOWN",
            "length_with_tongue_ft": length,
            "max_stack_step_deck": max_step,
            "max_stack_flat_bed": max_flat,
            "notes": (entry.get("notes") or "").strip(),
            "added_at": datetime.utcnow().isoformat(timespec="seconds"),
        }
        db.upsert_sku_spec(spec)
        created += 1

    return jsonify({"status": "ok", "created": created})


@app.route("/orders")
def orders():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    role = _get_session_role()
    profile_default_plants = session.get(SESSION_PROFILE_DEFAULT_PLANTS_KEY) or []
    allowed_plants = _get_allowed_plants()
    active_session_id = _get_active_planning_session_id()
    active_session = db.get_planning_session(active_session_id) if active_session_id else None
    if active_session and active_session.get("plant_code") not in allowed_plants:
        _set_active_planning_session_id(None)
        active_session = None
        active_session_id = None
    active_session_status = _normalize_session_status(active_session.get("status")) if active_session else ""
    needs_session = bool(request.args.get("needs_session"))
    needs_replace = bool(request.args.get("needs_replace"))
    plant_filters = _resolve_plant_filters(request.args.get("plants") or request.args.get("plant"))
    plant_scope = plant_filters or allowed_plants
    today_override = _resolve_today_override(request.args.get("today"))
    today = today_override or date.today()
    hide_past_due = _coerce_bool_value(request.args.get("hide_past_due"))

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
    if hide_past_due:
        orders_list = _filter_out_past_due_orders(orders_list)
        data["summary"] = order_service.summarize_orders(orders_list)
    orders_snapshot = _build_orders_snapshot(orders_list, today=today)

    plants = allowed_plants
    states = _distinct([order.get("state") for order in orders_list])
    customers = _distinct([order.get("cust_name") for order in orders_list])

    card_filters = {
        "plants": allowed_plants,
        "state": filters.get("state", ""),
        "cust_name": filters.get("cust_name", ""),
    }
    if hide_past_due:
        orders_by_plant = _count_active_orders_by_plant_from_rows(
            orders_list,
            plants=allowed_plants,
        )
    else:
        orders_by_plant = db.count_orders_by_plant(card_filters)
    plant_cards = []
    for plant in [code for code in plants if code != "CL"]:
        plant_cards.append(
            {
                "code": plant,
                "name": PLANT_NAMES.get(plant, plant),
                "orders": orders_by_plant.get(plant, 0),
            }
        )

    optimize_defaults = _default_optimize_form()
    if plant_filters:
        optimize_defaults["origin_plant"] = plant_filters[0]
    elif not optimize_defaults["origin_plant"] and plants:
        optimize_defaults["origin_plant"] = plants[0]
    optimize_defaults["state_filters"] = []
    optimize_defaults["customer_filters"] = []
    optimize_defaults["enforce_time_window"] = True
    optimize_defaults["batch_horizon_enabled"] = True
    optimize_defaults["batch_end_date"] = _default_batch_end_date().strftime("%Y-%m-%d")
    optimize_defaults["orders_start_date"] = date.today().strftime("%Y-%m-%d")
    optimize_defaults["ignore_due_date"] = False
    optimize_defaults["algorithm_version"] = "v2"
    optimize_defaults["compare_algorithms"] = False

    template_session_id = request.args.get("session_template_id")
    try:
        template_session_id = int(template_session_id) if template_session_id else None
    except (TypeError, ValueError):
        template_session_id = None
    if template_session_id:
        template_session = db.get_planning_session(template_session_id)
        if template_session and template_session.get("config_json"):
            try:
                config = json.loads(template_session.get("config_json") or "{}")
            except json.JSONDecodeError:
                config = {}
            optimize_defaults["origin_plant"] = config.get("origin_plant") or optimize_defaults["origin_plant"]
            optimize_defaults["capacity_feet"] = str(config.get("capacity_feet") or optimize_defaults["capacity_feet"])
            optimize_defaults["trailer_type"] = stack_calculator.normalize_trailer_type(
                config.get("trailer_type") or optimize_defaults["trailer_type"],
                default=optimize_defaults["trailer_type"],
            )
            optimize_defaults["max_detour_pct"] = str(config.get("max_detour_pct") or optimize_defaults["max_detour_pct"])
            optimize_defaults["time_window_days"] = str(config.get("time_window_days") or optimize_defaults["time_window_days"])
            optimize_defaults["geo_radius"] = str(config.get("geo_radius") or optimize_defaults["geo_radius"])
            optimize_defaults["stack_overflow_max_height"] = str(
                config.get("stack_overflow_max_height") or optimize_defaults["stack_overflow_max_height"]
            )
            optimize_defaults["max_back_overhang_ft"] = str(
                config.get("max_back_overhang_ft") or optimize_defaults["max_back_overhang_ft"]
            )
            optimize_defaults["upper_two_across_max_length_ft"] = str(
                config.get("upper_two_across_max_length_ft")
                or optimize_defaults.get("upper_two_across_max_length_ft", DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT)
            )
            optimize_defaults["upper_deck_exception_max_length_ft"] = str(
                config.get("upper_deck_exception_max_length_ft")
                or optimize_defaults.get(
                    "upper_deck_exception_max_length_ft",
                    DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT,
                )
            )
            optimize_defaults["upper_deck_exception_overhang_allowance_ft"] = str(
                config.get("upper_deck_exception_overhang_allowance_ft")
                or optimize_defaults.get(
                    "upper_deck_exception_overhang_allowance_ft",
                    DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT,
                )
            )
            optimize_defaults["upper_deck_exception_categories"] = stack_calculator.normalize_upper_deck_exception_categories(
                config.get("upper_deck_exception_categories")
                or optimize_defaults.get("upper_deck_exception_categories")
                or DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES,
                default=DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES,
            )
            optimize_defaults["enforce_time_window"] = bool(config.get("enforce_time_window", True))
            optimize_defaults["batch_horizon_enabled"] = bool(config.get("batch_horizon_enabled", False))
            optimize_defaults["batch_end_date"] = config.get("batch_end_date") or optimize_defaults["batch_end_date"]
            optimize_defaults["state_filters"] = config.get("state_filters") or []
            optimize_defaults["customer_filters"] = config.get("customer_filters") or []
            optimize_defaults["ignore_due_date"] = bool(config.get("ignore_due_date", False))
            config_start_date = (config.get("orders_start_date") or "").strip()
            if not config_start_date and config.get("ignore_past_due", True):
                config_start_date = date.today().strftime("%Y-%m-%d")
            optimize_defaults["orders_start_date"] = config_start_date or optimize_defaults["orders_start_date"]
            optimize_defaults["algorithm_version"] = "v2"
            optimize_defaults["compare_algorithms"] = False

    last_upload = db.get_last_upload()
    upload_history = db.list_upload_history(limit=12)
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
    eligible_strategic_customers = [
        entry for entry in strategic_customers if entry.get("include_in_optimizer_workbench", True)
    ]
    for entry in eligible_strategic_customers:
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
    strategic_orders = {entry["key"]: [] for entry in eligible_strategic_customers}
    other_orders = []
    for order in orders_list:
        cust_name = order.get("cust_name") or ""
        matched_key = None
        for entry in eligible_strategic_customers:
            if customer_rules.matches_any_customer_pattern(cust_name, entry.get("patterns")):
                matched_key = entry["key"]
                break
        if matched_key:
            strategic_orders[matched_key].append(order)
        else:
            other_orders.append(order)

    show_section = (request.args.get("show") or "").strip().lower()

    def _build_section(key, label, section_orders, default_limit):
        total_count = len(section_orders)
        render_all = show_section == key
        limit = None if render_all else default_limit
        render_orders = section_orders if render_all else section_orders[:default_limit]
        hidden_count = 0 if (render_all or limit is None) else max(total_count - limit, 0)

        show_url = None
        collapse_url = None
        if hidden_count and not render_all:
            params = request.args.to_dict(flat=True)
            params["show"] = key
            show_url = url_for("orders", **params)
        if render_all:
            params = request.args.to_dict(flat=True)
            params.pop("show", None)
            collapse_url = url_for("orders", **params)

        return {
            "key": key,
            "label": label,
            "orders": render_orders,
            "limit": limit,
            "hidden_count": hidden_count,
            "total_count": total_count,
            "show_all": render_all,
            "show_url": show_url,
            "collapse_url": collapse_url,
        }

    order_sections = []
    for entry in eligible_strategic_customers:
        section_orders = strategic_orders.get(entry["key"]) or []
        if not section_orders:
            continue
        order_sections.append(
            _build_section(entry["key"], entry["label"], section_orders, default_limit=5)
        )
    order_sections.append(
        _build_section("other", "Other Customers", other_orders, default_limit=5)
    )

    show_more_plants = False
    if profile_default_plants and role != ROLE_ADMIN:
        if not plant_filters:
            show_more_plants = True
        else:
            show_more_plants = any(code not in profile_default_plants for code in plant_filters)

    today_override_value = today_override.strftime("%Y-%m-%d") if today_override else ""
    today_override_label = today_override.strftime("%b %d, %Y") if today_override else ""
    today_display_label = (today_override or today).strftime("%b %d, %Y")
    plant_filter_param = ",".join(plant_filters) if plant_filters else ""
    reset_args = {}
    if plant_filter_param:
        reset_args["plants"] = plant_filter_param
    if today_override_value:
        reset_args["today"] = today_override_value
    if hide_past_due:
        reset_args["hide_past_due"] = "1"
    orders_reset_url = url_for("orders", **reset_args)
    toggle_args = request.args.to_dict(flat=True)
    if hide_past_due:
        toggle_args.pop("hide_past_due", None)
    else:
        toggle_args["hide_past_due"] = "1"
    past_due_toggle_url = url_for("orders", **_clean_query_params(toggle_args))

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
        upload_history=upload_history,
        rejected_orders=rejected_orders,
        ship_date_range=ship_date_range,
        plant_filters=plant_filters,
        plant_filter_param=plant_filter_param,
        due_filter=due_filter,
        due_start=due_start,
        due_end=due_end,
        assignment_filter=assignment_filter,
        plant_cards=plant_cards,
        orders_snapshot=orders_snapshot,
        hide_past_due=hide_past_due,
        past_due_toggle_url=past_due_toggle_url,
        orders_reset_url=orders_reset_url,
        today_override=today_override,
        today_override_value=today_override_value,
        today_override_label=today_override_label,
        today_display_label=today_display_label,
        profile_default_plants=profile_default_plants,
        show_more_plants=show_more_plants,
        active_session=active_session,
        active_session_status=active_session_status,
        active_session_id=active_session_id,
        needs_session=needs_session,
        needs_replace=needs_replace,
        is_admin=role == ROLE_ADMIN,
        optimizer_v2_enabled=OPTIMIZER_V2_ENABLED,
    )


@app.route("/orders/clear", methods=["POST"])
def clear_orders():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    _require_admin()
    db.clear_loads()
    db.clear_orders()
    db.mark_upload_history_deleted()
    return redirect(url_for("orders"))


def _coerce_order_ids_for_scope(raw_ids):
    cleaned_ids = []
    for raw in raw_ids or []:
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            cleaned_ids.append(parsed)
    if not cleaned_ids:
        return []

    records = db.list_orders_by_ids(cleaned_ids)
    allowed_plants = set(_get_allowed_plants())
    unauthorized = [
        row.get("id")
        for row in records
        if _normalize_plant_code(row.get("plant")) not in allowed_plants
    ]
    if unauthorized:
        abort(403)
    return [row.get("id") for row in records if row.get("id")]


@app.route("/orders/exclude", methods=["POST"])
def exclude_orders():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    order_ids = _coerce_order_ids_for_scope(request.form.getlist("order_ids"))
    order_service.exclude_orders(order_ids)
    return redirect(url_for("orders"))


@app.route("/orders/include", methods=["POST"])
def include_orders():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    order_ids = _coerce_order_ids_for_scope(request.form.getlist("order_ids"))
    order_service.include_orders(order_ids)
    return redirect(url_for("orders"))


@app.route("/orders/optimize", methods=["POST"])
def orders_optimize():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    role = _get_session_role()
    profile_default_plants = session.get(SESSION_PROFILE_DEFAULT_PLANTS_KEY) or []
    allowed_plants = _get_allowed_plants()
    active_session_id = _get_active_planning_session_id()
    active_session = db.get_planning_session(active_session_id) if active_session_id else None
    if active_session and active_session.get("plant_code") not in allowed_plants:
        _set_active_planning_session_id(None)
        active_session = None
        active_session_id = None
    active_session_status = _normalize_session_status(active_session.get("status")) if active_session else ""
    optimize_form = request.form
    origin_plant = _normalize_plant_code(optimize_form.get("origin_plant"))
    optimize_mode = (optimize_form.get("optimize_mode") or "auto").strip().lower()
    if optimize_mode not in {"auto", "manual"}:
        optimize_mode = "auto"
    manual_mode_error = ""

    if optimize_mode == "manual":
        # Paste mode intentionally ignores explicit plant/scope selections.
        origin_plant = ""
        optimize_form = request.form.copy()
        if hasattr(optimize_form, "setlist"):
            optimize_form.setlist("opt_states", [])
            optimize_form.setlist("opt_customers", [])
        optimize_form["batch_horizon_enabled"] = ""
        optimize_form["batch_end_date"] = ""

        raw_manual = str(optimize_form.get("manual_order_input") or "").strip()
        manual_so_nums = _parse_manual_so_nums(raw_manual)

        if manual_so_nums:
            manual_lines = db.list_order_lines_by_so_nums(manual_so_nums)
            matched_plants = sorted(
                {
                    _normalize_plant_code(line.get("plant"))
                    for line in manual_lines
                    if not _coerce_bool_value(line.get("is_excluded"))
                    and _normalize_plant_code(line.get("plant"))
                }
            )
            if not matched_plants:
                manual_mode_error = "No eligible open orders were found for the pasted order numbers."
            elif len(matched_plants) > 1:
                manual_mode_error = (
                    "Pasted orders span multiple plants. Paste orders from one plant only."
                )
            else:
                inferred_plant = matched_plants[0]
                if inferred_plant not in allowed_plants:
                    manual_mode_error = "Pasted orders do not match your allowed plant scope."
                else:
                    origin_plant = inferred_plant
        optimize_form["origin_plant"] = origin_plant or ""

    replace_session = optimize_form.get("replace_session") == "1"

    def _collect_form_data():
        form_data = _default_optimize_form()
        form_data["origin_plant"] = origin_plant
        form_data["trailer_type"] = stack_calculator.normalize_trailer_type(
            optimize_form.get("trailer_type"),
            default=form_data.get("trailer_type", "STEP_DECK"),
        )
        form_data["time_window_days"] = optimize_form.get(
            "time_window_days", form_data.get("time_window_days", "7")
        )
        form_data["geo_radius"] = optimize_form.get("geo_radius", form_data.get("geo_radius", "100"))
        form_data["max_detour_pct"] = optimize_form.get("max_detour_pct", form_data.get("max_detour_pct", "15"))
        form_data["capacity_feet"] = optimize_form.get("capacity_feet", form_data.get("capacity_feet", "53"))
        form_data["stack_overflow_max_height"] = optimize_form.get(
            "stack_overflow_max_height",
            form_data.get("stack_overflow_max_height", str(DEFAULT_STACK_OVERFLOW_MAX_HEIGHT)),
        )
        form_data["max_back_overhang_ft"] = optimize_form.get(
            "max_back_overhang_ft",
            form_data.get("max_back_overhang_ft", str(DEFAULT_MAX_BACK_OVERHANG_FT)),
        )
        form_data["upper_two_across_max_length_ft"] = optimize_form.get(
            "upper_two_across_max_length_ft",
            form_data.get(
                "upper_two_across_max_length_ft",
                str(DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT),
            ),
        )
        form_data["upper_deck_exception_max_length_ft"] = optimize_form.get(
            "upper_deck_exception_max_length_ft",
            form_data.get(
                "upper_deck_exception_max_length_ft",
                str(DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT),
            ),
        )
        form_data["upper_deck_exception_overhang_allowance_ft"] = optimize_form.get(
            "upper_deck_exception_overhang_allowance_ft",
            form_data.get(
                "upper_deck_exception_overhang_allowance_ft",
                str(DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT),
            ),
        )
        form_data["upper_deck_exception_categories"] = stack_calculator.normalize_upper_deck_exception_categories(
            optimize_form.getlist("upper_deck_exception_categories")
            or form_data.get("upper_deck_exception_categories")
            or DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES,
            default=DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES,
        )
        form_data["state_filters"] = [
            value.strip().upper()
            for value in optimize_form.getlist("opt_states")
            if value and value.strip()
        ]
        form_data["customer_filters"] = [
            value.strip()
            for value in optimize_form.getlist("opt_customers")
            if value and value.strip()
        ]
        ui_toggles = "opt_toggles" in optimize_form
        form_data["ignore_due_date"] = bool(optimize_form.get("ignore_due_date")) if ui_toggles else False
        form_data["enforce_time_window"] = bool(optimize_form.get("enforce_time_window")) if ui_toggles else True
        if form_data["ignore_due_date"]:
            form_data["enforce_time_window"] = False
        form_data["batch_horizon_enabled"] = bool(optimize_form.get("batch_horizon_enabled")) if ui_toggles else False
        form_data["batch_end_date"] = optimize_form.get("batch_end_date") or _default_batch_end_date().strftime(
            "%Y-%m-%d"
        )
        form_data["orders_start_date"] = optimize_form.get("orders_start_date") or date.today().strftime("%Y-%m-%d")
        form_data["algorithm_version"] = "v2"
        form_data["compare_algorithms"] = False
        mode = (optimize_form.get("optimize_mode") or "auto").strip().lower()
        form_data["optimize_mode"] = mode if mode in {"auto", "manual"} else "auto"
        form_data["manual_order_input"] = optimize_form.get("manual_order_input", "")
        return form_data

    if active_session and active_session_status == "DRAFT" and not replace_session:
        form_data = _collect_form_data()
        result = {
            "errors": {"session": "Replace existing load planning session to continue."},
            "form_data": form_data,
            "success_message": "",
            "summary": None,
        }
    elif manual_mode_error:
        form_data = _collect_form_data()
        result = {
            "errors": {"manual_order_input": manual_mode_error},
            "form_data": form_data,
            "success_message": "",
            "summary": None,
        }
    elif optimize_mode == "auto" and not _collect_form_data().get("customer_filters"):
        form_data = _collect_form_data()
        result = {
            "errors": {"customer_filters": "Select at least one customer to run optimization."},
            "form_data": form_data,
            "success_message": "",
            "summary": None,
        }
    elif origin_plant and origin_plant not in allowed_plants:
        form_data = _collect_form_data()
        result = {
            "errors": {"origin_plant": "Select a plant within your scope."},
            "form_data": form_data,
            "success_message": "",
            "summary": None,
        }
    else:
        if replace_session and active_session:
            _archive_session_and_release_loads(active_session_id)
        created_by = _get_session_profile_name() or _get_session_role()

        def _session_factory(form_data, params):
            session_plant = (
                _normalize_plant_code(params.get("origin_plant"))
                or _normalize_plant_code(form_data.get("origin_plant"))
                or origin_plant
            )
            return _create_planning_session(created_by, session_plant, form_data, params)
        result = load_builder.build_loads(
            optimize_form,
            session_factory=_session_factory,
            created_by=created_by,
        )

    resolved_origin_plant = _normalize_plant_code(
        (result.get("form_data") or {}).get("origin_plant") or origin_plant
    )

    if not result["errors"]:
        session_id = result.get("session_id")
        _set_active_planning_session_id(session_id)
        redirect_args = {"plants": resolved_origin_plant, "session_id": session_id}
        comparison = result.get("algorithm_comparison") or {}
        v1 = comparison.get("v1") or {}
        v2 = comparison.get("v2") or {}
        if v1 and v2:
            redirect_args.update(
                {
                    "alg_cmp": 1,
                    "alg_sel": comparison.get("selected") or "v2",
                    "v1_loads": int(v1.get("total_loads") or 0),
                    "v1_util": round(v1.get("avg_utilization") or 0.0, 1),
                    "v1_cost": round(v1.get("est_cost") or 0.0, 2),
                    "v2_loads": int(v2.get("total_loads") or 0),
                    "v2_util": round(v2.get("avg_utilization") or 0.0, 1),
                    "v2_cost": round(v2.get("est_cost") or 0.0, 2),
                }
            )
        return redirect(url_for("loads", **redirect_args))

    if result["errors"].get("order_lines") and resolved_origin_plant:
        resumable = _find_resumable_planning_session(resolved_origin_plant)
        if resumable and resumable.get("id"):
            resume_id = resumable["id"]
            _set_active_planning_session_id(resume_id)
            return redirect(url_for("loads", plants=resolved_origin_plant, session_id=resume_id))

    plant_filters = _resolve_plant_filters(request.args.get("plants") or request.args.get("plant"))
    plant_scope = plant_filters or allowed_plants

    due_filter = (request.args.get("due") or "").upper()
    due_start = request.args.get("due_start", "")
    due_end = request.args.get("due_end", "")
    today_override = _resolve_today_override(request.values.get("today"))
    today = today_override or date.today()
    hide_past_due = _coerce_bool_value(request.values.get("hide_past_due"))
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
    if hide_past_due:
        orders_list = _filter_out_past_due_orders(orders_list)
        data["summary"] = order_service.summarize_orders(orders_list)
    orders_snapshot = _build_orders_snapshot(orders_list, today=today)
    plants = allowed_plants
    states = _distinct([order.get("state") for order in orders_list])
    customers = _distinct([order.get("cust_name") for order in orders_list])
    last_upload = db.get_last_upload()
    upload_history = db.list_upload_history(limit=12)

    card_filters = {
        "plants": allowed_plants,
        "state": filters.get("state", ""),
        "cust_name": filters.get("cust_name", ""),
    }
    if hide_past_due:
        orders_by_plant = _count_active_orders_by_plant_from_rows(
            orders_list,
            plants=allowed_plants,
        )
    else:
        orders_by_plant = db.count_orders_by_plant(card_filters)
    plant_cards = [
        {
            "code": plant,
            "name": PLANT_NAMES.get(plant, plant),
            "orders": orders_by_plant.get(plant, 0),
        }
        for plant in plants
        if plant != "CL"
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
    eligible_strategic_customers = [
        entry for entry in strategic_customers if entry.get("include_in_optimizer_workbench", True)
    ]
    for entry in eligible_strategic_customers:
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
    strategic_orders = {entry["key"]: [] for entry in eligible_strategic_customers}
    other_orders = []
    for order in orders_list:
        cust_name = order.get("cust_name") or ""
        matched_key = None
        for entry in eligible_strategic_customers:
            if customer_rules.matches_any_customer_pattern(cust_name, entry.get("patterns")):
                matched_key = entry["key"]
                break
        if matched_key:
            strategic_orders[matched_key].append(order)
        else:
            other_orders.append(order)

    show_section = (request.args.get("show") or "").strip().lower()

    def _build_section(key, label, section_orders, default_limit):
        total_count = len(section_orders)
        render_all = show_section == key
        limit = None if render_all else default_limit
        render_orders = section_orders if render_all else section_orders[:default_limit]
        hidden_count = 0 if (render_all or limit is None) else max(total_count - limit, 0)

        show_url = None
        collapse_url = None
        if hidden_count and not render_all:
            params = request.args.to_dict(flat=True)
            params["show"] = key
            show_url = url_for("orders", **params)
        if render_all:
            params = request.args.to_dict(flat=True)
            params.pop("show", None)
            collapse_url = url_for("orders", **params)

        return {
            "key": key,
            "label": label,
            "orders": render_orders,
            "limit": limit,
            "hidden_count": hidden_count,
            "total_count": total_count,
            "show_all": render_all,
            "show_url": show_url,
            "collapse_url": collapse_url,
        }

    order_sections = []
    for entry in eligible_strategic_customers:
        section_orders = strategic_orders.get(entry["key"]) or []
        if not section_orders:
            continue
        order_sections.append(
            _build_section(entry["key"], entry["label"], section_orders, default_limit=5)
        )
    order_sections.append(
        _build_section("other", "Other Customers", other_orders, default_limit=5)
    )

    show_more_plants = False
    if profile_default_plants and role != ROLE_ADMIN:
        if not plant_filters:
            show_more_plants = True
        else:
            show_more_plants = any(code not in profile_default_plants for code in plant_filters)

    today_override_value = today_override.strftime("%Y-%m-%d") if today_override else ""
    today_override_label = today_override.strftime("%b %d, %Y") if today_override else ""
    today_display_label = (today_override or today).strftime("%b %d, %Y")
    plant_filter_param = ",".join(plant_filters) if plant_filters else ""
    reset_args = {}
    if plant_filter_param:
        reset_args["plants"] = plant_filter_param
    if today_override_value:
        reset_args["today"] = today_override_value
    if hide_past_due:
        reset_args["hide_past_due"] = "1"
    orders_reset_url = url_for("orders", **reset_args)
    toggle_args = request.args.to_dict(flat=True)
    if hide_past_due:
        toggle_args.pop("hide_past_due", None)
    else:
        toggle_args["hide_past_due"] = "1"
    past_due_toggle_url = url_for("orders", **_clean_query_params(toggle_args))

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
        upload_history=upload_history,
        rejected_orders=rejected_orders,
        ship_date_range=ship_date_range,
        plant_filters=plant_filters,
        plant_filter_param=plant_filter_param,
        due_filter=due_filter,
        due_start=due_start,
        due_end=due_end,
        assignment_filter=assignment_filter,
        plant_cards=plant_cards,
        orders_snapshot=orders_snapshot,
        hide_past_due=hide_past_due,
        past_due_toggle_url=past_due_toggle_url,
        orders_reset_url=orders_reset_url,
        today_override=today_override,
        today_override_value=today_override_value,
        today_override_label=today_override_label,
        today_display_label=today_display_label,
        profile_default_plants=profile_default_plants,
        show_more_plants=show_more_plants,
        active_session=active_session,
        active_session_status=active_session_status,
        active_session_id=active_session_id,
        needs_session=False,
        needs_replace=False,
        is_admin=role == ROLE_ADMIN,
        optimizer_v2_enabled=OPTIMIZER_V2_ENABLED,
    )


@app.route("/api/orders/manual-validate", methods=["POST"])
def api_manual_order_validate():
    session_redirect = _require_session()
    if session_redirect:
        return jsonify({"error": "Session expired"}), 401

    payload = request.get_json(silent=True) or {}
    manual_order_input = payload.get("manual_order_input") or ""
    parsed_so_nums = _parse_manual_so_nums(manual_order_input)
    if not parsed_so_nums:
        return jsonify(
            {
                "pasted_count": 0,
                "matched_count": 0,
                "not_found_count": 0,
                "matched_so_nums": [],
                "not_found_so_nums": [],
            }
        )

    allowed_plants = set(_get_allowed_plants())
    matches = []
    matched_set = set()
    rows = db.list_orders_by_so_nums_any(parsed_so_nums, include_closed=False)
    for row in rows:
        so_num = str(row.get("so_num") or "").strip()
        plant_code = _normalize_plant_code(row.get("plant"))
        if (
            not so_num
            or plant_code not in allowed_plants
            or _coerce_bool_value(row.get("is_excluded"))
            or so_num in matched_set
        ):
            continue
        matches.append(so_num)
        matched_set.add(so_num)

    not_found = [so_num for so_num in parsed_so_nums if so_num not in matched_set]
    return jsonify(
        {
            "pasted_count": len(parsed_so_nums),
            "matched_count": len(matches),
            "not_found_count": len(not_found),
            "matched_so_nums": matches,
            "not_found_so_nums": not_found,
        }
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

    active_session_id = _get_active_planning_session_id()
    active_session = db.get_planning_session(active_session_id) if active_session_id else None
    active_session_status = _normalize_session_status(active_session.get("status")) if active_session else ""
    replace_session = request.form.get("replace_session") == "1"
    if active_session and active_session_status == "DRAFT" and not replace_session:
        return redirect(url_for("orders", needs_replace=1))
    if replace_session and active_session:
        _archive_session_and_release_loads(active_session_id)

    created_by = _get_session_profile_name() or _get_session_role()

    def _session_factory(form_data, params):
        return _create_planning_session(created_by, origin_plant, form_data, params)

    result = load_builder.build_loads(
        request.form,
        session_factory=_session_factory,
        created_by=created_by,
    )
    if result["errors"]:
        return redirect(url_for("orders"))
    session_id = result.get("session_id")
    _set_active_planning_session_id(session_id)
    return redirect(url_for("loads", session_id=session_id))


@app.route("/loads")
def loads():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    allowed_plants = _get_allowed_plants()
    plant_filters = _resolve_plant_filters(request.args.get("plants") or request.args.get("plant"))
    plant_scope = plant_filters or allowed_plants
    replay_mode = False
    replay_context = {}
    replay_run_id = request.args.get("replay_run_id")
    replay_date_created = (request.args.get("replay_date_created") or "").strip()
    replay_plant_code = (request.args.get("replay_plant_code") or "").strip().upper()
    replay_scenario = (request.args.get("replay_scenario") or "OPTIMIZED").strip().upper()
    if replay_scenario not in {"ACTUAL", "OPTIMIZED"}:
        replay_scenario = "OPTIMIZED"
    try:
        replay_run_id = int(replay_run_id) if replay_run_id else None
    except (TypeError, ValueError):
        replay_run_id = None

    session_id = None
    active_session = None
    all_loads = []
    if replay_run_id:
        _require_admin()
        replay_mode = True
        run = db.get_replay_eval_run(replay_run_id)
        if not run:
            abort(404)
        run_status = (run.get("status") or "").upper()
        if run_status != "COMPLETED":
            return redirect(
                url_for(
                    "planning_sessions_replay",
                    run_id=replay_run_id,
                    replay_error="Replay run is not completed yet.",
                )
            )

        metrics = db.list_replay_eval_load_metrics(replay_run_id, scenario=replay_scenario)
        metrics = [
            row
            for row in metrics
            if (row.get("plant_code") or "").strip().upper() in allowed_plants
        ]
        day_values = sorted({row.get("date_created") for row in metrics if row.get("date_created")})
        plant_values = sorted({(row.get("plant_code") or "").strip().upper() for row in metrics if row.get("plant_code")})

        if replay_date_created:
            metrics = [
                row for row in metrics if (row.get("date_created") or "") == replay_date_created
            ]
        if replay_plant_code:
            metrics = [
                row
                for row in metrics
                if (row.get("plant_code") or "").strip().upper() == replay_plant_code
            ]

        all_loads = _build_replay_simulation_loads(metrics)
        replay_plants_in_scope = sorted(
            {
                (load.get("origin_plant") or "").strip().upper()
                for load in all_loads
                if (load.get("origin_plant") or "").strip().upper()
            }
        )
        if replay_plants_in_scope:
            allowed_plants = [plant for plant in allowed_plants if plant in replay_plants_in_scope]
        if replay_plant_code and replay_plant_code in allowed_plants:
            plant_scope = [replay_plant_code]
            plant_filters = [replay_plant_code]
        else:
            scoped_filters = [plant for plant in plant_filters if plant in allowed_plants]
            plant_scope = scoped_filters or allowed_plants
            plant_filters = scoped_filters

        replay_context = {
            "run_id": replay_run_id,
            "run_filename": run.get("filename") or "",
            "scenario": replay_scenario,
            "date_created": replay_date_created,
            "plant_code": replay_plant_code,
            "day_values": day_values,
            "plant_values": plant_values,
        }
    else:
        session_id = request.args.get("session_id")
        try:
            session_id = int(session_id) if session_id else None
        except (TypeError, ValueError):
            session_id = None
        if session_id:
            _set_active_planning_session_id(session_id)
        else:
            session_id = _get_active_planning_session_id()
        if not session_id:
            return redirect(url_for("orders", needs_session=1))

        active_session = db.get_planning_session(session_id)
        if not active_session:
            _set_active_planning_session_id(None)
            return redirect(url_for("orders", needs_session=1))
        if active_session.get("plant_code") and active_session.get("plant_code") not in allowed_plants:
            _set_active_planning_session_id(None)
            return redirect(url_for("orders", needs_session=1))
        if active_session.get("plant_code"):
            plant_scope = [active_session.get("plant_code")]
            plant_filters = list(plant_scope)
    tab = (request.args.get("tab") or "").strip().lower()
    status_filter = (request.args.get("status") or "").strip().upper()
    sort_mode = (request.args.get("sort") or "flow").strip().lower()
    if sort_mode not in {"flow", "util"}:
        sort_mode = "flow"
    today_override = _resolve_today_override(request.args.get("today"))
    today = today_override or date.today()
    reopt_status = request.args.get("reopt", "")
    reopt_job_id = (request.args.get("reopt_job") or "").strip()
    if reopt_job_id:
        reopt_job = _get_reopt_job(reopt_job_id)
        if reopt_job:
            if reopt_job.get("plant_code") not in allowed_plants:
                reopt_job_id = ""
            else:
                current_status = (reopt_job.get("status") or "").strip().lower()
                if current_status in {"running", "done", "failed"}:
                    reopt_status = current_status
        else:
            reopt_job_id = ""
    feedback_error = request.args.get("feedback_error") or ""
    feedback_target = request.args.get("feedback_target") or ""
    manual_error = request.args.get("manual_error") or ""
    algorithm_comparison = None
    if request.args.get("alg_cmp"):
        def _to_float(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        def _to_int(value):
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        v1_cost = _to_float(request.args.get("v1_cost"))
        v2_cost = _to_float(request.args.get("v2_cost"))
        v1_util = _to_float(request.args.get("v1_util"))
        v2_util = _to_float(request.args.get("v2_util"))
        v1_loads = _to_int(request.args.get("v1_loads"))
        v2_loads = _to_int(request.args.get("v2_loads"))
        if None not in {v1_cost, v2_cost, v1_util, v2_util, v1_loads, v2_loads}:
            cost_delta = v2_cost - v1_cost
            util_delta = v2_util - v1_util
            load_delta = v2_loads - v1_loads
            algorithm_comparison = {
                "selected": (request.args.get("alg_sel") or "v2").lower(),
                "v1": {"loads": v1_loads, "avg_util": v1_util, "cost": v1_cost},
                "v2": {"loads": v2_loads, "avg_util": v2_util, "cost": v2_cost},
                "delta": {
                    "cost": cost_delta,
                    "util": util_delta,
                    "loads": load_delta,
                    "cost_direction": "down" if cost_delta < 0 else ("up" if cost_delta > 0 else "flat"),
                },
            }
    if not replay_mode:
        all_loads = load_builder.list_loads(
            None,
            session_id=session_id,
            include_stack_metrics=False,
        )
    all_loads = [load for load in all_loads if load.get("origin_plant") in allowed_plants]
    loads_data = [load for load in all_loads if load.get("origin_plant") in plant_scope]
    if not replay_mode:
        session_status = _sync_planning_session_status(session_id, loads=all_loads)
        if active_session and session_status:
            active_session["status"] = session_status
    zip_coords = geo_utils.load_zip_coordinates()
    plant_names = {row["plant_code"]: row["name"] for row in db.list_plants()}
    sku_specs = {spec["sku"]: spec for spec in db.list_sku_specs()}
    stop_color_palette = _get_stop_color_palette()
    sku_color_palette = [
        "#137fec",
        "#10b981",
        "#f59e0b",
        "#ef4444",
        "#8b5cf6",
        "#22d3ee",
        "#f472b6",
        "#f97316",
    ]
    stop_fee_amount = _get_stop_fee_amount()
    fuel_surcharge_per_mile = _get_fuel_surcharge_per_mile()
    load_minimum_amount = _get_load_minimum_amount()
    trailer_assignment_rules = _get_trailer_assignment_rules()
    strategic_setting = db.get_planning_setting("strategic_customers") or {}
    strategic_customers = _parse_strategic_customers(strategic_setting.get("value_text") or "")

    for load in loads_data:
        lines = load.get("lines", [])
        trailer_type = stack_calculator.normalize_trailer_type(load.get("trailer_type"), default="STEP_DECK")
        load["trailer_type"] = trailer_type
        load["total_units"] = sum((line.get("qty") or 0) for line in lines)
        load["total_sales"] = sum((line.get("sales") or 0) for line in lines)
        stops = []
        stop_map = {}
        due_dates = []
        for line in lines:
            due_date = _parse_date(line.get("due_date"))
            if due_date:
                due_dates.append(due_date)
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
            group["sku_list"] = sorted(group.get("sku_set") or [])
            group_due = _parse_date(group.get("due_date"))
            group["due_status"] = _due_status(group_due, today=today)
            early_days = (group_due - anchor_date).days if group_due and anchor_date else 0
            group["early_days"] = early_days if early_days > 0 else 0

        for stop in stop_map.values():
            coords = None
            if stop.get("lat") is not None and stop.get("lng") is not None:
                coords = (stop.get("lat"), stop.get("lng"))
            stops.append(
                {
                    "zip": stop["zip"],
                    "state": stop["state"],
                    "city": stop.get("city") or "",
                    "city_abbr": _city_abbr(stop.get("city")),
                    "lat": stop.get("lat"),
                    "lng": stop.get("lng"),
                    "coords": coords,
                    "customers": sorted(stop.get("customers") or []),
                }
            )

        origin_code = load.get("origin_plant")
        origin_coords = geo_utils.plant_coords_for_code(origin_code)
        requires_return_to_origin = _requires_return_to_origin(lines)
        reverse_route = _is_load_route_reversed(load)
        ordered_stops = (
            tsp_solver.solve_route(
                origin_coords,
                stops,
                return_to_origin=requires_return_to_origin,
            )
            if origin_coords
            else list(stops)
        )
        ordered_stops = _apply_load_route_direction(ordered_stops, reverse_route=reverse_route)
        stop_sequence_map = _stop_sequence_map_from_ordered_stops(ordered_stops)
        order_colors = _build_order_colors_for_lines(
            lines,
            stop_sequence_map=stop_sequence_map,
            stop_palette=stop_color_palette,
        )
        load["order_colors"] = order_colors
        load["order_count"] = len(order_colors)

        for group in manifest_groups:
            group["stop_sequence"] = stop_sequence_map.get(
                _line_stop_key(group.get("state"), group.get("zip"))
            )
            group["color"] = order_colors.get(
                group["order_id"],
                _color_for_stop_sequence(group.get("stop_sequence"), stop_color_palette),
            )
        manifest_groups.sort(
            key=lambda group: (
                int(group.get("stop_sequence") or 999),
                group.get("due_date") or "9999-12-31",
                group.get("order_id") or "",
            )
        )
        origin_name = plant_names.get(origin_code, PLANT_NAMES.get(origin_code, origin_code))
        if requires_return_to_origin and ordered_stops:
            return_stop_sequence = len(ordered_stops) + 1
            manifest_groups.append(
                {
                    "order_id": "RETURN",
                    "due_date": "",
                    "cust_name": origin_name or origin_code or "Plant",
                    "city": "",
                    "state": origin_code or "",
                    "zip": "",
                    "total_qty": 0,
                    "status_label": "",
                    "sku_set": set(),
                    "lines": [],
                    "sku_list": [],
                    "due_status": "",
                    "early_days": 0,
                    "stop_sequence": return_stop_sequence,
                    "color": _color_for_stop_sequence(return_stop_sequence, stop_color_palette),
                    "is_terminal_stop": True,
                    "terminal_label": "Return to Plant",
                }
            )
        load["manifest_groups"] = manifest_groups

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

        for node in route_nodes:
            node_type = (node.get("type") or "").strip().lower()
            is_return_origin = (
                node_type == "final"
                and requires_return_to_origin
                and origin_coords
                and node.get("coords") == origin_coords
            )
            if node_type == "origin" or is_return_origin:
                color = "#38bdf8"
            else:
                color = _color_for_stop_sequence(node.get("sequence"), stop_color_palette)
            node["color"] = color
            node["bg"] = f"{color}22"

        route_metrics = _load_route_display_metrics(
            load,
            route_nodes,
            use_cached_route=not reverse_route,
        )
        route_legs = route_metrics["route_legs"]
        total_route_distance = route_metrics["route_distance"]
        route_geometry = route_metrics["route_geometry"]

        schematic, line_items, order_numbers = _calculate_load_schematic(
            lines,
            sku_specs,
            trailer_type,
            stop_sequence_map=stop_sequence_map,
        )
        auto_label, auto_reason = _auto_trailer_rule_annotation(
            load=load,
            lines=lines,
            trailer_type=trailer_type,
            schematic=schematic,
            sku_specs=sku_specs,
            stop_sequence_map=stop_sequence_map,
            trailer_assignment_rules=trailer_assignment_rules,
            strategic_customers=strategic_customers,
        )
        load["auto_trailer_label"] = auto_label
        load["auto_trailer_reason"] = auto_reason
        sku_colors = {}
        for idx, item in enumerate(line_items):
            sku = item.get("sku") or f"item-{idx}"
            if sku not in sku_colors:
                sku_colors[sku] = sku_color_palette[len(sku_colors) % len(sku_color_palette)]

        utilization_pct = schematic.get("utilization_pct", load.get("utilization_pct", 0)) or 0
        load["utilization_pct"] = utilization_pct
        exceeds_capacity = schematic.get("exceeds_capacity", False)
        load["over_capacity"] = exceeds_capacity and len(order_numbers) <= 1
        load["display_utilization_pct"] = utilization_pct
        load["utilization_display_note"] = ""
        load["raw_total_length_ft"] = round(
            sum(
                float(line.get("total_length_ft") or line.get("line_total_feet") or 0.0)
                for line in lines
            ),
            1,
        )
        load["utilization_credit_ft"] = float(schematic.get("utilization_credit_ft") or 0.0)
        load["total_linear_feet"] = float(schematic.get("total_linear_feet") or 0.0)
        load["schematic"] = schematic
        load["stops"] = ordered_stops
        load["stop_count"] = len(ordered_stops)
        load["sku_colors"] = sku_colors
        load["route_nodes"] = route_nodes
        load["route_legs"] = route_legs
        load["route_distance"] = total_route_distance
        load["route_geometry"] = route_geometry
        load["route_reversed"] = bool(reverse_route)
        load["freight_breakdown"] = _build_freight_breakdown(
            load,
            stop_fee_amount=stop_fee_amount,
            fuel_surcharge_per_mile=fuel_surcharge_per_mile,
            load_minimum_amount=load_minimum_amount,
        )
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
        build_source = (load.get("build_source") or "OPTIMIZED").upper()
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
    optimized_total_units = sum((load.get("total_units") or 0) for load in optimized_loads)
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

    spend_per_unit = (
        (optimized_total_spend / optimized_total_units) if optimized_total_units else 0.0
    )
    optimization_summary = {
        "total_orders": len(optimized_order_ids),
        "total_loads": len(optimized_loads),
        "total_spend": optimized_total_spend,
        "total_units": optimized_total_units,
        "spend_per_unit": spend_per_unit,
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

    for idx, load in enumerate(loads_data, start=1):
        load["display_sequence"] = idx

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
    session_config = {}
    if active_session and active_session.get("config_json"):
        try:
            session_config = json.loads(active_session.get("config_json") or "{}")
        except json.JSONDecodeError:
            session_config = {}
    replay_link_params = {}
    if replay_mode:
        replay_link_params = {
            "replay_run_id": replay_context.get("run_id"),
            "replay_scenario": replay_context.get("scenario"),
            "replay_date_created": replay_context.get("date_created") or None,
            "replay_plant_code": replay_context.get("plant_code") or None,
        }

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
        reopt_job_id=reopt_job_id,
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
        active_session=active_session,
        active_session_config=session_config,
        active_session_id=session_id,
        order_removal_reasons=ORDER_REMOVAL_REASONS,
        load_rejection_reasons=LOAD_REJECTION_REASONS,
        is_admin=_get_session_role() == ROLE_ADMIN,
        algorithm_comparison=algorithm_comparison,
        replay_mode=replay_mode,
        replay_context=replay_context,
        replay_link_params=replay_link_params,
    )


@app.route("/planning-sessions")
def planning_sessions():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    plant_code = (request.args.get("plant") or "").strip().upper()
    planner = (request.args.get("planner") or "").strip()
    start_date = (request.args.get("start") or "").strip()
    end_date = (request.args.get("end") or "").strip()
    role = _get_session_role()
    can_manage_sessions = role == ROLE_ADMIN
    allowed_plants = set(_get_allowed_plants())
    profile_name = (_get_session_profile_name() or "").strip()
    scoped_planner = planner if can_manage_sessions else profile_name

    if plant_code and plant_code not in allowed_plants:
        abort(403)

    archived_session_param = (request.args.get("archived_session_id") or "").strip()
    archived_all_param = (request.args.get("archived_all_count") or "").strip()
    try:
        archived_session_id = int(archived_session_param) if archived_session_param else None
    except ValueError:
        archived_session_id = None
    try:
        archived_all_count = int(archived_all_param) if archived_all_param else None
    except ValueError:
        archived_all_count = None

    sessions = db.list_planning_sessions(
        {
            "plant_code": plant_code or None,
            "created_by": scoped_planner or None,
            "start_date": start_date or None,
            "end_date": end_date or None,
        }
    )

    visible_sessions = []
    for session in sessions:
        session_plant = _normalize_plant_code(session.get("plant_code"))
        if session_plant and session_plant not in allowed_plants:
            continue
        if not can_manage_sessions:
            owner = (session.get("created_by") or "").strip()
            if owner.casefold() != profile_name.casefold():
                continue
        config = {}
        if session.get("config_json"):
            try:
                config = json.loads(session.get("config_json") or "{}")
            except json.JSONDecodeError:
                config = {}
        session["config"] = config
        session["status"] = _normalize_session_status(session.get("status"))
        session["created_at_label"] = _format_est_datetime_label(session.get("created_at"))
        visible_sessions.append(session)

    sessions = visible_sessions

    total_sessions = len(sessions)
    avg_efficiency = 0.0
    loads_optimized = 0
    active_session_id = _get_active_planning_session_id()
    active_session_label = None
    if sessions:
        util_values = [
            (session.get("avg_utilization") or 0) for session in sessions if session.get("avg_utilization") is not None
        ]
        avg_efficiency = round(sum(util_values) / len(util_values), 1) if util_values else 0.0
        loads_optimized = sum((session.get("load_count") or 0) for session in sessions)
    if active_session_id:
        active = next((session for session in sessions if session.get("id") == active_session_id), None)
        if not active:
            fetched = db.get_planning_session(active_session_id)
            active = fetched if _can_access_planning_session(fetched) else None
        if active and _can_access_planning_session(active):
            active_session_label = f"{active.get('plant_code') or ''} - {active.get('session_code') or ''}".strip(" -")

    planner_options = (
        sorted({session.get("created_by") for session in sessions if session.get("created_by")})
        if can_manage_sessions
        else ([profile_name] if profile_name else [])
    )
    plant_options = sorted({session.get("plant_code") for session in sessions if session.get("plant_code")})

    return render_template(
        "planning_sessions.html",
        sessions=sessions,
        total_sessions=total_sessions,
        avg_efficiency=avg_efficiency,
        loads_optimized=loads_optimized,
        active_session_label=active_session_label,
        active_session_id=active_session_id,
        planner_options=planner_options,
        plant_options=plant_options,
        archived_session_id=archived_session_id,
        archived_all_count=archived_all_count,
        can_manage_sessions=can_manage_sessions,
        filters={
            "plant": plant_code,
            "planner": planner if can_manage_sessions else profile_name,
            "start": start_date,
            "end": end_date,
        },
    )


def _build_replay_totals(network_rows):
    actual_loads = sum(int(row.get("actual_loads") or 0) for row in network_rows)
    optimized_loads = sum(int(row.get("optimized_loads") or 0) for row in network_rows)
    actual_util_num = sum(
        float(row.get("actual_avg_utilization") or 0.0) * int(row.get("actual_loads") or 0)
        for row in network_rows
    )
    optimized_util_num = sum(
        float(row.get("optimized_avg_utilization") or 0.0) * int(row.get("optimized_loads") or 0)
        for row in network_rows
    )
    actual_avg_util = (actual_util_num / actual_loads) if actual_loads else 0.0
    optimized_avg_util = (optimized_util_num / optimized_loads) if optimized_loads else 0.0
    actual_total_cost = sum(float(row.get("actual_total_cost") or 0.0) for row in network_rows)
    optimized_total_cost = sum(float(row.get("optimized_total_cost") or 0.0) for row in network_rows)
    delta_total_cost = optimized_total_cost - actual_total_cost
    return {
        "days": len(network_rows),
        "matched_orders": sum(int(row.get("matched_orders") or 0) for row in network_rows),
        "missing_orders": sum(int(row.get("missing_orders") or 0) for row in network_rows),
        "actual_loads": actual_loads,
        "optimized_loads": optimized_loads,
        "actual_avg_utilization": actual_avg_util,
        "optimized_avg_utilization": optimized_avg_util,
        "actual_total_miles": sum(float(row.get("actual_total_miles") or 0.0) for row in network_rows),
        "optimized_total_miles": sum(float(row.get("optimized_total_miles") or 0.0) for row in network_rows),
        "actual_total_cost": actual_total_cost,
        "optimized_total_cost": optimized_total_cost,
        "delta_total_cost": delta_total_cost,
        "delta_cost_pct": ((delta_total_cost / actual_total_cost) * 100.0) if actual_total_cost else None,
    }


def _parse_replay_load_json(raw_json):
    if not raw_json:
        return {}
    try:
        parsed = json.loads(raw_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_replay_order_numbers(raw_json):
    if not raw_json:
        return []
    try:
        parsed = json.loads(raw_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    values = []
    for value in parsed if isinstance(parsed, list) else []:
        text = str(value or "").strip()
        if text:
            values.append(text)
    return values


def _build_replay_simulation_loads(load_metrics):
    stop_fee_amount = _get_stop_fee_amount()
    fuel_surcharge_per_mile = _get_fuel_surcharge_per_mile()
    load_minimum_amount = _get_load_minimum_amount()

    loads = []
    for idx, metric in enumerate(load_metrics or [], start=1):
        load_data = _parse_replay_load_json(metric.get("load_json"))
        lines = load_data.get("lines") if isinstance(load_data.get("lines"), list) else []
        order_numbers = _parse_replay_order_numbers(metric.get("order_numbers_json"))
        if not lines and order_numbers:
            lines = [{"so_num": so_num} for so_num in order_numbers]

        load_number = (metric.get("load_key") or "").strip() or f"SIM-{idx:03d}"
        stop_count = int(load_data.get("stop_count") or 0)
        if not stop_count:
            stop_keys = {
                f"{(str(line.get('state') or '').strip().upper())}|{str(line.get('zip') or '').strip()}"
                for line in lines
                if str(line.get("state") or "").strip() or str(line.get("zip") or "").strip()
            }
            stop_count = len(stop_keys)

        load = {
            "id": idx,
            "load_number": load_number,
            "status": STATUS_PROPOSED,
            "simulation_status": "SIMULATED",
            "build_source": "OPTIMIZED",
            "trailer_type": stack_calculator.normalize_trailer_type(load_data.get("trailer_type"), default="STEP_DECK"),
            "utilization_pct": float(metric.get("utilization_pct") or 0.0),
            "estimated_miles": float(metric.get("estimated_miles") or 0.0),
            "estimated_cost": float(metric.get("estimated_cost") or 0.0),
            "rate_per_mile": float(load_data.get("rate_per_mile") or 0.0),
            "stop_count": stop_count,
            "return_to_origin": bool(load_data.get("return_to_origin")),
            "return_miles": float(load_data.get("return_miles") or 0.0),
            "return_cost": float(load_data.get("return_cost") or 0.0),
            "origin_plant": (load_data.get("origin_plant") or metric.get("plant_code") or "").strip().upper(),
            "destination_state": (load_data.get("destination_state") or "").strip().upper(),
            "route": load_data.get("route") if isinstance(load_data.get("route"), list) else [],
            "route_legs": load_data.get("route_legs") if isinstance(load_data.get("route_legs"), list) else [],
            "lines": lines,
        }
        load["freight_breakdown"] = _build_freight_breakdown(
            load,
            stop_fee_amount=stop_fee_amount,
            fuel_surcharge_per_mile=fuel_surcharge_per_mile,
            load_minimum_amount=load_minimum_amount,
        )
        loads.append(load)

    return loads


def _build_replay_workbook(run, network_rows, day_rows, issues, load_metrics):
    workbook = Workbook()
    network_sheet = workbook.active
    network_sheet.title = "Network Daily"
    network_headers = [
        "Replay Date / Period",
        "Plants",
        "Matched Orders",
        "Missing Orders",
        "Actual Loads",
        "Actual Avg Util %",
        "Actual Miles",
        "Actual Cost",
        "Optimized Loads",
        "Optimized Avg Util %",
        "Optimized Miles",
        "Optimized Cost",
        "Delta Loads",
        "Delta Util Pts",
        "Delta Miles",
        "Delta Cost",
        "Delta Cost %",
        "Report Ref Cost",
        "Report Ref Miles",
    ]
    network_sheet.append(network_headers)
    for row in network_rows:
        network_sheet.append(
            [
                row.get("date_created") or "",
                int(row.get("plants") or 0),
                int(row.get("matched_orders") or 0),
                int(row.get("missing_orders") or 0),
                int(row.get("actual_loads") or 0),
                round(float(row.get("actual_avg_utilization") or 0.0), 2),
                round(float(row.get("actual_total_miles") or 0.0), 2),
                round(float(row.get("actual_total_cost") or 0.0), 2),
                int(row.get("optimized_loads") or 0),
                round(float(row.get("optimized_avg_utilization") or 0.0), 2),
                round(float(row.get("optimized_total_miles") or 0.0), 2),
                round(float(row.get("optimized_total_cost") or 0.0), 2),
                int(row.get("delta_loads") or 0),
                round(float(row.get("delta_avg_utilization") or 0.0), 2),
                round(float(row.get("delta_total_miles") or 0.0), 2),
                round(float(row.get("delta_total_cost") or 0.0), 2),
                round(float(row.get("delta_cost_pct") or 0.0), 4) if row.get("delta_cost_pct") is not None else "",
                round(float(row.get("report_ref_cost") or 0.0), 2),
                round(float(row.get("report_ref_miles") or 0.0), 2),
            ]
        )

    plant_sheet = workbook.create_sheet("Plant Daily")
    plant_headers = [
        "Replay Date / Period",
        "Plant",
        "Report Rows",
        "Report Loads",
        "Report Orders",
        "Matched Orders",
        "Missing Orders",
        "Actual Loads",
        "Actual Avg Util %",
        "Actual Miles",
        "Actual Cost",
        "Optimized Loads",
        "Optimized Strategy",
        "Optimized Avg Util %",
        "Optimized Miles",
        "Optimized Cost",
        "Delta Loads",
        "Delta Util Pts",
        "Delta Miles",
        "Delta Cost",
        "Delta Cost %",
        "Report Ref Cost",
        "Report Ref Miles",
        "Report Ref Avg Truck Use",
    ]
    plant_sheet.append(plant_headers)
    for row in day_rows:
        plant_sheet.append(
            [
                row.get("date_created") or "",
                row.get("plant_code") or "",
                int(row.get("report_rows") or 0),
                int(row.get("report_loads") or 0),
                int(row.get("report_orders") or 0),
                int(row.get("matched_orders") or 0),
                int(row.get("missing_orders") or 0),
                int(row.get("actual_loads") or 0),
                round(float(row.get("actual_avg_utilization") or 0.0), 2),
                round(float(row.get("actual_total_miles") or 0.0), 2),
                round(float(row.get("actual_total_cost") or 0.0), 2),
                int(row.get("optimized_loads") or 0),
                row.get("optimized_strategy") or "",
                round(float(row.get("optimized_avg_utilization") or 0.0), 2),
                round(float(row.get("optimized_total_miles") or 0.0), 2),
                round(float(row.get("optimized_total_cost") or 0.0), 2),
                int(row.get("delta_loads") or 0),
                round(float(row.get("delta_avg_utilization") or 0.0), 2),
                round(float(row.get("delta_total_miles") or 0.0), 2),
                round(float(row.get("delta_total_cost") or 0.0), 2),
                round(float(row.get("delta_cost_pct") or 0.0), 4) if row.get("delta_cost_pct") is not None else "",
                round(float(row.get("report_ref_cost") or 0.0), 2) if row.get("report_ref_cost") is not None else "",
                round(float(row.get("report_ref_miles") or 0.0), 2) if row.get("report_ref_miles") is not None else "",
                round(float(row.get("report_ref_avg_truck_use") or 0.0), 2)
                if row.get("report_ref_avg_truck_use") is not None
                else "",
            ]
        )

    issues_sheet = workbook.create_sheet("Issues")
    issues_sheet.append(
        [
            "Replay Date / Period",
            "Plant",
            "Load Number",
            "Order Number",
            "Issue Type",
            "Severity",
            "Message",
            "Meta JSON",
        ]
    )
    for issue in issues:
        issues_sheet.append(
            [
                issue.get("date_created") or "",
                issue.get("plant_code") or "",
                issue.get("load_number") or "",
                issue.get("order_number") or "",
                issue.get("issue_type") or "",
                issue.get("severity") or "",
                issue.get("message") or "",
                issue.get("meta_json") or "",
            ]
        )

    metrics_sheet = workbook.create_sheet("Load Metrics")
    metrics_sheet.append(
        [
            "Replay Date / Period",
            "Plant",
            "Scenario",
            "Load Key",
            "Order Count",
            "Utilization %",
            "Estimated Miles",
            "Estimated Cost",
            "Order Numbers JSON",
        ]
    )
    for row in load_metrics:
        metrics_sheet.append(
            [
                row.get("date_created") or "",
                row.get("plant_code") or "",
                row.get("scenario") or "",
                row.get("load_key") or "",
                int(row.get("order_count") or 0),
                round(float(row.get("utilization_pct") or 0.0), 2),
                round(float(row.get("estimated_miles") or 0.0), 2),
                round(float(row.get("estimated_cost") or 0.0), 2),
                row.get("order_numbers_json") or "",
            ]
        )

    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        for idx, _ in enumerate(sheet[1], start=1):
            sheet.column_dimensions[chr(64 + min(idx, 26))].width = 18
    return workbook


@app.route("/planning-sessions/replay", methods=["GET", "POST"])
def planning_sessions_replay():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    _require_admin()

    preset = _get_replay_eval_preset()
    error = (request.args.get("replay_error") or "").strip()
    replay_success = (request.args.get("replay_success") or "").strip()
    source_run_id = (request.args.get("source_run_id") or "").strip()
    run_id_param = (request.args.get("run_id") or "").strip()
    selected_day = (request.args.get("day") or "").strip()
    scope_param = (request.args.get("evaluation_scope") or "").strip()
    requested_scope = replay_evaluator.normalize_evaluation_scope(scope_param)
    upload_scope = requested_scope
    upload_ops_parity_enabled = _coerce_bool_value(preset.get("ops_parity_enabled"))
    try:
        selected_run_id = int(run_id_param) if run_id_param else None
    except (TypeError, ValueError):
        selected_run_id = None
    run_id = None
    if request.method == "POST":
        upload_scope = replay_evaluator.normalize_evaluation_scope(request.form.get("evaluation_scope"))
        upload_ops_parity_enabled = _coerce_bool_value(request.form.get("ops_parity_enabled"))
        report_file = request.files.get("report_file")
        if not report_file or not getattr(report_file, "filename", ""):
            error = "Choose a .csv or .xlsx report file."
        else:
            try:
                run_preset = dict(preset)
                run_preset["ops_parity_enabled"] = upload_ops_parity_enabled
                run_id = replay_evaluator.run_replay_evaluation(
                    report_file,
                    preset=run_preset,
                    created_by=_get_session_profile_name() or _get_session_role(),
                    evaluation_scope=upload_scope,
                )
            except Exception as exc:
                error = str(exc)
        if run_id:
            return redirect(url_for("planning_sessions_replay", run_id=run_id, evaluation_scope=upload_scope))

    runs = db.list_replay_eval_runs(limit=20)
    for entry in runs:
        entry["summary"] = _parse_replay_summary(entry.get("summary_json"))
        entry["status"] = (entry.get("status") or "").upper()

    active_run = None
    if selected_run_id:
        active_run = next((entry for entry in runs if int(entry.get("id") or 0) == selected_run_id), None)
    if not active_run:
        active_run = runs[0] if runs else None

    active_summary = {}
    active_scope = requested_scope
    date_basis = "shipped_date"
    active_ops_parity_enabled = upload_ops_parity_enabled
    if active_run:
        active_summary = active_run.get("summary") or {}
        active_scope = replay_evaluator.normalize_evaluation_scope(
            scope_param or active_summary.get("evaluation_scope")
        )
        upload_scope = active_scope
        date_basis = (active_summary.get("date_basis") or "shipped_date").strip().lower()
        raw_params = (active_run.get("params_json") or "").strip()
        if raw_params:
            try:
                parsed_params = json.loads(raw_params)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed_params = None
            if isinstance(parsed_params, dict):
                active_ops_parity_enabled = _coerce_bool_value(
                    parsed_params.get("ops_parity_enabled")
                )
        upload_ops_parity_enabled = active_ops_parity_enabled

    day_values = []
    selected_day_row = {}
    selected_day_plants = []
    selected_day_issues = []
    issues_by_type = {}
    kpis = {}
    if active_run and active_run.get("status") == "COMPLETED":
        active_run_id = int(active_run.get("id") or 0)
        day_rows = db.list_replay_eval_day_plant(active_run_id)
        network_rows = replay_evaluator.build_network_daily_rollup(day_rows)
        day_values = sorted({row.get("date_created") for row in network_rows if row.get("date_created")})
        if selected_day not in day_values:
            selected_day = day_values[-1] if day_values else ""
        selected_day_row = next(
            (row for row in network_rows if (row.get("date_created") or "") == selected_day),
            {},
        )
        selected_day_plants = sorted(
            [row for row in day_rows if (row.get("date_created") or "") == selected_day],
            key=lambda row: row.get("plant_code") or "",
        )
        issues = db.list_replay_eval_issues(active_run_id)
        selected_day_issues = [
            issue for issue in issues if (issue.get("date_created") or "") == selected_day
        ] if selected_day else list(issues)
        for issue in selected_day_issues:
            issue_type = issue.get("issue_type") or "unknown"
            issues_by_type[issue_type] = issues_by_type.get(issue_type, 0) + 1

        actual_loads = int(selected_day_row.get("actual_loads") or 0)
        optimized_loads = int(selected_day_row.get("optimized_loads") or 0)
        actual_util = float(selected_day_row.get("actual_avg_utilization") or 0.0)
        optimized_util = float(selected_day_row.get("optimized_avg_utilization") or 0.0)
        actual_cost = float(selected_day_row.get("actual_total_cost") or 0.0)
        optimized_cost = float(selected_day_row.get("optimized_total_cost") or 0.0)
        actual_miles = float(selected_day_row.get("actual_total_miles") or 0.0)
        optimized_miles = float(selected_day_row.get("optimized_total_miles") or 0.0)
        load_delta = optimized_loads - actual_loads
        load_reduction_pct = (
            ((actual_loads - optimized_loads) / actual_loads) * 100.0
            if actual_loads
            else None
        )
        util_gain_pts = optimized_util - actual_util
        util_gain_pct = ((util_gain_pts / actual_util) * 100.0) if actual_util else None
        cost_delta = optimized_cost - actual_cost
        cost_savings = -cost_delta
        cost_savings_pct = ((cost_savings / actual_cost) * 100.0) if actual_cost else None
        miles_delta = optimized_miles - actual_miles
        miles_savings = -miles_delta
        miles_savings_pct = ((miles_savings / actual_miles) * 100.0) if actual_miles else None
        kpis = {
            "actual_loads": actual_loads,
            "optimized_loads": optimized_loads,
            "load_delta": load_delta,
            "load_reduction_pct": load_reduction_pct,
            "util_gain_pts": util_gain_pts,
            "util_gain_pct": util_gain_pct,
            "cost_delta": cost_delta,
            "cost_savings": cost_savings,
            "cost_savings_pct": cost_savings_pct,
            "miles_delta": miles_delta,
            "miles_savings": miles_savings,
            "miles_savings_pct": miles_savings_pct,
            "actual_util": actual_util,
            "optimized_util": optimized_util,
            "actual_cost": actual_cost,
            "optimized_cost": optimized_cost,
            "actual_miles": actual_miles,
            "optimized_miles": optimized_miles,
            "matched_orders": int(selected_day_row.get("matched_orders") or 0),
            "missing_orders": int(selected_day_row.get("missing_orders") or 0),
        }

    return render_template(
        "replay_eval.html",
        preset=preset,
        active_run=active_run,
        evaluation_scope=active_scope,
        date_basis=date_basis,
        day_label=("Replay Period" if active_scope == replay_evaluator.EVAL_SCOPE_WEEKLY_POOLED else "Shipped Day"),
        upload_scope=upload_scope,
        selected_day=selected_day,
        day_values=day_values,
        selected_day_row=selected_day_row,
        selected_day_plants=selected_day_plants,
        selected_day_issues=selected_day_issues,
        issues_by_type=issues_by_type,
        kpis=kpis,
        error=error,
        replay_success=replay_success,
        source_run_id=source_run_id,
        upload_ops_parity_enabled=upload_ops_parity_enabled,
        active_ops_parity_enabled=active_ops_parity_enabled,
        active_summary=active_summary,
    )


@app.route("/planning-sessions/replay/<int:run_id>")
def planning_sessions_replay_detail(run_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    _require_admin()

    replay_error = (request.args.get("replay_error") or "").strip()
    replay_success = (request.args.get("replay_success") or "").strip()
    source_run_id = (request.args.get("source_run_id") or "").strip()
    day = (request.args.get("day") or "").strip()
    evaluation_scope = (request.args.get("evaluation_scope") or "").strip()
    return redirect(
        url_for(
            "planning_sessions_replay",
            run_id=run_id,
            day=day or None,
            evaluation_scope=evaluation_scope or None,
            replay_error=replay_error or None,
            replay_success=replay_success or None,
            source_run_id=source_run_id or None,
        )
    )


@app.route("/planning-sessions/replay/<int:run_id>/loads")
def planning_sessions_replay_loads(run_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    _require_admin()

    scenario = (request.args.get("scenario") or "OPTIMIZED").strip().upper()
    if scenario not in {"OPTIMIZED", "ACTUAL"}:
        scenario = "OPTIMIZED"
    date_created = (request.args.get("date_created") or "").strip()
    plant_code = (request.args.get("plant_code") or "").strip().upper()
    return redirect(
        url_for(
            "loads",
            replay_run_id=run_id,
            replay_scenario=scenario,
            replay_date_created=date_created or None,
            replay_plant_code=plant_code or None,
        )
    )


@app.route("/planning-sessions/replay/<int:run_id>/reproduce", methods=["POST"])
def planning_sessions_replay_reproduce(run_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    _require_admin()

    date_created = (request.form.get("date_created") or "").strip()
    plant_code = (request.form.get("plant_code") or "").strip().upper()
    evaluation_scope = replay_evaluator.normalize_evaluation_scope(request.form.get("evaluation_scope"))
    if not date_created or not plant_code:
        return redirect(
            url_for(
                "planning_sessions_replay",
                run_id=run_id,
                evaluation_scope=evaluation_scope,
                replay_error="Select a valid day and plant bucket to reproduce.",
            )
        )

    try:
        reproduced_run_id = replay_evaluator.reproduce_replay_bucket(
            source_run_id=run_id,
            date_created=date_created,
            plant_code=plant_code,
            created_by=_get_session_profile_name() or _get_session_role(),
        )
    except Exception as exc:
        return redirect(
            url_for(
                "planning_sessions_replay",
                run_id=run_id,
                evaluation_scope=evaluation_scope,
                replay_error=str(exc),
            )
        )

    return redirect(
        url_for(
            "planning_sessions_replay",
            run_id=reproduced_run_id,
            day=date_created,
            evaluation_scope=evaluation_scope,
            replay_success=f"Reproduced bucket {date_created} / {plant_code} from run #{run_id}.",
            source_run_id=run_id,
        )
    )


@app.route("/planning-sessions/replay/<int:run_id>/export.xlsx")
def planning_sessions_replay_export(run_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    _require_admin()

    run = db.get_replay_eval_run(run_id)
    if not run:
        abort(404)
    day_rows = db.list_replay_eval_day_plant(run_id)
    network_rows = replay_evaluator.build_network_daily_rollup(day_rows)
    issues = db.list_replay_eval_issues(run_id)
    load_metrics = db.list_replay_eval_load_metrics(run_id)
    workbook = _build_replay_workbook(run, network_rows, day_rows, issues, load_metrics)

    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    filename = f"replay_eval_run_{run_id}_{date.today().isoformat()}.xlsx"
    return Response(
        output.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/planning-sessions/replay/<int:run_id>/issues.csv")
def planning_sessions_replay_issues_csv(run_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    _require_admin()

    run = db.get_replay_eval_run(run_id)
    if not run:
        abort(404)
    issues = db.list_replay_eval_issues(run_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "date_created",
            "plant_code",
            "load_number",
            "order_number",
            "issue_type",
            "severity",
            "message",
            "meta_json",
        ]
    )
    for issue in issues:
        writer.writerow(
            [
                issue.get("date_created") or "",
                issue.get("plant_code") or "",
                issue.get("load_number") or "",
                issue.get("order_number") or "",
                issue.get("issue_type") or "",
                issue.get("severity") or "",
                issue.get("message") or "",
                issue.get("meta_json") or "",
            ]
        )
    output.seek(0)
    filename = f"replay_eval_issues_run_{run_id}_{date.today().isoformat()}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _planning_session_filter_values(source):
    return {
        "plant": ((source.get("plant") if source else "") or "").strip().upper(),
        "planner": ((source.get("planner") if source else "") or "").strip(),
        "start": ((source.get("start") if source else "") or "").strip(),
        "end": ((source.get("end") if source else "") or "").strip(),
    }


def _planning_sessions_redirect_args(filters):
    return {
        "plant": filters.get("plant") or "",
        "planner": filters.get("planner") or "",
        "start": filters.get("start") or "",
        "end": filters.get("end") or "",
    }


def _session_plant_scope(session_id):
    plants = set()
    if not session_id:
        return []

    planning_session = db.get_planning_session(session_id)
    if planning_session:
        plant_code = _normalize_plant_code(planning_session.get("plant_code"))
        if plant_code in PLANT_CODES:
            plants.add(plant_code)

    for load in db.list_loads(None, session_id=session_id):
        plant_code = _normalize_plant_code(load.get("origin_plant"))
        if plant_code:
            plants.add(plant_code)

    return sorted(plants)


def _reintroduce_orders_to_pool(plants):
    cleaned = []
    seen = set()
    for plant in (plants or []):
        code = _normalize_plant_code(plant)
        if not code or code in seen:
            continue
        seen.add(code)
        cleaned.append(code)
    if not cleaned:
        return
    db.include_orders_for_plants(cleaned)


def _archive_session_and_release_loads(session_id):
    if not session_id:
        return False
    planning_session = db.get_planning_session(session_id)
    if not planning_session:
        return False
    _reintroduce_orders_to_pool(_session_plant_scope(session_id))
    db.clear_loads_for_session(session_id)
    db.archive_planning_session(session_id)
    if _get_active_planning_session_id() == session_id:
        _set_active_planning_session_id(None)
    return True


def _build_planning_session_rollup(loads):
    load_summaries = []
    order_map = {}

    for load in loads or []:
        lines = load.get("lines") or []
        stop_keys = set()
        load_order_map = {}
        total_feet = 0.0

        for line in lines:
            so_num = (line.get("so_num") or "").strip()
            if not so_num:
                continue

            total_feet += float(line.get("total_length_ft") or line.get("line_total_feet") or 0)
            state = (line.get("state") or "").strip().upper()
            zip_code = (line.get("zip") or "").strip()
            stop_keys.add(f"{state}|{zip_code}")

            line_due = _parse_date(line.get("due_date"))
            due_label = line_due.isoformat() if line_due else (line.get("due_date") or "")
            line_qty = float(line.get("qty") or 0)
            line_length = float(line.get("total_length_ft") or line.get("line_total_feet") or 0)

            load_order = load_order_map.get(so_num)
            if not load_order:
                load_order = {
                    "so_num": so_num,
                    "cust_name": (line.get("cust_name") or "").strip(),
                    "state": state,
                    "city": (line.get("city") or "").strip(),
                    "zip": zip_code,
                    "due_date": due_label,
                    "due_date_obj": line_due,
                    "line_count": 0,
                    "total_qty": 0.0,
                    "total_length_ft": 0.0,
                }
                load_order_map[so_num] = load_order
            load_order["line_count"] += 1
            load_order["total_qty"] += line_qty
            load_order["total_length_ft"] += line_length
            if line_due and (not load_order.get("due_date_obj") or line_due < load_order["due_date_obj"]):
                load_order["due_date_obj"] = line_due
                load_order["due_date"] = line_due.isoformat()

            session_order = order_map.get(so_num)
            if not session_order:
                session_order = {
                    "so_num": so_num,
                    "cust_name": (line.get("cust_name") or "").strip(),
                    "state": state,
                    "city": (line.get("city") or "").strip(),
                    "zip": zip_code,
                    "due_date": due_label,
                    "due_date_obj": line_due,
                    "line_count": 0,
                    "total_qty": 0.0,
                    "total_length_ft": 0.0,
                    "loads": set(),
                }
                order_map[so_num] = session_order
            session_order["line_count"] += 1
            session_order["total_qty"] += line_qty
            session_order["total_length_ft"] += line_length
            if line_due and (not session_order.get("due_date_obj") or line_due < session_order["due_date_obj"]):
                session_order["due_date_obj"] = line_due
                session_order["due_date"] = line_due.isoformat()
            load_number = load.get("load_number") or f"Load #{load.get('id')}"
            session_order["loads"].add(load_number)

        load_orders = []
        for entry in load_order_map.values():
            entry.pop("due_date_obj", None)
            entry["total_qty"] = round(entry["total_qty"], 1)
            entry["total_length_ft"] = round(entry["total_length_ft"], 1)
            load_orders.append(entry)
        load_orders.sort(key=lambda entry: (entry.get("due_date") or "9999-12-31", entry.get("so_num") or ""))

        load_summaries.append(
            {
                "id": load.get("id"),
                "load_number": load.get("load_number") or f"Load #{load.get('id')}",
                "status": (load.get("status") or "PROPOSED").upper(),
                "trailer_type": stack_calculator.normalize_trailer_type(load.get("trailer_type"), default="STEP_DECK"),
                "utilization_pct": round(float(load.get("utilization_pct") or 0), 1),
                "estimated_cost": round(float(load.get("estimated_cost") or 0), 2),
                "estimated_miles": round(float(load.get("estimated_miles") or 0), 1),
                "stop_count": len(stop_keys),
                "line_count": len(lines),
                "order_count": len(load_orders),
                "total_length_ft": round(total_feet, 1),
                "created_at": load.get("created_at"),
                "orders": load_orders,
                "return_to_origin": bool(load.get("return_to_origin")),
                "return_miles": round(float(load.get("return_miles") or 0.0), 1),
                "return_cost": round(float(load.get("return_cost") or 0.0), 2),
                "freight_breakdown": load.get("freight_breakdown") or {},
            }
        )

    load_summaries.sort(key=lambda entry: (entry.get("load_number") or "", entry.get("id") or 0))

    session_orders = []
    for entry in order_map.values():
        entry.pop("due_date_obj", None)
        entry["loads"] = sorted(entry.get("loads") or [])
        entry["load_count"] = len(entry["loads"])
        entry["total_qty"] = round(entry["total_qty"], 1)
        entry["total_length_ft"] = round(entry["total_length_ft"], 1)
        session_orders.append(entry)
    session_orders.sort(key=lambda entry: (entry.get("due_date") or "9999-12-31", entry.get("so_num") or ""))

    return {
        "loads": load_summaries,
        "orders": session_orders,
    }


def _build_load_report_rows(loads):
    sku_specs = {spec["sku"]: spec for spec in db.list_sku_specs()}
    zip_coords = geo_utils.load_zip_coordinates()
    stop_color_palette = _get_stop_color_palette()
    rows = []

    for load in loads or []:
        lines = load.get("lines") or []
        trailer_type = stack_calculator.normalize_trailer_type(load.get("trailer_type"), default="STEP_DECK")
        ordered_stops = _ordered_stops_for_lines(lines, load.get("origin_plant"), zip_coords)
        ordered_stops = _apply_load_route_direction(ordered_stops, load=load)
        stop_sequence_map = _stop_sequence_map_from_ordered_stops(ordered_stops)
        schematic, _, _ = _calculate_load_schematic(
            lines,
            sku_specs,
            trailer_type,
            stop_sequence_map=stop_sequence_map,
        )
        trailer_config = _trailer_config_for_type(trailer_type)
        schematic = dict(schematic or {})
        schematic.setdefault("positions", [])
        schematic.setdefault("warnings", [])
        schematic.setdefault("trailer_type", trailer_config["type"])
        schematic.setdefault("capacity_feet", trailer_config["capacity"])
        schematic.setdefault("lower_deck_length", trailer_config["lower"])
        schematic.setdefault("upper_deck_length", trailer_config["upper"])
        schematic.setdefault(
            "utilization_pct",
            round(float(load.get("utilization_pct") or 0), 1),
        )
        schematic.setdefault(
            "utilization_grade",
            _utilization_grade(float(schematic.get("utilization_pct") or 0)),
        )
        schematic_warnings = list(schematic.get("warnings") or [])
        display_utilization_pct = round(
            float(schematic.get("utilization_pct") or load.get("utilization_pct") or 0),
            1,
        )

        order_map = {}
        customers = set()
        for idx, line in enumerate(lines):
            so_num = (line.get("so_num") or "").strip() or f"UNASSIGNED-{idx + 1}"
            state = (line.get("state") or "").strip().upper()
            city = (line.get("city") or "").strip()
            zip_code = (line.get("zip") or "").strip()
            cust_name = (line.get("cust_name") or "").strip()
            stop_order = stop_sequence_map.get(_line_stop_key(state, zip_code))
            due_obj = _parse_date(line.get("due_date"))
            due_label = due_obj.isoformat() if due_obj else (line.get("due_date") or "")
            qty = float(line.get("qty") or 0)
            line_length = float(line.get("total_length_ft") or line.get("line_total_feet") or 0)
            if cust_name:
                customers.add(cust_name)

            entry = order_map.get(so_num)
            if not entry:
                entry = {
                    "so_num": so_num,
                    "cust_name": cust_name,
                    "state": state,
                    "city": city,
                    "zip": zip_code,
                    "due_date": due_label,
                    "due_date_obj": due_obj,
                    "stop_order": stop_order,
                    "line_count": 0,
                    "total_qty": 0.0,
                    "total_length_ft": 0.0,
                }
                order_map[so_num] = entry
            entry["line_count"] += 1
            entry["total_qty"] += qty
            entry["total_length_ft"] += line_length
            if stop_order and (not entry.get("stop_order") or stop_order < entry["stop_order"]):
                entry["stop_order"] = stop_order
            if due_obj and (not entry.get("due_date_obj") or due_obj < entry["due_date_obj"]):
                entry["due_date_obj"] = due_obj
                entry["due_date"] = due_obj.isoformat()
            if not entry.get("cust_name") and cust_name:
                entry["cust_name"] = cust_name
            if not entry.get("city") and city:
                entry["city"] = city
            if not entry.get("state") and state:
                entry["state"] = state
            if not entry.get("zip") and zip_code:
                entry["zip"] = zip_code

        ship_date_obj = None
        order_rows = []
        for order in order_map.values():
            due_obj = order.get("due_date_obj")
            if due_obj and (not ship_date_obj or due_obj < ship_date_obj):
                ship_date_obj = due_obj
            order_rows.append(order)
        order_rows.sort(
            key=lambda entry: (
                int(entry.get("stop_order") or 999),
                entry.get("due_date") or "9999-12-31",
                entry.get("so_num") or "",
            )
        )
        order_colors = _build_order_colors_for_lines(
            lines,
            stop_sequence_map=stop_sequence_map,
            stop_palette=stop_color_palette,
        )

        early_orders = []
        for order in order_rows:
            due_obj = order.pop("due_date_obj", None)
            early_days = (due_obj - ship_date_obj).days if due_obj and ship_date_obj else 0
            order["early_days"] = early_days if early_days > 0 else 0
            order["is_early_delivery"] = order["early_days"] > 0
            order["early_flag"] = "YES" if order["is_early_delivery"] else "NO"
            order["total_qty"] = round(order["total_qty"], 1)
            order["total_length_ft"] = round(order["total_length_ft"], 1)
            order["stop_order_display"] = (
                f"{int(order.get('stop_order')):02d}" if order.get("stop_order") else "--"
            )
            destination_bits = []
            if order.get("city"):
                destination_bits.append(order.get("city"))
            if order.get("state"):
                destination_bits.append(order.get("state"))
            order["destination_label"] = ", ".join(destination_bits) if destination_bits else "--"
            if order["is_early_delivery"]:
                early_orders.append(order)

        deck_blocks = {"lower": [], "upper": []}
        for deck_key in ("lower", "upper"):
            positions = [
                pos for pos in (schematic.get("positions") or [])
                if (pos.get("deck") or "lower") == deck_key
            ]
            total_length = sum(float(pos.get("length_ft") or 0) for pos in positions) or 1.0
            for pos in positions:
                length_ft = float(pos.get("length_ft") or 0)
                order_ids = []
                for item in pos.get("items", []) or []:
                    order_id = (item.get("order_id") or "").strip()
                    if order_id and order_id not in order_ids:
                        order_ids.append(order_id)
                if order_ids:
                    if len(order_ids) > 2:
                        label = f"{order_ids[0]}, {order_ids[1]} +{len(order_ids) - 2}"
                    else:
                        label = ", ".join(order_ids)
                else:
                    label = "Open"
                width_pct = max((length_ft / total_length) * 100.0, 8.0) if length_ft else 8.0
                deck_blocks[deck_key].append(
                    {
                        "length_ft": round(length_ft, 1),
                        "order_ids": order_ids,
                        "label": label,
                        "width_pct": round(width_pct, 1),
                    }
                )

        schematic_segments = []
        if deck_blocks["lower"]:
            lower_summary = " > ".join(
                [
                    f"{block['label']} ({block['length_ft']:.1f} ft)"
                    for block in deck_blocks["lower"]
                ]
            )
            schematic_segments.append(f"Lower: {lower_summary}")
        if deck_blocks["upper"]:
            upper_summary = " > ".join(
                [
                    f"{block['label']} ({block['length_ft']:.1f} ft)"
                    for block in deck_blocks["upper"]
                ]
            )
            schematic_segments.append(f"Upper: {upper_summary}")

        early_callout = ""
        if early_orders:
            details = ", ".join(
                [f"SO {entry['so_num']} ({entry['early_days']}d early)" for entry in early_orders[:4]]
            )
            if len(early_orders) > 4:
                details = f"{details}, +{len(early_orders) - 4} more"
            early_callout = f"Customer notification required: {details}."

        route_cities = [order.get("city") for order in order_rows if order.get("city")]
        route_states = [order.get("state") for order in order_rows if order.get("state")]
        route_city = route_cities[0] if route_cities else ""
        route_state = route_states[0] if route_states else ((load.get("destination_state") or "").strip().upper())
        route_label = (
            f"{route_city}, {route_state}".strip(", ")
            if route_city or route_state
            else (load.get("destination_state") or "--")
        )
        unique_cities = sorted({city for city in route_cities if city})
        if len(unique_cities) > 1:
            route_label = f"{route_label} (+{len(unique_cities) - 1} more)"

        total_units = sum((order.get("total_qty") or 0) for order in order_rows)

        rows.append(
            {
                "id": load.get("id"),
                "load_number": load.get("load_number") or f"Load #{load.get('id')}",
                "status": _normalize_session_status(load.get("status") or "PROPOSED"),
                "created_at": load.get("created_at"),
                "origin_plant": (load.get("origin_plant") or "").strip().upper(),
                "destination_state": (load.get("destination_state") or "").strip().upper(),
                "route_label": route_label,
                "trailer_type": trailer_type,
                "utilization_pct": round(float(load.get("utilization_pct") or 0), 1),
                "estimated_cost": round(float(load.get("estimated_cost") or 0), 2),
                "estimated_miles": round(float(load.get("route_distance") or load.get("estimated_miles") or 0), 1),
                "stop_count": len(ordered_stops),
                "line_count": len(lines),
                "order_count": len(order_rows),
                "total_units": round(total_units, 1),
                "ship_date": ship_date_obj.isoformat() if ship_date_obj else "",
                "orders": order_rows,
                "lines": lines,
                "order_numbers": [entry.get("so_num") for entry in order_rows if entry.get("so_num")],
                "customers": sorted(customers),
                "early_orders": early_orders,
                "has_early_delivery": bool(early_orders),
                "early_delivery_callout": early_callout,
                "schematic_utilization_pct": round(float(schematic.get("utilization_pct") or 0), 1),
                "schematic_grade": schematic.get("utilization_grade") or _utilization_grade(float(load.get("utilization_pct") or 0)),
                "schematic_blocks": deck_blocks,
                "schematic_summary_text": " | ".join(schematic_segments),
                "schematic": schematic,
                "display_utilization_pct": display_utilization_pct,
                "over_capacity": bool(schematic.get("exceeds_capacity")),
                "schematic_warnings": schematic_warnings,
                "schematic_warning_count": len(schematic_warnings),
                "has_custom_schematic": False,
                "order_colors": order_colors,
                "auto_trailer_label": "",
                "auto_trailer_reason": "",
            }
        )

    rows.sort(key=lambda entry: (entry.get("load_number") or "", entry.get("id") or 0))
    for idx, row in enumerate(rows, start=1):
        display_id = f"L#{idx:02d}"
        row["display_load_id"] = display_id
        for order in row.get("orders") or []:
            order["display_load_id"] = display_id
    return rows


def _build_load_report_preview_rows(report_rows, limit=8):
    preview = []
    for load in report_rows or []:
        for idx, order in enumerate(load.get("orders") or []):
            preview.append(
                {
                    "load_id": load.get("load_number") or load.get("display_load_id") or "",
                    "stop_order": order.get("stop_order_display") or "--",
                    "so_number": order.get("so_num") or "",
                    "customer_name": order.get("cust_name") or "",
                    "destination_city": order.get("city") or "",
                    "state": order.get("state") or "",
                    "ship_date": load.get("ship_date") or "",
                    "due_date": order.get("due_date") or "",
                    "total_units": order.get("total_qty") or 0,
                    "early_flag": order.get("early_flag") or "NO",
                    "is_group_start": idx == 0,
                }
            )
            if len(preview) >= limit:
                return preview
    return preview


def _hex_to_rgb_tuple(hex_value, fallback):
    raw = str(hex_value or "").strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join([ch * 2 for ch in raw])
    if len(raw) != 6:
        return fallback
    try:
        return tuple(int(raw[idx:idx + 2], 16) for idx in (0, 2, 4))
    except ValueError:
        return fallback


def _blend_rgb(base_rgb, target_rgb, ratio):
    ratio = max(0.0, min(float(ratio), 1.0))
    return tuple(
        int(base_rgb[idx] + (target_rgb[idx] - base_rgb[idx]) * ratio)
        for idx in range(3)
    )


def _measure_text(draw_ctx, text):
    try:
        left, top, right, bottom = draw_ctx.textbbox((0, 0), text)
        return max(right - left, 1), max(bottom - top, 1)
    except AttributeError:
        return draw_ctx.textsize(text)


def _build_excel_schematic_image(load):
    if not (PILImage and ImageDraw and OpenPyxlImage):
        return None

    schematic = load.get("schematic") or {}
    positions = schematic.get("positions") or []
    if not positions:
        return None

    image_width = 360
    image_height = 118
    border_rgb = (177, 194, 216)
    text_rgb = (44, 62, 92)
    muted_text_rgb = (92, 112, 142)
    track_fill_rgb = (233, 241, 252)
    deck_title_rgb = (62, 89, 129)

    image = PILImage.new("RGB", (image_width, image_height), (248, 251, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (4, 4, image_width - 5, image_height - 5),
        radius=10,
        fill=(243, 248, 255),
        outline=border_rgb,
        width=1,
    )

    trailer_label = (schematic.get("trailer_type") or load.get("trailer_type") or "TRAILER")
    trailer_label = trailer_label.replace("_", " ")
    draw.text((12, 10), f"{trailer_label} schematic", fill=text_rgb)

    deck_meta = [("lower", "Lower Deck", 34, 67)]
    has_upper = any((pos.get("deck") or "lower") == "upper" for pos in positions)
    if has_upper:
        deck_meta.append(("upper", "Upper Deck", 74, 106))

    order_color_map = load.get("order_colors") or {}
    for deck_key, deck_label, top_y, bottom_y in deck_meta:
        deck_positions = [
            pos for pos in positions
            if (pos.get("deck") or "lower") == deck_key
        ]
        if not deck_positions:
            continue

        draw.text((12, top_y - 13), deck_label, fill=deck_title_rgb)
        left_x = 12
        right_x = image_width - 12
        draw.rounded_rectangle(
            (left_x, top_y, right_x, bottom_y),
            radius=6,
            fill=track_fill_rgb,
            outline=border_rgb,
            width=1,
        )

        usable_width = right_x - left_x - 4
        lengths = [max(float(pos.get("length_ft") or 0), 0.0) for pos in deck_positions]
        total_length = sum(lengths) or float(len(deck_positions))
        cursor_x = left_x + 2

        for idx, pos in enumerate(deck_positions):
            segment_ratio = (lengths[idx] / total_length) if total_length else (1.0 / len(deck_positions))
            segment_width = max(int(round(usable_width * segment_ratio)), 18)
            segment_right = (
                right_x - 2
                if idx == len(deck_positions) - 1
                else min(cursor_x + segment_width, right_x - 2)
            )

            item_order_ids = []
            for item in pos.get("items") or []:
                order_id = str(item.get("order_id") or "").strip()
                if order_id and order_id not in item_order_ids:
                    item_order_ids.append(order_id)

            primary_order = item_order_ids[0] if item_order_ids else ""
            color_hex = order_color_map.get(primary_order, "#94A3B8")
            base_rgb = _hex_to_rgb_tuple(color_hex, (148, 163, 184))
            fill_rgb = _blend_rgb(base_rgb, (255, 255, 255), 0.72)
            outline_rgb = _blend_rgb(base_rgb, (32, 43, 63), 0.25)

            draw.rounded_rectangle(
                (cursor_x, top_y + 2, segment_right, bottom_y - 2),
                radius=4,
                fill=fill_rgb,
                outline=outline_rgb,
                width=1,
            )

            if primary_order:
                label = primary_order
                if len(item_order_ids) > 1:
                    label = f"{primary_order}+{len(item_order_ids) - 1}"
            else:
                label = "OPEN"
            if len(label) > 12:
                label = f"{label[:11]}~"

            text_w, text_h = _measure_text(draw, label)
            label_x = cursor_x + max(((segment_right - cursor_x) - text_w) // 2, 2)
            label_y = top_y + max(((bottom_y - top_y) - text_h) // 2, 1)
            draw.text((label_x, label_y), label, fill=text_rgb)
            cursor_x = segment_right + 2

    draw.text((12, image_height - 17), "Generated from current load stacking plan", fill=muted_text_rgb)

    image_stream = io.BytesIO()
    image.save(image_stream, format="PNG")
    image_stream.seek(0)
    excel_image = OpenPyxlImage(image_stream)
    excel_image.width = 270
    excel_image.height = 88 if has_upper else 72
    return excel_image


def _hex_to_excel_argb(hex_color, fallback="#94A3B8"):
    normalized = _normalize_hex_color(hex_color, fallback)
    return f"FF{normalized.lstrip('#')}"


def _lighten_hex_color(hex_color, ratio=0.84):
    base_rgb = _hex_to_rgb_tuple(_normalize_hex_color(hex_color, "#94A3B8"), (148, 163, 184))
    lighter = _blend_rgb(base_rgb, (255, 255, 255), ratio)
    return "#{:02X}{:02X}{:02X}".format(*lighter)


def _format_street_address(line):
    address1 = str(line.get("address1") or "").strip()
    address2 = str(line.get("address2") or "").strip()
    if address1 and address2:
        return f"{address1} {address2}".strip()
    return address1 or address2


def _build_load_sheet_stops(load):
    lines = load.get("lines") or []
    if not lines:
        return []

    stops_by_key = {}
    for line in lines:
        key = _line_stop_key(line.get("state"), line.get("zip"))
        if key not in stops_by_key:
            stops_by_key[key] = {
                "stop_key": key,
                "stop_order": None,
                "state": (line.get("state") or "").strip().upper(),
                "zip": (line.get("zip") or "").strip(),
                "city": (line.get("city") or "").strip(),
                "address": _format_street_address(line),
                "customers": [],
                "sku_entries": [],
            }

        stop = stops_by_key[key]
        customer = (line.get("cust_name") or "").strip()
        if customer and customer not in stop["customers"]:
            stop["customers"].append(customer)
        if not stop.get("city"):
            stop["city"] = (line.get("city") or "").strip()
        if not stop.get("address"):
            stop["address"] = _format_street_address(line)

        so_num = (line.get("so_num") or "").strip()
        sku = (line.get("sku") or "").strip()
        item = (line.get("item") or "").strip()
        qty_value = float(line.get("qty") or 0)
        qty_text = "{:,.0f}".format(qty_value) if qty_value.is_integer() else "{:,.1f}".format(qty_value)
        descriptor = sku or item or "SKU"
        entry = f"{so_num} / {descriptor} x{qty_text}" if so_num else f"{descriptor} x{qty_text}"
        if entry not in stop["sku_entries"]:
            stop["sku_entries"].append(entry)

    order_stops = {}
    for order in load.get("orders") or []:
        order_key = _line_stop_key(order.get("state"), order.get("zip"))
        raw_sequence = order.get("stop_order")
        if raw_sequence:
            order_stops[order_key] = int(raw_sequence)

    stop_rows = []
    for stop in stops_by_key.values():
        stop_order = order_stops.get(stop["stop_key"])
        stop["stop_order"] = stop_order or 999
        stop_rows.append(stop)

    stop_rows.sort(
        key=lambda entry: (
            int(entry.get("stop_order") or 999),
            entry.get("city") or "",
            entry.get("zip") or "",
        )
    )
    return stop_rows


def _color_luminance(hex_color):
    raw = _normalize_hex_color(hex_color, "#94A3B8").lstrip("#")
    r = int(raw[0:2], 16)
    g = int(raw[2:4], 16)
    b = int(raw[4:6], 16)
    return (0.299 * r) + (0.587 * g) + (0.114 * b)


def _expand_schematic_units_for_position(position):
    expanded = []
    for item in position.get("items") or []:
        units = max(_coerce_int_value(item.get("units"), 0), 0)
        if units <= 0:
            continue
        label = (item.get("sku") or item.get("item") or "SKU").strip() or "SKU"
        stop_sequence = _coerce_int_value(item.get("stop_sequence"), 0)
        for _ in range(units):
            expanded.append(
                {
                    "label": label,
                    "stop_sequence": stop_sequence,
                }
            )
    return expanded


def _write_load_sheet_schematic_grid(
    ws,
    start_row,
    load,
    stop_palette,
    medium_side,
    thin_side,
):
    schematic = load.get("schematic") or {}
    positions = list(schematic.get("positions") or [])
    if not positions:
        return start_row

    deck_groups = [
        ("upper", "Upper Deck"),
        ("lower", "Lower Deck"),
    ]
    columns = list(range(1, 9))
    chunk_size = len(columns)
    row_cursor = start_row

    for deck_key, deck_label in deck_groups:
        deck_positions = [pos for pos in positions if (pos.get("deck") or "lower") == deck_key]
        if not deck_positions:
            continue

        ws.merge_cells(start_row=row_cursor, start_column=1, end_row=row_cursor, end_column=8)
        title_cell = ws.cell(row=row_cursor, column=1, value=f"{deck_label} Stacking Schematic")
        title_cell.font = Font(bold=True, color="FF334155")
        title_cell.alignment = Alignment(horizontal="left", vertical="center")
        row_cursor += 1

        position_chunks = [deck_positions[idx : idx + chunk_size] for idx in range(0, len(deck_positions), chunk_size)]
        for chunk in position_chunks:
            expanded_columns = [_expand_schematic_units_for_position(pos) for pos in chunk]
            max_stack = max((len(column_units) for column_units in expanded_columns), default=1)
            max_stack = max(max_stack, 1)

            # Stack/position row
            ws.row_dimensions[row_cursor].height = 20
            for col_idx in columns:
                cell = ws.cell(row=row_cursor, column=col_idx, value="")
                if col_idx <= len(chunk):
                    cell.value = f"Stack {col_idx}"
                cell.font = Font(bold=True, color="FF475569")
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.fill = PatternFill(fill_type="solid", fgColor="FFF8FAFC")
                cell.border = Border(
                    left=medium_side if col_idx == 1 else thin_side,
                    right=medium_side if col_idx == columns[-1] else thin_side,
                    top=thin_side,
                    bottom=thin_side,
                )
            row_cursor += 1

            # Unit cells (one SKU per cell), bottom-aligned within each stack.
            for stack_row in range(max_stack):
                excel_row = row_cursor + stack_row
                ws.row_dimensions[excel_row].height = 22
                for col_idx in columns:
                    cell = ws.cell(row=excel_row, column=col_idx, value="")
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                    cell.border = Border(
                        left=medium_side if col_idx == 1 else thin_side,
                        right=medium_side if col_idx == columns[-1] else thin_side,
                        top=thin_side,
                        bottom=thin_side,
                    )
                    if col_idx > len(expanded_columns):
                        continue
                    col_units = expanded_columns[col_idx - 1]
                    blank_lead = max_stack - len(col_units)
                    unit_idx = stack_row - blank_lead
                    if unit_idx < 0 or unit_idx >= len(col_units):
                        continue
                    unit = col_units[unit_idx]
                    stop_sequence = _coerce_int_value(unit.get("stop_sequence"), 0)
                    stop_color = _color_for_stop_sequence(stop_sequence, stop_palette)
                    cell.value = unit.get("label") or "SKU"
                    cell.fill = PatternFill(fill_type="solid", fgColor=_hex_to_excel_argb(stop_color, fallback="#94A3B8"))
                    text_color = "FFFFFFFF" if _color_luminance(stop_color) < 138 else "FF0F172A"
                    cell.font = Font(bold=True, color=text_color)
            row_cursor += max_stack

            # Stop-sequence legend row aligned to stacks.
            ws.row_dimensions[row_cursor].height = 19
            for col_idx in columns:
                legend = ws.cell(row=row_cursor, column=col_idx, value="")
                legend.alignment = Alignment(horizontal="center", vertical="center")
                legend.border = Border(
                    left=medium_side if col_idx == 1 else thin_side,
                    right=medium_side if col_idx == columns[-1] else thin_side,
                    top=thin_side,
                    bottom=medium_side,
                )
                legend.fill = PatternFill(fill_type="solid", fgColor="FFF8FAFC")
                if col_idx > len(expanded_columns):
                    continue
                col_units = expanded_columns[col_idx - 1]
                top_sequence = _coerce_int_value((col_units[-1] if col_units else {}).get("stop_sequence"), 0)
                if top_sequence > 0:
                    legend.value = f"Stop {top_sequence}"
                    legend.font = Font(bold=True, color="FF334155")
            row_cursor += 1
            row_cursor += 1
    return row_cursor


def _write_load_sheet_block(
    ws,
    start_row,
    load,
    stop_palette,
    medium_side,
    thin_side,
    header_font,
    body_font,
):
    columns = list(range(1, 9))
    has_medium_right_col = columns[-1]
    route_stops = _build_load_sheet_stops(load)

    start_zip = route_stops[0].get("zip") if route_stops else ""
    stop_zip = route_stops[-1].get("zip") if route_stops else ""
    ship_budget = round(float(load.get("estimated_cost") or 0), 2)
    ship_date = load.get("ship_date") or ""

    trailer_label = str(load.get("trailer_type") or "").replace("_", " ").title() or "Trailer"
    load_title_values = [
        "COT Loadsheet",
        load.get("origin_plant") or "",
        load.get("load_number") or load.get("display_load_id") or "",
        trailer_label,
        "",
        "",
        "",
        "",
    ]
    meta_headers = [
        "Start ZIP",
        "Stop ZIP",
        "Carrier",
        "Trailer Type",
        "Trailer Count",
        "Total Miles",
        "Ship Budget",
        "Must Ship By",
    ]
    meta_values = [
        start_zip or "",
        stop_zip or "",
        "TBD",
        trailer_label,
        1,
        round(float(load.get("estimated_miles") or 0), 1),
        ship_budget,
        ship_date,
    ]

    for col_idx in columns:
        cell = ws.cell(row=start_row, column=col_idx, value=load_title_values[col_idx - 1])
        cell.font = Font(bold=True, color="FF1F2937")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.fill = PatternFill(fill_type="solid", fgColor="FFE5E7EB")
        cell.border = Border(
            left=medium_side if col_idx == 1 else thin_side,
            right=medium_side if col_idx == has_medium_right_col else thin_side,
            top=medium_side,
            bottom=medium_side,
        )

    for col_idx in columns:
        header = ws.cell(row=start_row + 1, column=col_idx, value=meta_headers[col_idx - 1])
        header.font = header_font
        header.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        header.fill = PatternFill(fill_type="solid", fgColor="FFF8FAFC")
        header.border = Border(
            left=medium_side if col_idx == 1 else thin_side,
            right=medium_side if col_idx == has_medium_right_col else thin_side,
            top=thin_side,
            bottom=thin_side,
        )
        value_cell = ws.cell(row=start_row + 2, column=col_idx, value=meta_values[col_idx - 1])
        value_cell.font = body_font
        value_cell.alignment = Alignment(horizontal="center", vertical="center")
        value_cell.border = Border(
            left=medium_side if col_idx == 1 else thin_side,
            right=medium_side if col_idx == has_medium_right_col else thin_side,
            top=thin_side,
            bottom=thin_side,
        )
        if col_idx == 7:
            value_cell.number_format = "$#,##0.00"
        if col_idx == 8:
            header.fill = PatternFill(fill_type="solid", fgColor="FFFFEB99")
            value_cell.fill = PatternFill(fill_type="solid", fgColor="FFFFF3BF")

    ws.merge_cells(start_row=start_row + 3, start_column=1, end_row=start_row + 3, end_column=8)
    instructions_cell = ws.cell(
        row=start_row + 3,
        column=1,
        value="Check Special Instructions",
    )
    instructions_cell.font = Font(bold=True, color="FFB91C1C")
    instructions_cell.alignment = Alignment(horizontal="center", vertical="center")
    instructions_cell.fill = PatternFill(fill_type="solid", fgColor="FFFEF2F2")
    instructions_cell.border = Border(left=medium_side, right=medium_side, top=thin_side, bottom=thin_side)

    chunk_size = 8
    route_rows_per_chunk = 10
    route_start_row = start_row + 4
    stop_chunks = [route_stops[i : i + chunk_size] for i in range(0, len(route_stops), chunk_size)] or [[]]

    for chunk_idx, stop_chunk in enumerate(stop_chunks):
        chunk_row = route_start_row + (chunk_idx * route_rows_per_chunk)
        row_labels = [
            "Route",
            "Stop #",
            "Customer",
            "Address",
            "City",
            "State",
            "ZIP",
            "Phone",
            "Group",
            "SKU / SO",
        ]
        for offset, label in enumerate(row_labels):
            row_number = chunk_row + offset
            ws.row_dimensions[row_number].height = 26
            if offset in {8, 9}:
                ws.row_dimensions[row_number].height = 42
            for col_idx in columns:
                cell = ws.cell(row=row_number, column=col_idx)
                stop_data = stop_chunk[col_idx - 1] if col_idx <= len(stop_chunk) else None
                stop_sequence = (stop_data or {}).get("stop_order") or 0
                color_hex = _color_for_stop_sequence(stop_sequence, stop_palette)
                light_hex = _lighten_hex_color(color_hex, ratio=0.7 if offset == 1 else 0.84)
                cell.fill = PatternFill(fill_type="solid", fgColor=_hex_to_excel_argb(light_hex, fallback="#EEF2FF"))
                cell.border = Border(
                    left=medium_side if col_idx == 1 else thin_side,
                    right=medium_side if col_idx == has_medium_right_col else thin_side,
                    top=thin_side,
                    bottom=medium_side if offset == len(row_labels) - 1 else thin_side,
                )
                cell.font = body_font
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

                if not stop_data:
                    continue
                if offset == 0:
                    cell.value = f"Stop {int(stop_sequence)}"
                    cell.font = Font(bold=True, color="FF1E293B")
                elif offset == 1:
                    cell.value = int(stop_sequence)
                    cell.font = header_font
                elif offset == 2:
                    customers = stop_data.get("customers") or []
                    cell.value = ", ".join(customers[:2]) + (f" (+{len(customers)-2})" if len(customers) > 2 else "")
                elif offset == 3:
                    cell.value = stop_data.get("address") or "(address unavailable)"
                elif offset == 4:
                    cell.value = stop_data.get("city") or ""
                elif offset == 5:
                    cell.value = stop_data.get("state") or ""
                elif offset == 6:
                    cell.value = stop_data.get("zip") or ""
                elif offset == 7:
                    cell.value = "(blank)"
                elif offset == 8:
                    cell.value = "COT Stickers"
                    cell.font = header_font
                elif offset in {8, 9}:
                    sku_entries = stop_data.get("sku_entries") or []
                    if offset == 9:
                        cell.value = "\n".join(sku_entries[:8])

    after_routes_row = route_start_row + (len(stop_chunks) * route_rows_per_chunk) + 1
    row_after_schematic = _write_load_sheet_schematic_grid(
        ws,
        start_row=after_routes_row,
        load=load,
        stop_palette=stop_palette,
        medium_side=medium_side,
        thin_side=thin_side,
    )
    return max(row_after_schematic + 1, after_routes_row + 3)


def _build_single_load_sheet_workbook(load):
    workbook = Workbook()
    sheet_name = (load.get("load_number") or "Load Sheet")[:31]
    ws = workbook.active
    ws.title = sheet_name

    for col_letter in ("A", "B", "C", "D", "E", "F", "G", "H"):
        ws.column_dimensions[col_letter].width = 26
    ws.sheet_view.showGridLines = True

    _write_load_sheet_block(
        ws,
        start_row=1,
        load=load,
        stop_palette=_get_stop_color_palette(),
        medium_side=Side(style="medium", color="FF475569"),
        thin_side=Side(style="thin", color="FFCBD5E1"),
        header_font=Font(bold=True, color="FF1F2937"),
        body_font=Font(color="FF111827"),
    )
    return workbook


def _build_load_report_workbook(planning_session, report_rows):
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Load Summary"

    header_fill = PatternFill(fill_type="solid", fgColor="FFE5E7EB")
    header_font = Font(bold=True, color="FF1F2937")
    body_font = Font(color="FF111827")
    muted_font = Font(color="FF94A3B8")
    total_fill = PatternFill(fill_type="solid", fgColor="FFD1D5DB")
    yes_fill = PatternFill(fill_type="solid", fgColor="FFFDE68A")
    yes_font = Font(bold=True, color="FF92400E")
    no_font = Font(color="FF64748B")
    link_font = Font(color="FF2563EB", underline="single")
    thin_side = Side(style="thin", color="FFCBD5E1")
    separator_side = Side(style="medium", color="FF9CA3AF")
    all_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)
    alternating_fill_a = PatternFill(fill_type="solid", fgColor="FFF3F8FF")
    alternating_fill_b = PatternFill(fill_type="solid", fgColor="FFFFFFFF")

    summary_headers = [
        "Load ID",
        "Stop Order",
        "SO Number",
        "Customer Name",
        "Destination City",
        "State",
        "Ship Date",
        "Due Date",
        "Total Units",
        "Early Delivery Flag",
    ]
    summary.append(summary_headers)
    for cell in summary[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.border = all_border
        cell.alignment = Alignment(horizontal="center", vertical="center")

    all_preview_rows = _build_load_report_preview_rows(report_rows, limit=200000)

    current_row = 2
    group_index = -1
    previous_load_id = None
    for row_idx, row in enumerate(all_preview_rows):
        load_id = str(row.get("load_id") or "").strip()
        if load_id != previous_load_id:
            group_index += 1
        next_load_id = (
            str(all_preview_rows[row_idx + 1].get("load_id") or "").strip()
            if row_idx + 1 < len(all_preview_rows)
            else None
        )
        is_group_end = load_id != next_load_id
        row_fill = alternating_fill_a if (group_index % 2 == 0) else alternating_fill_b

        summary.append(
            [
                load_id,
                row.get("stop_order") or "--",
                row.get("so_number") or "",
                row.get("customer_name") or "",
                row.get("destination_city") or "",
                row.get("state") or "",
                row.get("ship_date") or "",
                row.get("due_date") or "",
                row.get("total_units") or 0,
                row.get("early_flag") or "NO",
            ]
        )
        for col_idx in range(1, 11):
            cell = summary.cell(row=current_row, column=col_idx)
            cell.fill = row_fill
            cell.border = Border(
                left=thin_side,
                right=thin_side,
                top=thin_side,
                bottom=separator_side if is_group_end else thin_side,
            )
            cell.font = body_font
            cell.alignment = Alignment(
                horizontal="right" if col_idx in {9} else "left",
                vertical="center",
            )
        if row.get("is_group_start"):
            summary.cell(row=current_row, column=1).font = Font(bold=True, color="FF111827")
        else:
            summary.cell(row=current_row, column=1).font = muted_font
        summary.cell(row=current_row, column=3).font = link_font
        if row.get("early_flag") == "YES":
            summary.cell(row=current_row, column=10).fill = yes_fill
            summary.cell(row=current_row, column=10).font = yes_font
            summary.cell(row=current_row, column=10).alignment = Alignment(horizontal="center")
        else:
            summary.cell(row=current_row, column=10).font = no_font
            summary.cell(row=current_row, column=10).alignment = Alignment(horizontal="center")
        if isinstance(summary.cell(row=current_row, column=9).value, (int, float)):
            summary.cell(row=current_row, column=9).number_format = "#,##0"
        current_row += 1
        previous_load_id = load_id

    totals_row = current_row
    total_units = sum((row.get("total_units") or 0) for row in all_preview_rows)
    summary.merge_cells(start_row=totals_row, start_column=1, end_row=totals_row, end_column=8)
    summary.cell(row=totals_row, column=1, value="SESSION TOTALS")
    summary.cell(row=totals_row, column=9, value=round(total_units, 0))
    summary.cell(row=totals_row, column=10, value="")
    for col_idx in range(1, 11):
        cell = summary.cell(row=totals_row, column=col_idx)
        cell.fill = total_fill
        cell.font = Font(bold=True, color="FF111827")
        cell.border = all_border
        if col_idx in {9}:
            cell.alignment = Alignment(horizontal="right", vertical="center")
        else:
            cell.alignment = Alignment(horizontal="center", vertical="center")
        if col_idx in {9} and isinstance(cell.value, (int, float)):
            cell.number_format = "#,##0"

    summary.column_dimensions["A"].width = 13
    summary.column_dimensions["B"].width = 12
    summary.column_dimensions["C"].width = 14
    summary.column_dimensions["D"].width = 24
    summary.column_dimensions["E"].width = 18
    summary.column_dimensions["F"].width = 8
    summary.column_dimensions["G"].width = 13
    summary.column_dimensions["H"].width = 13
    summary.column_dimensions["I"].width = 12
    summary.column_dimensions["J"].width = 16
    summary.freeze_panes = "A2"

    stop_details = workbook.create_sheet(title="Stop Details")
    stop_headers = [
        "Load ID",
        "Stop Order",
        "SO Number",
        "Customer Name",
        "Destination City",
        "State",
        "ZIP",
        "Ship Date",
        "Due Date",
        "Early Delivery Flag",
        "Total Units",
        "Total Length (ft)",
    ]
    stop_details.append(stop_headers)
    for cell in stop_details[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.border = all_border
        cell.alignment = Alignment(horizontal="center", vertical="center")

    row_idx = 2
    for load in report_rows or []:
        for order in load.get("orders") or []:
            stop_details.append(
                [
                    load.get("load_number") or load.get("display_load_id") or "",
                    order.get("stop_order_display") or "--",
                    order.get("so_num") or "",
                    order.get("cust_name") or "",
                    order.get("city") or "",
                    order.get("state") or "",
                    order.get("zip") or "",
                    load.get("ship_date") or "",
                    order.get("due_date") or "",
                    order.get("early_flag") or "NO",
                    order.get("total_qty") or 0,
                    order.get("total_length_ft") or 0,
                ]
            )
            for col_idx in range(1, 13):
                cell = stop_details.cell(row=row_idx, column=col_idx)
                cell.border = all_border
                cell.font = body_font
                cell.alignment = Alignment(
                    horizontal="right" if col_idx in {11, 12} else "left",
                    vertical="center",
                )
            stop_details.cell(row=row_idx, column=3).font = link_font
            stop_details.cell(row=row_idx, column=10).alignment = Alignment(horizontal="center")
            if (order.get("early_flag") or "NO") == "YES":
                stop_details.cell(row=row_idx, column=10).fill = yes_fill
                stop_details.cell(row=row_idx, column=10).font = yes_font
            else:
                stop_details.cell(row=row_idx, column=10).font = no_font
            row_idx += 1

    stop_details.column_dimensions["A"].width = 13
    stop_details.column_dimensions["B"].width = 12
    stop_details.column_dimensions["C"].width = 14
    stop_details.column_dimensions["D"].width = 26
    stop_details.column_dimensions["E"].width = 18
    stop_details.column_dimensions["F"].width = 8
    stop_details.column_dimensions["G"].width = 10
    stop_details.column_dimensions["H"].width = 13
    stop_details.column_dimensions["I"].width = 13
    stop_details.column_dimensions["J"].width = 16
    stop_details.column_dimensions["K"].width = 11
    stop_details.column_dimensions["L"].width = 14
    stop_details.freeze_panes = "A2"

    sku_breakdown = workbook.create_sheet(title="SKU Breakdown")
    sku_headers = [
        "Load ID",
        "SO Number",
        "SKU",
        "Item",
        "Item Description",
        "QTY",
        "Unit Length (ft)",
        "Total Length (ft)",
    ]
    sku_breakdown.append(sku_headers)
    for cell in sku_breakdown[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.border = all_border
        cell.alignment = Alignment(horizontal="center", vertical="center")

    sku_row = 2
    for load in report_rows or []:
        for line in load.get("lines") or []:
            sku_breakdown.append(
                [
                    load.get("load_number") or load.get("display_load_id") or "",
                    line.get("so_num") or "",
                    line.get("sku") or "",
                    line.get("item") or "",
                    line.get("item_desc") or "",
                    line.get("qty") or 0,
                    line.get("unit_length_ft") or 0,
                    line.get("total_length_ft") or line.get("line_total_feet") or 0,
                ]
            )
            for col_idx in range(1, 9):
                cell = sku_breakdown.cell(row=sku_row, column=col_idx)
                cell.border = all_border
                cell.font = body_font
                cell.alignment = Alignment(
                    horizontal="right" if col_idx in {6, 7, 8} else "left",
                    vertical="center",
                )
            sku_breakdown.cell(row=sku_row, column=2).font = link_font
            sku_row += 1

    sku_breakdown.column_dimensions["A"].width = 13
    sku_breakdown.column_dimensions["B"].width = 14
    sku_breakdown.column_dimensions["C"].width = 12
    sku_breakdown.column_dimensions["D"].width = 12
    sku_breakdown.column_dimensions["E"].width = 30
    sku_breakdown.column_dimensions["F"].width = 9
    sku_breakdown.column_dimensions["G"].width = 15
    sku_breakdown.column_dimensions["H"].width = 15
    sku_breakdown.freeze_panes = "A2"

    load_sheets = workbook.create_sheet(title="Load Sheets")
    for col_letter in ("A", "B", "C", "D", "E", "F", "G", "H"):
        load_sheets.column_dimensions[col_letter].width = 26
    load_sheets.sheet_view.showGridLines = True

    row_pointer = 1
    stop_palette = _get_stop_color_palette()
    medium_side = Side(style="medium", color="FF475569")
    thin_side = Side(style="thin", color="FFCBD5E1")
    for load in report_rows or []:
        row_pointer = _write_load_sheet_block(
            load_sheets,
            row_pointer,
            load,
            stop_palette=stop_palette,
            medium_side=medium_side,
            thin_side=thin_side,
            header_font=header_font,
            body_font=body_font,
        )
        row_pointer += 1

    return workbook


def _get_session_report_data(session_id):
    planning_session = _get_scoped_planning_session_or_404(session_id)

    loads = load_builder.list_loads(None, session_id=session_id)
    session_status = _sync_planning_session_status(session_id, loads=loads)
    session_status = _normalize_session_status(session_status or planning_session.get("status"))
    planning_session["status"] = session_status
    approved_loads = [
        load for load in loads
        if (load.get("status") or "").strip().upper() == STATUS_APPROVED
    ]
    report_rows = _build_load_report_rows(approved_loads)
    return planning_session, report_rows


@app.route("/load-report/<int:session_id>")
def load_report(session_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    planning_session, report_rows = _get_session_report_data(session_id)
    total_miles = round(sum((row.get("estimated_miles") or 0) for row in report_rows), 1)
    total_cost = round(sum((row.get("estimated_cost") or 0) for row in report_rows), 2)
    early_count = sum(1 for row in report_rows if row.get("has_early_delivery"))
    avg_utilization = round(
        (sum((row.get("utilization_pct") or 0) for row in report_rows) / len(report_rows)),
        1,
    ) if report_rows else 0.0
    completion_candidates = [
        _parse_datetime(row.get("created_at"))
        for row in report_rows
        if row.get("created_at")
    ]
    completion_candidates = [value for value in completion_candidates if value]
    completion_dt = max(completion_candidates) if completion_candidates else _parse_datetime(planning_session.get("created_at"))
    completion_label = _format_datetime_label(completion_dt)
    export_preview_rows = _build_load_report_preview_rows(report_rows, limit=8)

    return render_template(
        "load_report.html",
        planning_session=planning_session,
        report_rows=report_rows,
        export_preview_rows=export_preview_rows,
        load_count=len(report_rows),
        total_orders=sum(len(row.get("orders") or []) for row in report_rows),
        total_miles=total_miles,
        total_cost=total_cost,
        avg_utilization=avg_utilization,
        completion_label=completion_label,
        early_load_count=early_count,
    )


@app.route("/load-report/<int:session_id>/export.xlsx")
def load_report_export(session_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    planning_session, report_rows = _get_session_report_data(session_id)
    workbook = _build_load_report_workbook(planning_session, report_rows)
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    safe_code = (planning_session.get("session_code") or f"session_{session_id}").replace(" ", "_")
    filename = f"load_report_{safe_code}_{date.today().isoformat()}.xlsx"

    return Response(
        output.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/load-report/<int:session_id>/load/<int:load_id>/sheet.xlsx")
def load_report_load_sheet_export(session_id, load_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    planning_session, report_rows = _get_session_report_data(session_id)
    matched = next((row for row in (report_rows or []) if int(row.get("id") or 0) == int(load_id)), None)
    if not matched:
        abort(404)

    workbook = _build_single_load_sheet_workbook(matched)
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)

    raw_load_number = str(matched.get("load_number") or f"load_{load_id}").strip()
    safe_load_number = re.sub(r"[^A-Za-z0-9_-]+", "_", raw_load_number).strip("_") or f"load_{load_id}"
    safe_code = (planning_session.get("session_code") or f"session_{session_id}").replace(" ", "_")
    filename = f"load_sheet_{safe_code}_{safe_load_number}_{date.today().isoformat()}.xlsx"

    return Response(
        output.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/planning-sessions/<int:session_id>")
def planning_session_detail(session_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    planning_session = _get_scoped_planning_session_or_404(session_id)
    planning_session["status"] = _normalize_session_status(planning_session.get("status"))

    session_config = {}
    if planning_session.get("config_json"):
        try:
            session_config = json.loads(planning_session.get("config_json") or "{}")
        except json.JSONDecodeError:
            session_config = {}

    loads = load_builder.list_loads(None, session_id=session_id)
    session_status = _sync_planning_session_status(session_id, loads=loads)
    planning_session["status"] = _normalize_session_status(session_status or planning_session.get("status"))
    rollup = _build_planning_session_rollup(loads)
    avg_util = round(
        sum((load.get("utilization_pct") or 0) for load in loads) / len(loads), 1
    ) if loads else 0.0

    return render_template(
        "planning_session_detail.html",
        planning_session=planning_session,
        session_config=session_config,
        loads=rollup["loads"],
        session_orders=rollup["orders"],
        load_count=len(rollup["loads"]),
        order_count=len(rollup["orders"]),
        avg_utilization=avg_util,
        can_manage_sessions=_get_session_role() == ROLE_ADMIN,
    )


@app.route("/planning-sessions/<int:session_id>/summary")
def planning_session_summary(session_id):
    session_redirect = _require_session()
    if session_redirect:
        return jsonify({"error": "Session expired"}), 401
    planning_session = db.get_planning_session(session_id)
    if not planning_session or not _can_access_planning_session(planning_session):
        return jsonify({"error": "Session not found"}), 404

    loads = load_builder.list_loads(None, session_id=session_id)
    rollup = _build_planning_session_rollup(loads)
    avg_util = round(
        sum((load.get("utilization_pct") or 0) for load in loads) / len(loads), 1
    ) if loads else 0.0
    total_cost = round(sum((load.get("estimated_cost") or 0) for load in loads), 2)

    return jsonify(
        {
            "session_id": session_id,
            "session_code": planning_session.get("session_code"),
            "status": _normalize_session_status(planning_session.get("status")),
            "load_count": len(rollup["loads"]),
            "order_count": len(rollup["orders"]),
            "avg_utilization": avg_util,
            "total_cost": total_cost,
            "loads": rollup["loads"],
            "orders": rollup["orders"],
        }
    )


@app.route("/planning-sessions/<int:session_id>/archive", methods=["POST"])
def planning_session_archive(session_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    _get_scoped_planning_session_or_404(session_id)
    filters = _planning_session_filter_values(request.form)
    if not _archive_session_and_release_loads(session_id):
        abort(404)
    redirect_args = _planning_sessions_redirect_args(filters)
    redirect_args["archived_session_id"] = session_id
    return redirect(url_for("planning_sessions", **redirect_args))


@app.route("/planning-sessions/archive-all", methods=["POST"])
def planning_sessions_archive_all():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    filters = _planning_session_filter_values(request.form)
    role = _get_session_role()
    profile_name = (_get_session_profile_name() or "").strip()
    created_by_filter = filters.get("planner") or None
    if role != ROLE_ADMIN:
        created_by_filter = profile_name
    sessions = db.list_planning_sessions(
        {
            "plant_code": filters.get("plant") or None,
            "created_by": created_by_filter,
            "start_date": filters.get("start") or None,
            "end_date": filters.get("end") or None,
        }
    )
    archived_count = 0
    for entry in sessions:
        if not _can_access_planning_session(entry):
            continue
        status = _normalize_session_status(entry.get("status"))
        if status == "ARCHIVED":
            continue
        session_id = entry.get("id")
        if _archive_session_and_release_loads(session_id):
            archived_count += 1

    redirect_args = _planning_sessions_redirect_args(filters)
    redirect_args["archived_all_count"] = archived_count
    return redirect(url_for("planning_sessions", **redirect_args))


@app.route("/planning-sessions/<int:session_id>/delete", methods=["POST"])
def planning_session_delete(session_id):
    abort(404)


@app.route("/planning-sessions/<int:session_id>/revise")
def planning_session_revise(session_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    _get_scoped_planning_session_or_404(session_id)
    return redirect(url_for("orders", session_template_id=session_id))


@app.route("/planning-sessions/<int:session_id>/resume")
def planning_session_resume(session_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    _get_scoped_planning_session_or_404(session_id)
    _set_active_planning_session_id(session_id)
    return redirect(url_for("loads", session_id=session_id))


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
    session_id = request.form.get("session_id")
    try:
        session_id = int(session_id) if session_id else None
    except (TypeError, ValueError):
        session_id = None
    redirect_session_id = session_id or _get_active_planning_session_id()

    plant_code = (request.form.get("plant") or "").strip().upper()
    if not plant_code or plant_code not in allowed_plants:
        return redirect(
            url_for("loads", manual_error="Select a valid plant.", session_id=redirect_session_id)
        )

    so_nums = list(
        dict.fromkeys(
            [value.strip() for value in request.form.getlist("so_nums") if (value or "").strip()]
        )
    )
    if not so_nums:
        return redirect(
            url_for(
                "loads",
                plants=plant_code,
                manual_error="Select at least one order.",
                session_id=redirect_session_id,
            )
        )

    eligible = db.filter_eligible_manual_so_nums(plant_code, so_nums)
    if eligible != set(so_nums) or len(eligible) != len(so_nums):
        return redirect(
            url_for(
                "loads",
                plants=plant_code,
                manual_error="Some selected orders are no longer available in Draft Loads.",
                session_id=redirect_session_id,
            )
        )

    trailer_type_raw = (request.form.get("trailer_type") or "").strip().upper()
    trailer_type = trailer_type_raw if stack_calculator.is_valid_trailer_type(trailer_type_raw) else None

    # Clear any non-manual draft loads so selected orders can be reassigned.
    if session_id:
        db.clear_unapproved_loads(session_id=session_id)
    else:
        db.clear_unapproved_loads(plant_code)

    result = load_builder.create_manual_load(
        plant_code,
        so_nums,
        trailer_type=trailer_type,
        created_by=_get_session_profile_name() or _get_session_role(),
        session_id=session_id,
    )
    if result.get("errors"):
        _reoptimize_for_plant(plant_code, session_id=session_id)
        message = next(iter(result["errors"].values()))
        return redirect(url_for("loads", plants=plant_code, manual_error=message, session_id=session_id))

    _reoptimize_for_plant(plant_code, session_id=session_id)
    return redirect(url_for("loads", plants=plant_code, tab="draft", reopt="done", session_id=session_id))


def _capacity_for_trailer(trailer_type):
    trailer_key = stack_calculator.normalize_trailer_type(trailer_type, default="STEP_DECK")
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
    trailer_type = stack_calculator.normalize_trailer_type(load.get("trailer_type"), default="STEP_DECK")
    capacity_ft = _capacity_for_trailer(trailer_type)

    lines = db.list_load_lines(load_id)
    existing_so_nums = {line.get("so_num") for line in lines if line.get("so_num")}
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

    zip_coords = geo_utils.load_zip_coordinates()
    stop_coords = []
    for line in lines:
        zip_code = geo_utils.normalize_zip(line.get("zip"))
        coords = zip_coords.get(zip_code) if zip_code else None
        if coords and coords not in stop_coords:
            stop_coords.append(coords)

    sku_specs = {spec["sku"]: spec for spec in db.list_sku_specs()}
    existing_schematic = {
        "trailer_type": trailer_type,
        "lower_deck_length": 0.0,
        "upper_deck_length": 0.0,
        "lower_remaining_ft": 0.0,
        "upper_remaining_ft": 0.0,
        "decks": {"lower": [], "upper": []},
    }
    if lines:
        schematic, _, _ = _calculate_load_schematic(
            lines,
            sku_specs,
            trailer_type,
            stop_sequence_map=None,
        )
        lower_deck_length = float(schematic.get("lower_deck_length") or 0.0)
        upper_deck_length = float(schematic.get("upper_deck_length") or 0.0)
        lower_used_ft = float(schematic.get("lower_deck_used_length_ft") or 0.0)
        upper_used_ft = float(schematic.get("upper_deck_effective_length_ft") or 0.0)
        deck_rows = {"lower": [], "upper": []}
        for pos in schematic.get("positions") or []:
            deck = (pos.get("deck") or "lower").strip().lower()
            if deck not in {"lower", "upper"}:
                deck = "lower"
            length_ft = float(pos.get("length_ft") or 0.0)
            effective_length_ft = float(pos.get("effective_length_ft") or length_ft)
            display_length_ft = effective_length_ft if deck == "upper" else length_ft
            capacity_used = max(float(pos.get("capacity_used") or 0.0), 0.0)
            vertical_fill_ratio = min(capacity_used, 1.0)
            vertical_open_ratio = max(1.0 - vertical_fill_ratio, 0.0)
            deck_rows.setdefault(deck, []).append(
                {
                    "position_id": pos.get("position_id") or "",
                    "length_ft": round(length_ft, 1),
                    "effective_length_ft": round(effective_length_ft, 1),
                    "display_length_ft": round(display_length_ft, 1),
                    "vertical_fill_ratio": round(vertical_fill_ratio, 4),
                    "vertical_open_ratio": round(vertical_open_ratio, 4),
                    "capacity_used": round(capacity_used, 4),
                    "overflow_applied": bool(pos.get("overflow_applied")),
                }
            )
        existing_schematic = {
            "trailer_type": trailer_type,
            "lower_deck_length": round(lower_deck_length, 1),
            "upper_deck_length": round(upper_deck_length, 1),
            "lower_remaining_ft": round(max(lower_deck_length - lower_used_ft, 0.0), 1),
            "upper_remaining_ft": round(max(upper_deck_length - upper_used_ft, 0.0), 1),
            "decks": deck_rows,
        }

    search_query = (request.args.get("q") or "").strip()
    candidates = db.list_eligible_manual_orders(
        plant_code,
        search=search_query or None,
        limit=None,
    )
    candidate_so_nums = []
    seen_candidate_so_nums = set()
    for order in candidates:
        so_num = str(order.get("so_num") or "").strip()
        if not so_num or so_num in existing_so_nums or so_num in seen_candidate_so_nums:
            continue
        seen_candidate_so_nums.add(so_num)
        candidate_so_nums.append(so_num)

    sku_lines = db.list_order_lines_for_so_nums(
        plant_code,
        list(existing_so_nums) + candidate_so_nums,
    )
    sku_rollups = {}
    for line in sku_lines:
        so_num = str(line.get("so_num") or "").strip()
        if not so_num:
            continue
        sku_key = str(line.get("sku") or line.get("item") or "").strip() or "UNKNOWN"
        so_rollup = sku_rollups.setdefault(so_num, {})
        sku_entry = so_rollup.setdefault(
            sku_key,
            {
                "sku": sku_key,
                "ft": 0.0,
                "qty": 0,
            },
        )
        sku_entry["ft"] += float(line.get("total_length_ft") or 0)
        try:
            sku_entry["qty"] += int(float(line.get("qty") or 0))
        except (TypeError, ValueError):
            pass

    def _serialize_sku_rollup(so_num):
        rollup = sku_rollups.get(so_num, {})
        entries = []
        for entry in rollup.values():
            entries.append(
                {
                    "sku": entry.get("sku") or "UNKNOWN",
                    "ft": round(float(entry.get("ft") or 0), 1),
                    "qty": int(entry.get("qty") or 0),
                }
            )
        entries.sort(key=lambda item: (-(item.get("ft") or 0), item.get("sku") or ""))
        return entries

    existing_segments = []
    existing_order_sort = sorted(
        existing_so_nums,
        key=lambda so_num: (
            (order_map.get(so_num) or {}).get("due_date") or "9999-12-31",
            so_num,
        ),
    )
    for so_num in existing_order_sort:
        order = order_map.get(so_num) or {}
        length_ft = (
            float(order.get("total_length_ft") or 0)
            if order.get("total_length_ft") is not None
            else float(line_totals.get(so_num) or 0)
        )
        if length_ft <= 0:
            continue
        existing_segments.append(
            {
                "so_num": so_num,
                "total_length_ft": round(length_ft, 1),
                "sku_breakdown": _serialize_sku_rollup(so_num),
            }
        )

    suggestions = []
    for order in candidates:
        so_num = str(order.get("so_num") or "").strip()
        if not so_num or so_num in existing_so_nums:
            continue

        dist = None
        order_zip = geo_utils.normalize_zip(order.get("zip"))
        order_coords = zip_coords.get(order_zip) if order_zip else None
        if order_coords and stop_coords:
            dist = min(
                geo_utils.haversine_distance_coords(order_coords, stop)
                for stop in stop_coords
            )

        total_length_ft = float(order.get("total_length_ft") or 0)
        sku_breakdown = _serialize_sku_rollup(so_num)

        suggestions.append(
            {
                "so_num": so_num,
                "cust_name": order.get("cust_name") or "",
                "due_date": order.get("due_date") or "",
                "city": order.get("city") or "",
                "state": order.get("state") or "",
                "zip": order.get("zip") or "",
                "total_length_ft": total_length_ft,
                "stack_added_ft": round(total_length_ft, 1),
                "utilization_pct": order.get("utilization_pct") or 0,
                "distance_miles": round(dist, 1) if dist is not None else None,
                "sku_breakdown": sku_breakdown,
            }
        )

    suggestions.sort(
        key=lambda item: (
            item.get("due_date") or "9999-12-31",
            item["distance_miles"] is None,
            item["distance_miles"] if item["distance_miles"] is not None else 0,
            item.get("so_num") or "",
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
            "existing_schematic": existing_schematic,
            "existing_segments": existing_segments,
            "params": {},
            "suggestions": suggestions,
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
    db.delete_load_schematic_override(load_id)

    session_id = load.get("planning_session_id")
    reopt_job_id = _start_reopt_job(plant_code, session_id=session_id, speed_profile="fast")
    return jsonify(
        {
            "redirect_url": url_for(
                "loads",
                plants=plant_code,
                tab="draft",
                reopt="done",
                session_id=session_id,
            ),
            "status_url": url_for("loads_reopt_job_status", job_id=reopt_job_id),
            "reopt_job_id": reopt_job_id,
        }
    )


@app.route("/loads/reopt_jobs/<job_id>")
def loads_reopt_job_status(job_id):
    session_redirect = _require_session()
    if session_redirect:
        return jsonify({"error": "Session expired"}), 401

    job = _get_reopt_job(job_id)
    if not job:
        return jsonify({"error": "Re-optimization job not found."}), 404

    if job.get("plant_code") not in _get_allowed_plants():
        return jsonify({"error": "Not authorized for this re-optimization job."}), 403

    return jsonify(
        {
            "job_id": job.get("id"),
            "status": job.get("status") or "unknown",
            "plant_code": job.get("plant_code") or "",
            "session_id": job.get("session_id"),
            "created_at": job.get("created_at") or "",
            "started_at": job.get("started_at") or "",
            "finished_at": job.get("finished_at") or "",
            "error": job.get("error") or "",
            "success_message": job.get("success_message") or "",
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

    route_error = (request.args.get("route_error") or "").strip().lower()

    with db.get_connection() as connection:
        load = connection.execute(
            "SELECT * FROM loads WHERE id = ?",
            (load_id,),
        ).fetchone()
    if not load:
        return redirect(url_for("loads", session_id=_get_active_planning_session_id()))

    allowed_plants = _get_allowed_plants()
    if load["origin_plant"] not in allowed_plants:
        return redirect(
            url_for("loads", session_id=load.get("planning_session_id") or _get_active_planning_session_id())
        )

    load_data = dict(load)
    trailer_type = stack_calculator.normalize_trailer_type(load_data.get("trailer_type"), default="STEP_DECK")
    load_data["trailer_type"] = trailer_type
    stop_color_palette = _get_stop_color_palette()
    lines = db.list_load_lines(load_id)
    plant_names = {row["plant_code"]: row["name"] for row in db.list_plants()}
    sku_specs = {spec["sku"]: spec for spec in db.list_sku_specs()}
    stops = []
    stop_map = {}
    zip_coords = geo_utils.load_zip_coordinates()
    for line in lines:
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
        coords = None
        if stop.get("lat") is not None and stop.get("lng") is not None:
            coords = (stop.get("lat"), stop.get("lng"))
        stops.append(
            {
                "zip": stop["zip"],
                "state": stop["state"],
                "customers": sorted(stop["customers"]),
                "lat": stop.get("lat"),
                "lng": stop.get("lng"),
                "coords": coords,
            }
        )

    origin_code = load_data.get("origin_plant")
    origin_coords = geo_utils.plant_coords_for_code(origin_code)
    requires_return_to_origin = _requires_return_to_origin(lines)
    reverse_route = _is_load_route_reversed(load_data)
    ordered_stops = (
        tsp_solver.solve_route(
            origin_coords,
            stops,
            return_to_origin=requires_return_to_origin,
        )
        if origin_coords
        else list(stops)
    )
    ordered_stops = _apply_load_route_direction(ordered_stops, reverse_route=reverse_route)
    stop_sequence_map = _stop_sequence_map_from_ordered_stops(ordered_stops)

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
        route_nodes.append(
            {
                "type": "customer",
                "label": f"{stop.get('state') or ''} {stop.get('zip') or ''}".strip(),
                "subtitle": ", ".join(stop.get("customers") or []),
                "icon": "person_pin_circle",
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
                "icon": "home",
                "coords": origin_coords,
                "sequence": len(route_nodes),
            }
        )

    for node in route_nodes:
        node_type = (node.get("type") or "").strip().lower()
        is_return_origin = (
            node_type == "final"
            and requires_return_to_origin
            and origin_coords
            and node.get("coords") == origin_coords
        )
        if node_type == "origin" or is_return_origin:
            color = "#38bdf8"
        else:
            color = _color_for_stop_sequence(node.get("sequence"), stop_color_palette)
        node["color"] = color
        node["bg"] = f"{color}22"

    route_metrics = _load_route_display_metrics(
        load_data,
        route_nodes,
        use_cached_route=not reverse_route,
    )
    route_legs = route_metrics["route_legs"]
    route_geometry = route_metrics["route_geometry"]

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

    schematic, line_items, order_numbers = _calculate_load_schematic(
        lines,
        sku_specs,
        trailer_type,
        stop_sequence_map=stop_sequence_map,
    )
    utilization_pct = schematic.get("utilization_pct", load_data.get("utilization_pct", 0)) or 0
    exceeds_capacity = schematic.get("exceeds_capacity", False)
    over_capacity = exceeds_capacity and len(order_numbers) <= 1
    sku_color_palette = [
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
            sku_colors[sku] = sku_color_palette[len(sku_colors) % len(sku_color_palette)]

    load_data["schematic"] = schematic
    load_data["sku_colors"] = sku_colors
    load_data["stops"] = ordered_stops
    load_data["route_nodes"] = route_nodes
    load_data["route_legs"] = route_legs
    load_data["route_distance"] = route_metrics["route_distance"]
    load_data["route_geometry"] = route_geometry
    load_data["map_stops"] = map_stops
    load_data["route_reversed"] = bool(reverse_route)

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
                "stop_sequence": stop_sequence_map.get(
                    _line_stop_key(line.get("state"), line.get("zip"))
                ),
            }
        )

    manifest_rows.sort(
        key=lambda row: (
            int(row.get("stop_sequence") or 999),
            row.get("due_date") or "9999-12-31",
            row.get("so_num") or "",
            row.get("sku") or "",
        )
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
        route_error=route_error,
    )


@app.route("/api/loads/<int:load_id>/route-geometry")
def load_route_geometry(load_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    load = db.get_load(load_id)
    if not load:
        return jsonify({"error": "Load not found"}), 404

    allowed_plants = _get_allowed_plants()
    if load.get("origin_plant") not in allowed_plants:
        return jsonify({"error": "Not authorized for this plant"}), 403

    if _is_load_route_reversed(load):
        return jsonify(
            {
                "load_id": load_id,
                "route_provider": "manual",
                "route_profile": "",
                "route_fallback": True,
                "route_total_miles": load.get("route_total_miles") or load.get("estimated_miles") or 0.0,
                "route_legs": [],
                "route_geometry": [],
            }
        )

    existing_geometry = load.get("route_geometry") or []
    existing_provider = (load.get("route_provider") or "").strip().lower()
    has_cached_road_geometry = bool(existing_geometry) and not bool(load.get("route_fallback")) and existing_provider not in {
        "",
        "none",
        "haversine",
    }
    force_refresh = _coerce_bool_value(request.args.get("force"))
    if has_cached_road_geometry and not force_refresh:
        return jsonify(
            {
                "load_id": load_id,
                "route_provider": load.get("route_provider"),
                "route_profile": load.get("route_profile"),
                "route_fallback": bool(load.get("route_fallback")),
                "route_total_miles": load.get("route_total_miles"),
                "route_legs": load.get("route_legs") or [],
                "route_geometry": existing_geometry,
            }
        )

    lines = db.list_load_lines(load_id)
    zip_coords = geo_utils.load_zip_coordinates()
    stops = _build_route_stops_for_lines(lines, zip_coords)
    origin_code = load.get("origin_plant")
    origin_coords = geo_utils.plant_coords_for_code(origin_code)
    if not origin_coords or not stops:
        return jsonify(
            {
                "load_id": load_id,
                "route_provider": "none",
                "route_profile": "",
                "route_fallback": True,
                "route_total_miles": 0.0,
                "route_legs": [],
                "route_geometry": [],
            }
        )

    requires_return_to_origin = _requires_return_to_origin(lines)
    route_result = routing_service.get_routing_service().build_route(
        origin_coords,
        stops,
        return_to_origin=requires_return_to_origin,
        objective="distance",
        include_geometry=True,
    )

    existing_total_miles = load.get("route_total_miles")
    if existing_total_miles is None:
        existing_total_miles = load.get("estimated_miles")
    try:
        existing_total_miles = float(existing_total_miles or 0.0)
    except (TypeError, ValueError):
        existing_total_miles = 0.0
    route_payload = {
        "route_provider": route_result.get("provider"),
        "route_profile": route_result.get("profile"),
        # Preserve optimization/cost miles from load build (haversine mode).
        "route_total_miles": existing_total_miles,
        "route_legs": load.get("route_legs") or [],
        "route_geometry": route_result.get("geometry_latlng") or [],
        "route_fallback": bool(route_result.get("used_fallback")),
    }
    db.update_load_route_data(load_id, route_payload)

    return jsonify(
        {
            "load_id": load_id,
            "route_provider": route_payload["route_provider"],
            "route_profile": route_payload["route_profile"],
            "route_fallback": route_payload["route_fallback"],
            "route_total_miles": route_payload["route_total_miles"],
            "route_legs": route_payload["route_legs"],
            "route_geometry": route_payload["route_geometry"],
        }
    )


@app.route("/loads/<int:load_id>/trailer", methods=["POST"])
def update_load_trailer(load_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    body_data = request.get_json(silent=True) or {}
    trailer_type = (
        request.form.get("trailer_type")
        or body_data.get("trailer_type")
        or ""
    ).strip().upper()
    if not stack_calculator.is_valid_trailer_type(trailer_type):
        return jsonify({"error": "Invalid trailer type"}), 400

    load = db.get_load(load_id)
    if not load:
        return jsonify({"error": "Load not found"}), 404

    allowed_plants = _get_allowed_plants()
    if load["origin_plant"] not in allowed_plants:
        return jsonify({"error": "Not authorized for this plant"}), 403

    status = (load.get("status") or STATUS_PROPOSED).upper()
    if status == STATUS_APPROVED:
        return jsonify({"error": "Approved loads cannot be modified."}), 400

    confirm_violation = _coerce_bool_value(
        request.form.get("confirm_violation") or body_data.get("confirm_violation")
    )
    assumptions = _get_stack_capacity_assumptions()

    lines = db.list_load_lines(load_id)
    sku_specs = {spec["sku"]: spec for spec in db.list_sku_specs()}
    zip_coords = geo_utils.load_zip_coordinates()
    ordered_stops = _ordered_stops_for_lines(lines, load["origin_plant"], zip_coords)
    ordered_stops = _apply_load_route_direction(ordered_stops, load=load)
    stop_sequence_map = _stop_sequence_map_from_ordered_stops(ordered_stops)
    order_colors = _build_order_colors_for_lines(
        lines,
        stop_sequence_map=stop_sequence_map,
        stop_palette=_get_stop_color_palette(),
    )

    override = db.get_load_schematic_override(load_id)
    if override:
        units = _build_schematic_units(
            lines,
            sku_specs,
            trailer_type,
            stop_sequence_map=stop_sequence_map,
            order_colors=order_colors,
        )
        units_by_id = {unit["unit_id"]: unit for unit in units}
        base_schematic, _, _ = _calculate_load_schematic(
            lines,
            sku_specs,
            trailer_type,
            stop_sequence_map=stop_sequence_map,
            assumptions=assumptions,
        )
        normalized_layout = _layout_from_schematic(base_schematic, units)

        remapped_schematic, warnings = _build_schematic_from_layout(
            normalized_layout,
            units_by_id,
            trailer_type,
            assumptions=assumptions,
        )
        if warnings and not confirm_violation:
            return jsonify(
                {
                    "requires_confirmation": True,
                    "warnings": warnings,
                    "warning_count": len(warnings),
                    "remap_preview": {
                        "layout": normalized_layout,
                        "metrics": {
                            "utilization_pct": remapped_schematic.get("utilization_pct") or 0,
                            "utilization_grade": remapped_schematic.get("utilization_grade") or "F",
                            "total_linear_feet": remapped_schematic.get("total_linear_feet") or 0,
                            "exceeds_capacity": bool(remapped_schematic.get("exceeds_capacity")),
                        },
                    },
                }
            ), 409

        db.update_load_trailer_type(load_id, trailer_type)
        db.upsert_load_schematic_override(
            load_id,
            trailer_type,
            json.dumps(normalized_layout),
            warnings_json=json.dumps(warnings),
            is_invalid=bool(warnings),
            updated_by=_get_session_profile_name() or _get_session_role(),
        )
        load_data = _build_load_schematic_payload(load_id)
        if not load_data:
            return jsonify({"error": "Load not found"}), 404
        tab = (request.args.get("tab") or request.form.get("tab") or "").strip().lower()
        payload = _build_schematic_fragment_payload(
            load_data,
            status=(load_data.get("status") or STATUS_PROPOSED).upper(),
            tab=tab,
        )
        payload["ok"] = True
        return jsonify(payload)

    order_numbers = {
        (line.get("so_num") or "").strip()
        for line in lines
        if (line.get("so_num") or "").strip()
    }
    if len(order_numbers) > 1:
        schematic, _, _ = _calculate_load_schematic(
            lines,
            sku_specs,
            trailer_type,
            stop_sequence_map=stop_sequence_map,
            assumptions=assumptions,
        )
        if schematic.get("exceeds_capacity") and not confirm_violation:
            return jsonify(
                {
                    "requires_confirmation": True,
                    "warnings": [
                        {
                            "code": "TRAILER_EXCEEDS_CAPACITY",
                            "message": (
                                "This trailer selection exceeds deck capacity/overhang limits for the current mix. "
                                "Continue anyway to keep the selected trailer."
                            ),
                            "level": "warning",
                        }
                    ],
                    "warning_count": 1,
                }
            ), 409

    best_schematic, _, _ = _calculate_load_schematic(
        lines,
        sku_specs,
        trailer_type,
        stop_sequence_map=stop_sequence_map,
        assumptions=assumptions,
    )
    best_units = _build_schematic_units(
        lines,
        sku_specs,
        trailer_type,
        stop_sequence_map=stop_sequence_map,
        order_colors=order_colors,
    )
    best_layout = _layout_from_schematic(best_schematic, best_units)
    units_by_id = {unit["unit_id"]: unit for unit in best_units}
    remapped_schematic, warnings = _build_schematic_from_layout(
        best_layout,
        units_by_id,
        trailer_type,
        assumptions=assumptions,
    )
    if warnings and not confirm_violation:
        return jsonify(
            {
                "requires_confirmation": True,
                "warnings": warnings,
                "warning_count": len(warnings),
                "remap_preview": {
                    "layout": best_layout,
                    "metrics": {
                        "utilization_pct": remapped_schematic.get("utilization_pct") or 0,
                        "utilization_grade": remapped_schematic.get("utilization_grade") or "F",
                        "total_linear_feet": remapped_schematic.get("total_linear_feet") or 0,
                        "exceeds_capacity": bool(remapped_schematic.get("exceeds_capacity")),
                    },
                },
            }
        ), 409

    db.update_load_trailer_type(load_id, trailer_type)
    db.upsert_load_schematic_override(
        load_id,
        trailer_type,
        json.dumps(best_layout),
        warnings_json=json.dumps(warnings),
        is_invalid=bool(warnings),
        updated_by=_get_session_profile_name() or _get_session_role(),
    )
    return ("", 204)


@app.route("/loads/<int:load_id>/reverse-order", methods=["POST"])
def reverse_load_order(load_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    next_url = _safe_next_url(request.form.get("next") or request.args.get("next")) or ""
    load = db.get_load(load_id)
    if not load:
        wants_json = request.is_json or request.accept_mimetypes.best == "application/json"
        if wants_json:
            return jsonify({"error": "Load not found"}), 404
        fallback = url_for("loads", session_id=_get_active_planning_session_id())
        return redirect(next_url or fallback)

    allowed_plants = _get_allowed_plants()
    if load.get("origin_plant") not in allowed_plants:
        wants_json = request.is_json or request.accept_mimetypes.best == "application/json"
        if wants_json:
            return jsonify({"error": "Not authorized for this plant"}), 403
        fallback = url_for(
            "loads",
            session_id=load.get("planning_session_id") or _get_active_planning_session_id(),
        )
        return redirect(next_url or fallback)

    status = (load.get("status") or STATUS_PROPOSED).upper()
    if status == STATUS_APPROVED:
        wants_json = request.is_json or request.accept_mimetypes.best == "application/json"
        if wants_json:
            return jsonify({"error": "Approved loads cannot be modified."}), 400
        fallback = url_for(
            "loads",
            session_id=load.get("planning_session_id") or _get_active_planning_session_id(),
        )
        return redirect(next_url or fallback)

    next_value = not _is_load_route_reversed(load)
    db.update_load_route_reversed(load_id, next_value)

    wants_json = request.is_json or request.accept_mimetypes.best == "application/json"
    if wants_json:
        return jsonify({"ok": True, "route_reversed": bool(next_value)})
    fallback = url_for(
        "loads",
        session_id=load.get("planning_session_id") or _get_active_planning_session_id(),
    )
    return redirect(next_url or fallback)


def _build_load_schematic_payload(load_id):
    load = db.get_load(load_id)
    if not load:
        return None

    trailer_type = stack_calculator.normalize_trailer_type(load.get("trailer_type"), default="STEP_DECK")
    assumptions = _get_stack_capacity_assumptions()
    load["trailer_type"] = trailer_type
    lines = db.list_load_lines(load_id)
    sku_specs = {spec["sku"]: spec for spec in db.list_sku_specs()}

    zip_coords = geo_utils.load_zip_coordinates()
    ordered_stops = _ordered_stops_for_lines(lines, load.get("origin_plant"), zip_coords)
    ordered_stops = _apply_load_route_direction(ordered_stops, load=load)
    stop_sequence_map = _stop_sequence_map_from_ordered_stops(ordered_stops)
    order_colors = _build_order_colors_for_lines(
        lines,
        stop_sequence_map=stop_sequence_map,
        stop_palette=_get_stop_color_palette(),
    )
    schematic, _, order_numbers = _calculate_load_schematic(
        lines,
        sku_specs,
        trailer_type,
        stop_sequence_map=stop_sequence_map,
        assumptions=assumptions,
    )
    schematic_warnings = list(schematic.get("warnings") or [])
    has_custom_schematic = False
    override = db.get_load_schematic_override(load_id)
    if override and (override.get("trailer_type") or "").strip().upper() == trailer_type:
        units = _build_schematic_units(
            lines,
            sku_specs,
            trailer_type,
            stop_sequence_map=stop_sequence_map,
            order_colors=order_colors,
        )
        units_by_id = {unit["unit_id"]: unit for unit in units}
        try:
            override_layout = json.loads(override.get("layout_json") or "{}")
            normalized_layout = _normalize_edit_layout(override_layout, units_by_id, trailer_type)
            schematic, schematic_warnings = _build_schematic_from_layout(
                normalized_layout,
                units_by_id,
                trailer_type,
                assumptions=assumptions,
            )
            has_custom_schematic = True
        except (json.JSONDecodeError, ValueError):
            has_custom_schematic = False
            schematic_warnings = []

    if (
        not schematic_warnings
        and override
        and (override.get("trailer_type") or "").strip().upper() == trailer_type
        and override.get("warnings_json")
    ):
        try:
            parsed_warnings = json.loads(override.get("warnings_json") or "[]")
        except json.JSONDecodeError:
            parsed_warnings = []
        if isinstance(parsed_warnings, list):
            schematic_warnings = parsed_warnings

    trailer_assignment_rules = _get_trailer_assignment_rules()
    strategic_setting = db.get_planning_setting("strategic_customers") or {}
    strategic_customers = _parse_strategic_customers(strategic_setting.get("value_text") or "")
    auto_label, auto_reason = _auto_trailer_rule_annotation(
        load=load,
        lines=lines,
        trailer_type=trailer_type,
        schematic=schematic,
        sku_specs=sku_specs,
        stop_sequence_map=stop_sequence_map,
        assumptions=assumptions,
        trailer_assignment_rules=trailer_assignment_rules,
        strategic_customers=strategic_customers,
    )
    load["auto_trailer_label"] = auto_label
    load["auto_trailer_reason"] = auto_reason
    utilization_pct = schematic.get("utilization_pct", load.get("utilization_pct", 0)) or 0
    exceeds_capacity = schematic.get("exceeds_capacity", False)
    over_capacity = exceeds_capacity and len(order_numbers) <= 1

    load["schematic"] = schematic
    load["order_colors"] = order_colors
    load["over_capacity"] = over_capacity
    load["utilization_pct"] = utilization_pct
    # Schematic partial expects this key during async refresh/save responses.
    load["display_utilization_pct"] = utilization_pct
    load["has_custom_schematic"] = has_custom_schematic
    load["schematic_warnings"] = schematic_warnings
    load["schematic_warning_count"] = len(schematic_warnings)
    load["schematic_is_invalid"] = bool((override or {}).get("is_invalid")) if override else False
    load["stack_assumptions"] = assumptions
    return load


@app.route("/loads/<int:load_id>/schematic")
def load_schematic_fragment(load_id):
    session_redirect = _require_session()
    if session_redirect:
        return _json_session_expired_response()

    try:
        load_data = _build_load_schematic_payload(load_id)
    except Exception as exc:
        logger.exception("Failed to build schematic fragment for load_id=%s", load_id)
        return jsonify({"error": f"Unable to load schematic: {exc}"}), 500
    if not load_data:
        return jsonify({"error": "Load not found"}), 404

    allowed_plants = _get_allowed_plants()
    if load_data.get("origin_plant") not in allowed_plants:
        return jsonify({"error": "Not authorized for this plant"}), 403

    status = (load_data.get("status") or STATUS_PROPOSED).upper()
    tab = (request.args.get("tab") or "").strip().lower()
    return jsonify(_build_schematic_fragment_payload(load_data, status=status, tab=tab))


@app.route("/loads/<int:load_id>/schematic/edit")
def load_schematic_edit(load_id):
    session_redirect = _require_session()
    if session_redirect:
        return _json_session_expired_response()

    try:
        payload = _build_load_schematic_edit_payload(load_id)
    except Exception as exc:
        logger.exception("Failed to build schematic edit payload for load_id=%s", load_id)
        return jsonify({"error": f"Unable to load schematic editor: {exc}"}), 500
    if not payload:
        return jsonify({"error": "Load not found"}), 404

    allowed_plants = _get_allowed_plants()
    if (payload.get("load") or {}).get("origin_plant") not in allowed_plants:
        return jsonify({"error": "Not authorized for this plant"}), 403

    return jsonify(
        {
            "load_id": payload.get("load_id"),
            "status": payload.get("status"),
            "trailer_type": payload.get("trailer_type"),
            "can_edit": bool(payload.get("can_edit")),
            "units": payload.get("units") or [],
            "layout": payload.get("layout") or {"positions": []},
            "base_layout": payload.get("base_layout") or {"positions": []},
            "metrics": payload.get("metrics") or {},
            "warnings": payload.get("warnings") or [],
            "warning_count": int(payload.get("warning_count") or 0),
            "assumptions": payload.get("assumptions") or {},
        }
    )


@app.route("/loads/<int:load_id>/schematic/edit/save", methods=["POST"])
def save_schematic_edit(load_id):
    session_redirect = _require_session()
    if session_redirect:
        return jsonify({"error": "Session expired"}), 401

    try:
        payload = _build_load_schematic_edit_payload(load_id)
    except Exception as exc:
        logger.exception("Failed to prepare schematic save payload for load_id=%s", load_id)
        return jsonify({"error": f"Unable to save schematic editor state: {exc}"}), 500
    if not payload:
        return jsonify({"error": "Load not found"}), 404

    allowed_plants = _get_allowed_plants()
    if (payload.get("load") or {}).get("origin_plant") not in allowed_plants:
        return jsonify({"error": "Not authorized for this plant"}), 403
    if not payload.get("can_edit"):
        return jsonify({"error": "Approved loads are read-only."}), 403

    try:
        data = request.get_json(silent=True) or {}
        requested_trailer_raw = (data.get("trailer_type") or payload.get("trailer_type") or "").strip().upper()
        if not stack_calculator.is_valid_trailer_type(requested_trailer_raw):
            return jsonify({"error": "Invalid trailer type"}), 400
        requested_trailer = stack_calculator.normalize_trailer_type(
            requested_trailer_raw,
            default=payload.get("trailer_type") or "STEP_DECK",
        )
        if requested_trailer != payload.get("trailer_type"):
            return jsonify({"error": "Trailer type changed. Update trailer first, then edit schematic."}), 400

        units_by_id = {unit["unit_id"]: unit for unit in (payload.get("units") or [])}
        try:
            normalized_layout = _normalize_edit_layout(
                data.get("layout") or {},
                units_by_id,
                requested_trailer,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        schematic, warnings = _build_schematic_from_layout(
            normalized_layout,
            units_by_id,
            requested_trailer,
            assumptions=payload.get("assumptions") or _get_stack_capacity_assumptions(),
        )
        confirm_violation = _coerce_bool_value(data.get("confirm_violation"))
        if warnings and not confirm_violation:
            return jsonify(
                {
                    "ok": False,
                    "requires_confirmation": True,
                    "warnings": warnings,
                    "warning_count": len(warnings),
                }
            ), 409

        db.upsert_load_schematic_override(
            load_id,
            requested_trailer,
            json.dumps(normalized_layout),
            warnings_json=json.dumps(warnings),
            is_invalid=bool(warnings),
            updated_by=_get_session_profile_name() or _get_session_role(),
        )

        load_data = _build_load_schematic_payload(load_id)
        if not load_data:
            return jsonify({"error": "Load not found"}), 404

        tab = (request.args.get("tab") or data.get("tab") or "").strip().lower()
        response_payload = _build_schematic_fragment_payload(
            load_data,
            status=(load_data.get("status") or STATUS_PROPOSED).upper(),
            tab=tab,
        )
        response_payload.update(
            {
                "ok": True,
                "warning_count": len(warnings),
                "warnings": warnings,
                "metrics": {
                    "utilization_pct": schematic.get("utilization_pct") or 0,
                    "utilization_grade": schematic.get("utilization_grade") or "F",
                    "total_linear_feet": schematic.get("total_linear_feet") or 0,
                    "exceeds_capacity": bool(schematic.get("exceeds_capacity")),
                },
            }
        )
        return jsonify(response_payload)
    except Exception as exc:
        logger.exception("Unhandled schematic save error for load_id=%s", load_id)
        return jsonify({"error": f"Unhandled schematic save error: {exc.__class__.__name__}: {exc}"}), 500


@app.route("/loads/<int:load_id>/status", methods=["POST"], strict_slashes=False)
def update_load_status(load_id):
    is_async = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    session_redirect = _require_session()
    if session_redirect:
        if is_async:
            return jsonify({"error": "Session expired"}), 401
        return session_redirect

    action = (request.form.get("action") or "").strip().lower()
    if action not in {"approve_draft", "approve_lock", "propose", "unapprove"}:
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
    planning_session_id = load.get("planning_session_id")
    planning_session = db.get_planning_session(planning_session_id) if planning_session_id else None

    redirect_target = request.referrer or url_for(
        "loads", session_id=_get_active_planning_session_id()
    )

    if action == "propose":
        if current_status != STATUS_PROPOSED:
            db.update_load_status(load_id, STATUS_PROPOSED, load_number)
            if planning_session_id:
                _sync_planning_session_status(planning_session_id)
        if is_async:
            snapshot_loads = (
                load_builder.list_loads(
                    None,
                    session_id=planning_session_id,
                    include_stack_metrics=False,
                )
                if planning_session_id
                else None
            )
            snapshot = _compute_load_progress_snapshot(
                plant_scope=plant_scope,
                all_loads=snapshot_loads,
                allowed_plants=plant_scope if planning_session_id else None,
            )
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

    if action == "unapprove":
        if current_status == STATUS_APPROVED:
            if not load_number:
                seq = db.get_next_load_sequence(plant_code, year_suffix)
                load_number = _format_load_number(plant_code, year_suffix, seq, draft=True)
            else:
                normalized, suffix = _normalize_load_number(load_number)
                if suffix != "D":
                    load_number = f"{normalized}-D"
            db.update_load_status(load_id, STATUS_DRAFT, load_number)
            if planning_session_id:
                _sync_planning_session_status(planning_session_id)
        if is_async:
            snapshot_loads = (
                load_builder.list_loads(
                    None,
                    session_id=planning_session_id,
                    include_stack_metrics=False,
                )
                if planning_session_id
                else None
            )
            snapshot = _compute_load_progress_snapshot(
                plant_scope=plant_scope,
                all_loads=snapshot_loads,
                allowed_plants=plant_scope if planning_session_id else None,
            )
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

    if action == "approve_draft":
        if current_status == STATUS_APPROVED:
            return redirect(
                url_for(
                    "loads",
                    session_id=planning_session_id or _get_active_planning_session_id(),
                )
            )
        if not load_number:
            seq = db.get_next_load_sequence(plant_code, year_suffix)
            load_number = _format_load_number(plant_code, year_suffix, seq, draft=True)
        else:
            normalized, suffix = _normalize_load_number(load_number)
            if suffix != "D":
                load_number = f"{normalized}-D"
        db.update_load_status(load_id, STATUS_DRAFT, load_number)
        if planning_session_id:
            _sync_planning_session_status(planning_session_id)
        if is_async:
            snapshot_loads = (
                load_builder.list_loads(
                    None,
                    session_id=planning_session_id,
                    include_stack_metrics=False,
                )
                if planning_session_id
                else None
            )
            snapshot = _compute_load_progress_snapshot(
                plant_scope=plant_scope,
                all_loads=snapshot_loads,
                allowed_plants=plant_scope if planning_session_id else None,
            )
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
        should_redirect_to_report = False
        if not load_number:
            if planning_session:
                start_value = (request.form.get("load_number_start") or "").strip()
                starting_sequence = None
                if start_value:
                    if not LOAD_NUMBER_START_PATTERN.fullmatch(start_value):
                        return jsonify(
                            {
                                "error": "Starting load number must be exactly 4 digits.",
                                "invalid_load_number_seed": True,
                            }
                        ), 400
                    starting_sequence = int(start_value)
                reservation = _reserve_session_load_number(
                    planning_session,
                    plant_code,
                    starting_sequence=starting_sequence,
                )
                if reservation.get("needs_start"):
                    return jsonify(
                        {
                            "error": "Enter the first 4-digit load number for this planning session.",
                            "requires_load_number_seed": True,
                            "load_number_prefix": reservation.get("prefix") or "",
                        }
                    ), 428
                reserved_number = reservation.get("load_number")
                if reserved_number:
                    load_number = reserved_number
            if not load_number:
                seq = db.get_next_load_sequence(plant_code, year_suffix)
                load_number = _format_load_number(plant_code, year_suffix, seq, draft=False)
        else:
            normalized, suffix = _normalize_load_number(load_number)
            if suffix == "D":
                load_number = normalized[:-2] if normalized.endswith("-D") else normalized
        db.update_load_status(load_id, STATUS_APPROVED, load_number)
        if planning_session_id:
            next_session_status = _normalize_session_status(
                _sync_planning_session_status(planning_session_id)
            )
            should_redirect_to_report = next_session_status == "COMPLETED"
        if is_async:
            snapshot_loads = (
                load_builder.list_loads(
                    None,
                    session_id=planning_session_id,
                    include_stack_metrics=False,
                )
                if planning_session_id
                else None
            )
            snapshot = _compute_load_progress_snapshot(
                plant_scope=plant_scope,
                all_loads=snapshot_loads,
                allowed_plants=plant_scope if planning_session_id else None,
            )
            return jsonify(
                {
                    "status": STATUS_APPROVED,
                    "load_id": load_id,
                    "load_number": load_number,
                    "redirect_to_report": bool(should_redirect_to_report and planning_session_id),
                    "session_report_url": (
                        url_for("load_report", session_id=planning_session_id)
                        if should_redirect_to_report and planning_session_id
                        else ""
                    ),
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
        if should_redirect_to_report and planning_session_id:
            return redirect(url_for("load_report", session_id=planning_session_id))
        return redirect(redirect_target)

    return redirect(redirect_target)


@app.route("/loads/<int:load_id>/remove_order", methods=["GET", "POST"])
def remove_order_from_load(load_id):
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    load = db.get_load(load_id)
    if not load:
        return redirect(url_for("loads", session_id=_get_active_planning_session_id()))

    allowed_plants = _get_allowed_plants()
    if load["origin_plant"] not in allowed_plants:
        return redirect(url_for("loads", session_id=_get_active_planning_session_id()))

    order_id = (request.values.get("order_id") or "").strip()
    load_data = dict(load)
    plant_code = load_data.get("origin_plant")
    load_label = load_data.get("load_number") or f"Load #{load_id}"
    load_status = (load_data.get("status") or STATUS_PROPOSED).upper()
    session_id = load_data.get("planning_session_id")

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
            return redirect(url_for("loads", plants=plant_code, session_id=session_id))
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
        return redirect(url_for("loads", plants=plant_code, session_id=session_id))

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
                session_id=session_id,
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

    session_id = load_data.get("planning_session_id") if load_data else None
    _reoptimize_for_plant(plant_code, session_id=session_id)
    return redirect(url_for("loads", plants=plant_code, reopt="done", session_id=session_id))


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

    session_id = load.get("planning_session_id")
    reason_category = (request.form.get("reason_category") or "").strip()
    details = (request.form.get("details") or "").strip()
    if not reason_category or len(details or "") < 10:
        error_message = "Select a reason and add at least 10 characters before returning these orders to the pool."
        return redirect(
            url_for(
                "loads",
                plants=load["origin_plant"],
                feedback_error=error_message,
                feedback_target=f"load-return-{load_id}",
                session_id=session_id,
            )
        )

    db.add_load_feedback(
        load_id,
        order_id=None,
        action_type="orders_returned_to_pool",
        reason_category=reason_category,
        details=details,
        planner_id=_get_session_profile_name() or _get_session_role(),
    )
    db.delete_load(load_id)
    if session_id:
        _sync_planning_session_status(session_id)
    return redirect(url_for("loads", plants=load["origin_plant"], tab="draft", session_id=session_id))


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
    session_id = request.form.get("session_id")
    try:
        session_id = int(session_id) if session_id else None
    except (TypeError, ValueError):
        session_id = None
    redirect_session_id = session_id

    if session_id:
        _archive_session_and_release_loads(session_id)
        redirect_session_id = None
    elif plant_filters:
        _reintroduce_orders_to_pool(plant_scope)
        for plant in plant_scope:
            db.clear_loads_for_plant(plant)
    else:
        _reintroduce_orders_to_pool(plant_scope)
        for plant in plant_scope:
            db.clear_loads_for_plant(plant)

    return redirect(
        url_for(
            "loads",
            plants=",".join(plant_filters) if plant_filters else None,
            tab=tab or None,
            sort=sort_mode or None,
            today=today_param or None,
            session_id=redirect_session_id,
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
    session_id = request.form.get("session_id")
    try:
        session_id = int(session_id) if session_id else None
    except (TypeError, ValueError):
        session_id = None

    if not plant_scope:
        return redirect(url_for("loads", session_id=session_id or _get_active_planning_session_id()))

    placeholders = ", ".join("?" for _ in plant_scope)
    params = list(plant_scope)
    status_clause = ""
    if status_filter:
        status_clause = " AND UPPER(status) = ?"
        params.append(status_filter)
    session_clause = ""
    if session_id:
        session_clause = " AND planning_session_id = ?"
        params.append(session_id)

    with db.get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT id, load_number, origin_plant, status
            FROM loads
            WHERE origin_plant IN ({placeholders})
            {status_clause}
            {session_clause}
            """,
            params,
        ).fetchall()

    planning_session = db.get_planning_session(session_id) if session_id else None
    year_suffix = _year_suffix()
    seed_year_suffix = _planning_session_year_suffix(planning_session) if planning_session else year_suffix
    for row in rows:
        current_status = (row["status"] or STATUS_PROPOSED).upper()
        if current_status == STATUS_APPROVED:
            continue
        plant_code = row["origin_plant"]
        load_number = row["load_number"]
        if not load_number:
            if planning_session:
                reservation = _reserve_session_load_number(planning_session, plant_code)
                if reservation.get("needs_start"):
                    seed_seq = db.get_next_load_sequence(plant_code, seed_year_suffix)
                    reservation = _reserve_session_load_number(
                        planning_session,
                        plant_code,
                        starting_sequence=seed_seq,
                    )
                reserved_number = reservation.get("load_number")
                if reserved_number:
                    load_number = reserved_number
            if not load_number:
                seq = db.get_next_load_sequence(plant_code, year_suffix)
                load_number = _format_load_number(plant_code, year_suffix, seq, draft=False)
        else:
            normalized, suffix = _normalize_load_number(load_number)
            if suffix == "D":
                load_number = normalized[:-2] if normalized.endswith("-D") else normalized
        db.update_load_status(row["id"], STATUS_APPROVED, load_number)

    if session_id:
        _sync_planning_session_status(session_id)
        return redirect(url_for("load_report", session_id=session_id))

    return redirect(
        url_for(
            "loads",
            plants=",".join(plant_filters) if plant_filters else None,
            status=status_filter or None,
            tab=tab or None,
            sort=sort_mode or None,
            today=today_param or None,
            session_id=session_id,
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
    sort_mode = (request.form.get("sort") or "").strip().lower()
    today_param = request.form.get("today")
    session_id = request.form.get("session_id")
    try:
        session_id = int(session_id) if session_id else None
    except (TypeError, ValueError):
        session_id = None

    if not plant_scope:
        return redirect(url_for("loads", session_id=session_id or _get_active_planning_session_id()))

    all_loads = load_builder.list_loads(None, session_id=session_id)
    candidates = [
        load
        for load in all_loads
        if load.get("origin_plant") in plant_scope
        and (load.get("status") or STATUS_PROPOSED).upper() in {STATUS_PROPOSED, STATUS_DRAFT}
        and (load.get("build_source") or "OPTIMIZED").upper() != "MANUAL"
        and _is_full_truckload(load)
    ]

    planning_session = db.get_planning_session(session_id) if session_id else None
    year_suffix = _year_suffix()
    seed_year_suffix = _planning_session_year_suffix(planning_session) if planning_session else year_suffix
    for load in candidates:
        current_status = (load.get("status") or STATUS_PROPOSED).upper()
        if current_status == STATUS_APPROVED:
            continue
        plant_code = load.get("origin_plant")
        load_number = load.get("load_number")
        if not load_number:
            if planning_session:
                reservation = _reserve_session_load_number(planning_session, plant_code)
                if reservation.get("needs_start"):
                    seed_seq = db.get_next_load_sequence(plant_code, seed_year_suffix)
                    reservation = _reserve_session_load_number(
                        planning_session,
                        plant_code,
                        starting_sequence=seed_seq,
                    )
                reserved_number = reservation.get("load_number")
                if reserved_number:
                    load_number = reserved_number
            if not load_number:
                seq = db.get_next_load_sequence(plant_code, year_suffix)
                load_number = _format_load_number(plant_code, year_suffix, seq, draft=False)
        else:
            normalized, suffix = _normalize_load_number(load_number)
            if suffix == "D":
                load_number = normalized[:-2] if normalized.endswith("-D") else normalized
        db.update_load_status(load["id"], STATUS_APPROVED, load_number)

    if session_id:
        _sync_planning_session_status(session_id)

    return redirect(
        url_for(
            "loads",
            plants=",".join(plant_filters) if plant_filters else None,
            tab=tab or None,
            sort=sort_mode or None,
            today=today_param or None,
            session_id=session_id,
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
    session_id = request.form.get("session_id")
    try:
        session_id = int(session_id) if session_id else None
    except (TypeError, ValueError):
        session_id = None

    if not plant_scope:
        return redirect(url_for("loads", session_id=session_id or _get_active_planning_session_id()))

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
    session_clause = ""
    if session_id:
        session_clause = " AND planning_session_id = ?"
        params.append(session_id)

    with db.get_connection() as connection:
        rows = connection.execute(
            f"""
              SELECT id, origin_plant
              FROM loads
              WHERE origin_plant IN ({placeholders})
              {status_clause}
              {session_clause}
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
        _reoptimize_for_plant(plant, session_id=session_id)

    return redirect(
        url_for(
            "loads",
              plants=",".join(plant_filters) if plant_filters else None,
              status=status_filter or None,
              tab=tab or None,
              reopt="done" if plants_to_reopt else None,
              session_id=session_id,
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
    tab = (request.args.get("tab") or "overview").strip().lower()
    valid_tabs = {"overview", "rates", "skus", "lookups"}
    if tab not in valid_tabs:
        tab = "overview"

    rates_data = []
    rate_plants = []
    rate_states = []
    rate_matrix = {}
    specs = []
    recent_specs = []
    sku_categories = []
    sku_lengths = []
    sku_step_decks = []
    sku_flat_beds = []
    optimizer_exception_category_options = []
    planner_specs = []
    system_specs = []
    source_led_specs = []
    source_led_mapped_count = 0
    source_led_unmapped_count = 0
    source_led_missing_spec_count = 0
    source_led_unique_sku_count = 0
    lookups_data = []
    plants_data = []
    strategic_customers_raw = ""
    strategic_customers = []
    stop_color_rows = []
    trailer_assignment_rules = _get_trailer_assignment_rules()
    rate_table_contexts = _get_rate_table_contexts()
    optimizer_defaults = _default_optimize_form()
    optimizer_defaults.update(_get_optimizer_default_settings())
    util_grade_thresholds = []
    fuel_surcharge_per_mile = _get_fuel_surcharge_per_mile()
    global_rate_metrics = _get_rates_overview_metrics()

    if tab in {"overview", "rates"}:
        rates_data = db.list_rate_matrix()
        rate_plants, rate_states, rate_matrix = _build_rate_matrix_records(rates_data)
    if tab in {"overview", "skus", "lookups"}:
        specs = db.list_sku_specs()
        category_source = {
            (spec.get("category") or "").strip().upper()
            for spec in specs
            if (spec.get("category") or "").strip()
        }
        category_source.update(DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES)
        category_source.update(
            stack_calculator.normalize_upper_deck_exception_categories(
                optimizer_defaults.get("upper_deck_exception_categories"),
                default=DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES,
            )
        )
        optimizer_exception_category_options = sorted(category_source)
    if tab == "overview":
        recent_specs = sorted(
            specs,
            key=lambda spec: spec.get("added_at") or spec.get("created_at") or "",
            reverse=True,
        )[:5]
    if tab == "skus":
        def _collect_numeric_filters(field_name, cast=int):
            values = set()
            for spec in specs:
                raw_value = spec.get(field_name)
                if raw_value in (None, ""):
                    continue
                try:
                    if cast is int:
                        normalized = str(int(float(raw_value)))
                    else:
                        normalized = f"{float(raw_value):g}"
                except (TypeError, ValueError):
                    continue
                values.add(normalized)
            return sorted(values, key=lambda value: float(value))

        planner_specs = [spec for spec in specs if _sku_is_planner_input(spec)]
        system_specs = [spec for spec in specs if not _sku_is_planner_input(spec)]
        raw_categories = {
            (spec.get("category") or "").strip().upper()
            for spec in specs
        }
        sku_categories = sorted([category for category in raw_categories if category])
        sku_lengths = _collect_numeric_filters("length_with_tongue_ft", cast=float)
        sku_step_decks = _collect_numeric_filters("max_stack_step_deck", cast=int)
        sku_flat_beds = _collect_numeric_filters("max_stack_flat_bed", cast=int)
        source_led_specs = _build_source_led_cheat_sheet_rows(specs)
        source_led_mapped_count = sum(
            1 for row in source_led_specs if row.get("mapping_status") in {"Mapped", "Mapped (Cargo Rule)"}
        )
        source_led_unmapped_count = sum(
            1 for row in source_led_specs if row.get("mapping_status") == "Unmapped"
        )
        source_led_missing_spec_count = sum(
            1 for row in source_led_specs if row.get("mapping_status") == "Mapped SKU Missing Spec"
        )
        source_led_unique_sku_count = len(
            {
                sku
                for row in source_led_specs
                for sku in (row.get("mapped_sku_list") or [])
                if str(sku or "").strip()
            }
        )
    elif tab == "lookups":
        lookups_data = db.list_item_lookups()
    if tab in {"overview", "plants"}:
        plants_data = db.list_plants()
    if tab in {"overview", "planning_tools"}:
        setting = db.get_planning_setting("strategic_customers") or {}
        strategic_customers_raw = setting.get("value_text") or ""
        strategic_customers = _parse_strategic_customers(strategic_customers_raw)
        util_grade_thresholds = _build_utilization_grade_rows(_get_utilization_grade_thresholds())
        stop_color_rows = [
            {"sequence": idx, "color": color}
            for idx, color in enumerate(_get_stop_color_palette(), start=1)
        ]

    return render_template(
        "settings.html",
        tab=tab,
        global_rate_metrics=global_rate_metrics,
        rates=rates_data,
        rate_plants=rate_plants,
        rate_states=rate_states,
        rate_matrix=rate_matrix,
        specs=specs,
        planner_specs=planner_specs,
        system_specs=system_specs,
        recent_specs=recent_specs,
        lookups=lookups_data,
        plants_data=plants_data,
        strategic_customers_raw=strategic_customers_raw,
        strategic_customers=strategic_customers,
        trailer_assignment_rules=trailer_assignment_rules,
        rate_table_contexts=rate_table_contexts,
        rate_table_key_options=RATE_TABLE_KEY_OPTIONS,
        optimizer_defaults=optimizer_defaults,
        util_grade_thresholds=util_grade_thresholds,
        sku_categories=sku_categories,
        sku_lengths=sku_lengths,
        sku_step_decks=sku_step_decks,
        sku_flat_beds=sku_flat_beds,
        source_led_specs=source_led_specs,
        source_led_mapped_count=source_led_mapped_count,
        source_led_unmapped_count=source_led_unmapped_count,
        source_led_missing_spec_count=source_led_missing_spec_count,
        source_led_unique_sku_count=source_led_unique_sku_count,
        fuel_surcharge_per_mile=fuel_surcharge_per_mile,
        stop_color_rows=stop_color_rows,
        optimizer_exception_category_options=optimizer_exception_category_options,
        is_admin=_get_session_role() == ROLE_ADMIN,
    )


@app.route("/planning-tools/save", methods=["POST"])
def save_planning_tools():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    _require_admin()

    if "strategic_customers" in request.form:
        strategic_customers_raw = request.form.get("strategic_customers") or ""
        parsed = customer_rules.parse_strategic_customers(strategic_customers_raw)
        serialized = customer_rules.serialize_strategic_customers(parsed)
        db.upsert_planning_setting("strategic_customers", serialized)

    has_trailer_rules_payload = request.form.get("trailer_rules_form") == "1"
    if has_trailer_rules_payload:
        trailer_rules = _get_trailer_assignment_rules()
        trailer_rules["livestock_wedge_enabled"] = _coerce_bool_value(
            request.form.get("livestock_wedge_enabled")
        )
        trailer_rules["auto_assign_hotshot_enabled"] = _coerce_bool_value(
            request.form.get("auto_assign_hotshot_enabled")
        )
        trailer_rules["auto_assign_hotshot_utilization_threshold_pct"] = round(
            _coerce_non_negative_float(
                request.form.get("auto_assign_hotshot_utilization_threshold_pct"),
                trailer_rules.get("auto_assign_hotshot_utilization_threshold_pct", 45.0),
            ),
            1,
        )
        db.upsert_planning_setting(
            TRAILER_ASSIGNMENT_RULES_SETTING_KEY,
            json.dumps(trailer_rules),
        )
    target_tab = (request.form.get("tab") or "overview").strip().lower()
    if target_tab != "overview":
        target_tab = "overview"
    return redirect(url_for("settings", tab=target_tab))


@app.route("/settings/stop-colors/save", methods=["POST"])
def save_stop_colors():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    _require_admin()

    palette = []
    for idx, default_color in enumerate(DEFAULT_STOP_COLOR_PALETTE, start=1):
        raw = request.form.get(f"stop_color_{idx}")
        palette.append(_normalize_hex_color(raw, default_color))
    db.upsert_planning_setting(STOP_COLOR_PALETTE_SETTING_KEY, json.dumps(palette))

    target_tab = (request.form.get("tab") or "overview").strip().lower()
    if target_tab != "overview":
        target_tab = "overview"
    return redirect(url_for("settings", tab=target_tab))


@app.route("/rates/fuel-surcharge/save", methods=["POST"])
def save_fuel_surcharge():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    _require_admin()

    payload = request.get_json(silent=True) or request.form
    value = round(
        _coerce_non_negative_float(
            payload.get("fuel_surcharge_per_mile"),
            DEFAULT_FUEL_SURCHARGE_PER_MILE,
        ),
        2,
    )
    db.upsert_planning_setting(FUEL_SURCHARGE_SETTING_KEY, f"{value:.2f}")

    if request.is_json:
        return jsonify({"fuel_surcharge_per_mile": value})
    target_tab = (payload.get("tab") or request.form.get("tab") or "rates").strip().lower()
    if target_tab not in {"overview", "rates"}:
        target_tab = "rates"
    return redirect(url_for("settings", tab=target_tab))


@app.route("/settings/rates/contexts/save", methods=["POST"])
def save_rate_table_contexts():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    _require_admin()

    payload = request.get_json(silent=True) or request.form
    contexts = _get_rate_table_contexts()
    contexts["default_rate_table_key"] = _normalize_rate_table_key(
        payload.get("default_rate_table_key"),
        contexts.get("default_rate_table_key", "DEFAULT"),
    )
    contexts["carrier_dedicated_ryder_rate_table_key"] = _normalize_rate_table_key(
        payload.get("carrier_dedicated_ryder_rate_table_key"),
        contexts.get("carrier_dedicated_ryder_rate_table_key", "DEDICATED_RYDER_FLEET"),
    )
    contexts["trailer_hotshot_rate_table_key"] = _normalize_rate_table_key(
        payload.get("trailer_hotshot_rate_table_key"),
        contexts.get("trailer_hotshot_rate_table_key", "HOTSHOT_TRAILER_TYPES"),
    )
    db.upsert_planning_setting(RATE_TABLE_CONTEXTS_SETTING_KEY, json.dumps(contexts))

    if request.is_json:
        return jsonify({"rate_table_contexts": contexts})
    target_tab = (payload.get("tab") or request.form.get("tab") or "rates").strip().lower()
    if target_tab not in {"overview", "rates"}:
        target_tab = "rates"
    return redirect(url_for("settings", tab=target_tab))


@app.route("/settings/global-metrics/save", methods=["POST"])
def save_global_metrics():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    _require_admin()

    payload = request.get_json(silent=True) or request.form
    stop_fee = round(
        _coerce_non_negative_float(
            payload.get("stop_fee"),
            DEFAULT_STOP_FEE,
        ),
        2,
    )
    load_minimum = round(
        _coerce_non_negative_float(
            payload.get("load_minimum"),
            DEFAULT_MIN_LOAD_COST,
        ),
        2,
    )
    fuel_surcharge = round(
        _coerce_non_negative_float(
            payload.get("fuel_surcharge"),
            DEFAULT_FUEL_SURCHARGE_PER_MILE,
        ),
        2,
    )

    db.upsert_planning_setting(STOP_FEE_SETTING_KEY, f"{stop_fee:.2f}")
    db.upsert_planning_setting(MIN_LOAD_COST_SETTING_KEY, f"{load_minimum:.2f}")
    db.upsert_planning_setting(FUEL_SURCHARGE_SETTING_KEY, f"{fuel_surcharge:.2f}")

    if request.is_json:
        return jsonify(
            {
                "stop_fee": stop_fee,
                "load_minimum": load_minimum,
                "fuel_surcharge": fuel_surcharge,
            }
        )

    target_tab = (payload.get("tab") or request.form.get("tab") or "overview").strip().lower()
    if target_tab not in {"overview", "rates"}:
        target_tab = "overview"
    return redirect(url_for("settings", tab=target_tab))


@app.route("/settings/optimizer-defaults/save", methods=["POST"])
def save_optimizer_defaults():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    _require_admin()

    payload = request.get_json(silent=True) or request.form
    current = _get_optimizer_default_settings()
    raw_exception_categories = payload.get("upper_deck_exception_categories")
    if not request.is_json and hasattr(request.form, "getlist"):
        form_categories = request.form.getlist("upper_deck_exception_categories")
        if form_categories:
            raw_exception_categories = form_categories
    trailer_type = (payload.get("trailer_type") or current["trailer_type"]).strip().upper()
    if not stack_calculator.is_valid_trailer_type(trailer_type):
        trailer_type = stack_calculator.normalize_trailer_type(current["trailer_type"], default="STEP_DECK")
    optimized = {
        "trailer_type": trailer_type,
        "capacity_feet": round(
            _coerce_non_negative_float(payload.get("capacity_feet"), current["capacity_feet"]),
            2,
        ),
        "max_detour_pct": round(
            _coerce_non_negative_float(payload.get("max_detour_pct"), current["max_detour_pct"]),
            2,
        ),
        "time_window_days": _coerce_non_negative_int(
            payload.get("time_window_days"),
            current["time_window_days"],
        ),
        "geo_radius": round(
            _coerce_non_negative_float(payload.get("geo_radius"), current["geo_radius"]),
            2,
        ),
        "stack_overflow_max_height": _coerce_non_negative_int(
            payload.get("stack_overflow_max_height"),
            current.get("stack_overflow_max_height", DEFAULT_STACK_OVERFLOW_MAX_HEIGHT),
        ),
        "max_back_overhang_ft": round(
            _coerce_non_negative_float(
                payload.get("max_back_overhang_ft"),
                current.get("max_back_overhang_ft", DEFAULT_MAX_BACK_OVERHANG_FT),
            ),
            2,
        ),
        "upper_two_across_max_length_ft": round(
            _coerce_non_negative_float(
                payload.get("upper_two_across_max_length_ft"),
                current.get(
                    "upper_two_across_max_length_ft",
                    DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT,
                ),
            ),
            2,
        ),
        "upper_deck_exception_max_length_ft": round(
            _coerce_non_negative_float(
                payload.get("upper_deck_exception_max_length_ft"),
                current.get(
                    "upper_deck_exception_max_length_ft",
                    DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT,
                ),
            ),
            2,
        ),
        "upper_deck_exception_overhang_allowance_ft": round(
            _coerce_non_negative_float(
                payload.get("upper_deck_exception_overhang_allowance_ft"),
                current.get(
                    "upper_deck_exception_overhang_allowance_ft",
                    DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT,
                ),
            ),
            2,
        ),
        "upper_deck_exception_categories": stack_calculator.normalize_upper_deck_exception_categories(
            raw_exception_categories,
            default=current.get(
                "upper_deck_exception_categories",
                DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES,
            ),
        ),
    }

    db.upsert_planning_setting(OPTIMIZER_DEFAULTS_SETTING_KEY, json.dumps(optimized))
    stack_calculator.invalidate_stack_assumptions_cache()

    if request.is_json:
        return jsonify({"optimizer_defaults": optimized})

    target_tab = (payload.get("tab") or request.form.get("tab") or "overview").strip().lower()
    if target_tab != "overview":
        target_tab = "overview"
    return redirect(url_for("settings", tab=target_tab))


@app.route("/settings/utilization-grades/save", methods=["POST"])
def save_utilization_grades():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect
    _require_admin()

    payload = request.get_json(silent=True) or request.form
    current = _get_utilization_grade_thresholds()
    raw_d_min = payload.get("grade_d_min")
    raw_f_max = payload.get("grade_f_max")
    d_value = raw_d_min if raw_d_min not in (None, "") else current["D"]
    if raw_d_min in (None, "") and raw_f_max not in (None, ""):
        d_value = _coerce_non_negative_int(raw_f_max, max(current["D"] - 1, 0)) + 1
    thresholds = _coerce_utilization_grade_thresholds(
        {
            "A": payload.get("grade_a_min", current["A"]),
            "B": payload.get("grade_b_min", current["B"]),
            "C": payload.get("grade_c_min", current["C"]),
            "D": d_value,
        }
    )
    db.upsert_planning_setting(UTILIZATION_GRADE_THRESHOLDS_SETTING_KEY, json.dumps(thresholds))
    stack_calculator.invalidate_utilization_grade_thresholds_cache()

    if request.is_json:
        return jsonify({"utilization_grade_thresholds": thresholds})

    target_tab = (payload.get("tab") or request.form.get("tab") or "overview").strip().lower()
    if target_tab != "overview":
        target_tab = "overview"
    return redirect(url_for("settings", tab=target_tab))


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
        "description": (payload.get("description") or "").strip(),
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
    geo_utils.invalidate_coordinate_caches(plant_coords=True)
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


@app.route("/skus/export-cheat-sheet.xlsx")
def export_sku_cheat_sheet():
    session_redirect = _require_session()
    if session_redirect:
        return session_redirect

    specs = db.list_sku_specs()
    ordered_specs = sorted(
        specs,
        key=lambda spec: (
            0 if _sku_is_planner_input(spec) else 1,
            str(spec.get("sku") or "").upper(),
        ),
    )

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "SKU Cheat Sheet"

    headers = [
        "Source",
        "SKU",
        "Description",
        "Category",
        "Length w/ Tongue (ft)",
        "Max Stack Step Deck",
        "Max Stack Flat Bed",
        "Notes",
        "Added",
    ]
    sheet.append(headers)

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    header_alignment = Alignment(horizontal="center", vertical="center")

    for col_idx, _ in enumerate(headers, start=1):
        header_cell = sheet.cell(row=1, column=col_idx)
        header_cell.fill = header_fill
        header_cell.font = header_font
        header_cell.alignment = header_alignment

    for spec in ordered_specs:
        sheet.append(
            [
                _sku_source_label(spec),
                spec.get("sku") or "",
                spec.get("description") or "",
                spec.get("category") or "",
                spec.get("length_with_tongue_ft"),
                spec.get("max_stack_step_deck"),
                spec.get("max_stack_flat_bed"),
                spec.get("notes") or "",
                spec.get("added_at") or spec.get("created_at") or "",
            ]
        )

    widths = {
        "A": 16,
        "B": 18,
        "C": 38,
        "D": 18,
        "E": 22,
        "F": 20,
        "G": 19,
        "H": 30,
        "I": 21,
    }
    for col, width in widths.items():
        sheet.column_dimensions[col].width = width

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:I{max(sheet.max_row, 1)}"

    data_alignment_left = Alignment(horizontal="left", vertical="center")
    data_alignment_center = Alignment(horizontal="center", vertical="center")
    for row_idx in range(2, sheet.max_row + 1):
        for col_idx in [1, 2, 3, 4, 8, 9]:
            sheet.cell(row=row_idx, column=col_idx).alignment = data_alignment_left
        for col_idx in [5, 6, 7]:
            sheet.cell(row=row_idx, column=col_idx).alignment = data_alignment_center

    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    filename = f"sku_cheat_sheet_{date.today().isoformat()}.xlsx"
    return Response(
        output.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/skus/add", methods=["POST"])
def add_sku():
    _require_admin()
    spec = {
        "sku": request.form.get("sku", "").strip(),
        "description": request.form.get("description", "").strip(),
        "category": request.form.get("category", "").strip(),
        "length_with_tongue_ft": float(request.form.get("length_with_tongue_ft", 0) or 0),
        "max_stack_step_deck": int(request.form.get("max_stack_step_deck", 1) or 1),
        "max_stack_flat_bed": int(request.form.get("max_stack_flat_bed", 1) or 1),
        "notes": request.form.get("notes", "").strip(),
    }
    db.upsert_sku_spec(spec)
    return redirect(request.referrer or url_for("settings", tab="skus"))


@app.route("/skus/delete/<int:spec_id>", methods=["POST"])
def delete_sku(spec_id):
    _require_admin()
    db.delete_sku_spec(spec_id)
    return redirect(request.referrer or url_for("settings", tab="skus"))


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
                ol.item_desc,
                ol.sku,
                ol.qty,
                ol.unit_length_ft,
                ol.max_stack_height,
                COALESCE(ss.category, ol.bin, '') AS category
            FROM order_lines ol
            LEFT JOIN sku_specifications ss ON ol.sku = ss.sku
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

    rows = [dict(row) for row in rows]
    order_row = dict(order_row) if order_row else None

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
                "item_desc": row.get("item_desc"),
                "sku": row["sku"],
                "qty": qty,
                "unit_length_ft": row["unit_length_ft"] or 0,
                "max_stack_height": max_stack,
                "positions_required": positions_required,
                "linear_feet": linear_feet,
                "category": row["category"] or "",
                "stop_sequence": 1,
            }
        )

    # Orders page schematic should show strict stack capacity without singleton overflow allowance.
    # Keep overflow logic enabled for optimization and Loads page workflows.
    config = stack_calculator.calculate_stack_configuration(
        line_items,
        stack_overflow_max_height=0,
    )
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

