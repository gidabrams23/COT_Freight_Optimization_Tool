# Utilization Scorer Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the bin-packing utilization calculation from `services/stack_calculator.py` into a standalone pip-installable package (`cot_utilization/`) and build a batch scoring interface on top of it.

**Architecture:** Pure math moves to `cot_utilization/stack_calculator.py` (no DB dependency). `services/stack_calculator.py` becomes a thin wrapper that reads settings from the DB and delegates to the package. `cot_utilization/scorer.py` provides the batch scoring interface that accepts caller-provided SKU data and a DataFrame of load records.

**Tech Stack:** Python stdlib (math, re, json), pandas (scorer only)

**Spec:** `docs/superpowers/specs/2026-04-10-utilization-scorer-package-design.md`

---

## File Map

| File | Role |
|---|---|
| `cot_utilization/__init__.py` | Package exports: `UtilizationScorer`, `calculate_stack_configuration`, key constants |
| `cot_utilization/stack_calculator.py` | Pure utilization math extracted from `services/stack_calculator.py` — all bin-packing, credit calc, grading, trailer configs, normalization helpers. No `db` or Flask imports. |
| `cot_utilization/scorer.py` | `UtilizationScorer` class — accepts SKU lookup dict, scores DataFrames of load records |
| `pyproject.toml` | Package metadata at repo root |
| `services/stack_calculator.py` | Compatibility wrapper — imports from `cot_utilization`, re-exports public symbols, overrides `calculate_stack_configuration()` to inject DB-sourced settings |
| `tests/test_cot_utilization_core.py` | Package-level tests for extracted core logic + injectable settings |
| `tests/test_cot_utilization_scorer.py` | Batch scorer tests — grouping, trailer inference, unmapped SKU handling, output schema |

---

### Task 1: Create Package Skeleton and pyproject.toml

**Files:**
- Create: `cot_utilization/__init__.py`
- Create: `pyproject.toml`

- [ ] **Step 1: Create `pyproject.toml` at repo root**

```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "cot-utilization"
version = "0.1.0"
description = "Standalone trailer utilization scorer for COT freight loads"
requires-python = ">=3.10"
dependencies = []

[project.optional-dependencies]
batch = ["pandas>=1.5"]

[tool.setuptools.packages.find]
include = ["cot_utilization*"]
```

- [ ] **Step 2: Create `cot_utilization/__init__.py`**

```python
"""COT Utilization Scorer — standalone trailer utilization calculation."""

from cot_utilization.stack_calculator import (
    TRAILER_CONFIGS,
    FIXED_CAPACITY_TRAILER_TYPES,
    calculate_stack_configuration,
)
from cot_utilization.scorer import UtilizationScorer

__all__ = [
    "TRAILER_CONFIGS",
    "FIXED_CAPACITY_TRAILER_TYPES",
    "calculate_stack_configuration",
    "UtilizationScorer",
]
```

