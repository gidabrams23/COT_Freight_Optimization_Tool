# ProGrade Inventory Gap Workflow (v0.4)

## Purpose
Defines the current inventory-gap panel behavior on the ProGrade load page for both brands:
- Big Tex: upload-backed availability (legacy workbook + BT inventory CSV).
- PJ: catalog-based suggestions from loaded PJ SKUs.

## Entry Points
- Load page: `/prograde/session/<session_id>/load`
- BT upload API: `POST /prograde/api/session/<session_id>/inventory/upload`

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
- No workbook upload in current process.
- Candidates are derived directly from `pj_skus`.
- Panel scores fit by stack-level vertical headroom and stack length constraints.

## Panel Behavior
- Panel title: `BT Inventory Gap Finder` or `PJ Inventory Gap Finder`.
- Big Tex panel includes:
  - `Upload Inventory` action (workbook or CSV).
  - `WHSE` dropdown when warehouse-level inventory exists (`ALL`, `501`, `601`, etc.).
- Summary tiles:
  - Remaining gap feet
  - Available units (BT) or catalog SKU count (PJ)
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
- PJ catalog mode does not write a separate inventory snapshot table.

## Current Process Standards
- Keep BT upload optional and non-blocking for load building.
- Keep PJ catalog mode always available when PJ SKUs exist.
- Keep fit scoring deterministic and tied to current session geometry/constraints.
