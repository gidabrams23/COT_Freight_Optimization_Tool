# IT Handoff - Carry On Tool (COT) Architecture and Maintenance Guide (Azure Production)

## 1) Purpose and Operational Background

### Purpose
This document is the technical handoff for IT/dev support to operate, debug, and maintain the Carry On Tool (COT) freight optimization application after ownership transition. It is architecture-first and maintenance-focused so technical teams and business stakeholders can align on how the tool works in production.

### Operational Background
The application supports freight/load planning operations by converting daily open-order data into actionable load plans.

Normal business flow:
1. Planner uploads current open-order CSV data.
2. App normalizes and validates row-level input and maps SKU/stacking/routing assumptions.
3. Optimization builds candidate loads using capacity, distance, and planning rules.
4. Planner reviews/edit loads (manual add/remove/re-sequence/schematic edits).
5. Planner approves/rejects loads.
6. Planner exports operational artifacts (CSV/XLSX load report outputs).

Operating profile (from existing operational requirements docs):
- Concurrent users: typically 8-10.
- Typical upload size: ~8,000 rows.
- Daily uploads can include overlap from prior-day order sets.
- Preferred response target: <= 30s for common interactions.
- Acceptable heavy-operation target: <= 90s (import + optimization paths).
- System criticality: non-critical; occasional restart/recovery is acceptable.

What healthy production operation looks like:
- Users can authenticate and reach dashboard/session pages.
- Upload completes and creates/updates order data without validation failure loops.
- Optimization returns draft loads and route details.
- Approval/rejection status changes persist.
- Export actions produce valid files.

## 2) App Workflow and Page Map

### Navigation Structure (Plain English)
The app is organized around the planner workflow: intake data, scope work, optimize, review, approve, and export.

Typical navigation sequence:
1. `Login` and `Session` pages: access and planning context setup.
2. `Dashboard` (`/dashboard`): daily entry point and quick status view.
3. `Upload / Intake Hub` (`/upload`): ingest open orders CSV and review upload results.
4. `Orders` (`/orders`): filter scope, validate order readiness, and launch optimization.
5. `Optimize` (`/optimize`): review optimization settings and build proposed loads.
6. `Loads` (`/loads`): compare/load proposals and manage approval workflow.
7. `Load Detail` (`/loads/<id>`): detailed manifest, sequencing, trailer/schematic edits, route view.
8. `Planning Sessions` (`/planning-sessions`): session history, archive, resume/revise, and replay.
9. `Load Report` (`/load-report/<session_id>`): final report and XLSX exports.
10. `Settings` family (`/settings`, `/rates`, `/skus`, `/lookups`): maintain rules, rates, and reference data.
11. `Tutorial` (`/tutorial`) and `Feedback` (`/feedback`, `/feedback/app`): user enablement and issue capture.

### What Each Major Page Does
- `Login` (`/login`): authentication entry point (legacy and Microsoft Entra SSO).
- `Session` (`/session`): choose planner context and plant scope for active planning work.
- `Upload` (`/upload`): submit source file and see import quality outcomes (mapped/unmapped items).
- `Orders` (`/orders`): inspect scoped order table, apply filters/exclusions, and trigger optimization.
- `Optimize` (`/optimize`): submit optimization run using current settings and constraints.
- `Loads` (`/loads`): worklist for drafted loads with status changes (approve/reject/rebuild).
- `Load Detail` (`/loads/<id>`): perform operational edits (carrier, trailer, sequence, manual add/remove) and view schematic/route.
- `Planning Sessions` (`/planning-sessions`, detail pages): maintain continuity across planning cycles and replay historical runs.
- `Load Report` (`/load-report/<session_id>`): produce operational handoff artifacts for dispatch/execution.
- `Settings` (`/settings`, `/rates`, `/skus`, `/lookups`): govern the assumptions that drive utilization and cost outcomes.

## 3) System Architecture at a Glance

### Runtime Topology (Azure)
```mermaid
flowchart LR
    U[Planner Browser] -->|HTTPS| A[Azure App Service\nLinux Container]
    A --> G[Gunicorn gthread workers]
    G --> F[Flask COT blueprint routes]
    F --> S[Service layer\noptimizer/load_builder/order_importer/routing]
    S --> D[(SQLite DB\nAPP_DB_PATH)]
    F --> T[Jinja templates + static assets]
    S --> O[OpenRouteService API\noptional, outbound HTTPS]
    A --> L[App Service logs / stdout-stderr]
    A --> B[Azure Backup target\n(Storage Account)]
```

