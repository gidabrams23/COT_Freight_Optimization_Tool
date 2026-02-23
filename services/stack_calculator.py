import json
import time

import db

TRAILER_CONFIGS = {
    "STEP_DECK": {"capacity": 53.0, "lower": 43.0, "upper": 10.0},
    "FLATBED": {"capacity": 53.0, "lower": 53.0, "upper": 0.0},
    "WEDGE": {"capacity": 51.0, "lower": 51.0, "upper": 0.0},
}

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
_UTILIZATION_GRADE_CACHE = {
    "thresholds": dict(DEFAULT_UTILIZATION_GRADE_THRESHOLDS),
    "expires_at": 0.0,
}
_STACK_ASSUMPTIONS_CACHE = {
    "assumptions": {
        "stack_overflow_max_height": DEFAULT_STACK_OVERFLOW_MAX_HEIGHT,
        "max_back_overhang_ft": DEFAULT_MAX_BACK_OVERHANG_FT,
    },
    "expires_at": 0.0,
}


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


def _normalize_stack_assumptions(raw_value):
    defaults = {
        "stack_overflow_max_height": DEFAULT_STACK_OVERFLOW_MAX_HEIGHT,
        "max_back_overhang_ft": DEFAULT_MAX_BACK_OVERHANG_FT,
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
    # Smaller sequence means earlier stop and should render closer to trailer back (left).
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
    trailer_key = (trailer_type or "STEP_DECK").strip().upper()
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


def _calculate_total_credit_feet(positions, trailer_config, max_stack_utilization_multiplier):
    lower_credit = 0.0
    upper_credit_raw = 0.0
    upper_length_used = 0.0

    for pos in positions or []:
        length_ft = float(pos.get("length_ft") or 0.0)
        multiplier = _position_credit_multiplier(pos, max_stack_utilization_multiplier)
        credit = length_ft * multiplier
        if (pos.get("deck") or "lower") == "upper":
            upper_credit_raw += credit
            upper_length_used += length_ft
        else:
            lower_credit += credit

    upper_credit = upper_credit_raw
    trailer_type = (trailer_config.get("type") or "").strip().upper()
    upper_length = float(trailer_config.get("upper") or 0.0)
    if (
        trailer_type == "STEP_DECK"
        and upper_length > 0
        and upper_length_used > 0
        and upper_length_used < (upper_length - 1e-6)
    ):
        # Normalize occupied upper-deck stacks to the full 10' basis.
        upper_credit *= (upper_length / upper_length_used)

    return lower_credit + upper_credit


def _deck_usage_totals(positions):
    lower_total = sum(
        pos.get("length_ft") or 0 for pos in positions if (pos.get("deck") or "lower") == "lower"
    )
    upper_total = sum(
        pos.get("length_ft") or 0 for pos in positions if (pos.get("deck") or "lower") == "upper"
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

    if upper_length <= 0:
        total_length = sum(float(pos.get("length_ft") or 0.0) for pos in positions)
        return round(max(total_length - (lower_length + allowed_overhang), 0.0), 4)

    lower_total, upper_total = _deck_usage_totals(positions)
    lower_over = max(lower_total - (lower_length + allowed_overhang), 0.0)
    upper_over = max(upper_total - (upper_length + allowed_overhang), 0.0)
    return round(lower_over + upper_over, 4)


def _build_capacity_warnings(
    positions,
    trailer_config,
    stack_overflow_max_height,
    max_back_overhang_ft,
):
    warnings = []
    max_stack_utilization = _max_stack_utilization_multiplier(stack_overflow_max_height)
    for idx, pos in enumerate(positions, start=1):
        capacity_used = float(pos.get("capacity_used") or 0.0)
        deck = (pos.get("deck") or "lower").strip().lower() or "lower"
        position_id = pos.get("position_id") or f"p{idx}"
        overflow_note = pos.get("overflow_note")
        if capacity_used > (max_stack_utilization + 1e-6):
            warnings.append(
                _warning_payload(
                    "STACK_TOO_HIGH",
                    (
                        f"Position {idx}: stack fill is {capacity_used:.2f}x, above allowed "
                        f"{max_stack_utilization:.2f}x."
                    ),
                    deck=deck,
                    position_id=position_id,
                )
            )
        elif capacity_used > (1.0 + 1e-6):
            if pos.get("overflow_applied"):
                message = overflow_note or (
                    f"Stack {idx} was overutilized to allow for additional space "
                    f"({capacity_used:.2f}x, allowed up to {max_stack_utilization:.2f}x)."
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
                        (
                            f"Position {idx}: stack fill is {capacity_used:.2f}x and is not "
                            "eligible for overflow allowance."
                        ),
                        deck=deck,
                        position_id=position_id,
                    )
                )

    lower_length = trailer_config.get("lower") or 0.0
    upper_length = trailer_config.get("upper") or 0.0
    overhang_allowance = _coerce_non_negative_float(max_back_overhang_ft, DEFAULT_MAX_BACK_OVERHANG_FT)
    lower_total, upper_total = _deck_usage_totals(positions)

    def _append_overhang_warnings(deck_label, overhang_ft, deck_key):
        if overhang_ft <= 0.05:
            return
        if overhang_ft <= (overhang_allowance + 1e-6):
            warnings.append(
                _warning_payload(
                    "BACK_OVERHANG_IN_ALLOWANCE",
                    (
                        f"{deck_label} deck back overhang is {overhang_ft:.1f} ft "
                        f"(allowance {overhang_allowance:.1f} ft)."
                    ),
                    deck=deck_key,
                )
            )
            return
        exceed_by = overhang_ft - overhang_allowance
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

    _append_overhang_warnings("Lower", max(lower_total - lower_length, 0.0), "lower")
    if upper_length > 0:
        _append_overhang_warnings("Upper", max(upper_total - upper_length, 0.0), "upper")

    return warnings


def calculate_stack_configuration(
    order_lines,
    trailer_type="STEP_DECK",
    capacity_feet=None,
    preserve_order_contiguity=True,
    stack_overflow_max_height=None,
    max_back_overhang_ft=None,
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
                    # Keep stack fill direction deterministic from left to right.
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

        # Fill the trailer left-to-right while keeping each order contiguous in the schematic.
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

    # Keep schematic columns deterministic by earliest accessible stop first (left to right).
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

    if upper_length > 0:
        upper_candidates = [
            pos for pos in positions if pos["length_ft"] <= upper_length
        ]
        upper_candidates.sort(key=lambda pos: pos["length_ft"], reverse=True)
        remaining_upper = upper_length
        for pos in upper_candidates:
            if pos["length_ft"] <= remaining_upper:
                pos["deck"] = "upper"
                remaining_upper -= pos["length_ft"]

    for pos in positions:
        deck_length = upper_length if pos["deck"] == "upper" else lower_length
        if deck_length:
            pos["width_pct"] = min(round((pos["length_ft"] / deck_length) * 100, 1), 100)
        else:
            pos["width_pct"] = 0

    total_linear_feet = sum(pos["length_ft"] for pos in positions)
    total_credit_feet = _calculate_total_credit_feet(
        positions,
        trailer_config,
        max_stack_utilization_multiplier,
    )
    utilization_pct = (total_credit_feet / capacity) * 100 if total_credit_feet else 0
    max_stack_height = max((pos["units_count"] for pos in positions), default=0)
    compatibility_issues = check_stacking_compatibility(positions)
    exceeds_capacity = _exceeds_capacity(
        positions,
        trailer_config,
        max_back_overhang_ft=max_back_overhang_ft,
    )
    utilization_grade = _grade_utilization(utilization_pct)
    warnings = _build_capacity_warnings(
        positions,
        trailer_config,
        stack_overflow_max_height=stack_overflow_max_height,
        max_back_overhang_ft=max_back_overhang_ft,
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
        "stack_overflow_max_height": stack_overflow_max_height,
        "max_back_overhang_ft": max_back_overhang_ft,
        "max_stack_utilization_multiplier": round(
            max_stack_utilization_multiplier,
            4,
        ),
    }


def check_stacking_compatibility(positions):
    issues = []
    for idx, pos in enumerate(positions):
        item_lengths = [
            float(item.get("unit_length_ft") or 0)
            for item in pos.get("items", [])
            if (item.get("unit_length_ft") or 0) > 0
        ]
        for prev_len, current_len in zip(item_lengths, item_lengths[1:]):
            if current_len > prev_len + 1e-6:
                issues.append(
                    f"Position {idx + 1}: Invalid stack (longer item above shorter item)."
                )
                break

        categories = [item["category"] for item in pos["items"] if item.get("category")]
        if "DUMP" in categories and len(set(categories)) > 1:
            issues.append(
                f"Position {idx + 1}: DUMP trailers cannot mix with other types."
            )

        if pos["units_count"] > 5:
            issues.append(
                f"Position {idx + 1}: Stack of {pos['units_count']} units may be unstable."
            )

        skus = [item["sku"] for item in pos["items"] if item.get("sku")]
        has_woody = any("WOODY" in sku for sku in skus)
        if has_woody and len(pos["items"]) > 1:
            issues.append(
                f"Position {idx + 1}: Mix includes wooden floor. Verify compatibility."
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


def _exceeds_capacity(positions, trailer_config, max_back_overhang_ft=0.0):
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
            }
        ) > 0.0

    return capacity_overflow_feet(
        {
            "positions": positions,
            "lower_deck_length": lower_length,
            "upper_deck_length": upper_length,
            "max_back_overhang_ft": allowed_overhang,
        }
    ) > 0.0