Note: this will fail to import until Task 2 and Task 5 create the referenced modules. That is expected.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml cot_utilization/__init__.py
git commit -m "feat(cot_utilization): add package skeleton and pyproject.toml"
```

---

### Task 2: Extract Pure Math into `cot_utilization/stack_calculator.py`

**Files:**
- Create: `cot_utilization/stack_calculator.py`
- Source: `services/stack_calculator.py` (lines 1–2075, minus DB-reading functions)

This is the largest task. It is a mechanical copy of all pure functions from `services/stack_calculator.py`, with one change: the `calculate_stack_configuration()` signature gains an optional `grade_thresholds` parameter and uses package-local defaults instead of calling `get_stack_capacity_assumptions()` / `get_utilization_grade_thresholds()`.

- [ ] **Step 1: Copy the pure math**

Create `cot_utilization/stack_calculator.py` containing the following from `services/stack_calculator.py`:

**Constants (copy exactly):**
- `TRAILER_CONFIGS` (line 8)
- `TRAILER_PROFILE_OPTIONS` (line 16)
- `TRAILER_TYPE_SET` (line 24)
- `FIXED_CAPACITY_TRAILER_TYPES` (line 25)
- `DEFAULT_UTILIZATION_GRADE_THRESHOLDS` (line 29)
- `DEFAULT_STACK_OVERFLOW_MAX_HEIGHT` (line 35)
- `DEFAULT_MAX_BACK_OVERHANG_FT` (line 36)
- `DEFAULT_UPPER_TWO_ACROSS_MAX_LENGTH_FT` (line 37)
- `DEFAULT_UPPER_DECK_EXCEPTION_MAX_LENGTH_FT` (line 38)
- `DEFAULT_UPPER_DECK_EXCEPTION_OVERHANG_ALLOWANCE_FT` (line 39)
- `DEFAULT_UPPER_DECK_EXCEPTION_CATEGORIES` (line 40)
- `DEFAULT_EQUAL_LENGTH_DECK_LENGTH_ORDER_ENABLED` (line 41)
- `_SKU_DIMENSION_PATTERN` (line 42)

**Do NOT copy:**
- `UTILIZATION_GRADE_THRESHOLDS_SETTING_KEY` (line 27) — DB-specific
- `OPTIMIZER_DEFAULTS_SETTING_KEY` (line 28) — DB-specific
- `_UTILIZATION_GRADE_CACHE` (line 43) — DB cache
- `_STACK_ASSUMPTIONS_CACHE` (line 47) — DB cache

**Public functions (copy exactly):**
- `trailer_profile_options()` (line 61)
- `is_valid_trailer_type()` (line 65)
- `normalize_trailer_type()` (line 70)
- `item_deck_length_ft()` (line 138)
- `normalize_upper_deck_exception_categories()` (line 170)
- `apply_upper_usage_metadata()` (line 979)
- `upper_deck_position_length_limit_ft()` (line 996)
- `is_upper_deck_exception_eligible_position()` (line 1023)
- `evaluate_upper_deck_overhang()` (line 1044)
- `capacity_overflow_feet()` (line 1154)
- `calculate_stack_configuration()` (line 1311) — **modified, see Step 2**
- `stack_display_index_map()` (line 1934)
- `check_stacking_compatibility()` (line 1954)

**Private helpers (copy all that are called by the above):**
- `_coerce_non_negative_int()` (line 80)
- `_coerce_non_negative_float()` (line 88)
- `_coerce_bool()` (line 96)
- `_deck_length_from_sku_text()` (line 113)
- `_item_deck_length_ft()` (line 126)
- `_normalize_upper_deck_exception_categories()` (line 142)
- `_normalize_stack_assumptions()` (line 174)
- `_normalize_threshold_map()` (line 230)
- `_resolve_trailer_config()` (line ~445)
- `_max_stack_utilization_multiplier()` (line 474)
- `_warning_payload()` (line 481)
- `_unit_capacity_fraction()` (line 490)
- `_position_stack_height_set()` (line 494)
- `_position_has_mixed_stack_heights()` (line 503)
- `_is_high_side_item()` (line 507)
- `_promote_high_side_items_within_equal_length()` (line 516)
- `_eligible_singleton_overflow_item()` (line 545)
- `_append_single_unit_item()` — find line
- `_apply_singleton_overflow_allowance()` — find line
- `_length_stack_compatible()` — find line
- `_dump_stack_preference_rank()` — find line
- `_stop_access_compatible()` (line 325)
- `_position_top_item()` (line 335)
- `_coerce_stop_sequence()` (line 318)
- `_position_credit_multiplier()` (line 706)
- `_calculate_total_credit_feet()` (line 1107)
- `_deck_usage_totals()` (line 1139)
- `_grade_utilization()` (line 2026)
- `_exceeds_capacity()` (line 2039)
- `_build_capacity_warnings()` (line 1199)
- `_build_order_rank()` — find line
- `_position_stop_priority()` — find line
- `_position_size_priority()` — find line
- `_position_order_priority()` — find line
- `_apply_upper_usage_metadata()` — the internal version called by the public `apply_upper_usage_metadata()`
- All other `_private` functions referenced by the above (trace all calls to ensure nothing is missing)

**Imports for `cot_utilization/stack_calculator.py`:**

```python
import json
import math
import re
```

No `import db`. No `import time` (that was only for cache TTL).

- [ ] **Step 2: Modify `calculate_stack_configuration()` signature**

The existing signature already accepts individual stack-assumption overrides. Add `grade_thresholds=None`:

```python
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
    equal_length_deck_length_order_enabled=None,
    grade_thresholds=None,
):
```

Replace the line `defaults = get_stack_capacity_assumptions()` (currently line 1324) with:

```python
    defaults = _normalize_stack_assumptions(None)
```

This uses the package-local defaults dict instead of reading from DB.

Replace the call to `_grade_utilization(utilization_pct)` (currently line 1880) so that it passes the caller-provided thresholds:

```python
    utilization_grade = _grade_utilization(utilization_pct, grade_thresholds=grade_thresholds)
```

And update `_grade_utilization`:

```python
def _grade_utilization(utilization_pct, grade_thresholds=None):
    thresholds = _normalize_threshold_map(grade_thresholds) if grade_thresholds else DEFAULT_UTILIZATION_GRADE_THRESHOLDS
    if utilization_pct >= thresholds["A"]:
        return "A"
    if utilization_pct >= thresholds["B"]:
        return "B"
    if utilization_pct >= thresholds["C"]:
        return "C"
    if utilization_pct >= thresholds["D"]:
        return "D"
    return "F"
```

- [ ] **Step 3: Verify the module imports cleanly**

```bash
cd /home/atw/COT_Freight_Optimization_Tool
python3 -c "from cot_utilization.stack_calculator import calculate_stack_configuration, TRAILER_CONFIGS; print('OK', len(TRAILER_CONFIGS))"
```

Expected: `OK 6`

If this fails with an ImportError for `db`, a function was copied that still references `db`. Find and remove the reference.

- [ ] **Step 4: Commit**

```bash
git add cot_utilization/stack_calculator.py
git commit -m "feat(cot_utilization): extract pure utilization math from services/stack_calculator"
```

---

### Task 3: Write Core Package Tests

**Files:**
- Create: `tests/test_cot_utilization_core.py`

These tests call `cot_utilization.stack_calculator` directly — no DB mocking needed. They validate that the extracted code produces the same results as the original.

- [ ] **Step 1: Write tests**

```python
import unittest