### Plain-English Stack Rationale
- Flask + Jinja (server-rendered pages):
  - Chosen for fast iteration on operations-heavy workflows (forms, tables, status actions) with low frontend complexity.
  - Keeps request logic and page rendering close together, which helps debugging and maintenance in a small team.
- SQLite:
  - Fits current workload (single-instance planning app, moderate concurrency, local transactional integrity).
  - Minimizes infrastructure overhead while preserving full relational query capability.
- Gunicorn gthread on App Service Linux:
  - Production-ready Python serving model with simple container deployment.
  - Threaded workers support concurrent web requests without introducing distributed coordination complexity.
- Python service modules (`services/*`):
  - Encapsulates calculation logic (optimization, utilization, routing, cost) so business rules are versioned in code and testable.
- Optional OpenRouteService integration:
  - Adds road-aware route geometry when needed, while fallback routing keeps core planning flows available if provider calls fail.

### Request/Data Lifecycle
1. Browser request hits Flask route in `blueprints/cot/routes.py` (registered via `app.py` shim).
2. Route performs session/auth checks and validates request payload.
3. Route invokes service-layer logic in `services/`.
4. Services read/write persisted state through `db.py` helpers.
5. Response renders Jinja templates (HTML) or returns JSON/file download.

### Compute-Heavy Paths
- Order import validation/transformation (`services/order_importer.py`).
- Optimization/build routines (`services/optimizer.py`, `services/optimizer_engine.py`, `services/load_builder.py`).
- Route geometry/cost computation (`services/routing_service.py`, `services/cost_calculator.py`).
- Replay evaluation analysis (`services/replay_evaluator.py`).
- XLSX report generation in route handlers (OpenPyXL usage in `blueprints/cot/routes.py`).

## 4) Core Components and How They Work Together

### Route/Controller Layer
- Primary entrypoint: `app.py` (shim that exports the live app object).
- Pattern: routes are registered via Flask blueprints:
  - `blueprints/cot/routes.py` for COT
- Route families:
  - Auth/session/access: login, Entra callback/start, access profile admin.
  - Orders: upload, filter/scope, optimize trigger, export.
  - Loads: review/detail, manual edits, schematic, status transitions, approvals.
  - Planning sessions/replay: session lifecycle and replay diagnostics.
  - Settings/lookups/rates/SKU/plants: maintenance/config screens.
  - API endpoints: order upload/validation helpers, optimization APIs, load routing geometry.

### Data Layer
- File: `db.py`.
- What it handles:
  - SQLite connection lifecycle (`PRAGMA journal_mode=WAL`, busy timeout, row factory).
  - Schema ensure/create paths for app tables.
  - CRUD/query helper functions used throughout routes/services.
  - Seed ingestion and profile identity snapshots.

### Service Layer (Business/Optimization)
- `services/order_importer.py`: CSV normalization, row-level validation, order ingestion behavior.
- `services/optimizer.py` + `services/optimizer_engine.py`: load assignment strategy and optimization execution.
- `services/load_builder.py`: composing and mutating loads, including manual changes.
- `services/stack_calculator.py`: stacking/utilization calculations and schematic assumptions.
- `services/cost_calculator.py`: cost metrics (rate + stop/fuel/min-cost settings).
- `services/routing_service.py` + `services/routing_providers/openrouteservice_provider.py`: road routing, geometry fetch, fallback logic.
- `services/replay_evaluator.py`: replay analysis dataset generation and issue export.
- `services/orders.py`, `services/order_categories.py`, `services/customer_rules.py`: supporting domain logic.

### Templates and Static Assets
- Server-rendered views in `templates/`.
- Front-end assets and static data in `static/`.
- `templates/orders.html` is a key operator workflow page for order review/scope.

### Maintenance Scripts
- `scripts/export_seed_data.py`: export DB table snapshots into `data/seed/` CSV files.
- `scripts/apply_seed_snapshots.py`: apply seed snapshots into a running DB.
- `scripts/import_pilot_ready_data.py`, `scripts/import_freight_rate_tables.py`, `scripts/import_uszips.py`: data bootstrap/import tools.

