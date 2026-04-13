# Standalone Utilization Scorer Package

**Date:** 2026-04-10
**Status:** Revised Draft

## Problem

The COT Freight Optimization Tool calculates per-load utilization via the bin-packing logic in `services/stack_calculator.py`. That logic accounts for SKU-specific stacking limits, upper-deck promotion, two-across pairing, overflow allowances, trailer geometry, and utilization grading. It is not a simple length or capacity ratio.

A separate analytics workflow needs to score historical loads outside the web app. Today that logic is coupled to the Flask/SQLite application because `services/stack_calculator.py` imports `db` for planner-setting lookups and cache management.

The analytics workflow also needs current SKU specifications, but the scorer package should not own remote data access. This application will separately export SKU snapshots on a schedule; the analytics system will retrieve that snapshot and pass SKU data into the scorer.

## Goals

1. **Single source of truth**: utilization math lives in one reusable code path shared by the web app and external consumers.
2. **Cross-project reuse**: the scorer is installable from this repo without Flask or SQLite dependencies.
3. **Clean separation of concerns**: the package performs scoring only; the app owns scheduled SKU snapshot export; external systems own snapshot retrieval.
4. **Backward-safe app integration**: existing imports and runtime behavior in the web app continue to work after extraction.

## Non-Goals

- Building a public or unauthenticated SKU API.
- Making the scorer package responsible for HTTP, blob storage, or credential management.
- Reworking unrelated web app architecture beyond the extraction boundary.
- Supporting real-time or streaming scoring. This remains batch-oriented.

## Design

### 1. Package Boundary

Create a reusable package at the repo root:

```text
cot_utilization/
  __init__.py
  stack_calculator.py
  scorer.py
```

Packaging metadata should live at the repository root so the package can be installed with normal repo-based workflows.

Example install targets:

```bash
pip install git+https://<repo-url>.git
pip install -e .
```

The package must not import:

- `db.py`
- Flask modules
- route/controller code

The package may depend on lightweight data-processing libraries only if needed for the batch scorer. If the scorer can operate on plain iterables or records, prefer that over adding heavy dependencies.

### 2. Extracted Core Logic

Move the pure utilization logic from `services/stack_calculator.py` into `cot_utilization/stack_calculator.py`.

This extracted module should contain:

- trailer constants and trailer normalization helpers
- stack assumption normalization helpers
- upper-deck and overflow logic
- compatibility checks
- utilization grading logic
- `calculate_stack_configuration()`
- any other non-DB helpers currently used by app services, routes, scripts, or tests

The extraction must account for the current public surface already used across the app, not just `calculate_stack_configuration()`.

At minimum, preserve these exported contracts if they remain referenced by the app:

- `TRAILER_CONFIGS`
- `FIXED_CAPACITY_TRAILER_TYPES`
- `trailer_profile_options()`
- `is_valid_trailer_type()`
- `normalize_trailer_type()`
- `item_deck_length_ft()`
- `normalize_upper_deck_exception_categories()`
- `apply_upper_usage_metadata()`
- `upper_deck_position_length_limit_ft()`
- `evaluate_upper_deck_overhang()`
- `stack_display_index_map()`
- `check_stacking_compatibility()`
- `capacity_overflow_feet()`
- `calculate_stack_configuration()`

If additional helpers are used by runtime code or tests, include them as needed. The extraction is complete only when current app imports continue to work without behavior regression.

### 3. Injectable Settings

The extracted calculation code must no longer read planner settings from the database internally.

`calculate_stack_configuration()` should accept optional injected settings:

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
    ...
```

Default behavior in the package should remain deterministic when injected values are omitted.

Use package-local defaults matching current application defaults:

```python
DEFAULT_UTILIZATION_GRADE_THRESHOLDS = {
    "A": 85,
    "B": 70,
    "C": 55,
    "D": 40,
}

DEFAULT_STACK_ASSUMPTIONS = {
    "stack_overflow_max_height": 5,
    "max_back_overhang_ft": 4.0,
    "upper_two_across_max_length_ft": 7.0,
    "upper_deck_exception_max_length_ft": 16.0,
    "upper_deck_exception_overhang_allowance_ft": 6.0,
    "upper_deck_exception_categories": ["USA", "UTA"],
    "equal_length_deck_length_order_enabled": True,
}
```

Implementation note:

- The package may normalize per-field overrides into an internal assumptions dict.
- The package must not perform DB reads or caching.

### 4. Web App Compatibility Wrapper

`services/stack_calculator.py` remains the web app entrypoint and compatibility layer.

Responsibilities that stay in the app module:

- reading planner settings from `planning_settings`
- 30-second TTL cache management
- invalidation helpers for app settings updates

The wrapper should import the extracted package module, re-export the public symbols the app already uses, and preserve current runtime behavior.

Example pattern:

```python
from cot_utilization import stack_calculator as core

TRAILER_CONFIGS = core.TRAILER_CONFIGS
FIXED_CAPACITY_TRAILER_TYPES = core.FIXED_CAPACITY_TRAILER_TYPES
normalize_trailer_type = core.normalize_trailer_type
...

def calculate_stack_configuration(order_lines, **kwargs):
    assumptions = get_stack_capacity_assumptions()
    thresholds = get_utilization_grade_thresholds()
    merged_kwargs = {
        "grade_thresholds": thresholds,
        **kwargs,
    }
    for key, value in assumptions.items():
        merged_kwargs.setdefault(key, value)
    return core.calculate_stack_configuration(order_lines, **merged_kwargs)
