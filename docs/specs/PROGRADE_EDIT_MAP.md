# ProGrade Edit Map

## Purpose
This map is the fastest path for code changes in ProGrade, especially stacking logic and schematic rendering. It links common change requests to the exact files, endpoints, and tests.

## Start Here (Stacking + Schematic)
- `blueprints/prograde/templates/prograde/load_builder.html`
  - Contains ProGrade load builder markup, most ProGrade CSS, and client-side JS.
  - Includes drag/drop flow, rotate/remove/toggle actions, render mode behavior, and stack/zone UI.
- `blueprints/prograde/routes.py`
  - Session state assembly and all load builder APIs (`/add`, `/remove`, `/rotate`, `/position/move`, `/column/*`, `/check`).
  - Computes canvas payload via `_build_canvas_data(...)`.
- `blueprints/prograde/db.py`
  - Session and position persistence (add/move/resequence/duplicate/reset).
  - Settings data, SKU data, and inventory upload persistence.
- `blueprints/prograde/services/bt_rules.py`
  - Big Tex stacking/length/height constraint logic.
- `blueprints/prograde/services/pj_rules.py`
  - PJ stacking/length/height/measurement-aware rules.

## If You Need To Change...
### 1) Stack behavior in the schematic (drag/drop, resequence, move)
- `blueprints/prograde/templates/prograde/load_builder.html`
  - JS functions: `initSkuDragAndDrop`, `dispatchDrop`, `moveUnit`, `moveStack`.
- `blueprints/prograde/routes.py`
  - Endpoints: `api_move_position`, `api_move_column`, `api_move_column_zone`, `api_resequence_column`.
- `blueprints/prograde/db.py`
  - Persistence: `move_position`, `move_column`, `move_column_zone`, `resequence_column`.

### 2) Unit rotation, orientation, tongue rendering
- `blueprints/prograde/templates/prograde/load_builder.html`
  - UI action: `rotateUnit(positionId)`.
  - Render-mode classes and tongue/deck display rules.
- `blueprints/prograde/routes.py`
  - Endpoint: `api_rotate_unit`.
  - Position normalization: `_build_position_view(...)`.
- `blueprints/prograde/db.py`
  - Field update: `update_position_field`.
- Tests:
  - `tests/test_prograde_rotation.py`
  - `tests/test_prograde_render_mode.py`

### 3) Deck usage, overlap credit, and stack-height enforcement
- `blueprints/prograde/services/bt_rules.py`
  - `compute_bt_length_metrics`, `_bt_total_length`, `_bt_height`.
- `blueprints/prograde/services/pj_rules.py`
  - `compute_pj_length_metrics`, `_pj_total_length`, `_pj_height_lower`, `_pj_height_upper`.
- `blueprints/prograde/routes.py`
  - `api_check` + `_build_canvas_data(...)` consumption of metrics and violations.
- Tests:
  - `tests/test_prograde_overlap_metrics.py`

### 4) Add/remove unit behavior from picker or inventory panel
- `blueprints/prograde/templates/prograde/load_builder.html`
  - JS: `addUnit`, `removeUnit`, picker modal helpers.
- `blueprints/prograde/routes.py`
  - Endpoints: `api_add_unit`, `api_remove_unit`.
- `blueprints/prograde/db.py`
  - Persistence: `add_position`, `remove_position`.

### 5) Big Tex inventory gap panel and upload
- `blueprints/prograde/templates/prograde/load_builder.html`
  - Inventory panel markup/controls.
- `blueprints/prograde/routes.py`
  - `api_upload_bt_inventory`, `_build_bt_inventory_gap_data`.
- `blueprints/prograde/db.py`
  - `import_bigtex_inventory_orders_workbook`, upload/snapshot fetchers.
- Tests:
  - `tests/test_prograde_inventory_upload.py`

## API Surface (Load Builder)
- Session state and validation:
  - `GET /prograde/api/session/<session_id>/state`
  - `GET /prograde/api/session/<session_id>/check`
- Unit actions:
  - `POST /prograde/api/session/<session_id>/add`
  - `POST /prograde/api/session/<session_id>/remove`
  - `POST /prograde/api/session/<session_id>/rotate`
  - `POST /prograde/api/session/<session_id>/toggle_axle_drop`
  - `POST /prograde/api/session/<session_id>/toggle_dump_door`
- Drag/drop and stack actions:
  - `POST /prograde/api/session/<session_id>/position/move`
  - `POST /prograde/api/session/<session_id>/column/move`
  - `POST /prograde/api/session/<session_id>/column/move-zone`
  - `POST /prograde/api/session/<session_id>/column/duplicate`
  - `POST /prograde/api/session/<session_id>/column/resequence`

## Fast Test Commands
- Stacking/overlap math:
  - `pytest tests/test_prograde_overlap_metrics.py`
- Rotate + render behavior:
  - `pytest tests/test_prograde_rotation.py tests/test_prograde_render_mode.py`
- Session/load builder workflow:
  - `pytest tests/test_prograde_sessions_workflow.py tests/test_prograde_settings_save.py`
- Inventory panel:
  - `pytest tests/test_prograde_inventory_upload.py`

## Current File Organization Risks
- `load_builder.html` is the main UI bottleneck (template + CSS + JS in one file).
- `routes.py` includes both route definitions and substantial state-shaping/helper logic.
- `db.py` owns both schema/bootstrap logic and many runtime data-access concerns.

## Recommended Refactor Sequence (for Faster Future Edits)
1. Split load builder template:
   - Extract `templates/prograde/_load_canvas.html`
   - Extract `templates/prograde/_sku_picker.html`
   - Extract `templates/prograde/_inventory_gap_panel.html`
   - Keep page-level wrapper in `load_builder.html`.
2. Move ProGrade client JS out of template:
   - `blueprints/prograde/static/js/load_builder_state.js`
   - `blueprints/prograde/static/js/load_builder_dragdrop.js`
   - `blueprints/prograde/static/js/load_builder_picker.js`
3. Move ProGrade CSS out of template:
   - `blueprints/prograde/static/css/load_builder.css`
4. Split route concerns:
   - Keep HTTP handlers in `routes.py`.
   - Move state composition helpers into `blueprints/prograde/services/canvas_state.py`.
5. Split DB concerns:
   - Keep connection/init in `db.py`.
   - Move session-position mutations to `blueprints/prograde/repositories/session_positions.py`.
   - Move SKU/settings access to `blueprints/prograde/repositories/settings_repo.py`.

This sequence keeps behavior stable while reducing search/edit surface area for the core stacking/schematic workflow.