### COT Code Construct Snapshot
- `blueprints/cot/routes.py`: primary controller/orchestration module (large route surface and workflow coupling).
- `db.py`: SQLite schema-ensure logic + data-access layer + seed/snapshot helpers.
- `services/optimizer.py`: optimization engine (baseline + v2 merge heuristics and rescue passes).
- `services/stack_calculator.py`: stacking and utilization engine used by import, optimization, and schematic views.
- `services/cost_calculator.py` + `services/routing_service.py`: lane-rate cost model + routing provider/fallback/cache handling.
- Design intent for maintenance: keep route handlers thin where practical, push business rules into `services/`, and keep schema semantics synchronized between `db.py`, migrations, and docs.

## 5) Data and Persistence Model

### SQLite Location and Persistence
- Runtime DB path resolution in `db.py`:
  - `APP_DB_PATH` if set.
  - Else Azure App Service auto-default `/home/site/app.db` when App Service env variables exist.
  - Else Render-specific fallback `/var/data/app.db` when Render env variables exist.
  - Else local fallback `data/db/app.db`.
- Runtime ProGrade DB path resolution in `blueprints/prograde/db.py`:
  - `PROGRADE_DB_PATH` if set.
  - Else sibling `prograde.db` next to `APP_DB_PATH` when `APP_DB_PATH` is set.
  - Else Azure App Service auto-default `/home/site/prograde.db` when App Service env variables exist.
  - Else Render-specific fallback `/var/data/prograde.db` when Render env variables exist.
  - Else local fallback `data/db/prograde.db`.
- Azure defaults are now safe without explicit DB path env vars, but persistent App Service storage must still be enabled for restart durability (`WEBSITES_ENABLE_APP_SERVICE_STORAGE=true`).
- ProGrade sessions are now created as saved sessions by default so they remain visible in `All Sessions` after restarts.

### Core Entities (Operationally Important)
- Order intake and planning:
  - `orders`, `order_lines`, `upload_history`, `upload_order_changes`, `upload_unmapped_items`.
- Load construction and execution:
  - `loads`, `load_lines`, `load_schematic_overrides`, `load_feedback`.
- Optimization and sessions:
  - `optimization_runs`, `optimized_loads`, `load_order_assignments`, `planning_sessions`.
- Settings/master data:
  - `optimizer_settings`, `planning_settings`, `sku_specifications`, `item_sku_lookup`, `rate_matrix`, `plants`, `zip_coordinates`.
- Access/auth mapping:
  - `access_profiles`, `access_profile_identities`.
- Replay tooling:
  - `replay_eval_runs`, `replay_eval_day_plant`, `replay_eval_issues`, `replay_eval_load_metrics`, `replay_eval_source_rows`.
- Routing cache:
  - `route_cache`.

### Seed/Snapshot and Data Dependency Notes
- On fresh initialization, app seeds core lookup/settings data from `data/seed/`.
- ProGrade SKU seed behavior defaults to startup upsert from CSV snapshots (`pj_skus`, `bigtex_skus`) so environments stay aligned with `data/seed/`.
- Optional ProGrade preservation mode: `PROGRADE_PRESERVE_SKU_EDITS_ON_START=true` seeds only empty SKU tables and avoids overwrite on restart.
- Access profile and identity mappings are represented in seed CSVs and persisted in DB.
- `export_seed_data.py` + `apply_seed_snapshots.py` form the controlled path for migrating selected configuration state between environments.
- Daily operational data is primarily in SQLite; treat DB file backup/restore as primary recovery mechanism.

## 6) Azure Runtime and Configuration Contract

### Hosting Assumptions
- Containerized deployment (`Dockerfile`) with Gunicorn serving Flask.
- Recommended Azure baseline from repository docs: App Service Linux (Web App for Containers), single instance, persistent storage, HTTPS ingress restrictions.
- Single-instance runtime is strongly preferred while SQLite is the data store.

### Startup/Process Model
- Docker command:
  - `gunicorn --preload --worker-class gthread --workers ${WEB_CONCURRENCY:-4} --threads ${GUNICORN_THREADS:-2} --timeout ${GUNICORN_TIMEOUT:-180} --graceful-timeout ${GUNICORN_GRACEFUL_TIMEOUT:-30} --keep-alive ${GUNICORN_KEEPALIVE:-5} -b 0.0.0.0:${PORT:-5000} app:app`
