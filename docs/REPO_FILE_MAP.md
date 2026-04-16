# Repo File Map

## Runtime Entry
- `app.py`: imports/exports the live Flask app for `python app.py` and `gunicorn app:app`.

## Blueprints
- `blueprints/cot/routes.py`: all existing COT routes, route helpers, auth/session handlers, reporting/export handlers.
- `blueprints/prograde/routes.py`: ProGrade routes and load-builder APIs at `/prograde/*`.
- `blueprints/prograde/db.py`: ProGrade DB init, settings/SKU access, sessions, position mutations, inventory snapshot import.
- `blueprints/prograde/services/`: ProGrade rules and checks:
  - `bt_rules.py`: Big Tex stacking/length/height rules.
  - `pj_rules.py`: PJ stacking/length/height and footprint-aware rules.
  - `load_constraint_checker.py`: dispatches rule checks by brand.

## Core Data + Domain
- `db.py`: SQLite schema/init/helpers.
- `services/`: optimization, imports, routing, replay, and domain services.

## UI
- `templates/`: Jinja templates for planner/operator screens.
- `static/`: CSS, JS, and static JSON/media used by templates.
- `blueprints/prograde/templates/prograde/load_builder.html`: primary ProGrade stack schematic, drag/drop handlers, and render modes.
- `blueprints/prograde/static/styles.css`: shared ProGrade styling.

## Ops + Utilities
- `scripts/`: seed/export/import and maintenance scripts.
- `migrations/`: forward-only schema migration SQL.
- `data/seed/`: environment bootstrap/reference CSV snapshots.
- `tests/`: regression and behavior tests.

## Product/Engineering Docs
- `docs/prd/`: product requirements.
- `docs/specs/`: implementation and UX specs.
- `docs/IT_HANDOFF_AZURE_ARCHITECTURE_AND_MAINTENANCE.md`: production architecture and maintenance runbook.
- `docs/specs/PROGRADE_EDIT_MAP.md`: quickest edit-path map for ProGrade logic and schematic changes.
- `docs/specs/PROGRADE_BT_INVENTORY_GAP_WORKFLOW.md`: ProGrade Big Tex inventory upload/gap workflow.
- `docs/specs/PROGRADE_VISUAL_GUIDELINES.md`: ProGrade visual and interaction guardrails.

## ProGrade Hotspots (Most Common Edit Targets)
- Stacking/drag-drop behavior:
  - `blueprints/prograde/templates/prograde/load_builder.html`
  - `blueprints/prograde/routes.py`
  - `blueprints/prograde/db.py`
- Constraint and overlap calculations:
  - `blueprints/prograde/services/bt_rules.py`
  - `blueprints/prograde/services/pj_rules.py`
- Regression tests:
  - `tests/test_prograde_overlap_metrics.py`
  - `tests/test_prograde_rotation.py`
  - `tests/test_prograde_render_mode.py`
  - `tests/test_prograde_sessions_workflow.py`
