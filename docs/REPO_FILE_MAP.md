# Repo File Map

## Runtime Entry
- `app.py`: imports/exports the live Flask app for `python app.py` and `gunicorn app:app`.

## Blueprints
- `blueprints/cot/routes.py`: all existing COT routes, route helpers, auth/session handlers, reporting/export handlers.
- `blueprints/prograde/routes.py`: empty ProGrade blueprint placeholder registered at `/prograde`.

## Core Data + Domain
- `db.py`: SQLite schema/init/helpers.
- `services/`: optimization, imports, routing, replay, and domain services.

## UI
- `templates/`: Jinja templates for planner/operator screens.
- `static/`: CSS, JS, and static JSON/media used by templates.

## Ops + Utilities
- `scripts/`: seed/export/import and maintenance scripts.
- `migrations/`: forward-only schema migration SQL.
- `data/seed/`: environment bootstrap/reference CSV snapshots.
- `tests/`: regression and behavior tests.

## Product/Engineering Docs
- `docs/prd/`: product requirements.
- `docs/specs/`: implementation and UX specs.
- `docs/IT_HANDOFF_AZURE_ARCHITECTURE_AND_MAINTENANCE.md`: production architecture and maintenance runbook.