- App exposes health endpoints:
  - Warmup path: `/robots933456.txt`
  - Health check: `/healthz`

### Environment Variables (Contract + Defaults)

#### Runtime/Core
| Variable | Default in Code | Current Value in This Environment (2026-03-19) | Notes |
|---|---|---|---|
| `PORT` | none | (empty) | Container bind port if supplied by platform. |
| `WEBSITES_PORT` | fallback input | (empty) | Azure App Service commonly set to `5000`. |
| `FLASK_SECRET_KEY` | required in non-dev; fallback `dev-session-key` only in local dev | `dev-session-key` | Must be strong secret in Azure prod. |
| `APP_ENV` | none | `development` | Used to infer dev mode. |
| `FLASK_ENV` | none | `development` | Dev-mode hint only. |
| `FLASK_DEBUG` | `0` behavior unless set to `1` | (empty) | Avoid `1` in prod. |
| `APP_VERSION` | computed from update date when empty | (empty) | Optional release labeling. |
| `APP_UPDATED_ON` | git/source/date fallback | (empty) | Optional release labeling. |
| `ADMIN_PASSWORD` | none | (empty) | Required for legacy admin login flows. |
| `SESSION_COOKIE_SECURE` | true outside local dev | not set | Set explicit `true` in prod. |
| `WEBSITES_ENABLE_APP_SERVICE_STORAGE` | platform-managed | (empty) | Must be `true` in Azure for SQLite restart persistence under `/home/site`. |

#### Concurrency / Gunicorn
| Variable | Default in Code/Image | Current Value in This Environment | Notes |
|---|---|---|---|
| `WEB_CONCURRENCY` | app warns if >1; image default workers=4 | (empty) | Set to `1` for SQLite + process-local reopt status safety. |
| `GUNICORN_THREADS` | `2` | (empty) | Thread count per worker. |
| `GUNICORN_TIMEOUT` | `180` | (empty) | Heavy requests need high timeout budget. |
| `GUNICORN_GRACEFUL_TIMEOUT` | `30` | (empty) | Graceful shutdown timeout. |
| `GUNICORN_KEEPALIVE` | `5` | (empty) | HTTP keepalive seconds. |

#### Database / Seed Paths
| Variable | Default in Code | Current Value in This Environment | Notes |
|---|---|---|---|
| `APP_DB_PATH` | env or derived fallback | (empty) | Optional override; when empty, Azure defaults to `/home/site/app.db`. |
| `PROGRADE_DB_PATH` | env or derived fallback | (empty) | Optional override; when empty, defaults to `APP_DB_PATH` sibling or Azure `/home/site/prograde.db`. |
| `APP_SEED_DIR` | `data/seed` | (empty) | Override only if seed location changes. |
| `SQLITE_BUSY_TIMEOUT_SEC` | `30` | (empty) | DB lock wait timeout (seconds). |
| `PROGRADE_SQLITE_BUSY_TIMEOUT_SEC` | falls back to `SQLITE_BUSY_TIMEOUT_SEC` (`30`) | (empty) | ProGrade DB lock wait timeout override (seconds). |
| `ACCESS_PROFILES_SEED_PATH` | `data/seed/access_profiles.csv` | (empty) | Optional override. |
| `ACCESS_PROFILE_IDENTITIES_SEED_PATH` | `data/seed/access_profile_identities.csv` | (empty) | Optional override. |

#### Entra SSO
| Variable | Default in Code | Current Value in This Environment | Notes |
|---|---|---|---|
| `ENTRA_SSO_ENABLED` | `false` | `true` | Enables SSO path if config complete + `msal` present. |
| `ENTRA_TENANT_ID` | none | `e3c2dcef-5940-4aa9-961e-cb8c2bcc08ee` | Tenant/domain identifier. |
| `ENTRA_CLIENT_ID` | none | `39bb5ca9-1431-4caa-badd-ae2a4a9ba81c` | App registration client ID. |
| `ENTRA_CLIENT_SECRET` | none | `[REDACTED]` | Client secret; store only in secure secret manager/app settings. |
| `ENTRA_REDIRECT_URI` | derived via callback helper when empty | `http://localhost:5000/auth/microsoft/callback` | Must match Azure production URL callback. |
| `ENTRA_SSO_REQUIRED` | defaults to `ENTRA_SSO_ENABLED` | `false` | If true, blocks legacy login without mapped SSO identity. |
| `ENTRA_ALLOW_LEGACY_LOGIN` | defaults opposite of SSO-required | `true` | Keep true only if fallback auth desired. |
| `ENTRA_SCOPES` | `openid profile email User.Read` | `User.Read` | Current env overrides broader default. |
| `ENTRA_ALLOWED_EMAIL_DOMAINS` | empty (no domain restriction) | (empty) | Optional comma-separated allowlist. |

