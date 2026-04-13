# ProGrade BT Inventory Gap Workflow (v0.1)

## Purpose
Adds a Big Tex-only inventory-aware panel on the ProGrade load builder page so planners can upload a current orders workbook and see which available SKUs can fill remaining trailer space.

## Entry Point
- Page: `/prograde/session/<session_id>/load`
- Upload API: `POST /prograde/api/session/<session_id>/inventory/upload`

## Workbook Contract
- Sheet expected: `All.Orders.Quick` (case-insensitive)
- Row grain: each row represents one item unit
- Parsed columns:
  - `C` (`Name`): populated = already assigned, blank = available inventory
  - `M` (`Item #`): SKU key used to aggregate inventory and join to `bigtex_skus`
  - `R` (`Days Old`): blank = future build, populated = already built

## Aggregation Logic
For each `Item #`, the importer stores:
- `total_count`
- `available_count` (`Name` blank)
- `assigned_count` (`Name` populated)
- `built_count` (`Days Old` populated)
- `future_build_count` (`Days Old` blank)
- `available_built_count` (`Name` blank + `Days Old` populated)
- `available_future_count` (`Name` blank + `Days Old` blank)

## UI Behavior
- New panel on load builder: **BT Inventory Gap Finder**
- Shows:
  - upload button
  - latest upload metadata
  - remaining deck gap (carrier length - current load footprint)
  - inventory table with available counts highlighted
  - fit indicator (`Fits Gap`) and `Suggest Qty` based on remaining gap and SKU footprint

## Storage
In ProGrade SQLite (`PROGRADE_DB_PATH`):
- `bt_inventory_snapshot`: latest per-SKU aggregated counts
- `bt_inventory_upload_log`: upload metadata history

## Current Scope
- Big Tex sessions only (`brand=bigtex`)
- PJ inventory ingestion intentionally deferred for later phase
