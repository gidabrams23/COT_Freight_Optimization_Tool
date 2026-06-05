# IT Handoff - ProGrade Blueprint Architecture and Maintenance Guide

## 1) Purpose and Operational Background

### Purpose
This document is the technical handoff for IT and development support teams responsible for operating, troubleshooting, and maintaining the ProGrade blueprint in its current production state. It is written to be readable by technical stakeholders who need both operational context and implementation detail.

### Current-State Product Position
ProGrade is not a separate standalone application in the current codebase. It is a Flask blueprint mounted inside the existing freight-planning web application and served under the `/prograde/*` route family. In practice, operators experience it as a distinct tool, but IT should understand that it shares the parent Flask/Gunicorn/Azure runtime with the broader app while maintaining its own SQLite persistence model, account model, settings, SKU catalogs, and load-builder workflows.

### Operational Background
ProGrade supports trailer-load planning for multiple brands through a visual, planner-driven load builder. The operator workflow is intentionally compact: choose an account, start a load session, pick a trailer/carrier profile, add units from the SKU catalog, arrange stacks visually, validate constraints, optionally overlay available inventory, and export a load summary for execution or review.

Normal business flow:
1. Planner selects or creates a ProGrade account and chooses a brand context.
2. Planner creates a new load session.
3. Planner selects the carrier/trailer profile for the load.
4. Planner adds SKUs to the canvas from the brand catalog.
5. Planner adjusts stack position, orientation, nesting, axle/drop-door state, and column placement.
6. Planner reviews validation feedback and resolves constraint issues.
7. Planner optionally uploads or refreshes inventory data to evaluate gap-fill candidates.
8. Planner saves the session and exports a summary artifact (`HTML`, `PDF`, or load-sheet `XLSX`).

What healthy operation looks like:
- Users can reach `/prograde`, select an account, and open saved sessions.
- New sessions open correctly for the requested brand and carrier type.
- SKU catalogs and settings pages load without missing-reference errors.
- Canvas edits persist and validation feedback refreshes correctly.
- Optional inventory upload and SQL refresh work for supported brands.
- Export routes return valid print and spreadsheet outputs.

## 2) System Architecture

### Runtime Topology
ProGrade currently runs inside the shared app runtime rather than in its own deployment unit.