#### Routing / External API
| Variable | Default in Code | Current Value in This Environment | Notes |
|---|---|---|---|
| `ROUTING_ENABLED` | `true` | `true` | Master toggle for routing provider usage. |
| `ROUTING_GEOMETRY_ONLY` | `true` | (empty) | If true, route geometry enrichment without replacing all distances. |
| `ROUTING_PROVIDER` | `ors` | `ors` | Current provider implementation. |
| `ROUTING_PROFILE` | `driving-hgv` | `driving-hgv` | ORS profile. |
| `ROUTING_TIMEOUT_MS` | `5000` | `5000` | Provider timeout per request. |
| `ROUTING_SNAP_RADIUS_M` | `5000` | (empty) | Snap radius fallback if unset. |
| `ROUTING_CACHE_TTL_DAYS` | `30` | (empty) | Route cache TTL. |
| `ORS_API_KEY` | none | `[REDACTED]` | External API key; store only in secure secret manager/app settings. |

### External Integration Behavior
- Entra SSO is active only when `ENTRA_SSO_ENABLED=true` and tenant/client/secret + `msal` are all present.
- If SSO is enabled but incomplete, app logs warning and does not enable active Microsoft sign-in flow.
- Routing service falls back gracefully when ORS key/config/error conditions prevent route fetch; map/detail behavior continues with fallback geometry/distance logic.

## 7) Core Calculations and Algorithms (COT)

### 7.1 Order Import Normalization and Line-Level Utilization
- File: `services/order_importer.py`.
- Core line transformations:
  - Lookup SKU from scoped item mapping (`item_sku_lookup`) by plant/bin/item pattern.
  - Read SKU dimensions/stack rules from `sku_specifications`.
  - Compute effective stacked positions:
    - `effective_units = ceil(qty / max_stack_height)`
  - Compute line linear feet:
    - `total_length_ft = effective_units * unit_length_ft`
  - Compute line utilization reference against 53-foot basis:
    - `utilization_pct = (total_length_ft / 53.0) * 100`
- Order-level utilization then re-computed using the stack engine (`stack_calculator.calculate_stack_configuration`) rather than summing raw line percentages.
- Operationally, this means planners see utilization values that reflect actual stack/deck assumptions, not just raw line totals.

### 7.2 Stack/Utilization Engine
- File: `services/stack_calculator.py`.
- Trailer profiles are explicit (`STEP_DECK`, `FLATBED`, `HOTSHOT`, `WEDGE`, and 48-foot variants) with deck lengths/capacities.
- Engine computes:
  - Position-level stacking feasibility by length, stop order, and stack heights.
  - Deck assignment (lower/upper for step deck).
  - Two-across behavior on eligible upper-deck positions.
  - Capacity overflow evaluation with configurable overhang rules.
- Primary utilization formula:
  - `utilization_pct = (utilization_credit_ft / capacity_feet) * 100`
- `utilization_credit_ft` is not raw linear feet; it is a stack-credit measure with:
  - Stack-height credit handling.
  - Controlled singleton overflow multiplier based on `stack_overflow_max_height`.
  - Upper-deck normalization behavior for step-deck profiles.
- Grade assignment is threshold-based (`A/B/C/D/F`) from planning settings (`utilization_grade_thresholds`).
- Operationally, this keeps grading consistent across pages (orders, loads, reports) and makes threshold updates centrally controlled.

### 7.3 Load Cost Model
- File: `services/cost_calculator.py`.
- Defaults:
  - Base rate-per-mile fallback: `3.12`
  - Stop fee default: `55.00`
  - Minimum load cost default: `800.00`
  - Fuel surcharge default: `0.40` per mile
- Effective per-mile rate is lane-based (`rate_matrix`) with fuel surcharge handling.
- Core cost equation in code:
  - `total_cost = SUM(leg_miles * lane_rate_with_fuel) + (stop_count * stop_fee) + return_leg_cost_if_required`
  - `total_cost = max(total_cost, min_load_cost)`
