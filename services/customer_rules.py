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


LOWES_PATTERNS = ["LOWES", "LOWE S"]
TRACTOR_SUPPLY_PATTERNS = ["TRACTOR SUPPLY", "TRACTORSUPPLY"]


def is_lowes_customer(customer_name):
    return matches_any_customer_pattern(customer_name, LOWES_PATTERNS)


def is_tractor_supply_customer(customer_name):
    return matches_any_customer_pattern(customer_name, TRACTOR_SUPPLY_PATTERNS)

