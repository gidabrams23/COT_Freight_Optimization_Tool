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

ORDER_CATEGORY_SCOPE_LABELS = {
    ORDER_CATEGORY_SCOPE_ALL: "All Orders",
    ORDER_CATEGORY_SCOPE_UTILITIES: "Utilities Only (USA/UTA)",
    ORDER_CATEGORY_SCOPE_DUMP: "Dump Only",
    ORDER_CATEGORY_SCOPE_CARGO: "Cargo Only",
    ORDER_CATEGORY_SCOPE_OTHER: "Other Only",
    ORDER_CATEGORY_SCOPE_MIXED: "Mixed Orders",
}


def normalize_order_category_scope(value, default=ORDER_CATEGORY_SCOPE_ALL):
    normalized = str(value or "").strip().lower()
    if normalized in ORDER_CATEGORY_SCOPES:
        return normalized
    fallback = str(default or "").strip().lower()
    if fallback in ORDER_CATEGORY_SCOPES:
        return fallback
    return ORDER_CATEGORY_SCOPE_ALL


def line_category_bucket(raw_value):
    value = str(raw_value or "").strip().upper()
    if not value:
        return ORDER_CATEGORY_SCOPE_OTHER
    if value.startswith("USA") or value.startswith("UTA"):
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