- Distances come from routing service output (provider or fallback); if route totals are available they override per-leg sum.
- Operationally, this allows finance-facing cost signals to remain available even if external routing providers are degraded.

### 7.4 Optimization Logic (Baseline and V2)
- Files: `services/optimizer.py`, `services/optimizer_engine.py`, `services/load_builder.py`.
- Baseline strategy:
  - Group eligible orders.
  - Bucket by destination key.
  - Apply first-fit-decreasing by length under compatibility and capacity constraints.
- V2 strategy:
  - Start from singleton loads.
  - Build merge candidates under compatibility checks (plant, date-window, geo constraints, customer-mix rules, stack feasibility).
  - Score candidates by savings and objective bonus:
    - `savings = cost(load_a) + cost(load_b) - cost(merged_load)`
    - `gain = savings + low-utilization objective bonus`
  - Iterate merge/rescue/rebalance/reassign passes with tuned guardrails for low-utilization cleanup.
- Hard guardrail:
  - Multi-order over-capacity loads are blocked; over-capacity is allowed only for single-order loads.
- Operationally, this protects planners from receiving infeasible multi-order plans while still allowing explicit treatment of oversized single-order exceptions.

### 7.5 Routing Behavior and Fallback
- Files: `services/routing_service.py`, `services/routing_providers/openrouteservice_provider.py`, `services/tsp_solver.py`.
- Default runtime behavior keeps optimization/costing on haversine routing unless geometry enrichment is requested (`ROUTING_GEOMETRY_ONLY=true` default behavior).
- ORS provider is used when configured and available.
- On provider failure/timeout/unavailable credentials:
  - Route ordering and distance fall back safely to in-app haversine + TSP logic.
- Route responses are cached in-memory and in SQLite (`route_cache`) with TTL.
- Operationally, fallback and caching reduce planner interruption and API-cost volatility during daily planning windows.

## 8) Debugging Playbooks

### Triage Matrix
| Symptom | First Suspect Components | First Checks |
|---|---|---|
| Cannot log in / SSO loop | Entra config, session config, access identity mapping | Check `ENTRA_*` values, callback URI match, `access_profile_identities` rows, app startup warnings. |
| Upload fails or partial ingest | `services/order_importer.py`, upload routes, lookup tables | Inspect upload error summary, unmapped SKU outputs, `upload_history`, `upload_unmapped_items`. |
| Slow/failed optimization | optimizer/load_builder stack, timeout/runtime sizing | Check request duration, Gunicorn timeout, CPU saturation, current order scope size. |
| Load edits not persisting | load mutation routes, `db.py` write paths, session context | Validate POST payload, DB write success, affected `loads`/`load_lines` rows. |
| Route map missing/incorrect | routing service/provider/cache | Confirm `ROUTING_ENABLED`, `ORS_API_KEY`, provider logs, cache freshness in `route_cache`. |
| Export issues (XLSX/CSV) | report routes, OpenPyXL generation, source data completeness | Verify export endpoint tracebacks and source session/load records. |

### High-Signal Checks
- Health check:
  - `GET /healthz` should return `ok` with HTTP 200.
- Warmup probe:
  - `GET /robots933456.txt` should return HTTP 200.
- DB connectivity sanity (run from app host shell):
  - `python -c "import db; c=db.get_connection(); print(c.execute('select count(*) from orders').fetchone()[0])"`
- Route-cache freshness:
  - `python -c "import db; c=db.get_connection(); print(c.execute('select count(*) from route_cache where expires_at > datetime(\'now\')').fetchone()[0])"`

### Reusable Diagnostic Prompts
- Root cause narrowing:
  - "Trace the request path for `<endpoint>` in `blueprints/cot/routes.py`, list called service/db functions, and identify top 3 failure points for `<symptom>`."
- Regression-safe patching:
  - "Implement the smallest fix for `<bug>`, preserve current behavior elsewhere, and add/adjust targeted tests under `tests/`."
- DB-side verification:
  - "Provide SQL read-only checks to confirm whether `<workflow>` persisted correctly in SQLite without mutating data."
- Configuration validation:
  - "Compare current environment variable values against code defaults and highlight values likely to cause `<issue>`."

