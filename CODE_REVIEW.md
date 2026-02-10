## Scope
Reviewed the Flask app and Python services: `app.py`, `db.py`, `services/*.py`. I did not review the `Context/` frontend subproject or docs.

## What The Code Does Well
1. Consistent use of parameterized SQL in `db.py` reduces SQL injection risk.
2. Query ordering is whitelisted in `db.list_orders` and `db.list_app_feedback`, which prevents order-by injection.
3. Business logic is mostly pushed into `services/` (e.g., optimizer, validation), keeping route handlers readable.
4. `geo_utils` caches zip and plant coordinates to avoid repeated I/O.

## Findings
1. **Security** — Default secret key fallback allows session forgery.  
Lines: `app.py:26`.  
Why: If `FLASK_SECRET_KEY` isn’t set, anyone who knows the fallback can forge session cookies and impersonate admins (especially given session-based roles).  
Fix: Require a strong secret at startup (e.g., `os.environ["FLASK_SECRET_KEY"]`) and fail fast if missing; rotate sessions after changing.

2. **Security** — Unauthenticated admin access via profile fallback.  
Lines: `app.py:516`, `app.py:520-529`.  
Why: `_ensure_active_profile` assigns the Admin profile (or first available profile) if no session exists, so any unauthenticated visitor gets elevated access.  
Fix: Remove the Admin fallback. Require explicit authentication or a profile selection workflow that is itself authenticated (password, SSO, etc.) before setting session roles.

3. **Security / Best Practices** — Debug mode enabled unconditionally.  
Lines: `app.py:4257-4258`.  
Why: Flask’s debug mode can expose an interactive debugger and code execution in production.  
Fix: Gate debug mode behind an env var or config and default it to `False`. Use a production WSGI server.

4. **Security** — Missing CSRF protection on state-changing POSTs.  
Lines: `app.py:1674-1682` (example; applies to many POST routes).  
Why: With cookie-based sessions, a malicious site can trigger POSTs on behalf of a logged-in user.  
Fix: Add CSRF tokens (Flask-WTF or custom) and enforce validation on all state-changing POST/JSON endpoints.

5. **Security** — Open redirect risk via `next` and `referrer`.  
Lines: `app.py:2992-3003`, `app.py:3014`, plus weak guard in `app.py:155-160`.  
Why: Unvalidated `next` values or referrers can redirect to external sites; `_safe_next_url` also accepts `//evil.com` (scheme-relative).  
Fix: Validate with `urlparse`/`werkzeug.urls.url_parse` to ensure `netloc` is empty and path does not start with `//`. Use `_safe_next_url` consistently.

6. **Correctness / Data Integrity** — Order import is not atomic.  
Lines: `app.py:1433-1437`, `db.py:917-921`, `db.py:924-1003`.  
Why: `clear_orders()` commits and inserts happen in separate connections. If insertion fails, data is lost or partially written.  
Fix: Use a single transaction for “replace all orders” (e.g., `db.replace_orders(summary)` that does `BEGIN`, clear, insert, commit, rollback on error).

7. **Security / Robustness** — File uploads lack size/type limits and leak exception details.  
Lines: `app.py:1427-1452`.  
Why: Any file type and size can be uploaded; large files can exhaust memory; error messages expose internals.  
Fix: Set `MAX_CONTENT_LENGTH`, validate extension/mime, and return a generic error while logging the exception server-side.

8. **Correctness** — Unvalidated numeric casts can raise 500s.  
Lines: `app.py:1691`, `app.py:3866-3867`, `app.py:3897`.  
Why: `int()`/`float()` on malformed input throws `ValueError`, resulting in 500s and potential partial writes.  
Fix: Validate inputs with try/except, return 400 on invalid values, or filter invalid IDs before processing.

9. **Data Integrity** — SQLite foreign keys are not enforced.  
Lines: `db.py:72-75`.  
Why: SQLite requires `PRAGMA foreign_keys = ON`; without it, constraints in schema are ignored, allowing orphan rows.  
Fix: Execute `PRAGMA foreign_keys = ON` immediately after connecting in `get_connection()`.

10. **Performance** — N+1 query pattern when listing loads.  
Lines: `services/load_builder.py:40-46`.  
Why: `list_loads()` issues one query per load to fetch lines, which is slow at scale.  
Fix: Fetch all `load_lines` for the relevant load IDs in one query and group in memory, or use a JOIN.

11. **Performance** — CSV parsing uses `iterrows()` and reads the whole file.  
Lines: `services/order_importer.py:28-45`.  
Why: `iterrows()` is slow for large DataFrames; `pd.read_csv` loads all data into memory.  
Fix: Use `itertuples()` or chunked `read_csv(..., chunksize=...)` and stream processing.

12. **Maintainability** — Duplicate upload logic in two routes.  
Lines: `app.py:1427-1447`, `app.py:1468-1478`.  
Why: The same CSV parsing/DB import logic is duplicated, increasing bug-fix and divergence risk.  
Fix: Extract a shared helper like `process_order_upload(file)` and call it from both routes.

## Remediation Plan
### P0 - Critical (fix immediately)
1. Disable debug mode by default. Effort: trivial. Order: do first before any deployment.
2. Remove Admin fallback and require authenticated profile selection. Effort: large. Order: do before tightening other security controls.
3. Enforce a strong secret key (no fallback). Effort: small. Order: can be done alongside auth changes.

### P1 - High (fix this sprint)
1. Add CSRF protection to all state-changing routes. Effort: medium. Order: after authentication is in place.
2. Make order imports atomic with a single transaction. Effort: medium. Order: independent of CSRF.
3. Add upload size/type validation and sanitize error messages. Effort: small. Order: alongside atomic import.

### P2 - Medium (fix soon)
1. Fix open redirect handling for `next`/`referrer`. Effort: small. Order: after CSRF.
2. Enable SQLite foreign keys in `get_connection()`. Effort: small. Order: independent.
3. Harden numeric input parsing for IDs/rates. Effort: small. Order: independent.
4. Remove N+1 queries in load listing. Effort: medium. Order: after correctness issues.
5. Optimize CSV parsing (chunked or `itertuples`). Effort: medium. Order: after upload validation.

### P3 - Low (backlog)
1. De-duplicate upload handling by extracting a helper. Effort: small. Order: after P1/P2 changes stabilize.