from cot_utilization.stack_calculator import (
    TRAILER_CONFIGS,
    FIXED_CAPACITY_TRAILER_TYPES,
    calculate_stack_configuration,
    normalize_trailer_type,
    is_valid_trailer_type,
    capacity_overflow_feet,
    check_stacking_compatibility,
    item_deck_length_ft,
    normalize_upper_deck_exception_categories,
)


class TestCoreTrailerHelpers(unittest.TestCase):
    def test_trailer_configs_has_six_types(self):
        self.assertEqual(len(TRAILER_CONFIGS), 6)

    def test_normalize_trailer_type_valid(self):
        self.assertEqual(normalize_trailer_type("step_deck"), "STEP_DECK")
        self.assertEqual(normalize_trailer_type("FLATBED"), "FLATBED")
        self.assertEqual(normalize_trailer_type("hotshot"), "HOTSHOT")

    def test_normalize_trailer_type_invalid_returns_default(self):
        self.assertEqual(normalize_trailer_type("INVALID"), "STEP_DECK")
        self.assertEqual(normalize_trailer_type("INVALID", default="FLATBED"), "FLATBED")

    def test_is_valid_trailer_type(self):
        self.assertTrue(is_valid_trailer_type("STEP_DECK"))
        self.assertFalse(is_valid_trailer_type("BOGUS"))

    def test_fixed_capacity_trailer_types(self):
        self.assertIn("HOTSHOT", FIXED_CAPACITY_TRAILER_TYPES)
        self.assertNotIn("STEP_DECK", FIXED_CAPACITY_TRAILER_TYPES)


class TestCoreCalculateStackConfiguration(unittest.TestCase):
    def test_empty_order_lines_returns_zero_utilization(self):
        config = calculate_stack_configuration([])
        self.assertEqual(config["utilization_pct"], 0)
        self.assertEqual(config["utilization_grade"], "F")
        self.assertEqual(config["positions"], [])

    def test_single_item_flatbed_utilization(self):
        lines = [
            {
                "item": "5X10GW",
                "sku": "5X10GW",
                "qty": 1,
                "unit_length_ft": 14.0,
                "max_stack_height": 1,
                "category": "USA",
            }
        ]
        config = calculate_stack_configuration(
            lines,
            trailer_type="FLATBED",
            stack_overflow_max_height=0,
        )
        self.assertEqual(len(config["positions"]), 1)
        expected_pct = round((14.0 / 53.0) * 100, 1)
        self.assertAlmostEqual(config["utilization_pct"], expected_pct, places=1)

    def test_stacked_items_increase_utilization_credit(self):
        lines = [
            {
                "item": "4X6G",
                "sku": "4X6G",
                "qty": 6,
                "unit_length_ft": 7.0,
                "max_stack_height": 6,
                "category": "USA",
            }
        ]
        config = calculate_stack_configuration(
            lines,
            trailer_type="FLATBED",
            stack_overflow_max_height=0,
        )
        self.assertEqual(len(config["positions"]), 1)
        self.assertAlmostEqual(config["utilization_pct"], round((7.0 / 53.0) * 100, 1), places=1)

    def test_grade_thresholds_override(self):
        lines = [
            {
                "item": "LONG",
                "sku": "LONG",
                "qty": 1,
                "unit_length_ft": 40.0,
                "max_stack_height": 1,
                "category": "CARGO",
            }
        ]
        strict = {"A": 95, "B": 90, "C": 85, "D": 80}
        config = calculate_stack_configuration(
            lines,
            trailer_type="FLATBED",
            stack_overflow_max_height=0,
            grade_thresholds=strict,
        )
        pct = config["utilization_pct"]
        self.assertGreater(pct, 70)
        self.assertLess(pct, 80)
        self.assertEqual(config["utilization_grade"], "F")

    def test_default_grade_thresholds_produce_expected_grades(self):
        lines = [
            {
                "item": "LONG",
                "sku": "LONG",
                "qty": 1,
                "unit_length_ft": 40.0,
                "max_stack_height": 1,
                "category": "CARGO",
            }
        ]
        config = calculate_stack_configuration(
            lines,
            trailer_type="FLATBED",
            stack_overflow_max_height=0,
        )
        pct = config["utilization_pct"]
        self.assertGreater(pct, 70)
        self.assertEqual(config["utilization_grade"], "B")

    def test_step_deck_upper_deck_credit_normalization(self):
        lines = [
            {
                "item": "4X6G",
                "sku": "4X6G",
                "qty": 6,
                "unit_length_ft": 7.0,
                "max_stack_height": 6,
                "category": "USA",
            }
        ]
        config = calculate_stack_configuration(
            lines,
            trailer_type="STEP_DECK",
            stack_overflow_max_height=0,
        )
        self.assertGreater(config["utilization_pct"], 0)
        upper_positions = [p for p in config["positions"] if p.get("deck") == "upper"]
        self.assertTrue(len(upper_positions) > 0, "Short item should be on upper deck")


