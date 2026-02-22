# ATW Load Planning App
## Hosting Operational Requirements (Client IT Owned)

This document defines operational requirements for hosting the application.  
Platform, SKU, VM/container host selection, and firewall architecture decisions are intentionally owned by ATW IT.

## 1. Application Overview
- Web application for freight/load planning workflows.
- Primary purpose: support the load planning team in batching customer orders into practical truck loads, with faster review and fewer manual planning steps.
- Workflow summary:
- Import daily open-order data from CSV.
- Standardize and evaluate order lines (SKU/stacking/utilization logic).
- Run optimization to propose grouped loads using planning rules (capacity, timing, geography).
- Let planners review draft loads, make manual adjustments, and approve/reject outcomes.
- Export operational outputs (orders/load reports) for downstream dispatch and execution.
- Core user actions: upload CSV order files, review/manage orders and sessions, run optimization calculations, approve/reject proposed loads, and export reports (CSV/XLSX).

## 2. Anticipated Usage
- Concurrent users: 8-10.
- Typical upload size: approximately 8,000 rows per CSV.
- Daily uploads contain overlapping orders from prior day (not all rows are net-new).
- Performance target: preferred user wait <= 30 seconds for common actions; maximum acceptable wait <= 90 seconds for heavy optimization/import actions.
- Business criticality: non-critical system; occasional restart/recovery is acceptable.

## 3. App Technology Profile
- Front end: server-rendered HTML templates with JavaScript/CSS.
- Back end: Python Flask application served by Gunicorn.
- Data layer: SQLite (file-based relational database).
- Packaging/deployment unit: Docker container.
- File operations: CSV ingest and XLSX report generation.

## 4. Minimum Operational Hosting Requirements
- Must support OCI/Docker container deployment.
- Must provide writable persistent storage for SQLite database and app-generated files.
- Must support secure environment-variable configuration for application secrets.
- Must terminate HTTPS/TLS and expose a stable URL for end users.
- Must provide enough CPU/memory to process 8-10 concurrent users with periodic 8k-row imports.
- Must support request processing behavior that can tolerate up to 90-second operations, or provide equivalent async/background execution controls.
- Must provide application and platform logs with retention and basic alerting (availability, restart loops, high error rate).
- Must include backup/restore capability for persistent app data (daily backup is sufficient for this workload).
- Must support controlled deployments with rollback capability.

## 5. Architecture Constraints IT Should Plan Around
- SQLite is file-based and can become a bottleneck under multi-instance write patterns.
- If active-active multi-instance scaling is desired, ATW IT should plan single-writer controls or migration from SQLite to a managed multi-user database.
- Optimization routines are CPU-intensive and may require capacity headroom during business-hour peaks.

## 6. Security and Access Requirements
- HTTPS required in transit.
- Secrets (admin password/session key) must be managed outside source code.
- Public URL access is acceptable for this deployment, with optional IP allowlisting per ATW policy.
- Standard host hardening, patching, and endpoint protection should follow ATW security baselines.

## 7. Handoff Acceptance Criteria
- End users can access the app via HTTPS and complete core workflows.
- System supports 8-10 concurrent users without instability.
- 8k-row upload + optimization workflows meet <= 90 second acceptable response target under normal conditions.
- Daily backups are configured, and restore procedure is documented by ATW IT.
