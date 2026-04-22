# ProGrade Inventory Gap Workflow (v0.5)

## Purpose
Defines the current inventory-gap panel behavior on the ProGrade load page for both brands:
- Big Tex: upload-backed availability (legacy workbook + BT inventory CSV).
- PJ: upload-backed availability from PJ inventory CSV with catalog fallback.

## Entry Points
- Load page: `/prograde/session/<session_id>/load`
- Inventory upload API: `POST /prograde/api/session/<session_id>/inventory/upload`

## Brand Modes
### Big Tex mode (`brand=bigtex`)
- Upload accepted formats:
  - Workbook (`.xlsx` / `.xlsm`), sheet `All.Orders.Quick` (case-insensitive).
  - BT inventory CSV (`itemnum`, `whse`, `serid`, `onhand`, `committed_`).
- Workbook parsing:
  - `C` (`Name`): populated means assigned, blank means available.
  - `M` (`Item #`): SKU key matched to `bigtex_skus`.
  - `R` (`Days Old`): populated means built, blank means future build.
- CSV parsing:
  - Item key is `itemnum`, matched to `bigtex_skus.item_number`.
  - Available units are computed as `onhand - committed_`.
  - Rows are de-duplicated by `serid` before aggregation.
  - Warehouse-specific aggregates are stored by `whse`.
- Snapshot aggregates persisted per item (all warehouses):
  - `total_count`
  - `available_count`
  - `assigned_count`
  - `built_count`
  - `future_build_count`
  - `available_built_count`
  - `available_future_count`
- Warehouse-level snapshot aggregates persisted per `(item_number, whse_code)`.

### PJ mode (`brand=pj`)
- Upload accepted format:
  - PJ inventory CSV (`hstrailerconfiglongitemid`, `itemid`, `inventsiteid`).
- CSV parsing:
  - Warehouse filter is driven by `inventsiteid` (site code).
  - Rows are de-duplicated by `id` when present (fallback composite key otherwise).
  - Canonical match target is `pj_skus.item_number`.
  - Normalization pipeline:
    - exact `hstrailerconfiglongitemid`
    - core token before first `-` suffix
    - model + bed-length inference using `itemid` and long-item parse
    - item-code fallback (`<model><2-digit length>`)
  - Unresolved SKUs remain visible as unmapped rows with inferred metadata when possible.
- Snapshot aggregates persisted per item and per `(item_number, whse_code)` using PJ inventory tables.
- If no PJ inventory upload exists yet, panel falls back to catalog candidates from `pj_skus`.

## Panel Behavior
- Panel title: `BT Inventory Gap Finder` or `PJ Inventory Gap Finder`.
- Both brand panels include:
  - `Upload Inventory` action (workbook or CSV).
  - `WHSE` dropdown when warehouse-level inventory exists (`ALL`, `301`, `306`, `501`, `601`, etc.).
- Summary tiles:
  - Remaining gap feet
  - Available units (upload-backed) or catalog SKU count (fallback mode)
  - Candidate row count
  - Active stack count
- Table shows:
  - Category, model, item #
  - Total footprint, stack height, available qty
  - One fit column per active stack (Stack 1 Fit, Stack 2 Fit, ...)
  - Each stack header includes stack length and remaining height
  - `+` action in a stack-fit cell adds directly to that specific stack target

## Fit and Suggest Logic
- Horizontal-gap targeting is not used in this mode.
- Candidate fit is evaluated per active stack using:
  - Stack remaining vertical headroom
  - Candidate footprint vs stack length
  - Existing brand constraints (BT/PJ rule engines) via simulated add checks
- Stack-fit actions are only shown when the candidate can be added without introducing new errors.

## Storage
In ProGrade SQLite (`PROGRADE_DB_PATH`):
- `bt_inventory_snapshot`: latest per-SKU BT aggregates.
- `bt_inventory_snapshot_whse`: latest per-SKU, per-warehouse BT aggregates.
- `bt_inventory_upload_log`: BT upload metadata history.
- `pj_inventory_snapshot`: latest per-SKU PJ aggregates.
- `pj_inventory_snapshot_whse`: latest per-SKU, per-warehouse PJ aggregates (`inventsiteid`).
- `pj_inventory_upload_log`: PJ upload metadata history.

## Current Process Standards
- Keep BT upload optional and non-blocking for load building.
- Keep PJ upload optional and non-blocking for load building.
- Keep PJ catalog fallback available when PJ SKUs exist and no PJ upload has been run.
- Keep fit scoring deterministic and tied to current session geometry/constraints.
