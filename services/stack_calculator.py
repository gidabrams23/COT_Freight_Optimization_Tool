import json
import math
import time

import db

TRAILER_CONFIGS = {
    "STEP_DECK": {"capacity": 53.0, "lower": 43.0, "upper": 10.0},
    "STEP_DECK_48": {"capacity": 48.0, "lower": 38.0, "upper": 10.0},
    "FLATBED": {"capacity": 53.0, "lower": 53.0, "upper": 0.0},
    "HOTSHOT": {"capacity": 40.0, "lower": 40.0, "upper": 0.0},
    "WEDGE": {"capacity": 51.0, "lower": 51.0, "upper": 0.0},
}
TRAILER_PROFILE_OPTIONS = [
    {"value": "STEP_DECK", "label": "53' Step Deck", "capacity": 53.0, "lower": 43.0, "upper": 10.0},
    {"value": "FLATBED", "label": "53' Flatbed", "capacity": 53.0, "lower": 53.0, "upper": 0.0},
    {"value": "WEDGE", "label": "51' Wedge", "capacity": 51.0, "lower": 51.0, "upper": 0.0},
    {"value": "STEP_DECK_48", "label": "48' Step Deck (38/10)", "capacity": 48.0, "lower": 38.0, "upper": 10.0},
    {"value": "HOTSHOT", "label": "40' Hotshot", "capacity": 40.0, "lower": 40.0, "upper": 0.0},
]
TRAILER_TYPE_SET = set(TRAILER_CONFIGS.keys())

UTILIZATION_GRADE_THRESHOLDS_SETTING_KEY = "utilization_grade_thresholds"
OPTIMIZER_DEFAULTS_SETTING_KEY = "optimizer_defaults"
DEFAULT_UTILIZATION_GRADE_THRESHOLDS = {
    "A": 85,
    "B": 70,
    "C": 55,
    "D": 40,
}
DEFAULT_STACK_OVERFLOW_MAX_HEIGHT = 5
DEFAULT_MAX_BACK_OVERHANG_FT = 4.0
DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT = 7.0
DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT = 16.0
DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT = 6.0
DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES = ("USA", "UTA")
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
    },
    "expires_at": 0.0,
}


def trailer_profile_options():
    return [dict(option) for option in TRAILER_PROFILE_OPTIONS]


def is_valid_trailer_type(trailer_type):
    trailer_key = (trailer_type or "").strip().upper()
    return trailer_key in TRAILER_TYPE_SET


def normalize_trailer_type(trailer_type, default="STEP_DECK"):
    trailer_key = (trailer_type or "").strip().upper()
    if trailer_key in TRAILER_TYPE_SET:
        return trailer_key
    fallback = (default or "STEP_DECK").strip().upper()
    if fallback in TRAILER_TYPE_SET:
        return fallback
    return "STEP_DECK"


def _coerce_non_negative_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(parsed, 0)


def _coerce_non_negative_float(value, default):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    return max(parsed, 0.0)


def _normalize_upper_deck_exception_categories(raw_value, default=None):
    default_categories = [
        str(category).strip().upper()
        for category in (default or DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES)
        if str(category or "").strip()
    ]
    if not default_categories:
        default_categories = [category for category in DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES]

    if isinstance(raw_value, str):
        source = [token.strip() for token in raw_value.split(",")]
    elif isinstance(raw_value, (list, tuple, set)):
        source = list(raw_value)
    else:
        source = list(default_categories)

    cleaned = []
    seen = set()
    for category in source:
        normalized = str(category or "").strip().upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)

    return cleaned or list(default_categories)


def normalize_upper_deck_exception_categories(raw_value, default=None):
    return _normalize_upper_deck_exception_categories(raw_value, default=default)


def _normalize_stack_assumptions(raw_value):
    defaults = {
        "stack_overflow_max_height": DEFAULT_STACK_OVERFLOW_MAX_HEIGHT,
        "max_back_overhang_ft": DEFAULT_MAX_BACK_OVERHANG_FT,
        "upper_two_across_max_length_ft": DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT,
        "upper_deck_exception_max_length_ft": DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT,
        "upper_deck_exception_overhang_allowance_ft": DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT,
        "upper_deck_exception_categories": list(DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES),
    }
    if not isinstance(raw_value, dict):
        return defaults
    return {
        "stack_overflow_max_height": _coerce_non_negative_int(
            raw_value.get("stack_overflow_max_height"),
            defaults["stack_overflow_max_height"],
        ),
        "max_back_overhang_ft": round(
            _coerce_non_negative_float(
                raw_value.get("max_back_overhang_ft"),
                defaults["max_back_overhang_ft"],
            ),
            2,
        ),
        "upper_two_across_max_length_ft": round(
            _coerce_non_negative_float(
                raw_value.get("upper_two_across_max_length_ft"),
                defaults["upper_two_across_max_length_ft"],
            ),
            2,
        ),
        "upper_deck_exception_max_length_ft": round(
            _coerce_non_negative_float(
                raw_value.get("upper_deck_exception_max_length_ft"),
                defaults["upper_deck_exception_max_length_ft"],
            ),
            2,
        ),
        "upper_deck_exception_overhang_allowance_ft": round(
            _coerce_non_negative_float(
                raw_value.get("upper_deck_exception_overhang_allowance_ft"),
                defaults["upper_deck_exception_overhang_allowance_ft"],
            ),
            2,
        ),
        "upper_deck_exception_categories": _normalize_upper_deck_exception_categories(
            raw_value.get("upper_deck_exception_categories"),
            default=defaults["upper_deck_exception_categories"],
        ),
    }


def _normalize_threshold_map(raw_value):
    defaults = dict(DEFAULT_UTILIZATION_GRADE_THRESHOLDS)
    if not isinstance(raw_value, dict):
        return defaults
    try:
        a = int(raw_value.get("A", defaults["A"]))
        b = int(raw_value.get("B", defaults["B"]))
        c = int(raw_value.get("C", defaults["C"]))
        d = int(raw_value.get("D", defaults["D"]))
    except (TypeError, ValueError):
        return defaults

    a = max(min(a, 100), 0)
    b = max(min(b, 99), 0)
    c = max(min(c, 99), 0)
    d = max(min(d, 99), 0)

    # Keep a strict descending ladder for deterministic grading.
    if b >= a:
        b = max(a - 1, 0)
    if c >= b:
        c = max(b - 1, 0)
    if d >= c:
        d = max(c - 1, 0)

    return {"A": a, "B": b, "C": c, "D": d}


def invalidate_utilization_grade_thresholds_cache():
    _UTILIZATION_GRADE_CACHE["expires_at"] = 0.0


def invalidate_stack_assumptions_cache():
    _STACK_ASSUMPTIONS_CACHE["expires_at"] = 0.0


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
    }
    setting = db.get_planning_setting(OPTIMIZER_DEFAULTS_SETTING_KEY) or {}
    raw_text = (setting.get("value_text") or "").strip()
    if raw_text:
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            parsed = None
        assumptions = _normalize_stack_assumptions(parsed)

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
        thresholds = _normalize_threshold_map(parsed)

    _UTILIZATION_GRADE_CACHE["thresholds"] = dict(thresholds)
    _UTILIZATION_GRADE_CACHE["expires_at"] = now + 30.0
    return dict(thresholds)


