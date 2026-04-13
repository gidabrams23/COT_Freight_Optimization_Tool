"""Web-app compatibility wrapper for cot_utilization.stack_calculator.

Re-exports all public symbols from the package so existing app imports
continue to work.  Overrides calculate_stack_configuration to inject
DB-sourced planner settings before delegating to the package core.
"""

import json
import time

import db

from cot_utilization import stack_calculator as _core

# ---------------------------------------------------------------------------
# Re-export constants from the package
# ---------------------------------------------------------------------------

TRAILER_CONFIGS = _core.TRAILER_CONFIGS
TRAILER_PROFILE_OPTIONS = _core.TRAILER_PROFILE_OPTIONS
TRAILER_TYPE_SET = _core.TRAILER_TYPE_SET
FIXED_CAPACITY_TRAILER_TYPES = _core.FIXED_CAPACITY_TRAILER_TYPES

DEFAULT_UTILIZATION_GRADE_THRESHOLDS = _core.DEFAULT_UTILIZATION_GRADE_THRESHOLDS
DEFAULT_STACK_OVERFLOW_MAX_HEIGHT = _core.DEFAULT_STACK_OVERFLOW_MAX_HEIGHT
DEFAULT_MAX_BACK_OVERHANG_FT = _core.DEFAULT_MAX_BACK_OVERHANG_FT
DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT = _core.DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT
DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT = _core.DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT
DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT = _core.DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT
DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES = _core.DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES
DEFAULT_EQUAL_LENGTH_DECK_LENGTH_ORDER_ENABLED = _core.DEFAULT_EQUAL_LENGTH_DECK_LENGTH_ORDER_ENABLED

# ---------------------------------------------------------------------------
# Re-export public functions from the package
# ---------------------------------------------------------------------------

trailer_profile_options = _core.trailer_profile_options
is_valid_trailer_type = _core.is_valid_trailer_type
normalize_trailer_type = _core.normalize_trailer_type
item_deck_length_ft = _core.item_deck_length_ft
normalize_upper_deck_exception_categories = _core.normalize_upper_deck_exception_categories
apply_upper_usage_metadata = _core.apply_upper_usage_metadata
upper_deck_position_length_limit_ft = _core.upper_deck_position_length_limit_ft
is_upper_deck_exception_eligible_position = _core.is_upper_deck_exception_eligible_position
evaluate_upper_deck_overhang = _core.evaluate_upper_deck_overhang
capacity_overflow_feet = _core.capacity_overflow_feet
stack_display_index_map = _core.stack_display_index_map
check_stacking_compatibility = _core.check_stacking_compatibility

# ---------------------------------------------------------------------------
# DB-specific setting keys
# ---------------------------------------------------------------------------

UTILIZATION_GRADE_THRESHOLDS_SETTING_KEY = "utilization_grade_thresholds"
OPTIMIZER_DEFAULTS_SETTING_KEY = "optimizer_defaults"

# ---------------------------------------------------------------------------
# Caches (DB-layer concern, not in the pure-math package)
# ---------------------------------------------------------------------------

_UTILIZATION_GRADE_CACHE = {
    "thresholds": dict(DEFAULT_UTILIZATION_GRADE_THRESHOLDS),
    "expires_at": 0.0,
}
_STACK_ASSUMPTIONS_CACHE = {
    "assumptions": {
        "stack_overflow_max_height": DEFAULT_STACK_OVERFLOW_MAX_HEIGHT,
        "max_back_overhang_ft": DEFAULT_MAX_BACK_OVERHANG_FT,
        "upper_two_across_max_length_ft": DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT,
        "upper_deck_exception_max_length_ft": DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT,
        "upper_deck_exception_overhang_allowance_ft": DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT,
        "upper_deck_exception_categories": list(DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES),
        "equal_length_deck_length_order_enabled": DEFAULT_EQUAL_LENGTH_DECK_LENGTH_ORDER_ENABLED,
    },
    "expires_at": 0.0,
}

# ---------------------------------------------------------------------------
# Cache invalidation helpers
# ---------------------------------------------------------------------------


def invalidate_utilization_grade_thresholds_cache():
    _UTILIZATION_GRADE_CACHE["expires_at"] = 0.0


def invalidate_stack_assumptions_cache():
    _STACK_ASSUMPTIONS_CACHE["expires_at"] = 0.0


# ---------------------------------------------------------------------------
# DB-backed lookups
# ---------------------------------------------------------------------------


def get_stack_capacity_assumptions(force_refresh=False):
    now = time.time()
    if force_refresh:
        invalidate_stack_assumptions_cache()
    if _STACK_ASSUMPTIONS_CACHE["expires_at"] > now:
        return dict(_STACK_ASSUMPTIONS_CACHE["assumptions"])

    assumptions = {
        "stack_overflow_max_height": DEFAULT_STACK_OVERFLOW_MAX_HEIGHT,
        "max_back_overhang_ft": DEFAULT_MAX_BACK_OVERHANG_FT,
        "upper_two_across_max_length_ft": DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT,
        "upper_deck_exception_max_length_ft": DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT,
        "upper_deck_exception_overhang_allowance_ft": DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT,
        "upper_deck_exception_categories": list(DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES),
        "equal_length_deck_length_order_enabled": DEFAULT_EQUAL_LENGTH_DECK_LENGTH_ORDER_ENABLED,
    }
    setting = db.get_planning_setting(OPTIMIZER_DEFAULTS_SETTING_KEY) or {}
    raw_text = (setting.get("value_text") or "").strip()
    if raw_text:
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            parsed = None
        assumptions = _core._normalize_stack_assumptions(parsed)

    _STACK_ASSUMPTIONS_CACHE["assumptions"] = dict(assumptions)
    _STACK_ASSUMPTIONS_CACHE["expires_at"] = now + 30.0
    return dict(assumptions)


def get_utilization_grade_thresholds(force_refresh=False):
    now = time.time()
    if force_refresh:
        invalidate_utilization_grade_thresholds_cache()
    if _UTILIZATION_GRADE_CACHE["expires_at"] > now:
        return dict(_UTILIZATION_GRADE_CACHE["thresholds"])

    thresholds = dict(DEFAULT_UTILIZATION_GRADE_THRESHOLDS)
    setting = db.get_planning_setting(UTILIZATION_GRADE_THRESHOLDS_SETTING_KEY) or {}
    raw_text = (setting.get("value_text") or "").strip()
    if raw_text:
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            parsed = None
        thresholds = _core._normalize_threshold_map(parsed)

    _UTILIZATION_GRADE_CACHE["thresholds"] = dict(thresholds)
    _UTILIZATION_GRADE_CACHE["expires_at"] = now + 30.0
    return dict(thresholds)


# ---------------------------------------------------------------------------
# App-level wrapper: injects DB-sourced settings, then delegates to core
# ---------------------------------------------------------------------------


def calculate_stack_configuration(order_lines, **kwargs):
    """App-level wrapper: injects DB-sourced settings, then delegates to core."""
    assumptions = get_stack_capacity_assumptions()
    thresholds = get_utilization_grade_thresholds()
    merged = dict(kwargs)
    merged.setdefault("grade_thresholds", thresholds)
    for key, value in assumptions.items():
        merged.setdefault(key, value)
    return _core.calculate_stack_configuration(order_lines, **merged)
