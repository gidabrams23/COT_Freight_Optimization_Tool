# Architecture Standards Gap Assessment

Date: 2026-05-06  
Repository: `AppDev-V2`  
Applications assessed: Carry On Tool (COT) and ProGrade

## 1. Scope and Context

This assessment compares the current COT/ProGrade codebase to the client-provided **Architecture Standards - Overview** document (last updated March 17, 2026).

Important context from the client standard:
- It is framed for **new applications** going forward.
- It explicitly notes existing/legacy production apps are out of scope.

Because of that, this is a **gap assessment** and modernization effort estimate, not a binary pass/fail audit.

## 2. Scoring Model

Scale used for each section:
- `5.0` = strong alignment with standard
- `4.0` = mostly aligned, minor gaps
- `3.0` = partially aligned, meaningful gaps
- `2.0` = weak alignment, major gaps
- `1.0` = largely misaligned

## 3. Executive Summary Scores

Overall weighted impression:
- **COT: 2.8 / 5**
- **ProGrade: 2.5 / 5**

Interpretation:
- Engineering discipline and test coverage are meaningful strengths.
- Main gaps are architectural layering purity, observability maturity, and deviation from the client’s preferred stack (.NET/Vue/SQL Server/Azure-native platform standards).

## 4. Section-by-Section Scorecard

| Client Standard Section | COT | ProGrade | Summary |
|---|---:|---:|---|
| 1. Purpose & Scope | 3.0 | 3.0 | App purpose is clear and workflow-centered, but not formally structured under the client’s new-app architecture template. |
| 2. Architectural Principles | 2.8 | 2.5 | Good practical modularity in services, but large route/controller modules create coupling and reduce predictability. |
| 3. Solution Structure (Layering) | 2.6 | 2.1 | Partial layering exists, but API, orchestration, domain logic, and persistence concerns are frequently mixed in route files. |
| 4. API Design Guidelines | 3.1 | 3.0 | Many endpoints provide frontend-ready payloads and status handling; explicit typed view-model contracts are not formalized consistently. |
| 5. Frontend Responsibilities | 2.0 | 2.0 | Current UI is server-rendered Jinja + JS, not Vue SPA with strict display-only responsibility boundaries. |
| 6. Observability & Logging | 2.3 | 2.0 | Logging exists, but centralized telemetry model and consistent request/response/endpoint/status event structure are incomplete. |
| 7. Security & Identity | 3.1 | 2.6 | COT has Entra integration and role checks; model is session-driven and not uniformly JWT-based at API entry for all backend APIs. |
| 8. Testing Requirements | 4.2 | 4.0 | Strong test footprint, including many domain-level and workflow tests; this is a clear strength. |
| 9. Infra / Source Control / Deployment | 2.0 | 2.0 | CI/CD exists but not in the client’s Azure DevOps DEV->UAT->PROD promotion pattern with artifact promotion controls. |

## 5. Key Evidence From Repository

### Architecture and Layering
- Very large controller surface:
  - `blueprints/cot/routes.py` (~17.7k lines)
  - `blueprints/prograde/routes.py` (~6.5k lines)
- Route count concentration:
  - COT: 106 route handlers
  - ProGrade: 35 route handlers
- This indicates substantial orchestration and business/persistence coupling in controller layer.

### Authentication and Authorization
- Entra SSO integration is present in COT (`/auth/microsoft/start`, callback flow).
- Session role/profile model is enforced for many workflows.
- Standard calls for JWT validation at API entry and claims-based RBAC checks in a standardized manner across backend APIs.

### Persistence and Data Access
- Primary persistence is SQLite for both COT and ProGrade.
- Client standard preference is SQL Server + Dapper.
- There is selective SQL Server connectivity for refresh ingestion via SQLAlchemy/pyodbc, but core transactional model remains SQLite.

### Frontend Structure
- COT and ProGrade are primarily server-rendered templates (Jinja) with client-side JS enhancements.
- Client standard expects Vue SPA pattern with backend-shaped view models and minimal business logic in UI.