def _coerce_stop_sequence(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _stop_access_compatible(position, incoming_sequence):
    if incoming_sequence is None:
        return True
    top_sequence = position.get("top_stop_sequence")
    if top_sequence is None:
        return True
    # Earlier stops (smaller sequence) may sit on top of later stops (larger sequence).
    return incoming_sequence <= top_sequence


def _length_stack_compatible(position, incoming_length_ft):
    if incoming_length_ft is None:
        incoming_length_ft = 0
    top_length = position.get("top_length_ft")
    if top_length is None:
        top_length = position.get("length_ft") or 0
    return incoming_length_ft <= top_length + 1e-6


def _position_stop_priority(position):
    # Smaller sequence means earlier stop; UI renders columns right-to-left so these
    # naturally appear closer to trailer back (right/rear).
    sequence = position.get("top_stop_sequence")
    if sequence is None:
        return 10**9
    try:
        return int(sequence)
    except (TypeError, ValueError):
        return 10**9


def _build_order_rank(order_lines):
    entries = []
    seen = set()
    for item in order_lines or []:
        order_id = item.get("order_id")
        if not order_id or order_id in seen:
            continue
        seen.add(order_id)
        stop_sequence = _coerce_stop_sequence(item.get("stop_sequence"))
        if stop_sequence is None:
            stop_sequence = 10**9
        entries.append((stop_sequence, str(order_id), order_id))
    entries.sort()
    return {order_id: idx for idx, (_, __, order_id) in enumerate(entries)}


def _position_order_priority(position, order_rank):
    if not order_rank:
        return 10**9
    ranks = [
        order_rank.get(item.get("order_id"), 10**9)
        for item in (position.get("items") or [])
        if item.get("order_id")
    ]
    return min(ranks) if ranks else 10**9


def _resolve_trailer_config(trailer_type, capacity_feet=None):
    trailer_key = normalize_trailer_type(trailer_type, default="STEP_DECK")
    base = dict(TRAILER_CONFIGS.get(trailer_key, TRAILER_CONFIGS["STEP_DECK"]))
    if capacity_feet:
        try:
            capacity = float(capacity_feet)
        except (TypeError, ValueError):
            capacity = base["capacity"]
        base["capacity"] = capacity
        if base["upper"] > 0:
            base["lower"] = max(capacity - base["upper"], 0)
        else:
            base["lower"] = capacity
    base["type"] = trailer_key
    return base


def _max_stack_utilization_multiplier(stack_overflow_max_height):
    overflow_height = _coerce_non_negative_int(stack_overflow_max_height, 0)
    if overflow_height <= 0:
        return 1.0
    return 1.0 + (1.0 / overflow_height)


def _warning_payload(code, message, deck=None, position_id=None):
    payload = {"code": code, "message": message, "severity": "warning"}
    if deck:
        payload["deck"] = deck
    if position_id:
        payload["position_id"] = position_id
    return payload


def _unit_capacity_fraction(max_stack):
    return 1.0 / max(_coerce_non_negative_int(max_stack, 1), 1)


def _position_stack_height_set(position):
    heights = set()
    for item in position.get("items") or []:
        if max(_coerce_non_negative_int(item.get("units"), 0), 0) <= 0:
            continue
        heights.add(max(_coerce_non_negative_int(item.get("max_stack"), 1), 1))
    return heights


def _position_has_mixed_stack_heights(position):
    return len(_position_stack_height_set(position)) >= 2


def _is_high_side_item(item):
    item_text = str((item or {}).get("item") or "").strip().upper()
    sku_text = str((item or {}).get("sku") or "").strip().upper()
    desc_text = str((item or {}).get("item_desc") or "").strip().upper()
    if "HIGH SIDE" in desc_text:
        return True
    return "HS" in item_text or "HS" in sku_text


def _promote_high_side_items_within_equal_length(position):
    items = list((position or {}).get("items") or [])
    if len(items) <= 1:
        return

    buckets = {}
    for idx, item in enumerate(items):
        length_key = round(float(item.get("unit_length_ft") or 0.0), 6)
        stop_key = _coerce_stop_sequence(item.get("stop_sequence"))
        # Never reorder across stop layers; stop accessibility/customer order has priority.
        buckets.setdefault((length_key, stop_key), []).append((idx, item))

    for entries in buckets.values():
        if len(entries) <= 1:
            continue
        non_hs = [item for _, item in entries if not _is_high_side_item(item)]
        hs = [item for _, item in entries if _is_high_side_item(item)]
        reordered = non_hs + hs
        if len(hs) == 0 or len(non_hs) == 0:
            continue
        for (idx, _), replacement in zip(entries, reordered):
            items[idx] = replacement

    position["items"] = items


def _eligible_singleton_overflow_item(position, stack_overflow_max_height):
    threshold = _coerce_non_negative_int(stack_overflow_max_height, 0)
    if threshold <= 0:
        return None
    if max(_coerce_non_negative_int(position.get("units_count"), 0), 0) != 1:
        return None
    items = position.get("items") or []
    if len(items) != 1:
        return None
    item = items[0]
    units = max(_coerce_non_negative_int(item.get("units"), 0), 0)
    max_stack = max(_coerce_non_negative_int(item.get("max_stack"), 1), 1)
    if units != 1 or max_stack < threshold:
        return None
    return item


def _append_single_unit_item(target, source_item):
    payload = {
        "item": source_item.get("item"),
        "sku": source_item.get("sku"),
        "item_desc": source_item.get("item_desc"),
        "category": source_item.get("category", "UNKNOWN"),
        "units": 1,
        "max_stack": max(_coerce_non_negative_int(source_item.get("max_stack"), 1), 1),
        "upper_max_stack": max(
            _coerce_non_negative_int(
                source_item.get("upper_max_stack"),
                source_item.get("max_stack"),
            ),
            1,
        ),
        "unit_length_ft": source_item.get("unit_length_ft") or 0,
        "order_id": source_item.get("order_id"),
        "stop_sequence": _coerce_stop_sequence(source_item.get("stop_sequence")),
    }
    items = target.get("items") or []
    if items:
        last = items[-1]
        same_signature = (
            (last.get("item") or "") == (payload.get("item") or "")
            and (last.get("sku") or "") == (payload.get("sku") or "")
            and (last.get("item_desc") or "") == (payload.get("item_desc") or "")
            and (last.get("category") or "") == (payload.get("category") or "")
            and max(_coerce_non_negative_int(last.get("max_stack"), 1), 1) == payload["max_stack"]
            and max(
                _coerce_non_negative_int(
                    last.get("upper_max_stack"),
                    last.get("max_stack"),
                ),
                1,
            ) == payload["upper_max_stack"]
            and abs(float(last.get("unit_length_ft") or 0) - float(payload.get("unit_length_ft") or 0)) <= 1e-6
            and (last.get("order_id") or "") == (payload.get("order_id") or "")
            and _coerce_stop_sequence(last.get("stop_sequence")) == payload.get("stop_sequence")
        )
        if same_signature:
            last["units"] = max(_coerce_non_negative_int(last.get("units"), 0), 0) + 1
            return
    items.append(payload)
    target["items"] = items


def _apply_singleton_overflow_allowance(
    positions,
    stack_overflow_max_height,
    max_stack_utilization_multiplier,
):
    threshold = _coerce_non_negative_int(stack_overflow_max_height, 0)
    if threshold <= 0 or not positions:
        return

    idx = 0
    while idx < len(positions):
        source = positions[idx]
        source_item = _eligible_singleton_overflow_item(source, threshold)
        if not source_item:
            idx += 1
            continue

        source_length = float(source.get("length_ft") or 0.0)
        source_stop = _coerce_stop_sequence(source_item.get("stop_sequence"))
        source_max_stack = max(_coerce_non_negative_int(source_item.get("max_stack"), 1), 1)
        source_fraction = _unit_capacity_fraction(source_max_stack)

        candidates = []
        for target_idx, target in enumerate(positions):
            if target_idx == idx:
                continue
            if max(_coerce_non_negative_int(target.get("overflow_units_used"), 0), 0) >= 1:
                continue
            if float(target.get("capacity_used") or 0.0) < (1.0 - 1e-6):
                continue
            # Only allow overflow on stacks that were already mixed-height before adding the overflow unit.
            if not _position_has_mixed_stack_heights(target):
                continue
            if not _length_stack_compatible(target, source_length):
                continue
            if not _stop_access_compatible(target, source_stop):
                continue
            next_capacity = float(target.get("capacity_used") or 0.0) + source_fraction
            if next_capacity > (max_stack_utilization_multiplier + 1e-6):
                continue
            candidates.append((target_idx, target))

        if not candidates:
            idx += 1
            continue

        # Prefer a physically similar stack first, then left-most for deterministic layout.
        candidates.sort(
            key=lambda entry: (
                abs(float(entry[1].get("length_ft") or 0.0) - source_length),
                entry[0],
            )
        )
        target = candidates[0][1]
        target.setdefault("overflow_units_used", 0)
        target.setdefault("overflow_applied", False)
        _append_single_unit_item(target, source_item)
        target["capacity_used"] = round(
            float(target.get("capacity_used") or 0.0) + source_fraction,
            6,
        )
        target["units_count"] = max(_coerce_non_negative_int(target.get("units_count"), 0), 0) + 1
        target["overflow_units_used"] = min(
            max(_coerce_non_negative_int(target.get("overflow_units_used"), 0), 0) + 1,
            1,
        )
        target["overflow_applied"] = True
        if source_stop is not None:
            target["top_stop_sequence"] = source_stop
        target["top_length_ft"] = min(
            float(target.get("top_length_ft") or target.get("length_ft") or source_length),
            source_length,
        )

        positions.pop(idx)


def _position_credit_multiplier(position, max_stack_utilization_multiplier):
    capacity_used = float(position.get("capacity_used") or 0.0)
    if capacity_used <= 1.0:
        return max(capacity_used, 0.0)
    if position.get("overflow_applied"):
        return min(capacity_used, max_stack_utilization_multiplier)
    return 1.0


def _upper_max_stack_for_item(item):
    return max(
        _coerce_non_negative_int(
            item.get("upper_max_stack"),
            item.get("max_stack"),
        ),
        1,
    )


def _upper_capacity_used_for_position(position):
    total = 0.0
    for item in position.get("items") or []:
        units = max(_coerce_non_negative_int(item.get("units"), 0), 0)
        if units <= 0:
            continue
        total += units / _upper_max_stack_for_item(item)
    return total


def _compute_upper_usage_metadata(positions, two_across_max_length_ft):
    threshold = _coerce_non_negative_float(
        two_across_max_length_ft,
        DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT,
    )
    upper_positions = [
        pos for pos in (positions or [])
        if (pos.get("deck") or "lower") == "upper"
    ]

    metadata = {}
    for pos in upper_positions:
        position_id = pos.get("position_id") or ""
        length_ft = _coerce_non_negative_float(pos.get("length_ft"), 0.0)
        upper_capacity_used = _upper_capacity_used_for_position(pos)
        required_stacks = max(int(math.ceil(max(upper_capacity_used - 1e-9, 0.0))), 1)
        two_across_eligible = threshold > 0 and length_ft <= (threshold + 1e-6)
        metadata[position_id] = {
            "position_id": position_id,
            "length_ft": length_ft,
            "upper_capacity_used": upper_capacity_used,
            "upper_required_stack_count": required_stacks,
            "upper_two_across_eligible": bool(two_across_eligible),
            "two_across_applied": False,
            "paired_slot_count": 0,
            "effective_length_ft": 0.0,
        }

    for position_id, meta in metadata.items():
        required = max(_coerce_non_negative_int(meta["upper_required_stack_count"], 1), 1)
        length_ft = _coerce_non_negative_float(meta["length_ft"], 0.0)
        if not meta["upper_two_across_eligible"] or required <= 1:
            meta["effective_length_ft"] = length_ft * required
            meta["two_across_applied"] = False
            meta["paired_slot_count"] = 0
            continue

        # Two-across is modeled inside a single position (not across sibling positions).
        # Required upper stacks compress by half (rounded up) for <= threshold lengths.
        paired_slot_count = required // 2
        compressed_stacks = int(math.ceil(required / 2.0))
        meta["effective_length_ft"] = length_ft * compressed_stacks
        meta["two_across_applied"] = paired_slot_count > 0
        meta["paired_slot_count"] = paired_slot_count

    effective_total_length = sum(meta["effective_length_ft"] for meta in metadata.values())
    raw_total_length = sum(meta["length_ft"] for meta in metadata.values())
    return {
        "by_position_id": metadata,
        "effective_total_length_ft": effective_total_length,
        "raw_total_length_ft": raw_total_length,
        "threshold_ft": threshold,
    }


def _two_across_group_key(item):
    stop_sequence = _coerce_stop_sequence(item.get("stop_sequence"))
    if stop_sequence is not None:
        return f"stop:{stop_sequence}"
    order_id = str(item.get("order_id") or "").strip()
    if order_id:
        return f"order:{order_id}"
    sku = str(item.get("sku") or "").strip()
    if sku:
        return f"sku:{sku}"
    return None


def _assign_two_across_item_distribution(position):
    items = list(position.get("items") or [])
    if not items:
        return

    if not position.get("two_across_applied"):
        for item in items:
            units = max(_coerce_non_negative_int(item.get("units"), 0), 0)
            item["two_across_left_units"] = units
            item["two_across_right_units"] = 0
            item["two_across_split"] = False
        return

    total_units = sum(max(_coerce_non_negative_int(item.get("units"), 0), 0) for item in items)
    if total_units <= 0:
        for item in items:
            item["two_across_left_units"] = 0
            item["two_across_right_units"] = 0
            item["two_across_split"] = False
        return

    # Auto layouts should bias right stack >= left stack while keeping stop/order groups together.
    left_target = total_units // 2
    right_target = total_units - left_target
    left_remaining = left_target
    right_remaining = right_target
    preferred_side_by_group = {}

    for item in items:
        units = max(_coerce_non_negative_int(item.get("units"), 0), 0)
        if units <= 0:
            item["two_across_left_units"] = 0
            item["two_across_right_units"] = 0
            item["two_across_split"] = False
            continue

        group_key = _two_across_group_key(item)
        preferred_side = preferred_side_by_group.get(group_key)
        if preferred_side not in {"left", "right"}:
            preferred_side = "right" if right_remaining >= left_remaining else "left"
        secondary_side = "left" if preferred_side == "right" else "right"

        preferred_remaining = right_remaining if preferred_side == "right" else left_remaining
        secondary_remaining = left_remaining if preferred_side == "right" else right_remaining

        preferred_units = 0
        secondary_units = 0

        if units <= preferred_remaining:
            preferred_units = units
        elif units <= secondary_remaining:
            preferred_side, secondary_side = secondary_side, preferred_side
            preferred_remaining, secondary_remaining = secondary_remaining, preferred_remaining
            preferred_units = units
        else:
            preferred_units = min(units, preferred_remaining)
            secondary_units = units - preferred_units
            if secondary_units > secondary_remaining:
                overflow = secondary_units - secondary_remaining
                secondary_units = secondary_remaining
                preferred_units += overflow

        left_units = preferred_units if preferred_side == "left" else secondary_units
        right_units = preferred_units if preferred_side == "right" else secondary_units

        left_units = max(min(left_units, left_remaining), 0)
        right_units = max(min(right_units, right_remaining), 0)
        assigned = left_units + right_units
        if assigned < units:
            deficit = units - assigned
            if right_remaining - right_units >= left_remaining - left_units:
                add_right = min(deficit, right_remaining - right_units)
                right_units += add_right
                deficit -= add_right
            if deficit > 0:
                add_left = min(deficit, left_remaining - left_units)
                left_units += add_left

        left_remaining -= left_units
        right_remaining -= right_units

        item["two_across_left_units"] = max(left_units, 0)
        item["two_across_right_units"] = max(right_units, 0)
        item["two_across_split"] = bool(left_units and right_units)

        if group_key:
            dominant_side = "right" if right_units >= left_units else "left"
            preferred_side_by_group.setdefault(group_key, dominant_side)

    # Guarantee right >= left in auto distribution after rounding/edge handling.
    if right_target < left_target:
        swap_pairs = [
            (item, item.get("two_across_left_units", 0), item.get("two_across_right_units", 0))
            for item in items
        ]
        for item, left_units, right_units in swap_pairs:
            item["two_across_left_units"] = right_units
            item["two_across_right_units"] = left_units
            item["two_across_split"] = bool(left_units and right_units)


def _enforce_upper_two_across_exclusive_deck_usage(
    positions,
    trailer_config,
    two_across_max_length_ft,
):
    usage = _apply_upper_usage_metadata(
        positions,
        trailer_config,
        two_across_max_length_ft,
    )
    active_two_across = [
        pos
        for pos in (positions or [])
        if (pos.get("deck") or "lower") == "upper" and bool(pos.get("two_across_applied"))
    ]
    if not active_two_across:
        return usage

    active_two_across.sort(
        key=lambda pos: (
            -_coerce_non_negative_int(pos.get("upper_required_stack_count"), 1),
            -_coerce_non_negative_float(pos.get("length_ft"), 0.0),
            pos.get("position_id") or "",
        )
    )
    keep_position_id = active_two_across[0].get("position_id") or ""
    changed = False
    for pos in (positions or []):
        if (pos.get("deck") or "lower") != "upper":
            continue
        if (pos.get("position_id") or "") == keep_position_id:
            continue
        pos["deck"] = "lower"
        changed = True

    if changed:
        usage = _apply_upper_usage_metadata(
            positions,
            trailer_config,
            two_across_max_length_ft,
        )
    return usage


def _apply_upper_usage_metadata(positions, trailer_config, two_across_max_length_ft):
    threshold = _coerce_non_negative_float(
        two_across_max_length_ft,
        DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT,
    )
    trailer_type = normalize_trailer_type(trailer_config.get("type"), default="STEP_DECK")
    has_step_deck_upper = trailer_type.startswith("STEP_DECK") and float(trailer_config.get("upper") or 0.0) > 0

    if not has_step_deck_upper:
        for pos in positions or []:
            pos["effective_length_ft"] = _coerce_non_negative_float(pos.get("length_ft"), 0.0)
            pos["upper_capacity_used"] = _coerce_non_negative_float(pos.get("capacity_used"), 0.0)
            pos["upper_required_stack_count"] = 1
            pos["upper_two_across_eligible"] = False
            pos["two_across_applied"] = False
            pos["paired_slot_count"] = 0
            pos["two_across_note"] = ""
            _assign_two_across_item_distribution(pos)
        return {
            "effective_total_length_ft": 0.0,
            "raw_total_length_ft": 0.0,
            "threshold_ft": threshold,
            "paired_positions": 0,
        }

    usage = _compute_upper_usage_metadata(positions, threshold)
    by_position_id = usage["by_position_id"]
    paired_positions = 0
    for pos in positions or []:
        position_id = pos.get("position_id") or ""
        if (pos.get("deck") or "lower") != "upper":
            pos["effective_length_ft"] = _coerce_non_negative_float(pos.get("length_ft"), 0.0)
            pos["upper_capacity_used"] = _coerce_non_negative_float(pos.get("capacity_used"), 0.0)
            pos["upper_required_stack_count"] = 1
            pos["upper_two_across_eligible"] = False
            pos["two_across_applied"] = False
            pos["paired_slot_count"] = 0
            pos["two_across_note"] = ""
            _assign_two_across_item_distribution(pos)
            continue
        meta = by_position_id.get(position_id) or {}
        pos["upper_capacity_used"] = round(
            _coerce_non_negative_float(meta.get("upper_capacity_used"), pos.get("capacity_used")),
            6,
        )
        pos["capacity_used"] = pos["upper_capacity_used"]
        pos["upper_required_stack_count"] = max(
            _coerce_non_negative_int(meta.get("upper_required_stack_count"), 1),
            1,
        )
        pos["upper_two_across_eligible"] = bool(meta.get("upper_two_across_eligible"))
        pos["two_across_applied"] = bool(meta.get("two_across_applied"))
        pos["paired_slot_count"] = _coerce_non_negative_int(meta.get("paired_slot_count"), 0)
        if pos["two_across_applied"]:
            paired_positions += 1
        pos["effective_length_ft"] = round(
            _coerce_non_negative_float(meta.get("effective_length_ft"), pos.get("length_ft")),
            6,
        )
        pos["two_across_note"] = (
            f"items less than {usage['threshold_ft']:g} ft stacked 2 across to allow for additional capacity"
            if pos["two_across_applied"]
            else ""
        )
        _assign_two_across_item_distribution(pos)
    return {
        "effective_total_length_ft": usage["effective_total_length_ft"],
        "raw_total_length_ft": usage["raw_total_length_ft"],
        "threshold_ft": usage["threshold_ft"],
        "paired_positions": paired_positions,
    }


def apply_upper_usage_metadata(positions, trailer_config, two_across_max_length_ft):
    return _apply_upper_usage_metadata(
        positions,
        trailer_config,
        two_across_max_length_ft,
    )


def _position_categories(position):
    categories = set()
    for item in (position or {}).get("items") or []:
        category = str(item.get("category") or "").strip().upper()
        if category:
            categories.add(category)
    return categories


def upper_deck_position_length_limit_ft(
    position,
    trailer_config,
    upper_deck_exception_max_length_ft,
    upper_deck_exception_categories,
):
    upper_length = _coerce_non_negative_float((trailer_config or {}).get("upper"), 0.0)
    trailer_type = normalize_trailer_type((trailer_config or {}).get("type"), default="STEP_DECK")
    if not trailer_type.startswith("STEP_DECK") or upper_length <= 0:
        return upper_length

    exception_max = _coerce_non_negative_float(
        upper_deck_exception_max_length_ft,
        DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT,
    )
    allowed_categories = set(
        _normalize_upper_deck_exception_categories(
            upper_deck_exception_categories,
            default=DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES,
        )
    )
    categories = _position_categories(position)
    if categories and categories.issubset(allowed_categories):
        return max(upper_length, exception_max)
    return upper_length


def is_upper_deck_exception_eligible_position(
    position,
    trailer_config,
    upper_deck_exception_max_length_ft,
    upper_deck_exception_categories,
):
    if not isinstance(position, dict):
        return False
    length_ft = _coerce_non_negative_float(position.get("length_ft"), 0.0)
    upper_length = _coerce_non_negative_float((trailer_config or {}).get("upper"), 0.0)
    if length_ft <= (upper_length + 1e-6):
        return False
    limit_ft = upper_deck_position_length_limit_ft(
        position,
        trailer_config,
        upper_deck_exception_max_length_ft,
        upper_deck_exception_categories,
    )
    return length_ft <= (limit_ft + 1e-6)


def evaluate_upper_deck_overhang(
    positions,
    trailer_config,
    max_back_overhang_ft,
    upper_deck_exception_max_length_ft,
    upper_deck_exception_overhang_allowance_ft,
    upper_deck_exception_categories,
):
    upper_length = _coerce_non_negative_float((trailer_config or {}).get("upper"), 0.0)
    base_allowance = _coerce_non_negative_float(max_back_overhang_ft, DEFAULT_MAX_BACK_OVERHANG_FT)
    exception_allowance = _coerce_non_negative_float(
        upper_deck_exception_overhang_allowance_ft,
        DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT,
    )
    if upper_length <= 0:
        return {
            "upper_length_ft": 0.0,
            "upper_total_ft": 0.0,
            "upper_overhang_ft": 0.0,
            "eligible_total_ft": 0.0,
            "allowed_overhang_ft": base_allowance,
            "base_allowance_ft": base_allowance,
            "exception_allowance_ft": exception_allowance,
            "eligible_extra_allowance_ft": 0.0,
        }

    _, upper_total = _deck_usage_totals(positions or [], use_effective_upper=True)
    upper_total = _coerce_non_negative_float(upper_total, 0.0)
    upper_overhang = max(upper_total - upper_length, 0.0)

    eligible_total = 0.0
    for pos in positions or []:
        if (pos.get("deck") or "lower") != "upper":
            continue
        if not is_upper_deck_exception_eligible_position(
            pos,
            trailer_config,
            upper_deck_exception_max_length_ft,
            upper_deck_exception_categories,
        ):
            continue
        eligible_total += _coerce_non_negative_float(
            pos.get("effective_length_ft"),
            pos.get("length_ft"),
        )

    extra_cap = max(exception_allowance - base_allowance, 0.0)
    eligible_overhang_potential = max(eligible_total - upper_length, 0.0)
    eligible_extra_allowance = min(eligible_overhang_potential, extra_cap)
    allowed_overhang = base_allowance + eligible_extra_allowance

    return {
        "upper_length_ft": upper_length,
        "upper_total_ft": upper_total,
        "upper_overhang_ft": upper_overhang,
        "eligible_total_ft": eligible_total,
        "allowed_overhang_ft": allowed_overhang,
        "base_allowance_ft": base_allowance,
        "exception_allowance_ft": exception_allowance,
        "eligible_extra_allowance_ft": eligible_extra_allowance,
    }


def _calculate_total_credit_feet(positions, trailer_config, max_stack_utilization_multiplier):
    lower_credit = 0.0
    upper_credit_raw = 0.0
    upper_length_used = 0.0

    for pos in positions or []:
        deck = (pos.get("deck") or "lower")
        length_ft = float(pos.get("length_ft") or 0.0)
        effective_length_ft = float(pos.get("effective_length_ft") or length_ft)
        multiplier = _position_credit_multiplier(pos, max_stack_utilization_multiplier)
        credit = (effective_length_ft if deck == "upper" else length_ft) * multiplier
        if deck == "upper":
            upper_credit_raw += credit
            upper_length_used += effective_length_ft
        else:
            lower_credit += credit

    upper_credit = upper_credit_raw
    trailer_type = normalize_trailer_type(trailer_config.get("type"), default="STEP_DECK")
    upper_length = float(trailer_config.get("upper") or 0.0)
    if (
        trailer_type.startswith("STEP_DECK")
        and upper_length > 0
        and upper_length_used > 0
        and upper_length_used < (upper_length - 1e-6)
    ):
        # Normalize occupied upper-deck stacks to the full 10' basis.
        upper_credit *= (upper_length / upper_length_used)

    return lower_credit + upper_credit


def _deck_usage_totals(positions, use_effective_upper=False):
    lower_total = sum(
        pos.get("length_ft") or 0 for pos in positions if (pos.get("deck") or "lower") == "lower"
    )
    upper_total = sum(
        (
            (pos.get("effective_length_ft") if use_effective_upper else pos.get("length_ft"))
            or 0
        )
        for pos in positions
        if (pos.get("deck") or "lower") == "upper"
    )
    return lower_total, upper_total


def capacity_overflow_feet(stack_config):
    if not isinstance(stack_config, dict):
        return 0.0

    positions = stack_config.get("positions") or []
    lower_length = float(stack_config.get("lower_deck_length") or 0.0)
    upper_length = float(stack_config.get("upper_deck_length") or 0.0)
    allowed_overhang = _coerce_non_negative_float(
        stack_config.get("max_back_overhang_ft"),
        DEFAULT_MAX_BACK_OVERHANG_FT,
    )
    upper_deck_exception_max_length_ft = _coerce_non_negative_float(
        stack_config.get("upper_deck_exception_max_length_ft"),
        DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT,
    )
    upper_deck_exception_overhang_allowance_ft = _coerce_non_negative_float(
        stack_config.get("upper_deck_exception_overhang_allowance_ft"),
        DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT,
    )
    upper_deck_exception_categories = _normalize_upper_deck_exception_categories(
        stack_config.get("upper_deck_exception_categories"),
        default=DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES,
    )

    if upper_length <= 0:
        total_length = sum(float(pos.get("length_ft") or 0.0) for pos in positions)
        return round(max(total_length - (lower_length + allowed_overhang), 0.0), 4)

    lower_total, _ = _deck_usage_totals(positions, use_effective_upper=True)
    lower_over = max(lower_total - (lower_length + allowed_overhang), 0.0)
    upper_eval = evaluate_upper_deck_overhang(
        positions,
        {"type": stack_config.get("trailer_type") or "STEP_DECK", "upper": upper_length},
        max_back_overhang_ft=allowed_overhang,
        upper_deck_exception_max_length_ft=upper_deck_exception_max_length_ft,
        upper_deck_exception_overhang_allowance_ft=upper_deck_exception_overhang_allowance_ft,
        upper_deck_exception_categories=upper_deck_exception_categories,
    )
    upper_over = max(
        upper_eval["upper_overhang_ft"] - upper_eval["allowed_overhang_ft"],
        0.0,
    )
    return round(lower_over + upper_over, 4)


def _build_capacity_warnings(
    positions,
    trailer_config,
    stack_overflow_max_height,
    max_back_overhang_ft,
    upper_deck_exception_max_length_ft,
    upper_deck_exception_overhang_allowance_ft,
    upper_deck_exception_categories,
):
    warnings = []
    stack_index_by_position_id = stack_display_index_map(positions, trailer_config)
    max_stack_utilization = _max_stack_utilization_multiplier(stack_overflow_max_height)
    for idx, pos in enumerate(positions, start=1):
        capacity_used = float(pos.get("capacity_used") or 0.0)
        deck = (pos.get("deck") or "lower").strip().lower() or "lower"
        position_id = pos.get("position_id") or f"p{idx}"
        stack_idx = int(stack_index_by_position_id.get(position_id, idx))
        two_across_applied = bool(pos.get("two_across_applied")) and deck == "upper"
        overflow_note = pos.get("overflow_note")
        if two_across_applied:
            continue
        if capacity_used > (max_stack_utilization + 1e-6):
            warnings.append(
                _warning_payload(
                    "STACK_TOO_HIGH",
                    f"Stack {stack_idx} is {capacity_used * 100:.0f}% overfilled relative to SKU-specific stacking maximums.",
                    deck=deck,
                    position_id=position_id,
                )
            )
        elif capacity_used > (1.0 + 1e-6):
            if pos.get("overflow_applied"):
                message = overflow_note or (
                    f"Stack {stack_idx} is {capacity_used * 100:.0f}% overfilled relative to "
                    "SKU-specific stacking maximums."
                )
                pos["overflow_note"] = message
                warnings.append(
                    _warning_payload(
                        "STACK_OVERFLOW_ALLOWANCE_USED",
                        message,
                        deck=deck,
                        position_id=position_id,
                    )
                )
            else:
                warnings.append(
                    _warning_payload(
                        "STACK_TOO_HIGH",
                        f"Stack {stack_idx} is {capacity_used * 100:.0f}% overfilled relative to SKU-specific stacking maximums.",
                        deck=deck,
                        position_id=position_id,
                    )
                )

    lower_length = trailer_config.get("lower") or 0.0
    upper_length = trailer_config.get("upper") or 0.0
    overhang_allowance = _coerce_non_negative_float(max_back_overhang_ft, DEFAULT_MAX_BACK_OVERHANG_FT)
    lower_total, _ = _deck_usage_totals(positions, use_effective_upper=True)

    def _append_overhang_warnings(deck_label, overhang_ft, deck_key, allowance_ft):
        if overhang_ft <= 0.05:
            return
        if overhang_ft <= (allowance_ft + 1e-6):
            warnings.append(
                _warning_payload(
                    "BACK_OVERHANG_IN_ALLOWANCE",
                    (
                        f"{deck_label} deck back overhang is {overhang_ft:.1f} ft "
                        f"(allowance {allowance_ft:.1f} ft)."
                    ),
                    deck=deck_key,
                )
            )
            return
        exceed_by = overhang_ft - allowance_ft
        warnings.append(
            _warning_payload(
                "ITEM_HANGS_OVER_DECK",
                (
                    f"{deck_label} deck back overhang is {overhang_ft:.1f} ft, "
                    f"exceeding allowance by {exceed_by:.1f} ft."
                ),
                deck=deck_key,
            )
        )

    _append_overhang_warnings(
        "Lower",
        max(lower_total - lower_length, 0.0),
        "lower",
        overhang_allowance,
    )
    if upper_length > 0:
        upper_eval = evaluate_upper_deck_overhang(
            positions,
            trailer_config,
            max_back_overhang_ft=max_back_overhang_ft,
            upper_deck_exception_max_length_ft=upper_deck_exception_max_length_ft,
            upper_deck_exception_overhang_allowance_ft=upper_deck_exception_overhang_allowance_ft,
            upper_deck_exception_categories=upper_deck_exception_categories,
        )
        _append_overhang_warnings(
            "Upper",
            upper_eval["upper_overhang_ft"],
            "upper",
            upper_eval["allowed_overhang_ft"],
        )

    return warnings


def calculate_stack_configuration(
    order_lines,
    trailer_type="STEP_DECK",
    capacity_feet=None,
    preserve_order_contiguity=True,
    stack_overflow_max_height=None,
    max_back_overhang_ft=None,
    upper_two_across_max_length_ft=None,
    upper_deck_exception_max_length_ft=None,
    upper_deck_exception_overhang_allowance_ft=None,
    upper_deck_exception_categories=None,
):
    defaults = get_stack_capacity_assumptions()
    stack_overflow_max_height = _coerce_non_negative_int(
        defaults["stack_overflow_max_height"]
        if stack_overflow_max_height is None
        else stack_overflow_max_height,
        defaults["stack_overflow_max_height"],
    )
    max_back_overhang_ft = round(
        _coerce_non_negative_float(
            defaults["max_back_overhang_ft"]
            if max_back_overhang_ft is None
            else max_back_overhang_ft,
            defaults["max_back_overhang_ft"],
        ),
        2,
    )
    upper_two_across_max_length_ft = round(
        _coerce_non_negative_float(
            defaults["upper_two_across_max_length_ft"]
            if upper_two_across_max_length_ft is None
            else upper_two_across_max_length_ft,
            defaults["upper_two_across_max_length_ft"],
        ),
        2,
    )
    upper_deck_exception_max_length_ft = round(
        _coerce_non_negative_float(
            defaults["upper_deck_exception_max_length_ft"]
            if upper_deck_exception_max_length_ft is None
            else upper_deck_exception_max_length_ft,
            defaults["upper_deck_exception_max_length_ft"],
        ),
        2,
    )
    upper_deck_exception_overhang_allowance_ft = round(
        _coerce_non_negative_float(
            defaults["upper_deck_exception_overhang_allowance_ft"]
            if upper_deck_exception_overhang_allowance_ft is None
            else upper_deck_exception_overhang_allowance_ft,
            defaults["upper_deck_exception_overhang_allowance_ft"],
        ),
        2,
    )
    upper_deck_exception_categories = _normalize_upper_deck_exception_categories(
        defaults["upper_deck_exception_categories"]
        if upper_deck_exception_categories is None
        else upper_deck_exception_categories,
        default=defaults["upper_deck_exception_categories"],
    )
    max_stack_utilization_multiplier = _max_stack_utilization_multiplier(
        stack_overflow_max_height
    )

    if not order_lines:
        return {
            "positions": [],
            "total_linear_feet": 0,
            "utilization_pct": 0,
            "max_stack_height": 0,
            "compatibility_issues": [],
            "exceeds_capacity": False,
            "utilization_credit_ft": 0,
            "utilization_grade": "F",
            "warnings": [],
            "stack_overflow_max_height": stack_overflow_max_height,
            "max_back_overhang_ft": max_back_overhang_ft,
            "upper_two_across_max_length_ft": upper_two_across_max_length_ft,
            "upper_deck_exception_max_length_ft": upper_deck_exception_max_length_ft,
            "upper_deck_exception_overhang_allowance_ft": upper_deck_exception_overhang_allowance_ft,
            "upper_deck_exception_categories": list(upper_deck_exception_categories),
            "max_stack_utilization_multiplier": round(
                max_stack_utilization_multiplier, 4
            ),
        }

    positions = []
    has_order_ids = any(item.get("order_id") for item in order_lines)

    if not has_order_ids or not preserve_order_contiguity:
        def _sort_key(item):
            stop_sequence = _coerce_stop_sequence(item.get("stop_sequence"))
            if stop_sequence is None:
                stop_sequence = 0
            return (
                stop_sequence,
                item.get("unit_length_ft") or 0,
                item.get("max_stack_height") or 0,
            )

        sorted_items = sorted(
            order_lines,
            key=_sort_key,
            reverse=True,
        )

        for item in sorted_items:
            qty_remaining = item["qty"]
            max_stack = item["max_stack_height"] or 1
            upper_max_stack = item.get("upper_deck_max_stack_height") or max_stack
            length_ft = item["unit_length_ft"] or 0
            item_stop_sequence = _coerce_stop_sequence(item.get("stop_sequence"))

            while qty_remaining > 0:
                candidates = []
                for pos_idx, pos in enumerate(positions):
                    if (
                        pos["length_ft"] >= length_ft
                        and _length_stack_compatible(pos, length_ft)
                        and pos["capacity_used"] < (1.0 - 1e-6)
                        and _stop_access_compatible(pos, item_stop_sequence)
                    ):
                        candidates.append((pos_idx, pos))
                if candidates:
                    # Keep stack fill direction deterministic by stable column index.
                    candidates.sort(
                        key=lambda entry: (
                            entry[0],
                            entry[1]["length_ft"],
                            -(1.0 - entry[1]["capacity_used"]),
                        )
                    )
                    target = candidates[0][1]
                else:
                    target = {
                        "length_ft": length_ft,
                        "items": [],
                        "capacity_used": 0.0,
                        "overflow_units_used": 0,
                        "overflow_applied": False,
                        "units_count": 0,
                        "top_stop_sequence": None,
                        "top_length_ft": length_ft,
                    }
                    positions.append(target)

                target.setdefault("overflow_units_used", 0)
                target.setdefault("overflow_applied", False)
                capacity_available = max(1.0 - target["capacity_used"], 0.0)
                max_units_that_fit = int((capacity_available * max_stack) + 1e-9)

                if max_units_that_fit <= 0:
                    target["capacity_used"] = 1.0
                    continue

                units_to_add = min(qty_remaining, max_units_that_fit)
                capacity_fraction = units_to_add / max_stack
                target["items"].append(
                    {
                        "item": item["item"],
                        "sku": item["sku"],
                        "item_desc": item.get("item_desc") or item.get("desc"),
                        "category": item.get("category", "UNKNOWN"),
                        "units": units_to_add,
                        "max_stack": max_stack,
                        "upper_max_stack": max(_coerce_non_negative_int(upper_max_stack, max_stack), 1),
                        "unit_length_ft": length_ft,
                        "order_id": item.get("order_id"),
                        "stop_sequence": item_stop_sequence,
                    }
                )
                target["capacity_used"] += capacity_fraction
                if target["capacity_used"] >= (1.0 - 1e-6):
                    target["capacity_used"] = 1.0
                target["units_count"] += units_to_add
                if item_stop_sequence is not None:
                    target["top_stop_sequence"] = item_stop_sequence
                target["top_length_ft"] = min(
                    target.get("top_length_ft") or target.get("length_ft") or length_ft,
                    length_ft,
                )
                qty_remaining -= units_to_add
    else:
        order_buckets = {}
        for line in order_lines:
            order_id = line.get("order_id") or "__UNSPECIFIED__"
            order_buckets.setdefault(order_id, []).append(line)

        # Fill by stable column index while keeping each order contiguous in the schematic.
        # Overflow consolidation is handled in a dedicated post-pass.
        cursor = 0
        for order_id, items in order_buckets.items():
            sorted_items = sorted(
                items,
                key=lambda x: (x.get("unit_length_ft") or 0, x.get("max_stack_height") or 0),
                reverse=True,
            )
            for item in sorted_items:
                qty_remaining = item["qty"]
                max_stack = item["max_stack_height"] or 1
                upper_max_stack = item.get("upper_deck_max_stack_height") or max_stack
                length_ft = item["unit_length_ft"] or 0
                item_stop_sequence = _coerce_stop_sequence(item.get("stop_sequence"))

                while qty_remaining > 0:
                    if cursor >= len(positions):
                        positions.append(
                            {
                                "length_ft": length_ft,
                                "items": [],
                                "capacity_used": 0.0,
                                "overflow_units_used": 0,
                                "overflow_applied": False,
                                "units_count": 0,
                                "top_stop_sequence": None,
                                "top_length_ft": length_ft,
                            }
                        )

                    target = positions[cursor]
                    if target["length_ft"] < length_ft:
                        cursor += 1
                        continue
                    if not _length_stack_compatible(target, length_ft):
                        cursor += 1
                        continue
                    if not _stop_access_compatible(target, item_stop_sequence):
                        cursor += 1
                        continue

                    target.setdefault("overflow_units_used", 0)
                    target.setdefault("overflow_applied", False)
                    capacity_available = max(1.0 - target["capacity_used"], 0.0)
                    max_units_that_fit = int((capacity_available * max_stack) + 1e-9)

                    if max_units_that_fit <= 0:
                        target["capacity_used"] = 1.0
                        cursor += 1
                        continue

                    units_to_add = min(qty_remaining, max_units_that_fit)
                    capacity_fraction = units_to_add / max_stack
                    target["items"].append(
                        {
                            "item": item["item"],
                            "sku": item["sku"],
                            "item_desc": item.get("item_desc") or item.get("desc"),
                            "category": item.get("category", "UNKNOWN"),
                            "units": units_to_add,
                            "max_stack": max_stack,
                            "upper_max_stack": max(_coerce_non_negative_int(upper_max_stack, max_stack), 1),
                            "unit_length_ft": length_ft,
                            "order_id": order_id,
                            "stop_sequence": item_stop_sequence,
                        }
                    )
                    target["capacity_used"] += capacity_fraction
                    if target["capacity_used"] >= (1.0 - 1e-6):
                        target["capacity_used"] = 1.0
                    target["units_count"] += units_to_add
                    if item_stop_sequence is not None:
                        target["top_stop_sequence"] = item_stop_sequence
                    target["top_length_ft"] = min(
                        target.get("top_length_ft") or target.get("length_ft") or length_ft,
                        length_ft,
                    )
                    qty_remaining -= units_to_add

                    if (1.0 - target["capacity_used"]) < 0.01:
                        target["capacity_used"] = 1.0
                        cursor += 1

    _apply_singleton_overflow_allowance(
        positions,
        stack_overflow_max_height=stack_overflow_max_height,
        max_stack_utilization_multiplier=max_stack_utilization_multiplier,
    )

    # Keep schematic columns deterministic by earliest accessible stop first.
    # UI renders right-to-left, so earliest stops land on the right/rear.
    # Tie-break by manifest-like order among same-stop orders.
    order_rank = _build_order_rank(order_lines if has_order_ids else [])
    positions = sorted(
        positions,
        key=lambda pos: (
            _position_stop_priority(pos),
            _position_order_priority(pos, order_rank),
        ),
    )
    for idx, pos in enumerate(positions, start=1):
        pos["position_id"] = f"p{idx}"
        pos.setdefault("overflow_applied", False)

    trailer_config = _resolve_trailer_config(trailer_type, capacity_feet)
    upper_length = trailer_config["upper"]
    lower_length = trailer_config["lower"]
    capacity = trailer_config["capacity"]

    for pos in positions:
        pos["deck"] = "lower"

    upper_usage_meta = {
        "effective_total_length_ft": 0.0,
        "raw_total_length_ft": 0.0,
        "threshold_ft": upper_two_across_max_length_ft,
        "paired_positions": 0,
    }

    if upper_length > 0:
        def _upper_candidate_length_limit(pos):
            return upper_deck_position_length_limit_ft(
                pos,
                trailer_config,
                upper_deck_exception_max_length_ft,
                upper_deck_exception_categories,
            )

        def _upper_effective_limit():
            upper_eval = evaluate_upper_deck_overhang(
                positions,
                trailer_config,
                max_back_overhang_ft=0.0,
                upper_deck_exception_max_length_ft=upper_deck_exception_max_length_ft,
                upper_deck_exception_overhang_allowance_ft=upper_deck_exception_overhang_allowance_ft,
                upper_deck_exception_categories=upper_deck_exception_categories,
            )
            return upper_length + upper_eval["allowed_overhang_ft"]

        def _standard_upper_raw_total():
            return sum(
                _coerce_non_negative_float(pos.get("length_ft"), 0.0)
                for pos in positions
                if (pos.get("deck") or "lower") == "upper"
                and _coerce_non_negative_float(pos.get("length_ft"), 0.0) <= (upper_length + 1e-6)
            )

        upper_candidates = [
            pos
            for pos in positions
            if _coerce_non_negative_float(pos.get("length_ft"), 0.0)
            <= (_upper_candidate_length_limit(pos) + 1e-6)
        ]

        def _upper_candidate_priority(pos):
            length_ft = _coerce_non_negative_float(pos.get("length_ft"), 0.0)
            required_stacks = max(
                int(math.ceil(max(_upper_capacity_used_for_position(pos) - 1e-9, 0.0))),
                1,
            )
            two_across_eligible = (
                upper_two_across_max_length_ft > 0
                and length_ft <= (upper_two_across_max_length_ft + 1e-6)
            )
            # Prioritize short candidates that can exploit two-across on upper deck.
            # This tends to preserve lower-deck room while increasing upper packing density.
            two_across_gain = (required_stacks - 1) if two_across_eligible else 0
            return (
                two_across_gain,
                required_stacks,
                length_ft,
            )

        upper_candidates.sort(key=_upper_candidate_priority, reverse=True)
        for pos in upper_candidates:
            pos["deck"] = "upper"
            candidate_meta = _apply_upper_usage_metadata(
                positions,
                trailer_config,
                upper_two_across_max_length_ft,
            )
            if (
                candidate_meta["effective_total_length_ft"] <= (_upper_effective_limit() + 1e-6)
                and _standard_upper_raw_total() <= (upper_length + 1e-6)
            ):
                upper_usage_meta = candidate_meta
                continue
            pos["deck"] = "lower"
            upper_usage_meta = _apply_upper_usage_metadata(
                positions,
                trailer_config,
                upper_two_across_max_length_ft,
            )

        upper_usage_meta = _apply_upper_usage_metadata(
            positions,
            trailer_config,
            upper_two_across_max_length_ft,
        )
        if normalize_trailer_type(trailer_config.get("type"), default="STEP_DECK").startswith("STEP_DECK"):
            while (
                upper_usage_meta["effective_total_length_ft"] > (_upper_effective_limit() + 1e-6)
                or _standard_upper_raw_total() > (upper_length + 1e-6)
            ):
                active_upper_positions = [
                    pos for pos in positions
                    if (pos.get("deck") or "lower") == "upper"
                ]
                if not active_upper_positions:
                    break
                active_upper_positions.sort(
                    key=lambda pos: (
                        -_coerce_non_negative_float(
                            pos.get("effective_length_ft"),
                            pos.get("length_ft"),
                        ),
                        -_coerce_non_negative_float(pos.get("length_ft"), 0.0),
                        pos.get("position_id") or "",
                    )
                )
                active_upper_positions[0]["deck"] = "lower"
                upper_usage_meta = _apply_upper_usage_metadata(
                    positions,
                    trailer_config,
                    upper_two_across_max_length_ft,
                )

            promotable = sorted(
                [
                    pos for pos in positions
                    if (pos.get("deck") or "lower") == "lower"
                    and _coerce_non_negative_float(pos.get("length_ft"), 0.0)
                    <= (_upper_candidate_length_limit(pos) + 1e-6)
                ],
                key=lambda pos: _coerce_non_negative_float(pos.get("length_ft"), 0.0),
                reverse=True,
            )
            for pos in promotable:
                pos["deck"] = "upper"
                candidate_meta = _apply_upper_usage_metadata(
                    positions,
                    trailer_config,
                    upper_two_across_max_length_ft,
                )
                if (
                    candidate_meta["effective_total_length_ft"] <= (_upper_effective_limit() + 1e-6)
                    and _standard_upper_raw_total() <= (upper_length + 1e-6)
                ):
                    upper_usage_meta = candidate_meta
                    continue
                pos["deck"] = "lower"
                upper_usage_meta = _apply_upper_usage_metadata(
                    positions,
                    trailer_config,
                    upper_two_across_max_length_ft,
                )
        upper_usage_meta = _enforce_upper_two_across_exclusive_deck_usage(
            positions,
            trailer_config,
            upper_two_across_max_length_ft,
        )
    else:
        upper_usage_meta = _apply_upper_usage_metadata(
            positions,
            trailer_config,
            upper_two_across_max_length_ft,
        )

    for pos in positions:
        _promote_high_side_items_within_equal_length(pos)
        deck_length = upper_length if pos["deck"] == "upper" else lower_length
        length_for_width = (
            _coerce_non_negative_float(
                pos.get("effective_length_ft"),
                pos.get("length_ft"),
            )
            if pos["deck"] == "upper"
            else _coerce_non_negative_float(pos.get("length_ft"), 0.0)
        )
        if deck_length:
            pos["width_pct"] = min(round((length_for_width / deck_length) * 100, 1), 100)
        else:
            pos["width_pct"] = 0

    lower_total_linear, upper_total_linear_effective = _deck_usage_totals(
        positions,
        use_effective_upper=True,
    )
    _, upper_total_linear_raw = _deck_usage_totals(
        positions,
        use_effective_upper=False,
    )
    total_linear_feet = lower_total_linear + upper_total_linear_effective
    total_credit_feet = _calculate_total_credit_feet(
        positions,
        trailer_config,
        max_stack_utilization_multiplier,
    )
    utilization_pct = (total_credit_feet / capacity) * 100 if total_credit_feet else 0
    max_stack_height = max((pos["units_count"] for pos in positions), default=0)
    compatibility_issues = check_stacking_compatibility(positions, trailer_config=trailer_config)
    exceeds_capacity = _exceeds_capacity(
        positions,
        trailer_config,
        max_back_overhang_ft=max_back_overhang_ft,
        upper_deck_exception_max_length_ft=upper_deck_exception_max_length_ft,
        upper_deck_exception_overhang_allowance_ft=upper_deck_exception_overhang_allowance_ft,
        upper_deck_exception_categories=upper_deck_exception_categories,
    )
    utilization_grade = _grade_utilization(utilization_pct)
    warnings = _build_capacity_warnings(
        positions,
        trailer_config,
        stack_overflow_max_height=stack_overflow_max_height,
        max_back_overhang_ft=max_back_overhang_ft,
        upper_deck_exception_max_length_ft=upper_deck_exception_max_length_ft,
        upper_deck_exception_overhang_allowance_ft=upper_deck_exception_overhang_allowance_ft,
        upper_deck_exception_categories=upper_deck_exception_categories,
    )
    for issue in compatibility_issues:
        warnings.append(_warning_payload("COMPATIBILITY_ISSUE", issue))

    for pos in positions:
        pos.pop("overflow_units_used", None)

    return {
        "positions": positions,
        "total_linear_feet": round(total_linear_feet, 1),
        "utilization_pct": round(utilization_pct, 1),
        "max_stack_height": max_stack_height,
        "compatibility_issues": compatibility_issues,
        "exceeds_capacity": exceeds_capacity,
        "utilization_credit_ft": round(total_credit_feet, 1),
        "utilization_grade": utilization_grade,
        "warnings": warnings,
        "trailer_type": trailer_config["type"],
        "capacity_feet": capacity,
        "lower_deck_length": lower_length,
        "upper_deck_length": upper_length,
        "lower_deck_used_length_ft": round(lower_total_linear, 1),
        "upper_deck_raw_length_ft": round(upper_total_linear_raw, 1),
        "upper_deck_effective_length_ft": round(upper_total_linear_effective, 1),
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
    }


def stack_display_index_map(positions, trailer_config=None):
    has_upper = False
    if isinstance(trailer_config, dict):
        has_upper = _coerce_non_negative_float(trailer_config.get("upper"), 0.0) > 0
    if not has_upper:
        has_upper = any(((pos.get("deck") or "lower").strip().lower() == "upper") for pos in (positions or []))
    ordered_positions = list(positions or [])
    if has_upper:
        ordered_positions = [
            *[pos for pos in ordered_positions if (pos.get("deck") or "lower").strip().lower() == "upper"],
            *[pos for pos in ordered_positions if (pos.get("deck") or "lower").strip().lower() != "upper"],
        ]
    mapping = {}
    for idx, pos in enumerate(ordered_positions, start=1):
        position_id = (pos or {}).get("position_id")
        if position_id:
            mapping[position_id] = idx
    return mapping


def check_stacking_compatibility(positions, trailer_config=None):
    issues = []
    stack_index_by_position_id = stack_display_index_map(positions, trailer_config)
    for idx, pos in enumerate(positions):
        position_id = (pos or {}).get("position_id")
        stack_idx = int(stack_index_by_position_id.get(position_id, idx + 1))
        item_lengths = [
            float(item.get("unit_length_ft") or 0)
            for item in pos.get("items", [])
            if (item.get("unit_length_ft") or 0) > 0
        ]
        for prev_len, current_len in zip(item_lengths, item_lengths[1:]):
            if current_len > prev_len + 1e-6:
                issues.append(
                    f"Stack {stack_idx}: Invalid stack (longer item above shorter item)."
                )
                break

        is_upper_two_across = (
            (str((pos or {}).get("deck") or "lower").strip().lower() == "upper")
            and bool((pos or {}).get("two_across_applied"))
        )
        max_item_stack = max(
            (
                _coerce_non_negative_int(item.get("max_stack"), 0)
                for item in (pos.get("items") or [])
            ),
            default=0,
        )
        if (not is_upper_two_across) and max_item_stack > 0 and pos["units_count"] > max_item_stack:
            issues.append(
                f"Stack {stack_idx}: {pos['units_count']} units may be unstable."
            )

        skus = [item["sku"] for item in pos["items"] if item.get("sku")]
        has_woody = any("WOODY" in sku for sku in skus)
        if has_woody and len(pos["items"]) > 1:
            issues.append(
                f"Stack {stack_idx}: Mix includes wooden floor. Verify compatibility."
            )

    return issues


def _grade_utilization(utilization_pct):
    thresholds = get_utilization_grade_thresholds()
    if utilization_pct >= thresholds["A"]:
        return "A"
    if utilization_pct >= thresholds["B"]:
        return "B"
    if utilization_pct >= thresholds["C"]:
        return "C"
    if utilization_pct >= thresholds["D"]:
        return "D"
    return "F"


def _exceeds_capacity(
    positions,
    trailer_config,
    max_back_overhang_ft=0.0,
    upper_deck_exception_max_length_ft=DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT,
    upper_deck_exception_overhang_allowance_ft=DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT,
    upper_deck_exception_categories=DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES,
):
    lower_length = trailer_config["lower"]
    upper_length = trailer_config["upper"]
    allowed_overhang = _coerce_non_negative_float(max_back_overhang_ft, 0.0)

    if upper_length <= 0:
        return capacity_overflow_feet(
            {
                "positions": positions,
                "lower_deck_length": lower_length,
                "upper_deck_length": 0.0,
                "max_back_overhang_ft": allowed_overhang,
                "upper_deck_exception_max_length_ft": upper_deck_exception_max_length_ft,
                "upper_deck_exception_overhang_allowance_ft": upper_deck_exception_overhang_allowance_ft,
                "upper_deck_exception_categories": upper_deck_exception_categories,
            }
        ) > 0.0

    return capacity_overflow_feet(
        {
            "positions": positions,
            "lower_deck_length": lower_length,
            "upper_deck_length": upper_length,
            "max_back_overhang_ft": allowed_overhang,
            "trailer_type": trailer_config.get("type"),
            "upper_deck_exception_max_length_ft": upper_deck_exception_max_length_ft,
            "upper_deck_exception_overhang_allowance_ft": upper_deck_exception_overhang_allowance_ft,
            "upper_deck_exception_categories": upper_deck_exception_categories,
        }
    ) > 0.0
