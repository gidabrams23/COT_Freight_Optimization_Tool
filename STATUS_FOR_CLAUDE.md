# AppDev-V2 Status Summary (for Claude)

Last updated: 2026-01-23

## High-level purpose
Internal dispatch optimization web app. Upload open orders CSV, compute utilization (per load utilization framework), review orders, and build optimized draft loads with geographic/time-window clustering.

## Tech stack
- Python 3 + Flask
- SQLite (`app.db`)
- Jinja templates + single CSS (`static/styles.css`)
- pandas + openpyxl for file ingestion

## Current UI / Interface (redesigned)
- **Global UI**: new “enterprise logistics” look with bold typography, modern cards, KPIs, split layouts, and Material Symbols icons.
- **Top nav**: Upload, Orders, Optimize, Loads, Rates, SKUs, Lookups.

### Upload page (`/upload`)
- Drag-and-drop style CSV upload form.
- Upload summary with totals, mapping rate, and unmapped items table.

### Orders dashboard (`/orders`)
- KPI cards (Open Orders, Total Capacity ft, Avg Utilization, Excluded Orders).
- Split layout:
  - Left: orders table with filters (plant/state/customer), bulk actions (exclude/include/clear), export CSV.
  - Right: upload widget + quick “Load Builder” panel + map placeholder.
- Expandable rows show **line items** and **stack configuration** via `/api/orders/<so_num>/stack-config`.

### Optimize page (`/optimize`)
- Optimization settings (plant, capacity 53 ft default, max detour %, time window, geo radius).
- Results summary cards (baseline vs optimized).
- Draft loads presented as cards with utilization meter + metrics + line previews.

### Loads page (`/loads`)
- Table of draft loads built by optimizer (legacy table view).

## Core functionality
- **CSV ingestion**: `OrderImporter` parses CSV (Amanda report format), maps SKU specs, calculates utilization via stack rules, and writes to DB.
- **Stacking/utilization**: `stack_calculator.py` implements position-based stacking and capacity checks (53' step deck split 43'+10'), producing utilization grade.
- **Order review**: filters and expand to show stack layout.
- **Optimization**:
  - Group by SONUM, cluster by geography (Haversine) and time windows.
  - Greedy pack into loads up to capacity (53 ft).
  - Cost model: max((miles * 1.2 * rate) + (stops * $55), $800).
  - Loads are scored by utilization + consolidation + route efficiency.

## Key DB schema (current)

### orders
Stores order-level summary (SONUM). Includes:
- `so_num`, `due_date`, `plant`, `cust_name`, `state`, `zip`
- `total_qty`, `total_length_ft`, `utilization_pct`, `utilization_grade`, `utilization_credit_ft`, `exceeds_capacity`, `line_count`, `is_excluded`

### order_lines
Stores line items:
- Item, qty, SKU, unit length, total length, stack height
- Zip, plant, due date, customer info

### loads (new schema)
Rebuilt table (old columns removed):
- `origin_plant`, `destination_state`
- `estimated_miles`, `rate_per_mile`, `estimated_cost`
- `utilization_pct`, `optimization_score`, `status`, `created_at`

### load_lines
Links loads to line items.

### zip_coordinates
ZIP ? lat/lng (imported from uszips.xlsx).

### plants
Plant code ? lat/lng (seeded).

### optimized_loads / optimization_runs / load_order_assignments
Created for PRD, but primary UI still uses `loads` table.

## Data ingestion & reference files
- `uszips.xlsx` is imported into `zip_coordinates` table using `scripts/import_uszips.py`.
- `static/data/zip_coords.json` still exists but DB is now primary.

## Recent fixes & changes
- Removed the **capacity must be at least largest grouped order** hard error in `load_builder.py`.
- Optimizer now uses order-level `total_length_ft` from `orders` table (stack framework) rather than line-summed lengths.
- Added migration for legacy `loads` table to prevent SQLite NOT NULL integrity errors.
- Modern UI redesign applied to base, orders, optimize, upload pages.

## Known issues / open items
- Optimization can be slow on large datasets (1k+ orders).
- Debug server logs are limited unless running with file redirects.
- Optimized loads are persisted to `loads` table; the newer `optimized_loads` tables are not yet wired to UI.
- No async upload progress (visual placeholders only).

## Relevant files for Claude to inspect
- `app.py`
- `db.py`
- `services/optimizer.py`
- `services/load_builder.py`
- `services/stack_calculator.py`
- `services/order_importer.py`
- `services/geo_utils.py`
- `templates/orders.html`
- `templates/optimize.html`
- `templates/upload.html`
- `static/styles.css`

## Suggested next work for Claude
- Make optimizer use `optimized_loads` tables + API endpoints instead of the legacy `loads` table.
- Add per-order splitting when a single order exceeds 53' capacity.
- Add upload progress (AJAX) and optimizer progress states.
- Integrate map preview using Mapbox or Google Maps.
- Improve optimization heuristic (route-based batching) and performance.