class TestCoreItemDeckLength(unittest.TestCase):
    def test_parses_sku_name_dimensions(self):
        item = {"sku": "5X10GW", "unit_length_ft": 14.0}
        self.assertEqual(item_deck_length_ft(item), 10.0)

    def test_fallback_when_no_dimensions_in_name(self):
        item = {"sku": "CUSTOM", "unit_length_ft": 14.0}
        self.assertEqual(item_deck_length_ft(item, fallback_length_ft=14.0), 14.0)


class TestCoreNormalizeUpperDeckExceptionCategories(unittest.TestCase):
    def test_string_input_splits_on_comma(self):
        result = normalize_upper_deck_exception_categories("USA,UTA,CARGO")
        self.assertEqual(result, ["USA", "UTA", "CARGO"])

    def test_none_returns_defaults(self):
        result = normalize_upper_deck_exception_categories(None)
        self.assertIn("USA", result)
        self.assertIn("UTA", result)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests**

```bash
cd /home/atw/COT_Freight_Optimization_Tool
python3 -m pytest tests/test_cot_utilization_core.py -v
```

Expected: all tests pass. If any fail, fix the extracted module (not the tests — the tests encode the spec).

- [ ] **Step 3: Commit**

```bash
git add tests/test_cot_utilization_core.py
git commit -m "test(cot_utilization): add core package tests for extracted stack calculator"
```

---

### Task 4: Rewire `services/stack_calculator.py` as Compatibility Wrapper

**Files:**
- Modify: `services/stack_calculator.py`

This replaces the bulk of the file with imports from the package, keeping only the DB-specific functions.

- [ ] **Step 1: Rewrite `services/stack_calculator.py`**

Replace the entire file contents with:

```python
"""Web-app compatibility wrapper for cot_utilization.stack_calculator.

Re-exports all public symbols from the package so existing app imports
(``from services.stack_calculator import X`` or
``from services import stack_calculator; stack_calculator.X``)
continue to work.  Overrides ``calculate_stack_configuration`` to inject
DB-sourced planner settings before delegating to the package core.
"""

import json
import time

import db

from cot_utilization import stack_calculator as _core

# ---------------------------------------------------------------------------
# Re-export public symbols from the package
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
# DB-specific setting keys and cache
# ---------------------------------------------------------------------------

UTILIZATION_GRADE_THRESHOLDS_SETTING_KEY = "utilization_grade_thresholds"
OPTIMIZER_DEFAULTS_SETTING_KEY = "optimizer_defaults"

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


def calculate_stack_configuration(order_lines, **kwargs):
    """App-level wrapper: injects DB-sourced settings, then delegates to core."""
    assumptions = get_stack_capacity_assumptions()
    thresholds = get_utilization_grade_thresholds()
    merged = {
        "grade_thresholds": thresholds,
        **kwargs,
    }
    for key, value in assumptions.items():
        merged.setdefault(key, value)
    return _core.calculate_stack_configuration(order_lines, **merged)
```

- [ ] **Step 2: Verify existing tests still pass**

```bash
cd /home/atw/COT_Freight_Optimization_Tool
python3 -m pytest tests/test_stack_calculator_assumptions.py -v
```

Expected: all 24 existing tests pass. The `@patch("services.stack_calculator.db.get_planning_setting")` decorators will still work because `services/stack_calculator.py` still imports `db`.

If any test fails, investigate — likely a missing re-export or a function reference that moved. Fix the wrapper to re-export whatever is missing.

- [ ] **Step 3: Run broader test suite to catch import breakage**

```bash
cd /home/atw/COT_Freight_Optimization_Tool
python3 -m pytest tests/ -v --tb=short 2>&1 | tail -40
```

Expected: no new failures. All existing imports of `from services.stack_calculator import X` and `from services import stack_calculator; stack_calculator.X` continue to resolve.

- [ ] **Step 4: Commit**

```bash
git add services/stack_calculator.py
git commit -m "refactor(services): rewire stack_calculator as thin wrapper over cot_utilization"
```

---

### Task 5: Build Batch Scorer

**Files:**
- Create: `cot_utilization/scorer.py`

- [ ] **Step 1: Write `cot_utilization/scorer.py`**

