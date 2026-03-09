"""Helpers for order category scope classification used by optimizer/workbench."""

ORDER_CATEGORY_SCOPE_ALL = "all"
ORDER_CATEGORY_SCOPE_UTILITIES = "utilities"
ORDER_CATEGORY_SCOPE_DUMP = "dump"
ORDER_CATEGORY_SCOPE_CARGO = "cargo"
ORDER_CATEGORY_SCOPE_OTHER = "other"
ORDER_CATEGORY_SCOPE_MIXED = "mixed"

ORDER_CATEGORY_SCOPES = (
    ORDER_CATEGORY_SCOPE_ALL,
    ORDER_CATEGORY_SCOPE_UTILITIES,
    ORDER_CATEGORY_SCOPE_DUMP,
    ORDER_CATEGORY_SCOPE_CARGO,
    ORDER_CATEGORY_SCOPE_OTHER,
    ORDER_CATEGORY_SCOPE_MIXED,
)
ORDER_CATEGORY_FILTER_SCOPES = (
    ORDER_CATEGORY_SCOPE_UTILITIES,
    ORDER_CATEGORY_SCOPE_DUMP,
    ORDER_CATEGORY_SCOPE_CARGO,
    ORDER_CATEGORY_SCOPE_OTHER,
    ORDER_CATEGORY_SCOPE_MIXED,
)

ORDER_CATEGORY_SCOPE_LABELS = {
    ORDER_CATEGORY_SCOPE_ALL: "All Orders",
    ORDER_CATEGORY_SCOPE_UTILITIES: "Utilities Only (USA/UTA)",
    ORDER_CATEGORY_SCOPE_DUMP: "Dump Only",
    ORDER_CATEGORY_SCOPE_CARGO: "Cargo Only",
    ORDER_CATEGORY_SCOPE_OTHER: "Other Only",
    ORDER_CATEGORY_SCOPE_MIXED: "Mixed Orders",
}

_UTILITY_PREFIXES = ("USA", "UTA", "ECOM", "HDEQ", "UTIL")
_UTILITY_EXACT = {"UTILITY", "UTILITIES"}
DEFAULT_UTILITY_CATEGORY_TOKENS = ("USA", "UTA", "ECOM", "HDEQ", "UTILITIES")


def normalize_order_category_scope(value, default=ORDER_CATEGORY_SCOPE_ALL):
    normalized = str(value or "").strip().lower()
    if normalized in ORDER_CATEGORY_SCOPES:
        return normalized
    fallback = str(default or "").strip().lower()
    if fallback in ORDER_CATEGORY_SCOPES:
        return fallback
    return ORDER_CATEGORY_SCOPE_ALL


def normalize_order_category_scopes(values, default=None):
    if values is None:
        source = []
    elif isinstance(values, str):
        source = [part.strip() for part in values.split(",")]
    elif isinstance(values, (list, tuple, set)):
        source = values
    else:
        source = [values]

    selected = []
    seen = set()
    for raw in source:
        normalized = normalize_order_category_scope(raw, default="")
        if not normalized or normalized == ORDER_CATEGORY_SCOPE_ALL:
            continue
        if normalized not in ORDER_CATEGORY_FILTER_SCOPES:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        selected.append(normalized)
    if selected:
        return selected

    if default is None:
        return []
    if isinstance(default, str):
        normalized_default = normalize_order_category_scope(default, default=ORDER_CATEGORY_SCOPE_ALL)
        if normalized_default == ORDER_CATEGORY_SCOPE_ALL:
            return []
    return normalize_order_category_scopes(default, default=None)


def primary_order_category_scope(selected_scopes):
    normalized = normalize_order_category_scopes(selected_scopes, default=None)
    if len(normalized) == 1:
        return normalized[0]
    return ORDER_CATEGORY_SCOPE_ALL


def normalize_order_category_tokens(values):
    if values is None:
        source = []
    elif isinstance(values, str):
        source = [part.strip() for part in values.split(",")]
    elif isinstance(values, (list, tuple, set)):
        source = values
    else:
        source = [values]
    cleaned = []
    seen = set()
    for raw in source:
        token = str(raw or "").strip().upper()
        if not token or token in seen:
            continue
        seen.add(token)
        cleaned.append(token)
    return cleaned


def line_category_bucket(raw_value):
    value = str(raw_value or "").strip().upper()
    if not value:
        return ORDER_CATEGORY_SCOPE_OTHER
    if value in _UTILITY_EXACT or value.startswith(_UTILITY_PREFIXES):
        return ORDER_CATEGORY_SCOPE_UTILITIES
    if "CARGO" in value:
        return ORDER_CATEGORY_SCOPE_CARGO
    if "DUMP" in value:
        return ORDER_CATEGORY_SCOPE_DUMP
    return ORDER_CATEGORY_SCOPE_OTHER


def order_category_scope_from_tokens(category_tokens):
    buckets = {
        line_category_bucket(token)
        for token in (category_tokens or [])
        if str(token or "").strip()
    }
    if not buckets:
        return ORDER_CATEGORY_SCOPE_OTHER
    if len(buckets) == 1:
        return next(iter(buckets))
    return ORDER_CATEGORY_SCOPE_MIXED


def empty_category_counts():
    return {
        ORDER_CATEGORY_SCOPE_UTILITIES: 0,
        ORDER_CATEGORY_SCOPE_DUMP: 0,
        ORDER_CATEGORY_SCOPE_CARGO: 0,
        ORDER_CATEGORY_SCOPE_OTHER: 0,
        ORDER_CATEGORY_SCOPE_MIXED: 0,
    }
