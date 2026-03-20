# Historical Load Utilization Report (Finance Workflow)

Script:
- `scripts/historical_load_utilization_report.py`

This version is designed for Finance users who only have a historical order/load report and should not need to know trailer length or stacking math.

## Workflow (What Finance Does)

1. Export one historical report (CSV/XLSX) with at least:
- `load number`
- `SO #`
- `SKU`
- `Quantity`
- `Origin plant` (recommended)

2. Run one command.

3. Review output CSV with a row per load and utilization/grade.

## Embedded Assumptions In Script

The script automatically embeds two core assumption sources:

1. SKU cheat sheet (stacking + length)
- Default source: `data/seed/sku_specifications.csv`
- Auto-loaded by script, no manual lookup required.
- For each SKU, script uses:
  - `length_with_tongue_ft`
  - `max_stack_step_deck`
  - `max_stack_flat_bed`
  - `category`

2. Default trailer-by-plant rules
- Uses app defaults from `services/load_builder.py`.
- Current plant overrides:
  - `VA -> FLATBED_48`
  - `NV -> STEP_DECK_48`
- Any other plant defaults to `STEP_DECK`.
- If origin plant is missing, script falls back to plant prefix from load number (first two letters).

## Best-Guess Stacking Logic

For each load, script does this:

1. Chooses trailer type in priority order:
- load-level override file (`--load-trailers`) if provided
- trailer type present on order report rows
- default by origin plant
- fallback by load number prefix

2. Converts each report row into stack-calculator input using cheat sheet:
- unit length from SKU cheat sheet
- max stack chosen by trailer type:
  - flatbed trailers use `max_stack_flat_bed`
  - step deck / other trailers use `max_stack_step_deck` (fallback to flatbed value)
- upper-deck stack assumption mirrors app behavior

3. Runs `stack_calculator.calculate_stack_configuration(...)` to produce:
- utilization percent
- utilization grade
- utilization credit feet
- deck usage and capacity warnings

## Required Columns In `--order-report`

Accepted aliases are shown so Finance can map to existing exports.

Required:
- Load number:
  - `load_number`, `load_no`, `load`, `load_id`, `load_name`
- Order number:
  - `order_number`, `order_no`, `order`, `so_num`, `sonum`, `sales_order`, `name`
- SKU:
  - `sku`, `item`, `itemnum`, `item_num`
- Quantity:
  - `qty`, `quantity`, `ordqty`

Recommended:
- Origin plant:
  - `origin_plant`, `plant`, `plant_code`

Optional:
- `trailer_type`
- `capacity_feet`
- `item_desc`, `category/bin`, `stop_sequence`, `destination/state/zip`

## Commands

Basic (single report only):

```bash
python scripts/historical_load_utilization_report.py \
  --order-report data/historical_orders_month.csv \
  --output exports/historical_utilization_month.csv
```

If you need a different cheat sheet file:

```bash
python scripts/historical_load_utilization_report.py \
  --order-report data/historical_orders_month.xlsx \
  --sku-cheat-sheet data/seed/sku_specifications.csv \
  --output exports/historical_utilization_month.csv
```

Optional trailer override file by load:

```bash
python scripts/historical_load_utilization_report.py \
  --order-report data/historical_orders_month.xlsx \
  --load-trailers data/historical_load_trailer_overrides.xlsx \
  --output exports/historical_utilization_month.csv
```

## Output Columns

Primary load-level results:
- `load_number`
- `origin_plant_assumed`
- `trailer_type_used`
- `capacity_feet_used`
- `utilization_pct`
- `utilization_grade`
- `utilization_credit_ft`
- `total_linear_feet`
- `exceeds_capacity`

Coverage / confidence fields:
- `rows_in_report_load`
- `rows_used_in_calc`
- `missing_sku_rows`
- `missing_skus`

## Data Quality Handling

- Rows missing load/order/sku/qty are skipped.
- SKUs missing from cheat sheet are skipped from calculations and listed in output/console notes.
- If an entire load has no calculable rows, that load is excluded and reported in notes.

## Important Interpretation Note

This is a retroactive modeled estimate, not a physical scan of how trailers were actually loaded. It applies the same default stacking assumptions used by the tool to generate a consistent utilization benchmark for Finance review.