```python
"""Batch utilization scorer for historical load data."""

import re

from cot_utilization.stack_calculator import (
    TRAILER_CONFIGS,
    calculate_stack_configuration,
    normalize_trailer_type,
)

_SKU_DIMENSION_PATTERN = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*[Xx]\s*(\d+(?:\.\d+)?)"
)

_DEFAULT_COLUMN_MAP = {
    "load_number": "load_number",
    "qty": "qty",
    "sku": "sku",
    "trailer_hint": "trailer_hint",
}

_DEFAULT_TRAILER_RULES = {
    "default": "STEP_DECK",
    "overrides": {},
}


def _normalize_sku_lookup(raw):
    """Build case-insensitive SKU lookup dict."""
    lookup = {}
    for key, value in (raw or {}).items():
        normalized_key = str(key).strip().upper()
        if not normalized_key:
            continue
        lookup[normalized_key] = {
            "length_with_tongue_ft": float(value.get("length_with_tongue_ft") or 0),
            "max_stack_step_deck": int(value.get("max_stack_step_deck") or 1),
            "max_stack_flat_bed": int(value.get("max_stack_flat_bed") or 1),
            "category": str(value.get("category") or "UNKNOWN").strip().upper(),
        }
    return lookup


def _parse_sku_dimensions(sku_text):
    """Attempt to extract length from SKU name like '5X10GW' -> 10.0."""
    match = _SKU_DIMENSION_PATTERN.search(str(sku_text or ""))
    if not match:
        return None
    try:
        dim_a = float(match.group(1))
        dim_b = float(match.group(2))
        return max(dim_a, dim_b)
    except (TypeError, ValueError):
        return None


def _resolve_sku(sku_text, sku_lookup, trailer_type):
    """Resolve a SKU to dimensions and stacking rules.

    Returns (spec_dict, unmapped_flag) where unmapped_flag is True
    if the SKU could not be found in the lookup or parsed from its name.
    """
    key = str(sku_text or "").strip().upper()
    spec = sku_lookup.get(key)
    if spec:
        is_step_deck = trailer_type.startswith("STEP_DECK")
        max_stack = spec["max_stack_step_deck"] if is_step_deck else spec["max_stack_flat_bed"]
        return {
            "unit_length_ft": spec["length_with_tongue_ft"],
            "max_stack_height": max(max_stack, 1),
            "category": spec["category"],
        }, False

    parsed_length = _parse_sku_dimensions(sku_text)
    if parsed_length is not None:
        return {
            "unit_length_ft": parsed_length,
            "max_stack_height": 1,
            "category": "UNKNOWN",
        }, False

    return None, True


def _determine_trailer_type(rows, trailer_hint_col, trailer_rules):
    """Determine trailer type for a load based on trailer_rules and row values."""
    overrides = trailer_rules.get("overrides") or {}
    default = trailer_rules.get("default") or "STEP_DECK"
    for value in rows:
        hint = str(value.get(trailer_hint_col) or "").strip()
        if hint in overrides:
            return normalize_trailer_type(overrides[hint], default=default)
    return normalize_trailer_type(default)


class UtilizationScorer:
    """Score historical loads using the COT bin-packing utilization algorithm.

    Accepts a pre-built SKU lookup dict. The caller is responsible for
    loading SKU data from whatever source (CSV, blob snapshot, etc.).
    """

    def __init__(self, sku_lookup):
        self._sku_lookup = _normalize_sku_lookup(sku_lookup)

    @classmethod
    def from_csv(cls, path):
        """Convenience constructor for local dev/testing — load SKU specs from CSV."""
        import csv

        lookup = {}
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sku = (row.get("sku") or "").strip()
                if not sku:
                    continue
                lookup[sku] = {
                    "length_with_tongue_ft": row.get("length_with_tongue_ft", 0),
                    "max_stack_step_deck": row.get("max_stack_step_deck", 1),
                    "max_stack_flat_bed": row.get("max_stack_flat_bed", 1),
                    "category": row.get("category", "UNKNOWN"),
                }
        return cls(lookup)

    def score_loads(self, df, column_map=None, trailer_rules=None):
        """Score a DataFrame of load records.

        Parameters
        ----------
        df : pandas.DataFrame
            Input data with one row per load line item.
        column_map : dict, optional
            Maps scorer fields to DataFrame column names.
            Keys: ``load_number``, ``qty``, ``sku``, ``trailer_hint``.
        trailer_rules : dict, optional
            ``default``: fallback trailer type (default ``"STEP_DECK"``).
            ``overrides``: map of trailer_hint values to trailer types.

        Returns
        -------
        pandas.DataFrame
            One row per load with utilization scores.
        """
        import pandas as pd

        cmap = dict(_DEFAULT_COLUMN_MAP)
        if column_map:
            cmap.update(column_map)

        rules = dict(_DEFAULT_TRAILER_RULES)
        if trailer_rules:
            rules.update(trailer_rules)

        load_col = cmap["load_number"]
        qty_col = cmap["qty"]
        sku_col = cmap["sku"]
        hint_col = cmap["trailer_hint"]

        results = []

        for load_number, group in df.groupby(load_col, sort=False):
            rows = group.to_dict("records")

            trailer_type = _determine_trailer_type(rows, hint_col, rules)
            trailer_config = TRAILER_CONFIGS.get(
                trailer_type, TRAILER_CONFIGS["STEP_DECK"]
            )
            capacity = trailer_config["capacity"]

            line_items = []
            unmapped_skus = []

            for row in rows:
                sku_text = row.get(sku_col, "")
                qty = int(row.get(qty_col) or 0)
                if qty <= 0:
                    continue

                resolved, is_unmapped = _resolve_sku(
                    sku_text, self._sku_lookup, trailer_type
                )
                if is_unmapped:
                    unmapped_skus.append(str(sku_text))
                    continue

                line_items.append(
                    {
                        "item": str(sku_text),
                        "sku": str(sku_text).strip().upper(),
                        "qty": qty,
                        "unit_length_ft": resolved["unit_length_ft"],
                        "max_stack_height": resolved["max_stack_height"],
                        "category": resolved["category"],
                    }
                )

            config = calculate_stack_configuration(
                line_items,
                trailer_type=trailer_type,
                capacity_feet=capacity,
                stack_overflow_max_height=0,
            )

            results.append(
                {
                    "load_number": load_number,
                    "utilization_pct": config.get("utilization_pct", 0),
                    "utilization_grade": config.get("utilization_grade", "F"),
                    "utilization_credit_ft": config.get("utilization_credit_ft", 0),
                    "total_linear_feet": config.get("total_linear_feet", 0),
                    "trailer_type": trailer_type,
                    "capacity_ft": capacity,
                    "position_count": len(config.get("positions") or []),
                    "line_count": len(line_items),
                    "unmapped_skus": unmapped_skus,
                }
            )

        return pd.DataFrame(results)
```