### DevOps and Deployment
- CI pipeline present via GitHub Actions (Docker image build/push).
- Deployment config includes Render setup and Azure-oriented runtime notes in docs.
- Client standard specifies Azure DevOps pipelines/repos and strict promotion model from deployable trunk to DEV/UAT/PROD.

### Testing
- Extensive `tests/` suite including optimization, route behavior, session workflows, and ProGrade-specific behavior.
- This is a strong alignment area relative to testing expectations.

## 6. Level of Effort to Reach “Great Standards” (Without Full Rewrite)

Assumption: Keep current Python/Flask/Jinja architecture, improve conformance where practical.

Estimated LOE:
- Cross-cutting platform standards uplift (logging, API contracts, auth hardening, deployment controls): **8-12 weeks**
- COT architecture decomposition and boundary cleanup: **8-12 weeks**
- ProGrade architecture decomposition and boundary cleanup: **10-14 weeks**
- Total blended program (parallelized workstreams): **14-22 weeks**

Typical workstreams:
- Introduce standardized API response contracts and endpoint purpose boundaries.
- Extract route-heavy domain logic into service/application layer classes.
- Formalize centralized structured logging and telemetry fields.
- Tighten auth posture for API endpoints and role claims handling consistency.
- Harden release controls and environment promotion mechanics.

## 7. Preferred Technology Stack Fit Assessment

Estimated current direct fit to preferred stack: **~25-35%**

| Preferred Category | Client Preference | Current State | Alignment |
|---|---|---|---|
| Cloud Platform | Microsoft Azure | Azure-compatible runtime documented, Render config also present | Partial |
| CI/CD & DevOps | Azure DevOps | GitHub Actions Docker build/push | Low |
| Infrastructure as Code | Bicep | No Bicep artifacts found | Low |
| Secret Management | Azure Key Vault | Env-var driven secrets | Low |
| Backend Framework | .NET (C#) | Flask/Python | Low |
| Frontend Framework | Vue.js SPA | Jinja + JS | Low |
| Database / Persistence | SQL Server | SQLite primary | Low |
| Data Access | Dapper.NET | `sqlite3` + targeted SQLAlchemy/pyodbc utility path | Low |
| Observability | Azure Monitor + App Insights | App logging present, no full standardized telemetry integration | Low |
| Edge Gateway | Cloudflare | Not established as standard edge pattern | Low |
| Messaging / Integration | Azure Service Bus | Not part of core architecture | Low |
| Distributed Caching | Redis | No Redis in core app architecture | Low |
| Identity & Auth | Entra External ID | Entra SSO present in COT | Partial |

## 8. LOE for Full Rebuild in Preferred Stack

Assumption: Rebuild both COT and ProGrade into target architecture:
- Backend: .NET/C#
- Frontend: Vue SPA
- Database: SQL Server
- Data access: Dapper
- Azure-native CI/CD and observability stack

Estimated effort: **9-14 months**

Indicative staffing model:
- 2 backend engineers
- 2 frontend engineers
- 1 QA engineer
- 0.5 DevOps engineer
- 0.5 architect/product lead

Primary risk areas:
- Functional parity for optimization and load-building logic.
- Exact behavioral parity for ProGrade interactive builder workflows.
- Data migration and session history continuity.
- UAT cycle length for planner-facing UX equivalency.

## 9. Recommended Delivery Strategy

Two pragmatic options:

1. **Modernize in place first (14-22 weeks)**  
   - Raise architecture and operational quality quickly.
   - Reduce risk before any rewrite decision.
   - Build clearer service/domain boundaries that can later map to .NET layers.

2. **Plan phased rebuild after stabilization (9-14 months)**  
   - Use modernized system as behavior baseline.
   - Migrate by domain slices (auth/session, orders intake, optimization, load review, exports, ProGrade builder).
   - Run dual-track validation in UAT until parity acceptance.

## 10. Assumptions and Caveats

- This assessment is based on repository evidence and documented runtime architecture, not live production telemetry.
- No new runtime tests were executed for this document; scoring reflects structural and implementation review.
- Existing in-progress workspace edits were intentionally left untouched.