```

Do not rely on `from ... import *` as the primary compatibility mechanism. The wrapper should make the retained public surface explicit.

### 5. Batch Scoring Interface

The reusable scorer must accept SKU specs as caller-provided input. It should not fetch them from the app or blob storage directly.

Preferred interface:

```python
scorer = UtilizationScorer(sku_lookup)
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

Acceptable alternative:

```python
results = score_loads(df, sku_lookup, column_map=..., trailer_rules=...)
```

All scorer entrypoints should normalize the provided SKU data into a case-insensitive lookup of:

- `length_with_tongue_ft`
- `max_stack_step_deck`
- `max_stack_flat_bed`
- `category`
- optional descriptive metadata if useful for debugging

Optional convenience helpers such as `from_csv()` are allowed for local development and testing, but they are not core architecture.

No package-level `from_url()` or blob client behavior should be included in this spec.

### 6. Score Processing Rules

Per load, the scorer should:

1. Group input rows by `load_number`.
2. Determine trailer type from `trailer_rules` and load rows.
3. Resolve each row to SKU specifications using case-insensitive lookup.
4. Convert resolved rows into the line-item shape expected by `calculate_stack_configuration()`.
5. Call `calculate_stack_configuration(...)`.
6. Return one scored output row per load.

Default `column_map` fields:

- `load_number`
- `qty`
- `sku`
- `trailer_hint`

`trailer_rules` behavior:

- `default`: trailer type to use when no override matches, defaulting to `"STEP_DECK"`
- `overrides`: map of source trailer-hint values to trailer types; if any line on a load matches an override, that trailer type applies to the whole load

### 7. Unmapped SKU Handling

When a SKU is missing from the provided snapshot or lookup:

1. Attempt dimension parsing from the SKU text using the same pattern currently used by the app:

```python
SKU_DIMENSION_PATTERN = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*[Xx]\s*(\d+(?:\.\d+)?)")
```

2. If parsing succeeds:
   - use the larger dimension as `unit_length_ft`
   - use `max_stack_height=1`
   - use `category="UNKNOWN"`

3. If parsing fails:
   - add the SKU to `unmapped_skus`
   - continue scoring with the remaining resolved items

This preserves batch output while making data quality gaps visible.

### 8. Batch Output Contract

The scorer should return one row per load with at least:

| Column | Type | Description |
|---|---|---|
| `load_number` | str | Input identifier |
| `utilization_pct` | float | Utilization score |
| `utilization_grade` | str | A/B/C/D/F |
| `utilization_credit_ft` | float | Credit feet earned |
| `total_linear_feet` | float | Total used linear feet |
| `trailer_type` | str | Applied trailer config |
| `capacity_ft` | float | Applied trailer capacity |
| `position_count` | int | Number of stack columns |
| `line_count` | int | Number of load rows consumed |
| `unmapped_skus` | list[str] | Unresolved SKUs |

Additional diagnostic columns are allowed if helpful for downstream analysis.

## Scheduled SKU Snapshot Export

This application must separately produce a scheduled SKU snapshot export for downstream analytics consumers.

This export is distinct from the scorer package and belongs to the app/runtime layer.

### Export Requirements

The app must publish a periodic snapshot containing, at minimum:

- `sku`
- `category`
- `description`
- `length_with_tongue_ft`
- `max_stack_step_deck`
- `max_stack_flat_bed`

Recommended metadata fields:

- `generated_at`
- `source_app_version`
- `row_count`

### Export Destination

The snapshot may be written to private blob storage or another approved private artifact location.

This spec does not mandate the retrieval mechanism for downstream consumers. The analytics environment is responsible for reading the snapshot and constructing `sku_lookup` input for the scorer.

### Export Security

- No new public or unauthenticated endpoint is required.
- The snapshot must be stored in a private location with environment-appropriate access control.
- Any credentials or upload configuration must follow existing app configuration and secret-management rules.

### Export Failure Behavior

- A failed export must not block core planner workflows.
- The last successful snapshot should remain usable by downstream consumers.
- Export failures should be logged and surfaced through the app’s normal operational monitoring path.

## Testing Strategy

### Package Tests

- Preserve and adapt existing stack-calculator tests to validate no behavioral drift after extraction.
- Add tests for injected grade thresholds.
- Add tests for scorer grouping, trailer inference, unmapped SKU handling, and output schema.

### App Compatibility Tests

- Verify `services/stack_calculator.py` continues to satisfy current app imports.
- Run existing tests that cover optimizer, load builder, replay evaluator, and stack-calculator assumptions.

### Snapshot Export Tests

- Verify the export uses the expected SKU fields and metadata.
- Verify export failures do not affect core app workflows.
- Verify at least one successful export path for the configured destination.

## Migration Risk

Primary risks:

1. The current `services/stack_calculator.py` surface is larger than a single function, so incomplete extraction can break runtime imports.
2. Packaging changes can fail if repo-level install metadata is not defined correctly.
3. Scheduled snapshot export introduces operational behavior that must not interfere with planner workflows.

Mitigations:

1. Preserve explicit public exports in the compatibility wrapper.
2. Run existing stack-calculator, optimizer, and replay-related tests unchanged where possible.
3. Keep the scorer package free of network and blob logic.
4. Treat the scheduled export as a separate app concern with isolated failure handling.

## File Change Summary

| File | Change |
|---|---|
| `cot_utilization/__init__.py` | New package exports |
| `cot_utilization/stack_calculator.py` | New extracted pure utilization logic |
| `cot_utilization/scorer.py` | New batch scoring interface using caller-provided SKU data |
| repo-root `pyproject.toml` | New or updated package metadata |
| `services/stack_calculator.py` | Modified compatibility wrapper with DB-backed setting injection |
| app-side export script/job module | New scheduled SKU snapshot export path |

