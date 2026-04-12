# Standalone Utilization Scorer Package

**Date:** 2026-04-10
**Status:** Approved

## Problem

The COT Freight Optimization Tool calculates a per-load utilization score via a 2D bin-packing algorithm in `services/stack_calculator.py`. This score accounts for SKU-specific stacking limits, upper-deck promotion, two-across pairing, overflow allowances, and trailer geometry — it is not a simple length/capacity ratio.

A separate analytics project needs to score historical loads from a parquet dataset on blob storage using the same algorithm. Today there is no way to do this without running the full web app. The scoring logic is coupled to the Flask/SQLite stack via `import db` in `stack_calculator.py`.

## Goals

1. **Single source of truth** — the utilization math lives in one place, used by both the web app and external projects. No reimplementation, no drift.
2. **Cross-project reuse** — the scorer is pip-installable from this repo's git URL, with no Flask/SQLite dependency.
3. **Live SKU access** — the consuming project can fetch current SKU specifications from the running web app via API.

## Non-Goals

- Replacing or restructuring the web app's internal architecture beyond what's needed for extraction.
- Building a full REST API for the web app. Only one new endpoint (read-only SKU specs).
- Supporting real-time/streaming scoring. This is batch-oriented.

## Design

### 1. Package: `cot_utilization/`

A new directory at the repo root containing a pip-installable Python package.

```
cot_utilization/
  __init__.py              # exports UtilizationScorer, calculate_stack_configuration
  stack_calculator.py      # pure math — bin-packing, credit calc, grading, trailer configs
  scorer.py                # batch scoring: DataFrame in -> scored DataFrame out
  sku_loader.py            # load SKU specs from URL, CSV, or dict
  pyproject.toml           # package metadata + dependencies (pandas, pyarrow, requests)
```

#### Key design decisions

- **The pure math moves here.** `calculate_stack_configuration()` and all its internal helpers (`_position_credit_multiplier`, `_calculate_total_credit_feet`, `_grade_utilization`, bin-packing loop, trailer configs, default constants) move from `services/stack_calculator.py` into `cot_utilization/stack_calculator.py`.
- **Zero database dependency.** The package never imports `db.py`. Settings (stack assumptions, grade thresholds) are passed as parameters with hardcoded defaults as fallback.
- **Installation:** `pip install git+https://<repo-url>.git` or `pip install -e .` for local development.

### 2. Injectable Settings

`calculate_stack_configuration()` gains two optional parameters:

```python
def calculate_stack_configuration(
    order_lines,
    trailer_type=None,
    capacity_feet=None,
    ...,
    stack_assumptions=None,      # dict, optional
    grade_thresholds=None,       # dict, optional
):
```

**When `stack_assumptions` is provided:** used directly. No DB read, no cache.
**When `None`:** hardcoded defaults apply:

```python
DEFAULT_STACK_ASSUMPTIONS = {
    "stack_overflow_max_height": 5,
    "max_back_overhang_ft": 4.0,
    "upper_two_across_max_length_ft": 7.0,
    "upper_deck_exception_max_length_ft": 16.0,
    "upper_deck_exception_overhang_allowance_ft": 6.0,
    "upper_deck_exception_categories": ["USA", "UTA"],
    "equal_length_deck_length_order_enabled": True,
}

DEFAULT_GRADE_THRESHOLDS = {"A": 85, "B": 70, "C": 55, "D": 40}
```

Same pattern for `grade_thresholds`.

### 3. Web App Wrapper: `services/stack_calculator.py`

After extraction, this file becomes a thin wrapper that:

1. **Imports everything from the package:** `from cot_utilization.stack_calculator import *`
2. **Overrides `calculate_stack_configuration()`** to inject DB-sourced settings:

```python
from cot_utilization.stack_calculator import (
    calculate_stack_configuration as _core_calc,
)

def calculate_stack_configuration(order_lines, **kwargs):
    assumptions = get_stack_capacity_assumptions()    # reads from DB + cache
    thresholds = get_utilization_grade_thresholds()   # reads from DB + cache
    return _core_calc(
        order_lines,
        stack_assumptions=assumptions,
        grade_thresholds=thresholds,
        **kwargs,
    )
```

3. **Retains DB-specific functions** that stay in the web app layer:
   - `get_stack_capacity_assumptions()` — reads `planning_settings`, caches with 30s TTL
   - `get_utilization_grade_thresholds()` — reads `planning_settings`, caches with 30s TTL
   - `invalidate_stack_assumptions_cache()`, `invalidate_utilization_grade_thresholds_cache()`

4. **Re-exports all public symbols** from the package so that existing `from services.stack_calculator import X` statements throughout the web app continue to work with no changes.

### 4. Batch Scorer: `cot_utilization/scorer.py`

#### Class: `UtilizationScorer`

**Constructors:**

```python
# Primary — fetch live SKU specs from the web app API
scorer = UtilizationScorer.from_url("https://your-app/api/skus/specifications")

# Fallback — load from a local CSV export
scorer = UtilizationScorer.from_csv("/path/to/sku_specifications.csv")

# Programmatic — pass a pre-built dict
scorer = UtilizationScorer.from_dict(sku_lookup)
```

All constructors produce the same internal state: a case-insensitive dict mapping SKU name to `{length_with_tongue_ft, max_stack_step_deck, max_stack_flat_bed, category}`.

**Scoring method:**

```python
results = scorer.score_loads(
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
```