### Safe Patch Checklist (Before Deploy)
1. Reproduce on production-like data shape (especially large upload/order scopes).
2. Confirm fix by targeted test(s) and at least one end-to-end workflow path.
3. Confirm no write-path regression in `orders`, `loads`, `planning_sessions`, and settings tables.
4. Validate auth behavior (legacy + SSO expectations) after any login/session change.
5. Validate export and load-detail rendering for impacted sessions.

## 9) Testing and Verification

### Test Map by Subsystem
- Auth and identity mapping:
  - `tests/test_entra_sso_access_mapping.py`
- Upload and order validation:
  - `tests/test_orders_upload_validation.py`
  - `tests/test_order_importer_lookup_scopes.py`
  - `tests/test_order_category_scope_filters.py`
  - `tests/test_order_singularity.py`
- Optimization and assignment behavior:
  - `tests/test_optimizer_group_reassign.py`
  - `tests/test_optimizer_home_length_priority.py`
- Load mutation and sequencing:
  - `tests/test_manual_load_mutations.py`
  - `tests/test_load_reverse_order.py`
- Schematic/stacking behavior:
  - `tests/test_stack_calculator_assumptions.py`
  - `tests/test_schematic_upper_deck_exception.py`
  - `tests/test_schematic_layout_stop_mapping.py`
  - `tests/test_schematic_save_two_across_autostack.py`
  - `tests/test_schematic_edit_payload_return_hint.py`
- Replay and reports:
  - `tests/test_replay_evaluator.py`
  - `tests/test_orders_load_report_snapshot.py`
- Settings/UI contract checks:
  - `tests/test_settings_sku_source_led_view.py`
  - `tests/test_settings_plant_trailer_defaults.py`
  - `tests/test_tutorial_manifest.py`
  - `tests/test_tutorial_route.py`

### Regression Suite Recommendation (Minimum)
Run at least:
- `pytest tests/test_entra_sso_access_mapping.py`
- `pytest tests/test_orders_upload_validation.py tests/test_order_importer_lookup_scopes.py`
- `pytest tests/test_optimizer_group_reassign.py tests/test_optimizer_home_length_priority.py`
- `pytest tests/test_manual_load_mutations.py tests/test_load_reverse_order.py`
- `pytest tests/test_orders_load_report_snapshot.py tests/test_replay_evaluator.py`

### Production-Safe Verification After Changes
1. Verify login path(s) and dashboard navigation.
2. Upload representative CSV and confirm order counts/scoping behavior.
3. Run optimization and confirm load outputs/status transitions.
4. Open load detail and verify routing/schematic visibility.
5. Generate export and verify file output integrity.

## 10) Known Constraints and Risk Areas

- SQLite constraints:
  - Multi-instance scale-out is unsafe without DB architecture change.
  - Write contention can surface as lock waits; tune `SQLITE_BUSY_TIMEOUT_SEC` as needed.
- Runtime sensitivity:
  - Import/optimization are synchronous and CPU-intensive.
  - Timeouts and worker/thread settings directly affect heavy workflow reliability.
- Process-local job status caveat:
  - App explicitly warns that `WEB_CONCURRENCY > 1` can break re-optimization job status visibility across workers.
- Coupling hotspot:
  - `blueprints/cot/routes.py` remains a large route/controller module; edits can have broad side effects.
- External dependency risk:
  - ORS failures/latency can degrade routing enrichments; fallback behavior exists but may change map/detail fidelity.
- Auth risk:
  - Entra config mismatch (redirect URI, tenant/client/secret) causes login failure even if app boots.
- Schema-evolution risk:
  - `db.py` contains schema ensure/rebuild logic beyond the SQL files in `migrations/`; changes must be reviewed as both code and migration artifacts to keep production behavior deterministic.

## 11) Appendices

### Appendix A - Functional Route Inventory

#### Health and Session/Auth
- `/robots933456.txt`, `/healthz`, `/session`, `/session/reset`, `/login`
- `/auth/microsoft/start`, `/auth/microsoft/callback`
- `/access/switch`, `/access/manage`, `/access/delete`

#### Orders and Optimization
- `/upload`, `/orders/upload`, `/orders`, `/orders/clear`, `/orders/exclude`, `/orders/include`, `/orders/optimize`, `/orders/export`
- `/orders/load-report/upload`
- `/api/orders/upload`, `/api/orders/manual-validate`, `/api/orders/scope-count`, `/api/orders/<so_num>/stack-config`
- `/api/skus/bulk-add`
- `/optimize`, `/optimize/build`, `/api/optimize`, `/api/optimize/<int:run_id>/loads`