- [ ] **Step 2: Verify import**

```bash
cd /home/atw/COT_Freight_Optimization_Tool
python3 -c "from cot_utilization.scorer import UtilizationScorer; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add cot_utilization/scorer.py
git commit -m "feat(cot_utilization): add batch utilization scorer"
```

---

### Task 6: Write Scorer Tests

**Files:**
- Create: `tests/test_cot_utilization_scorer.py`

- [ ] **Step 1: Write tests**

```python
import unittest

import pandas as pd

from cot_utilization.scorer import (
    UtilizationScorer,
    _parse_sku_dimensions,
    _resolve_sku,
    _normalize_sku_lookup,
)


SKU_LOOKUP = {
    "5X8GW": {
        "length_with_tongue_ft": 12.0,
        "max_stack_step_deck": 5,
        "max_stack_flat_bed": 4,
        "category": "USA",
    },
    "5.5X10GWE2K": {
        "length_with_tongue_ft": 14.0,
        "max_stack_step_deck": 5,
        "max_stack_flat_bed": 4,
        "category": "USA",
    },
    "7X16TA": {
        "length_with_tongue_ft": 22.0,
        "max_stack_step_deck": 2,
        "max_stack_flat_bed": 2,
        "category": "CARGO",
    },
}


class TestSKUDimensionParsing(unittest.TestCase):
    def test_parses_simple_dimensions(self):
        self.assertEqual(_parse_sku_dimensions("5X8GW"), 8.0)

    def test_parses_decimal_dimensions(self):
        self.assertEqual(_parse_sku_dimensions("5.5X10GWE2K"), 10.0)

    def test_returns_none_for_unparseable(self):
        self.assertIsNone(_parse_sku_dimensions("CUSTOM-ITEM"))

    def test_returns_larger_dimension(self):
        self.assertEqual(_parse_sku_dimensions("10X5"), 10.0)


class TestSKUResolution(unittest.TestCase):
    def setUp(self):
        self._lookup = _normalize_sku_lookup(SKU_LOOKUP)

    def test_exact_match(self):
        spec, unmapped = _resolve_sku("5X8GW", self._lookup, "STEP_DECK")
        self.assertFalse(unmapped)
        self.assertEqual(spec["unit_length_ft"], 12.0)
        self.assertEqual(spec["max_stack_height"], 5)

    def test_case_insensitive_match(self):
        spec, unmapped = _resolve_sku("5x8gw", self._lookup, "STEP_DECK")
        self.assertFalse(unmapped)
        self.assertEqual(spec["unit_length_ft"], 12.0)

    def test_flatbed_uses_flatbed_max_stack(self):
        spec, unmapped = _resolve_sku("5X8GW", self._lookup, "FLATBED")
        self.assertFalse(unmapped)
        self.assertEqual(spec["max_stack_height"], 4)

    def test_fallback_to_dimension_parsing(self):
        spec, unmapped = _resolve_sku("6X12NEWSKU", self._lookup, "STEP_DECK")
        self.assertFalse(unmapped)
        self.assertEqual(spec["unit_length_ft"], 12.0)
        self.assertEqual(spec["max_stack_height"], 1)

    def test_unmapped_when_no_match_and_no_dimensions(self):
        spec, unmapped = _resolve_sku("CUSTOM", self._lookup, "STEP_DECK")
        self.assertTrue(unmapped)
        self.assertIsNone(spec)


class TestUtilizationScorer(unittest.TestCase):
    def setUp(self):
        self.scorer = UtilizationScorer(SKU_LOOKUP)

    def test_score_single_load(self):
        df = pd.DataFrame(
            [
                {"load_number": "L001", "shippedqty": 5, "itemnum": "5X8GW", "fancy_cat": "Utility"},
                {"load_number": "L001", "shippedqty": 3, "itemnum": "5.5X10GWE2K", "fancy_cat": "Utility"},
            ]
        )
        results = self.scorer.score_loads(
            df,
            column_map={
                "load_number": "load_number",
                "qty": "shippedqty",
                "sku": "itemnum",
                "trailer_hint": "fancy_cat",
            },
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results.iloc[0]["load_number"], "L001")
        self.assertGreater(results.iloc[0]["utilization_pct"], 0)
        self.assertEqual(results.iloc[0]["trailer_type"], "STEP_DECK")

    def test_cargo_triggers_wedge(self):
        df = pd.DataFrame(
            [
                {"load_number": "L002", "shippedqty": 1, "itemnum": "7X16TA", "fancy_cat": "Cargo"},
            ]
        )
        results = self.scorer.score_loads(
            df,
            column_map={
                "load_number": "load_number",
                "qty": "shippedqty",
                "sku": "itemnum",
                "trailer_hint": "fancy_cat",
            },
            trailer_rules={
                "default": "STEP_DECK",
                "overrides": {"Cargo": "WEDGE"},
            },
        )
        self.assertEqual(results.iloc[0]["trailer_type"], "WEDGE")
        self.assertEqual(results.iloc[0]["capacity_ft"], 51.0)

    def test_multiple_loads_grouped(self):
        df = pd.DataFrame(
            [
                {"load_number": "A", "shippedqty": 2, "itemnum": "5X8GW", "fancy_cat": "Utility"},
                {"load_number": "B", "shippedqty": 3, "itemnum": "5X8GW", "fancy_cat": "Utility"},
            ]
        )
        results = self.scorer.score_loads(
            df,
            column_map={
                "load_number": "load_number",
                "qty": "shippedqty",
                "sku": "itemnum",
                "trailer_hint": "fancy_cat",
            },
        )
        self.assertEqual(len(results), 2)
        load_numbers = set(results["load_number"])
        self.assertEqual(load_numbers, {"A", "B"})

    def test_unmapped_skus_flagged(self):
        df = pd.DataFrame(
            [
                {"load_number": "L003", "shippedqty": 1, "itemnum": "UNKNOWN_ITEM", "fancy_cat": "Utility"},
                {"load_number": "L003", "shippedqty": 2, "itemnum": "5X8GW", "fancy_cat": "Utility"},
            ]
        )
        results = self.scorer.score_loads(
            df,
            column_map={
                "load_number": "load_number",
                "qty": "shippedqty",
                "sku": "itemnum",
                "trailer_hint": "fancy_cat",
            },
        )
        self.assertEqual(results.iloc[0]["unmapped_skus"], ["UNKNOWN_ITEM"])
        self.assertGreater(results.iloc[0]["utilization_pct"], 0)

    def test_output_schema_columns(self):
        df = pd.DataFrame(
            [
                {"load_number": "L001", "shippedqty": 1, "itemnum": "5X8GW", "fancy_cat": "Utility"},
            ]
        )
        results = self.scorer.score_loads(
            df,
            column_map={
                "load_number": "load_number",
                "qty": "shippedqty",
                "sku": "itemnum",
                "trailer_hint": "fancy_cat",
            },
        )
        expected_cols = {
            "load_number",
            "utilization_pct",
            "utilization_grade",
            "utilization_credit_ft",
            "total_linear_feet",
            "trailer_type",
            "capacity_ft",
            "position_count",
            "line_count",
            "unmapped_skus",
        }
        self.assertEqual(set(results.columns), expected_cols)

    def test_empty_dataframe(self):
        df = pd.DataFrame(columns=["load_number", "shippedqty", "itemnum", "fancy_cat"])
        results = self.scorer.score_loads(
            df,
            column_map={
                "load_number": "load_number",
                "qty": "shippedqty",
                "sku": "itemnum",
                "trailer_hint": "fancy_cat",
            },
        )
        self.assertEqual(len(results), 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests**

```bash
cd /home/atw/COT_Freight_Optimization_Tool
python3 -m pytest tests/test_cot_utilization_scorer.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_cot_utilization_scorer.py
git commit -m "test(cot_utilization): add batch scorer tests"
```

---

### Task 7: Update Package `__init__.py` and Final Verification

**Files:**
- Modify: `cot_utilization/__init__.py`

- [ ] **Step 1: Update `__init__.py` to include all public exports**

Re-read `cot_utilization/__init__.py` and ensure it exports the full public surface referenced by the spec:

```python
"""COT Utilization Scorer — standalone trailer utilization calculation."""

