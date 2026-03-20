# AGENTS.md - Freight Optimization App Operating Manual

This file governs AI agents working in this repository:
`AppDev-V2`.

It is tool-agnostic and intended to work for Codex, Claude Code, and similar coding agents.

## 1. Scope and Boundaries

- This repository is a freight/load optimization web application, not a generic design workspace.
- Primary stack: Flask + SQLite + Jinja templates + Python services.
- Treat `Context/my-project-design/` as a separate design-tool subtree.
- Do not modify `Context/my-project-design/` unless the user explicitly requests it.

## 2. Session Bootstrap (Read Order)

On each new session, read these in order before making major changes:

1. `AGENTS.md` (this file)
2. `README.md`
3. `docs/README.md`
4. `docs/IT_HANDOFF_AZURE_ARCHITECTURE_AND_MAINTENANCE.md`
5. Relevant PRD/spec for the requested change:
   - `docs/prd/*.md`
   - `docs/specs/*.md`
6. Any directly impacted template/service/module files

If these sources conflict, treat production-safe behavior and explicit user instructions as highest priority.

## 3. Product Context

The app supports planning teams by converting open order data into optimized freight loads.

Core flow:
1. Upload open orders data
2. Validate/map SKUs and assumptions
3. Build and optimize draft loads
4. Review/edit/approve loads
5. Export operational reports

Primary goals:
- Improve trailer utilization
- Reduce load count and freight cost
- Preserve planner control and auditability

## 4. Architecture Snapshot

- `app.py`: Flask routes/controllers and page/API orchestration
- `db.py`: SQLite connection and data access helpers
- `services/`: optimization, importing, routing, costing, validation, replay logic
- `templates/`: operator-facing Jinja pages
- `static/`: CSS, JS, and media assets
- `scripts/`: imports, seed sync, reports, maintenance utilities
- `data/seed/`: environment bootstrap data and selected config snapshots
- `tests/`: regression and feature tests
- `docs/`: PRDs/specs/IT handoff notes

## 5. Operational Invariants

- Do not break upload -> optimize -> review -> export workflow.
- Preserve historical data unless user explicitly requests destructive cleanup.
- Keep fallback behavior for routing/provider failures.
- Preserve access-control behavior and session safety on auth changes.
- Keep DB schema changes backward-safe via migrations and data-preserving defaults.
- Prefer single-instance-safe assumptions while SQLite is primary persistence.

## 6. Security and Config Guardrails

- Never commit secrets, tokens, client secrets, or credentials.
- Treat environment-variable values in docs as examples unless explicitly approved as live values.
- Avoid introducing debug-only behavior into production paths.
- Require validation for user inputs and uploaded files.
- Prefer least-privilege changes for auth/access logic.

When touching auth/session/upload/export paths, call out security impact in change notes.

## 7. Data and Migration Rules

- All schema changes must be captured in `migrations/` with clear forward-only steps.
- Do not silently rewrite seed/reference files unless the task requires it.
- For setting/profile changes that affect environment portability, update related seed snapshot scripts/docs.
- If changing table semantics, update both code paths and docs that describe the table contract.

## 8. Implementation Workflow Expectations

For non-trivial work:
1. Identify impacted files and risks
2. Implement smallest safe change
3. Add/update targeted tests
4. Run relevant tests (or explain why not run)
5. Update docs when behavior/config/ops guidance changed

When fixing bugs:
- Reproduce with a failing test when practical
- Fix root cause, not just symptoms
- Verify no regression in adjacent workflows

## 9. Testing Guidance

Preferred checks (based on change scope):

- Full suite: `pytest`
- Single file: `pytest tests/<target_file>.py`
- Endpoint smoke test for changed route
- Manual UI validation for changed templates

Do not claim success without evidence:
- Test output, or
- Clear explanation of what was validated manually and what remains unverified

## 10. UI/Template Change Rules

- Keep operator workflows clear and fast for dispatch/planning users.
- Preserve accessibility basics (labels, keyboard flow, contrast).
- Avoid visual-only changes that degrade data density on core pages (`orders`, `optimize`, `loads`, `load_report`).
- If changing template data dependencies, verify route payload compatibility.

## 11. Documentation and Handoff Rules

Update docs when any of the following changes:
- Environment/config contract
- Deployment/runtime behavior
- Data model or migration assumptions
- Core planner workflow
- Export format expectations

Preferred docs to update:
- `README.md` for developer setup/usage
- `docs/IT_HANDOFF_AZURE_ARCHITECTURE_AND_MAINTENANCE.md` for ops/runtime changes
- Relevant `docs/prd/*.md` or `docs/specs/*.md` for product behavior changes

## 12. Cross-Agent Compatibility (Codex + Claude)

- Keep this file as the source of truth for project context and guardrails.
- If a tool also uses `CLAUDE.md` or other instruction files, keep them consistent with this file.
- Do not duplicate large policy text across multiple files; link back to this file when possible.

## 13. Superpowers Guidance

Superpowers is an optional process framework (planning/debugging/testing skills).

- Use it as a workflow overlay, not as project domain truth.
- Do not copy superpowers bootstrap text into this project file.
- Project-specific context (business rules, architecture, constraints) belongs here in `AGENTS.md`.

## 14. Out of Scope by Default

Unless explicitly requested by the user, do not:
- Rework unrelated legacy modules
- Refactor large areas for style-only reasons
- Change deployment platform assumptions
- Modify files under `Context/my-project-design/`

## 15. Definition of Done

A task is done when all are true:
1. Requested behavior is implemented
2. Relevant tests/checks pass or gaps are explicitly stated
3. Related docs are updated when needed
4. No unrelated files were modified
5. Risks/assumptions are called out clearly