```text
Planner Browser
  -> Azure App Service (Linux Container)
  -> Gunicorn gthread workers
  -> Flask application
  -> ProGrade blueprint (`/prograde/*`)
  -> ProGrade service layer
  -> ProGrade SQLite database (`prograde.db`)
```

Optional outbound dependencies:
- SQL Server / Synapse connectivity for inventory SQL refresh.
- File-based workbook and CSV imports for settings and inventory.

### Technology Stack
- Web framework: Flask
- Rendering model: server-rendered Jinja templates with client-side JavaScript for canvas interactions
- Application server: Gunicorn `gthread`
- Persistence: SQLite (`blueprints/prograde/db.py`)
- Data processing: `pandas`, `openpyxl`
- Image/PDF export support: `Pillow`, browser print-to-PDF route support
- SQL refresh integration: `SQLAlchemy` + `pyodbc`
- Hosting model: containerized Python app on Azure App Service Linux

### Architectural Characteristics
- Single-instance-friendly design due to SQLite persistence and process-local interaction patterns.
- Shared web runtime with COT, but separate ProGrade database and support model.
- Rule engines are brand-aware, while the operator experience is intentionally one cohesive load-building workflow.
- Most interactive behavior is orchestrated through one large route/controller module plus a template-heavy load builder UI.

## 3) Planner Workflow

### Entry and Account Selection
Planners land on `/prograde` and select a ProGrade account. These accounts are separate from COT access profiles, although the application will attempt to auto-select or auto-create a matching ProGrade account when a user is already authenticated into the parent COT tool.

### Session Lifecycle
After account selection, planners work from the `All Sessions` view, where they can:
- Create a new session
- Open an existing saved session
- Preview saved sessions
- Delete sessions
- Filter by brand and saved-state context

Sessions begin effectively as draft work. A session becomes materially useful after at least one trailer/unit layout exists and the planner saves it.

### Load Builder Workflow
The load builder is the operational core of ProGrade. Within a session, planners can:
- Choose a carrier type (`53_step_deck`, `53_flatbed`, `ground_pull`)
- Add units from the active brand catalog
- Drag/drop units and columns across deck zones
- Duplicate and resequence columns
- Rotate trailers and adjust stack/nesting behavior
- Toggle axle-drop and dump-door states where supported
- Review the manifest, schematic, and validation output in one screen

### Inventory Gap Workflow
The inventory gap panel is optional and support-relevant.
- `Big Tex`: accepts workbook or CSV uploads and can also refresh via SQL.
- `PJ`: accepts inventory CSV uploads and can also refresh via SQL.
- `B-Wise`: accepts workbook uploads from the B-Wise sales-order inventory report and falls back to catalog mode when no upload has been run. SQL refresh is not currently supported.

### Export Workflow
Supported outputs include:
- Session preview
- Print-friendly load summary
- `PDF` export route
- Load-sheet `XLSX` export route

## 4) Component Architecture

### Route and Controller Layer
Primary file: `blueprints/prograde/routes.py`.

This module handles:
- Account selection and account administration
- Session lifecycle and saved-session listing
- Load-builder page assembly
- AJAX/API actions for unit add/remove/edit operations
- Validation and canvas refresh responses
- Inventory upload and SQL refresh endpoints
- Settings screens and SKU maintenance APIs
- HTML/PDF/XLSX export handlers

This file is a support hotspot. A large percentage of operational behavior is orchestrated here.

### Data Layer
Primary file: `blueprints/prograde/db.py`.

This module handles:
- ProGrade SQLite path resolution
- Schema initialization and guarded table evolution
- Seed loading for access profiles, carrier configs, and SKU catalogs
- Session persistence
- Position and column mutation helpers
- Inventory snapshot import and query helpers
- SKU/settings update and recompute logic

Unlike a fully migration-driven architecture, ProGrade currently relies heavily on `CREATE TABLE IF NOT EXISTS` and guarded `ALTER TABLE` logic inside `blueprints/prograde/db.py` for runtime schema maintenance.

### Service Layer
Primary modules:
- `blueprints/prograde/services/bt_rules.py`
- `blueprints/prograde/services/pj_rules.py`
- `blueprints/prograde/services/load_constraint_checker.py`
- `blueprints/prograde/services/inventory_gap_finder.py`
- `blueprints/prograde/services/bt_sql_refresh.py`
- `blueprints/prograde/services/pj_sql_refresh.py`

Responsibilities:
- Brand-specific stacking, height, and footprint logic
- Constraint checking and validation reporting
- Inventory gap candidate scoring
- Optional SQL-backed inventory extraction for supported brands

### UI Layer
Primary templates:
- `blueprints/prograde/templates/prograde/load_builder.html`
- `blueprints/prograde/templates/prograde/index.html`
- `blueprints/prograde/templates/prograde/settings.html`
- `blueprints/prograde/templates/prograde/_load_canvas.html`
- `blueprints/prograde/templates/prograde/_inventory_gap_panel.html`
- `blueprints/prograde/templates/prograde/export.html`

The load builder template contains a significant amount of client-side JavaScript for drag/drop, auto-collapse behavior, canvas refresh, and interaction state.

## 5) Data and Persistence Model

### Database Location
ProGrade uses a separate SQLite database from the core COT tool.

Path resolution order:
1. `PROGRADE_DB_PATH`
2. Sibling `prograde.db` next to `APP_DB_PATH` when `APP_DB_PATH` is set
3. Azure default `/home/site/prograde.db`
4. Render default `/var/data/prograde.db`
5. Local fallback `data/db/prograde.db`

### Operationally Important Tables
Core entities include:
- `prograde_access_profiles`
- `carrier_configs`
- `load_sessions`
- `load_positions`
- `bigtex_skus`
- `pj_skus`
- `bwise_skus`
- `bt_inventory_snapshot`
- `bt_inventory_snapshot_whse`
- `bt_inventory_upload_log`
- `pj_inventory_snapshot`
- `pj_inventory_snapshot_whse`
- `pj_inventory_upload_log`
- `bwise_inventory_snapshot`
- `bwise_inventory_upload_log`

### Seed and Bootstrap Behavior
On startup, ProGrade can seed or upsert:
- Brand SKU catalogs from `data/seed/*.csv`
- ProGrade account profiles from `data/seed/prograde_access_profiles.csv`
- Carrier defaults from code-defined reference records

Current support implication:
- Seed CSVs are part of the operational contract for fresh environments.
- Startup behavior can overwrite or preserve SKU edits depending on environment configuration.

## 6) Infrastructure and Configuration Contract

### Hosting Assumptions
ProGrade currently inherits the host application runtime model:
- Dockerized Python application
- Azure App Service Linux hosting
- Gunicorn process model
- Shared Flask runtime with COT
- Strong preference for single-instance operation while SQLite remains the persistence layer

### Process Model
Shared app startup currently resolves through the main app entrypoint and serves ProGrade from the same Flask application object exported by `app.py`.

Representative production command:
```text
gunicorn --preload --worker-class gthread --workers ${WEB_CONCURRENCY:-4} --threads ${GUNICORN_THREADS:-2} --timeout ${GUNICORN_TIMEOUT:-180} --graceful-timeout ${GUNICORN_GRACEFUL_TIMEOUT:-30} --keep-alive ${GUNICORN_KEEPALIVE:-5} -b 0.0.0.0:${PORT:-5000} app:app
```

### Shared Runtime Variables
These affect ProGrade because it runs inside the shared Flask runtime:

| Variable | Default / Typical Behavior | Purpose |
|---|---|---|
| `PORT` | platform-supplied | Container bind port |
| `FLASK_SECRET_KEY` | dev fallback only in local development | Session and cookie signing |
| `APP_ENV` | none | Environment hint |
| `FLASK_ENV` | none | Development hint |
| `FLASK_DEBUG` | off unless explicitly enabled | Debug mode control |
| `WEB_CONCURRENCY` | image default may exceed safe SQLite posture | Prefer `1` for SQLite/process-local safety |
| `GUNICORN_THREADS` | `2` | Thread count per worker |
| `GUNICORN_TIMEOUT` | `180` | Heavy operation timeout |
| `WEBSITES_ENABLE_APP_SERVICE_STORAGE` | platform-managed | Required for Azure SQLite durability |
| `APP_DB_PATH` | app-level DB path | Used indirectly to resolve sibling `prograde.db` |

### ProGrade-Specific Variables

| Variable | Default / Resolution | Purpose |
|---|---|---|
| `PROGRADE_DB_PATH` | resolved from environment or fallback path rules | Primary ProGrade SQLite file |
| `PROGRADE_ACCESS_PROFILES_SEED_PATH` | `data/seed/prograde_access_profiles.csv` | Seed source for ProGrade accounts |
| `PROGRADE_DEFAULT_ADMIN_NAME` | OS `USERNAME` or `Admin` fallback | Default initial admin identity |
| `PROGRADE_SQLITE_BUSY_TIMEOUT_SEC` | falls back to `SQLITE_BUSY_TIMEOUT_SEC`, default `30` | SQLite lock wait timeout |
| `PROGRADE_PRESERVE_SKU_EDITS_ON_START` | false unless enabled | Preserve existing SKU edits instead of startup overwrite |
| `PROGRADE_BOOTSTRAP_FROM_ARCHIVE_DB` | false unless enabled | Bootstrap from archived ProGrade DB when present |
| `PROGRADE_BT_DATA_WORKBOOK_PATH` | external workbook path or fallback reference | Big Tex workbook import source |
| `PROGRADE_PJ_DATA_WORKBOOK_PATH` | external workbook path or fallback reference | PJ workbook import source |

### Supported Carrier Profiles
The seeded/default carrier types currently include:
- `53_step_deck`
- `53_flatbed`
- `ground_pull`

Support note:
- `ground_pull` behaves differently from deck-based carriers because the first placed unit establishes the effective deck length behavior.
- Upper-deck logic is carrier-dependent.

### Inventory SQL Refresh Variables
Big Tex SQL refresh:

| Variable | Purpose |
|---|---|
| `PROGRADE_BT_SQL_HOST` | SQL Server host |
| `PROGRADE_BT_SQL_DATABASE` | source database |
| `PROGRADE_BT_SQL_DRIVER` | ODBC driver |
| `PROGRADE_BT_SQL_PORT` | SQL port |
| `PROGRADE_BT_SQL_ENCRYPT` | connection encryption |
| `PROGRADE_BT_SQL_TRUST_SERVER_CERTIFICATE` | server certificate policy |
| `PROGRADE_BT_SQL_CONNECT_TIMEOUT_SEC` | connection timeout |
| `PROGRADE_BT_SQL_AUTHENTICATION` | authentication mode |
| `PROGRADE_BT_SQL_USERNAME` | SQL username |
| `PROGRADE_BT_SQL_PASSWORD` | SQL password |
| `PROGRADE_BT_SQL_QUERY` | extraction query text |

PJ SQL refresh:

| Variable | Purpose |
|---|---|
| `PROGRADE_PJ_SQL_HOST` | SQL/Synapse host |
| `PROGRADE_PJ_SQL_DATABASE` | source database |
| `PROGRADE_PJ_SQL_DRIVER` | ODBC driver |
| `PROGRADE_PJ_SQL_PORT` | SQL port |
| `PROGRADE_PJ_SQL_ENCRYPT` | connection encryption |
| `PROGRADE_PJ_SQL_TRUST_SERVER_CERTIFICATE` | server certificate policy |
| `PROGRADE_PJ_SQL_CONNECT_TIMEOUT_SEC` | connection timeout |
| `PROGRADE_PJ_SQL_AUTHENTICATION` | authentication mode |
| `PROGRADE_PJ_SQL_USER_PRINCIPAL` | optional interactive principal |
| `PROGRADE_PJ_SQL_USERNAME` | optional username |
| `PROGRADE_PJ_SQL_PASSWORD` | optional password |
| `PROGRADE_PJ_SQL_QUERY` | extraction query text |

### Security Handling Note
The current SQL refresh helper modules include proof-of-concept default connection values in code. Treat those values as sensitive, remove or override them in production configuration, and avoid relying on code-embedded defaults for long-term operations. Production support should store all live credentials in approved secret management and rotate any values that may have been exposed in development artifacts.

## 7) Core Rules and Interaction Logic

### 7.1 Brand Model
From an operator perspective, ProGrade is one tool with one core workflow. Internally, it uses brand-specific rules and data catalogs.
- `Big Tex` and `B-Wise` share several load-builder behaviors and footprint conventions.
- `PJ` uses its own stacking and dimension logic.
- The route layer dispatches brand-specific rule handling while keeping the UI contract broadly consistent.

### 7.2 Carrier and Deck Logic
Carrier selection materially changes validation and rendering behavior.
- `53_step_deck` supports lower and upper deck behavior.
- `53_flatbed` uses a single deck profile.
- `ground_pull` suppresses the structural deck line and derives effective behavior from the first placed unit.

### 7.3 Position and Column Mutations
The load builder persists both position-level and column-level actions, including:
- add/remove unit
- rotate unit
- toggle axle drop
- toggle dump door
- move position
- move column
- move column between zones
- duplicate column
- resequence column
- nest units where rule-compatible

### 7.4 Constraint Validation
Constraint validation is performed continuously through the service layer and returned to the UI as session-state feedback. Validation depends on:
- brand
- carrier type
- deck zone
- footprint and height metrics
- nesting compatibility
- stack-height assumptions

### 7.5 Inventory Gap Scoring
Inventory gap scoring evaluates which available units can be added to existing active stacks without introducing new rule violations. Current implementation is deterministic and tied to current session geometry.

## 8) Maintenance and Testing

### Test Coverage
The ProGrade feature set has a meaningful targeted regression suite. Key test files include:
- `tests/test_prograde_sessions_workflow.py`
- `tests/test_prograde_settings_save.py`
- `tests/test_prograde_rotation.py`
- `tests/test_prograde_render_mode.py`
- `tests/test_prograde_dump_height_overrides.py`
- `tests/test_prograde_inventory_gap_finder.py`
- `tests/test_prograde_inventory_upload.py`
- `tests/test_prograde_export_summary.py`
- `tests/test_prograde_pj_stacking_rules.py`
- `tests/test_prograde_pj_picker_categories.py`
- `tests/test_prograde_overlap_metrics.py`
- `tests/test_prograde_db_path_resolution.py`
- `tests/test_prograde_account_seed_persistence.py`
- `tests/test_prograde_sku_import_persistence.py`

### Recommended Minimum Regression Pass
At minimum, run:
- `pytest tests/test_prograde_sessions_workflow.py`
- `pytest tests/test_prograde_settings_save.py`
- `pytest tests/test_prograde_rotation.py tests/test_prograde_render_mode.py`
- `pytest tests/test_prograde_inventory_gap_finder.py tests/test_prograde_inventory_upload.py`
- `pytest tests/test_prograde_export_summary.py`

### Production Verification
After a deployment or config change, verify:
1. `/prograde` loads and account selection works.
2. `All Sessions` renders and existing sessions open.
3. A new session can be created for each required carrier type.
4. SKU picker loads for the target brand.
5. Add/rotate/move/remove actions persist and refresh correctly.
6. Validation output updates after mutations.
7. Export routes return valid files.
8. If applicable, inventory upload and SQL refresh complete successfully.

## 9) Diagnostic Reference

### Triage Matrix
| Symptom | First Suspect Components | First Checks |
|---|---|---|
| Cannot reach ProGrade account page or sessions | shared Flask runtime, blueprint registration, session state | Confirm app booted cleanly, `/prograde` route resolves, session cookies valid |
| Account auto-selection or account admin behavior is wrong | `routes.py` account handlers, `prograde_access_profiles` | Verify active session keys and profile rows in ProGrade DB |
| New session cannot be created | route/controller logic, `carrier_configs`, session persistence | Confirm selected brand is valid and carrier config exists |
| SKU picker is empty or incomplete | seed import, SKU tables, settings/UI data assembly | Check `bigtex_skus`, `pj_skus`, or `bwise_skus` row counts |
| Drag/drop or canvas edits do not persist | `load_builder.html` JS, mutation routes, `db.py` mutation helpers | Check API responses for move/add/remove actions and persisted `load_positions` rows |
| Validation looks wrong | brand rule engine, carrier config, overridden dimensions | Check brand, carrier type, and SKU metrics used in the failing stack |
| Inventory upload fails | brand-specific import path, file type, snapshot tables | Confirm file format matches brand expectations and review upload logs |
| SQL refresh fails | SQL config, ODBC/SQLAlchemy deps, network/auth | Validate env vars, ODBC driver presence, and exception text |
| Export output is blank or malformed | export route, template render, session state | Check that session has persisted positions and carrier metadata |

### High-Signal Checks
- Route reachability:
  - `GET /prograde`
  - `GET /prograde/sessions?brand=bigtex`
- ProGrade DB connectivity:
  - `python -c "from blueprints.prograde import db; c=db.get_connection(); print(c.execute('select count(*) from load_sessions').fetchone()[0])"`
- Brand SKU counts:
  - `python -c "from blueprints.prograde import db; c=db.get_connection(); print(c.execute('select count(*) from bigtex_skus').fetchone()[0], c.execute('select count(*) from pj_skus').fetchone()[0], c.execute('select count(*) from bwise_skus').fetchone()[0])"`
- Inventory snapshot freshness:
  - `python -c "from blueprints.prograde import db; c=db.get_connection(); print(c.execute('select max(uploaded_at) from bt_inventory_upload_log').fetchone()[0], c.execute('select max(uploaded_at) from pj_inventory_upload_log').fetchone()[0])"`

### Safe Patch Checklist
1. Reproduce with the affected brand and carrier profile.
2. Confirm whether the issue is route-layer, UI-layer, DB-layer, or rule-engine-layer.
3. Add or update a targeted ProGrade regression test when practical.
4. Verify at least one add/move/save/export path after the fix.
5. If inventory behavior changed, verify both upload and non-upload paths.

## 10) Route and Feature Inventory

### Account and Session Routes
- `/prograde`
- `/prograde/account`
- `/prograde/account/manage`
- `/prograde/account/select`
- `/prograde/account/create`
- `/prograde/account/update`
- `/prograde/account/delete`
- `/prograde/sessions`
- `/prograde/session/new`
- `/prograde/session/<session_id>/preview`
- `/prograde/session/<session_id>/delete`

### Load Builder and Export Routes
- `/prograde/session/<session_id>/load`
- `/prograde/session/<session_id>/export`
- `/prograde/session/<session_id>/export.pdf`
- `/prograde/session/<session_id>/export-load-sheet.xlsx`

### API Routes
- `GET /prograde/api/session/<session_id>/state`
- `POST /prograde/api/session/<session_id>/save`
- `POST /prograde/api/session/<session_id>/carrier`
- `POST /prograde/api/session/<session_id>/add`
- `POST /prograde/api/session/<session_id>/remove`
- `POST /prograde/api/session/<session_id>/rotate`
- `POST /prograde/api/session/<session_id>/toggle_axle_drop`
- `POST /prograde/api/session/<session_id>/toggle_dump_door`
- `POST /prograde/api/session/<session_id>/nest`
- `POST /prograde/api/session/<session_id>/acknowledge`
- `GET /prograde/api/session/<session_id>/check`
- `POST /prograde/api/session/<session_id>/position/move`
- `POST /prograde/api/session/<session_id>/column/move`
- `POST /prograde/api/session/<session_id>/column/duplicate`
- `POST /prograde/api/session/<session_id>/column/move-zone`
- `POST /prograde/api/session/<session_id>/column/resequence`
- `POST /prograde/api/session/<session_id>/reset`
- `POST /prograde/api/session/<session_id>/inventory/upload`
- `POST /prograde/api/session/<session_id>/inventory/sql-refresh`
- `GET /prograde/settings`
- `POST /prograde/api/settings/save`
- `POST /prograde/api/settings/bigtex/import`
- `POST /prograde/api/settings/pj/import`
- `POST /prograde/api/settings/pj/sku`
- `POST /prograde/api/settings/bigtex/sku`
- `POST /prograde/api/settings/bwise/sku`

## 11) Known Constraints and Risk Areas

- ProGrade is coupled to the shared Flask runtime; an issue in the parent app runtime can affect ProGrade even if ProGrade code is unchanged.
- `blueprints/prograde/routes.py` is large and operationally dense; changes there can have wide side effects.
- SQLite keeps infrastructure simple but limits safe horizontal scaling.
- The load builder relies on a substantial amount of template-embedded JavaScript, which can make debugging UI state and persistence issues harder than in a more componentized frontend.
- Brand behavior is unified at the workflow level but not fully identical in implementation; support should not assume all import and rule paths are interchangeable.
- Inventory upload is supported for `Big Tex`, `PJ`, and `B-Wise`; SQL refresh remains supported only for `Big Tex` and `PJ`.
- SQL refresh helpers currently deserve extra scrutiny from a security perspective because of code-level proof-of-concept defaults.

## 12) Operational Summary for Support Teams

For IT and dev support, the fastest way to reason about ProGrade is:
- It is a shared-runtime Flask blueprint with its own SQLite database.
- The most important code paths are account/session management, load-builder mutations, brand rule engines, inventory import, and export rendering.
- Most operator-visible issues will trace back to one of four areas: session/account state, SKU/reference data, canvas mutation persistence, or brand-specific validation logic.
- Support should treat inventory SQL refresh as an optional integration, not a required dependency for baseline load-building availability.