#### Loads and Manual Editing
- `/loads`, `/loads/<int:load_id>`, `/loads/<int:load_id>/status`, `/loads/<int:load_id>/reject`
- `/loads/<int:load_id>/carrier`, `/loads/<int:load_id>/trailer`, `/loads/<int:load_id>/reverse-order`, `/loads/<int:load_id>/manifest-sequence`
- `/loads/<int:load_id>/schematic`, `/loads/<int:load_id>/schematic/edit`, `/loads/<int:load_id>/schematic/edit/save`
- `/loads/<int:load_id>/remove_order`, `/loads/<int:load_id>/manual_add`, `/loads/<int:load_id>/manual_add/suggestions`
- `/loads/manual/search`, `/loads/manual/suggest`, `/loads/manual/create`, `/loads/reopt_jobs/<job_id>`
- `/api/loads/<int:load_id>/route-geometry`
- `/loads/clear`, `/loads/approve_all`, `/loads/approve_full`, `/loads/reject_all`

#### Planning Sessions and Reports
- `/planning-sessions`, `/planning-sessions/<int:session_id>`, `/planning-sessions/<int:session_id>/summary`
- `/planning-sessions/<int:session_id>/archive`, `/planning-sessions/archive-all`, `/planning-sessions/<int:session_id>/delete`
- `/planning-sessions/<int:session_id>/revise`, `/planning-sessions/<int:session_id>/resume`
- `/planning-sessions/replay`, `/planning-sessions/replay/<int:run_id>`, `/planning-sessions/replay/<int:run_id>/loads`
- `/planning-sessions/replay/<int:run_id>/reproduce`, `/planning-sessions/replay/<int:run_id>/export.xlsx`, `/planning-sessions/replay/<int:run_id>/issues.csv`
- `/load-report/<int:session_id>`, `/load-report/<int:session_id>/export.xlsx`, `/load-report/<int:session_id>/load/<int:load_id>/sheet.xlsx`

#### Settings, Lookup, Feedback, Tutorial
- `/`, `/dashboard`, `/tutorial`, `/rates`, `/settings`, `/skus`, `/lookups`, `/feedback`, `/feedback/app`
- Save/mutation routes under `/settings/*`, `/rates/*`, `/skus/*`, `/lookups/*`, `/plants/save`, `/feedback/app/*`
- Planning settings mutation endpoint: `/planning-tools/save`

### Appendix B - Module Inventory
- Core:
  - `app.py`, `blueprints/cot/routes.py`, `db.py`
- Services:
  - `services/order_importer.py`, `services/optimizer.py`, `services/optimizer_engine.py`, `services/load_builder.py`, `services/stack_calculator.py`, `services/cost_calculator.py`, `services/routing_service.py`, `services/replay_evaluator.py`, `services/orders.py`, `services/order_categories.py`, `services/customer_rules.py`, `services/validation.py`, `services/geo_utils.py`, `services/tsp_solver.py`
- Templates and static:
  - `templates/`, `static/`
- Scripts:
  - `scripts/*.py`
- Tests:
  - `tests/*.py`

### Appendix C - Migration and Schema Notes
- Migration files:
  - `migrations/001_iteration1_schema.sql`
  - `migrations/002_pilot_ready.sql`
  - `migrations/003_road_routing.sql`
- Key schema evolution points:
  - Pilot-ready expansion of `order_lines` and load costing fields.
  - Road routing metadata + `route_cache` persistence table.
- DB initialization in `db.py` also contains `CREATE TABLE IF NOT EXISTS` guards and index creation for operational tables.

### Appendix D - Glossary
- SSO: Single Sign-On (Microsoft Entra in this app).
- ORS: OpenRouteService routing API.
- Replay Evaluator: Historical/comparative analysis tooling for planning outcomes.
- Schematic: Visual/structured trailer load layout representation.
- Seed Snapshot: CSV-based export/import mechanism for selected lookup/settings tables.

## Security Handling Note
This document references environment settings used in a live-like environment (`ENTRA_CLIENT_SECRET`, `ORS_API_KEY`, etc.). Before broad distribution:
1. Rotate all exposed secrets.
2. Restrict circulation to least-privileged recipients.
3. Store final Word/PDF artifact in approved secure location only.
