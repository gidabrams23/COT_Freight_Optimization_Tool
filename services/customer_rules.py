import json
import re


def normalize_customer_text(value):
    """Normalize customer text for lightweight substring matching.

    - uppercases
    - removes punctuation and extra whitespace
    """

    if not value:
        return ""
    text = str(value).upper()
    text = re.sub(r"[^A-Z0-9\\s]", "", text)
    text = re.sub(r"\\s+", " ", text).strip()
    return text


def matches_any_customer_pattern(customer_name, patterns):
    normalized = normalize_customer_text(customer_name)
    if not normalized:
        return False
    for pattern in patterns or []:
        normalized_pattern = normalize_customer_text(pattern)
        if normalized_pattern and normalized_pattern in normalized:
            return True
    return False


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _coerce_non_negative_int(value, default=0):
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return default
    return max(parsed, 0)


def _coerce_optional_non_negative_int(value):
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        parsed = int(text)
    except (TypeError, ValueError):
        return None
    return max(parsed, 0)


def _normalize_patterns(raw_patterns, fallback_label=""):
    if isinstance(raw_patterns, str):
        values = [part.strip() for part in raw_patterns.split(",")]
    elif isinstance(raw_patterns, (list, tuple, set)):
        values = [str(part or "").strip() for part in raw_patterns]
    else:
        values = []
    patterns = [value for value in values if value]
    if not patterns and fallback_label:
        patterns = [fallback_label]
    return patterns


def _strategic_key(label, used_keys):
    base_key = normalize_customer_text(label).lower().replace(" ", "_") or "customer"
    key = base_key
    suffix = 2
    while key in used_keys:
        key = f"{base_key}_{suffix}"
        suffix += 1
    used_keys.add(key)
    return key


def _default_requires_return_to_origin(label, patterns):
    values = [label] + list(patterns or [])
    for value in values:
        if matches_any_customer_pattern(value, LOWES_PATTERNS):
            return True
    return False


def parse_strategic_customers(value_text):
    entries = []
    used_keys = set()

    raw_text = str(value_text or "").strip()
    parsed_structured = False
    if raw_text.startswith("["):
        try:
            payload = json.loads(raw_text)
            if isinstance(payload, list):
                parsed_structured = True
                for row in payload:
                    if not isinstance(row, dict):
                        continue
                    label = str(row.get("label") or "").strip()
                    patterns = _normalize_patterns(row.get("patterns"), fallback_label=label)
                    if not label:
                        label = patterns[0] if patterns else ""
                    if not label:
                        continue
                    key = _strategic_key(label, used_keys)
                    has_return_flag = (
                        "requires_return_to_origin" in row
                        or "dedicated_return" in row
                    )
                    has_due_flex_flag = (
                        "default_due_date_flex_days" in row
                        or "due_date_flex_days" in row
                    )
                    has_ignore_for_optimization_flag = (
                        "ignore_for_optimization" in row
                        or "exclude_from_optimization" in row
                    )
                    has_include_workbench_flag = (
                        "include_in_optimizer_workbench" in row
                        or "show_in_optimizer_workbench" in row
                    )
                    if has_return_flag:
                        requires_return = _coerce_bool(
                            row.get(
                                "requires_return_to_origin",
                                row.get("dedicated_return", False),
                            )
                        )
                    else:
                        requires_return = _default_requires_return_to_origin(label, patterns)
                    due_flex_days = None
                    if has_due_flex_flag:
                        due_flex_days = _coerce_optional_non_negative_int(
                            row.get("default_due_date_flex_days", row.get("due_date_flex_days"))
                        )
                    entries.append(
                        {
                            "key": key,
                            "label": label,
                            "patterns": patterns or [label],
                            "default_due_date_flex_days": due_flex_days,
                            "no_mix": _coerce_bool(
                                row.get("no_mix", row.get("no_mix_with_other_customers", False))
                            ),
                            "default_wedge_51": _coerce_bool(
                                row.get("default_wedge_51", row.get("prefer_wedge_51", False))
                            ),
                            "requires_return_to_origin": requires_return,
                            "ignore_for_optimization": _coerce_bool(
                                row.get(
                                    "ignore_for_optimization",
                                    row.get("exclude_from_optimization", False),
                                )
                            )
                            if has_ignore_for_optimization_flag
                            else False,
                            "include_in_optimizer_workbench": _coerce_bool(
                                row.get(
                                    "include_in_optimizer_workbench",
                                    row.get("show_in_optimizer_workbench", True),
                                )
                            )
                            if has_include_workbench_flag
                            else True,
                        }
                    )
        except json.JSONDecodeError:
            parsed_structured = False

    if parsed_structured:
        return entries

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

        key = _strategic_key(label, used_keys)
        entries.append(
            {
                "key": key,
                "label": label,
                "patterns": patterns,
                "default_due_date_flex_days": None,
                "no_mix": False,
                "default_wedge_51": False,
                "requires_return_to_origin": _default_requires_return_to_origin(
                    label,
                    patterns,
                ),
                "ignore_for_optimization": False,
                "include_in_optimizer_workbench": True,
            }
        )

    return entries


def serialize_strategic_customers(entries):
    normalized = []
    for entry in entries or []:
        label = str((entry or {}).get("label") or "").strip()
        patterns = _normalize_patterns((entry or {}).get("patterns"), fallback_label=label)
        if not label or not patterns:
            continue
        normalized.append(
            {
                "label": label,
                "patterns": patterns,
                "default_due_date_flex_days": _coerce_optional_non_negative_int(
                    (entry or {}).get("default_due_date_flex_days")
                ),
                "no_mix": _coerce_bool((entry or {}).get("no_mix")),
                "default_wedge_51": _coerce_bool((entry or {}).get("default_wedge_51")),
                "requires_return_to_origin": _coerce_bool(
                    (entry or {}).get("requires_return_to_origin")
                ),
                "ignore_for_optimization": _coerce_bool(
                    (entry or {}).get("ignore_for_optimization")
                ),
                "include_in_optimizer_workbench": _coerce_bool(
                    (entry or {}).get("include_in_optimizer_workbench", True)
                ),
            }
        )
    return json.dumps(normalized, separators=(",", ":"))


def find_matching_strategic_customer(customer_name, strategic_customers):
    for entry in strategic_customers or []:
        if matches_any_customer_pattern(customer_name, (entry or {}).get("patterns")):
            return entry
    return None


LOWES_PATTERNS = ["LOWES", "LOWE S"]
TRACTOR_SUPPLY_PATTERNS = ["TRACTOR SUPPLY", "TRACTORSUPPLY"]


def is_lowes_customer(customer_name):
    return matches_any_customer_pattern(customer_name, LOWES_PATTERNS)


def is_tractor_supply_customer(customer_name):
    return matches_any_customer_pattern(customer_name, TRACTOR_SUPPLY_PATTERNS)