from cot_utilization.stack_calculator import (
    TRAILER_CONFIGS,
    TRAILER_PROFILE_OPTIONS,
    TRAILER_TYPE_SET,
    FIXED_CAPACITY_TRAILER_TYPES,
    DEFAULT_UTILIZATION_GRADE_THRESHOLDS,
    calculate_stack_configuration,
    trailer_profile_options,
    is_valid_trailer_type,
    normalize_trailer_type,
    item_deck_length_ft,
    normalize_upper_deck_exception_categories,
    apply_upper_usage_metadata,
    upper_deck_position_length_limit_ft,
    evaluate_upper_deck_overhang,
    capacity_overflow_feet,
    stack_display_index_map,
    check_stacking_compatibility,
)
from cot_utilization.scorer import UtilizationScorer

__all__ = [
    "TRAILER_CONFIGS",
    "TRAILER_PROFILE_OPTIONS",
    "TRAILER_TYPE_SET",
    "FIXED_CAPACITY_TRAILER_TYPES",
    "DEFAULT_UTILIZATION_GRADE_THRESHOLDS",
    "calculate_stack_configuration",
    "trailer_profile_options",
    "is_valid_trailer_type",
    "normalize_trailer_type",
    "item_deck_length_ft",
    "normalize_upper_deck_exception_categories",
    "apply_upper_usage_metadata",
    "upper_deck_position_length_limit_ft",
    "evaluate_upper_deck_overhang",
    "capacity_overflow_feet",
    "stack_display_index_map",
    "check_stacking_compatibility",
    "UtilizationScorer",
]
```

- [ ] **Step 2: Run the full test suite**

```bash
cd /home/atw/COT_Freight_Optimization_Tool
python3 -m pytest tests/ -v --tb=short 2>&1 | tail -50
```

Expected: all tests pass — package tests, scorer tests, and all existing app tests.

- [ ] **Step 3: Verify package installs cleanly**

```bash
cd /home/atw/COT_Freight_Optimization_Tool
pip install -e . 2>&1 | tail -5
python3 -c "from cot_utilization import UtilizationScorer, calculate_stack_configuration; print('Package OK')"
```

Expected: `Package OK`

- [ ] **Step 4: Commit**

```bash
git add cot_utilization/__init__.py
git commit -m "feat(cot_utilization): finalize package exports"
```

---

### Task 8: SKU Snapshot Export Script

**Files:**
- Create: `scripts/export_sku_snapshot.py`

This is the app-side scheduled export that writes the SKU specs to a file for downstream consumers.

- [ ] **Step 1: Write the export script**

```python
"""Export current SKU specifications to a CSV snapshot.

Usage:
    python scripts/export_sku_snapshot.py [--output PATH]

Defaults to writing ``data/exports/sku_specifications_snapshot.csv``.
The snapshot includes a header comment with generation metadata.
"""