**`column_map`** — maps scorer's expected fields to actual DataFrame column names. Defaults assume `load_number`, `qty`, `sku`, `trailer_hint`.

**`trailer_rules`** — determines trailer type per load:
- `default`: trailer type when no override matches (default: `"STEP_DECK"`)
- `overrides`: dict mapping `trailer_hint` column values to trailer types. If any line on a load matches a key, that trailer type is used for the entire load.

**Processing per load:**

1. Group input DataFrame by `load_number`.
2. For each group, determine trailer type from `trailer_rules` + `fancy_cat` values.
3. For each line item, resolve SKU via:
   a. Exact match (case-insensitive) against SKU specs.
   b. Dimension parsing from SKU name via regex `(\d+(?:\.\d+)?)\s*[Xx]\s*(\d+(?:\.\d+)?)`.
   c. If both fail, flag as unmapped. Use parsed dimensions with `max_stack=1` if dimensions were extractable.
4. Build `line_items` array for the load.
5. Call `calculate_stack_configuration(line_items, trailer_type=..., capacity_feet=...)`.
6. Collect results.

**Output DataFrame** — one row per load:

| Column | Type | Description |
|---|---|---|
| `load_number` | str | Passthrough from input |
| `utilization_pct` | float | Utilization score (0-100+) |
| `utilization_grade` | str | A/B/C/D/F |
| `utilization_credit_ft` | float | Total credit feet earned |
| `total_linear_feet` | float | Raw linear feet on trailer |
| `trailer_type` | str | Trailer config used |
| `capacity_ft` | float | Trailer total capacity |
| `position_count` | int | Number of stack columns |
| `unmapped_skus` | list[str] | SKUs that couldn't be resolved |
| `line_count` | int | Number of input line items |

### 5. SKU Loader: `cot_utilization/sku_loader.py`

Three loading strategies, all producing the same dict:

- **`from_url(url)`** — `GET` request to the SKU endpoint, parse JSON response, build lookup dict. Raises on HTTP errors.
- **`from_csv(path)`** — read CSV with pandas, build lookup dict. Expects columns matching `sku_specifications.csv` schema.
- **`from_dict(d)`** — passthrough validation. Expects `{sku_name: {length_with_tongue_ft, max_stack_step_deck, max_stack_flat_bed, category}}`.

All keys normalized to uppercase for case-insensitive matching.

### 6. API Endpoint: `GET /api/skus/specifications`

New route in `blueprints/cot/routes.py`.

**No authentication.** This is the only unauthenticated route in the app. It returns read-only product dimension data — not sensitive.

**Response:**

```json
{
  "skus": [
    {
      "sku": "5X8GW",
      "category": "USA",
      "description": "",
      "length_with_tongue_ft": 12.0,
      "max_stack_step_deck": 5,
      "max_stack_flat_bed": 4
    }
  ],
  "count": 261
}
```

**Excluded fields:** `id`, `added_at`, `source`, `updated_at`, `updated_by`, `created_at` — internal bookkeeping not relevant to scoring.

**Implementation:** ~15 lines. `SELECT` from `sku_specifications`, serialize to dicts, return `jsonify`.

### 7. Unmapped SKU Handling

When `itemnum` doesn't match any SKU in the specs:

**Step 1 — Dimension parsing.** Apply the existing regex pattern to extract dimensions from the SKU name:

```python
SKU_DIMENSION_PATTERN = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*[Xx]\s*(\d+(?:\.\d+)?)")
```

Example: `"5X8GW"` → width=5, length=8. Use the larger value as `unit_length_ft`. Default `max_stack_height=1` (conservative — no stacking assumed). Default `category="UNKNOWN"`.

**Step 2 — Flag.** If dimension parsing also fails (no match), add the SKU to the load's `unmapped_skus` list. The load is still scored using whatever items did resolve — utilization will be understated but not missing.

## Testing Strategy

- **Unit tests for the package:** test `calculate_stack_configuration()` with known inputs/outputs, matching existing `test_stack_calculator_assumptions.py` cases to verify no behavioral change after extraction.
- **Integration test for the wrapper:** verify `services/stack_calculator.py` produces identical results before and after the refactor by running existing test suite unchanged.
- **Scorer tests:** test `score_loads()` with a small synthetic DataFrame — verify grouping, trailer inference, SKU resolution, unmapped handling, output schema.
- **Endpoint test:** verify `/api/skus/specifications` returns valid JSON with expected schema, no auth required.

## Migration Risk

The primary risk is the extraction of ~2000 lines of pure math from `services/stack_calculator.py` into the package. Mitigations:

1. **Existing test suite runs unchanged** — `services/stack_calculator.py` re-exports all symbols, so all current imports and tests continue to work. If tests pass, the extraction preserved behavior.
2. **No logic changes during extraction** — the move is mechanical. Injectable settings are additive (new optional params with defaults matching current behavior).
3. **The web app wrapper injects the same DB-sourced settings** that the functions previously read internally. Net behavior is identical.

## File Change Summary

| File | Change |
|---|---|
| `cot_utilization/__init__.py` | New — package exports |
| `cot_utilization/stack_calculator.py` | New — pure math extracted from services |
| `cot_utilization/scorer.py` | New — batch scoring logic |
| `cot_utilization/sku_loader.py` | New — SKU loading from URL/CSV/dict |
| `cot_utilization/pyproject.toml` | New — package metadata |
| `services/stack_calculator.py` | Modified — becomes thin wrapper + DB functions |
| `blueprints/cot/routes.py` | Modified — add `GET /api/skus/specifications` |
