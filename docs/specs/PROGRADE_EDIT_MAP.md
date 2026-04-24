# ProGrade Edit Map

## Purpose
This is the fastest path for ProGrade edits. It maps common requests to the exact files, endpoints, and tests currently in use.

## Start Here
- `blueprints/prograde/routes.py`
  - Route handlers and state assembly for load builder and settings.
- `blueprints/prograde/templates/prograde/settings.html`
  - ProGrade settings UI and save/import client logic.
- `blueprints/prograde/templates/prograde/load_builder.html`
  - Main load page CSS + JS and sidebar picker behavior.
- `blueprints/prograde/templates/prograde/_load_canvas.html`
  - Manifest card markup under the load canvas.
- `blueprints/prograde/templates/prograde/_inventory_gap_panel.html`
  - Inventory gap panel markup for both BT and PJ modes.
- `blueprints/prograde/db.py`
  - Schema and persistence for sessions, positions, SKUs, settings, and BT inventory snapshots.
- `blueprints/prograde/services/bt_rules.py`
- `blueprints/prograde/services/pj_rules.py`
- `blueprints/prograde/services/inventory_gap_finder.py`

## If You Need To Change
### 1) Settings standards and save behavior
- `blueprints/prograde/templates/prograde/settings.html`
  - Editable settings tables and tabbed standards layout.
- `blueprints/prograde/routes.py`
  - `settings`, `api_settings_save`, `api_bigtex_import`, `api_pj_import`.
- `blueprints/prograde/db.py`
  - `update_*` field handlers and recompute helpers for PJ/BT SKU metrics.
- PJ dimension contract (current):
  - `pj_skus` is the source of truth for PJ dimensions (`bed_length_measured`, `tongue_feet`, `height_mid_ft`, `height_top_ft`, `dump_side_height_ft`).
  - Legacy `pj_height_reference` and `pj_tongue_groups` are no longer wired to settings save/UI paths.
- Tests:
  - `tests/test_prograde_settings_save.py`

### 2) Add SKUs panel behavior (hierarchy/filter/collapse)
- `blueprints/prograde/templates/prograde/load_builder.html`
  - JS functions: `renderSkuTree`, `applySkuFilter`, `bindSkuAutoCollapseHandlers`, `initSkuHierarchyControls`.
- Notes:
  - Big Tex supports model/item hierarchy mode toggles.
  - Auto-collapse is active when no search query is applied.

### 3) Manifest and inventory table readability
- `blueprints/prograde/templates/prograde/_load_canvas.html`
  - Manifest column structure.
- `blueprints/prograde/templates/prograde/_inventory_gap_panel.html`
  - Inventory table columns and add controls.
- `blueprints/prograde/templates/prograde/load_builder.html`
  - Table CSS tokens, sticky headers, and compact row density.

### 4) Stack behavior (drag/drop/resequence/move)
- `blueprints/prograde/templates/prograde/load_builder.html`
  - JS: `initSkuDragAndDrop`, `dispatchDrop`, `moveUnit`, `moveStack`.
- `blueprints/prograde/routes.py`
  - `api_move_position`, `api_move_column`, `api_move_column_zone`, `api_resequence_column`.
- `blueprints/prograde/db.py`
  - `move_position`, `move_column`, `move_column_zone`, `resequence_column`.

### 5) Unit actions (add/remove/rotate/toggles)
- `blueprints/prograde/routes.py`
  - `api_add_unit`, `api_remove_unit`, `api_rotate_unit`, `api_toggle_axle_drop`, `api_toggle_dump_door`.
- `blueprints/prograde/db.py`
  - Position row mutation and override persistence.
- Rule of thumb:
  - Big Tex defaults new units to left-facing tongues (`is_rotated=1`) on add; use rotate only for intentional exceptions.
- Tests:
  - `tests/test_prograde_rotation.py`
  - `tests/test_prograde_dump_height_overrides.py`
  - `tests/test_prograde_render_mode.py`

### 6) Inventory gap scoring and upload
- `blueprints/prograde/services/inventory_gap_finder.py`
  - Fit scoring (`stack_top`, `horizontal`, or no-fit) and suggested qty.
- `blueprints/prograde/routes.py`
  - `api_upload_bt_inventory` and load-page state hydration.
- `blueprints/prograde/db.py`
  - BT workbook import + snapshot persistence.
- Tests:
  - `tests/test_prograde_inventory_gap_finder.py`
  - `tests/test_prograde_inventory_upload.py`

### 7) Export / print summary output
- `blueprints/prograde/routes.py`
  - `export_load` route payload and print-mode flags.
- `blueprints/prograde/templates/prograde/export.html`
  - Print-to-PDF summary layout (schematic + manifest).
- `blueprints/prograde/templates/prograde/_spatial_canvas.html`
  - Shared schematic drawing used by load builder and export output.
- Tests:
  - `tests/test_prograde_export_summary.py`

## API Surface
- Load state + validation:
  - `GET /prograde/api/session/<session_id>/state`
  - `GET /prograde/api/session/<session_id>/check`
- Export:
  - `GET /prograde/session/<session_id>/export`
  - `GET /prograde/session/<session_id>/export.pdf`
- Unit actions:
  - `POST /prograde/api/session/<session_id>/add`
  - `POST /prograde/api/session/<session_id>/remove`
  - `POST /prograde/api/session/<session_id>/rotate`
  - `POST /prograde/api/session/<session_id>/toggle_axle_drop`
  - `POST /prograde/api/session/<session_id>/toggle_dump_door`
  - `POST /prograde/api/session/<session_id>/nest`
  - `POST /prograde/api/session/<session_id>/carrier`
- Drag/drop:
  - `POST /prograde/api/session/<session_id>/position/move`
  - `POST /prograde/api/session/<session_id>/column/move`
  - `POST /prograde/api/session/<session_id>/column/move-zone`
  - `POST /prograde/api/session/<session_id>/column/duplicate`
  - `POST /prograde/api/session/<session_id>/column/resequence`
- Settings:
  - `GET /prograde/settings`
  - `POST /prograde/api/settings/save`
  - `POST /prograde/api/settings/pj/import`
  - `POST /prograde/api/settings/bigtex/import`
- Inventory:
  - `POST /prograde/api/session/<session_id>/inventory/upload`

## Fast Test Commands
- `pytest tests/test_prograde_settings_save.py`
- `pytest tests/test_prograde_sessions_workflow.py`
- `pytest tests/test_prograde_rotation.py tests/test_prograde_render_mode.py`
- `pytest tests/test_prograde_inventory_gap_finder.py tests/test_prograde_inventory_upload.py`