import argparse
import csv
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import db

logger = logging.getLogger(__name__)

EXPORT_FIELDS = [
    "sku",
    "category",
    "description",
    "length_with_tongue_ft",
    "max_stack_step_deck",
    "max_stack_flat_bed",
]

DEFAULT_OUTPUT = ROOT / "data" / "exports" / "sku_specifications_snapshot.csv"


def export_sku_snapshot(output_path=None):
    """Read SKU specs from DB and write to CSV."""
    output_path = Path(output_path or DEFAULT_OUTPUT)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    specs = db.list_sku_specs()
    if not specs:
        logger.warning("No SKU specifications found in database.")
        return None

    generated_at = datetime.now(timezone.utc).isoformat()
    row_count = len(specs)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        f.write(f"# generated_at: {generated_at}\n")
        f.write(f"# row_count: {row_count}\n")
        writer = csv.DictWriter(f, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for spec in specs:
            row = {field: spec.get(field, "") for field in EXPORT_FIELDS}
            writer.writerow(row)

    logger.info("Exported %d SKU specs to %s", row_count, output_path)
    return str(output_path)


def main():
    parser = argparse.ArgumentParser(description="Export SKU specifications snapshot")
    parser.add_argument("--output", type=str, default=None, help="Output CSV path")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = export_sku_snapshot(args.output)
    if result:
        print(f"Snapshot written to {result}")
    else:
        print("No data to export.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the script runs** (requires a DB with seed data)

```bash
cd /home/atw/COT_Freight_Optimization_Tool
python3 scripts/export_sku_snapshot.py --output /tmp/sku_test_snapshot.csv
head -5 /tmp/sku_test_snapshot.csv
```

Expected: CSV with `# generated_at:` header comment, then `sku,category,description,...` header, then data rows.

- [ ] **Step 3: Commit**

```bash
git add scripts/export_sku_snapshot.py
git commit -m "feat(scripts): add SKU snapshot export for downstream consumers"
```

---

### Task 9: Final Integration Smoke Test

- [ ] **Step 1: Run all tests**

```bash
cd /home/atw/COT_Freight_Optimization_Tool
python3 -m pytest tests/ -v 2>&1 | tail -60
```

Expected: all tests pass with no regressions.

- [ ] **Step 2: End-to-end smoke test of the scorer**

```bash
cd /home/atw/COT_Freight_Optimization_Tool
python3 -c "
import pandas as pd
from cot_utilization import UtilizationScorer

scorer = UtilizationScorer.from_csv('data/seed/sku_specifications.csv')
df = pd.DataFrame([
    {'load_number': 'TEST-001', 'shippedqty': 6, 'itemnum': '5X8GW', 'fancy_cat': 'Utility'},
    {'load_number': 'TEST-001', 'shippedqty': 5, 'itemnum': '5.5X10GWE2K', 'fancy_cat': 'Utility'},
    {'load_number': 'TEST-002', 'shippedqty': 1, 'itemnum': '7X16TA', 'fancy_cat': 'Cargo'},
])
results = scorer.score_loads(
    df,
    column_map={'load_number': 'load_number', 'qty': 'shippedqty', 'sku': 'itemnum', 'trailer_hint': 'fancy_cat'},
    trailer_rules={'default': 'STEP_DECK', 'overrides': {'Cargo': 'WEDGE'}},
)
print(results[['load_number', 'utilization_pct', 'utilization_grade', 'trailer_type', 'capacity_ft']].to_string(index=False))
"
```

Expected: two rows — TEST-001 on STEP_DECK with a meaningful utilization score, TEST-002 on WEDGE.

- [ ] **Step 3: Verify the app can still start**

```bash
cd /home/atw/COT_Freight_Optimization_Tool
python3 -c "from blueprints.cot.routes import app; print('App loads OK')"
```

Expected: `App loads OK` — confirms all `services.stack_calculator` imports in routes, optimizer, load_builder, etc. still resolve.
