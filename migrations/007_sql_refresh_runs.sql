-- SQL refresh run logging for automated COT order refresh workflow.
-- Tracks auto/manual run outcomes and one-time first-login announcement state.

CREATE TABLE IF NOT EXISTS sql_refresh_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_started_at TEXT NOT NULL DEFAULT (datetime('now')),
    run_finished_at TEXT,
    refresh_day TEXT,
    trigger_source TEXT,
    initiated_by TEXT,
    status TEXT NOT NULL,
    upload_id INTEGER,
    filename TEXT,
    total_rows INTEGER DEFAULT 0,
    total_orders INTEGER DEFAULT 0,
    new_orders INTEGER DEFAULT 0,
    changed_orders INTEGER DEFAULT 0,
    unchanged_orders INTEGER DEFAULT 0,
    reopened_orders INTEGER DEFAULT 0,
    dropped_orders INTEGER DEFAULT 0,
    mapping_rate REAL DEFAULT 0.0,
    unmapped_count INTEGER DEFAULT 0,
    error_message TEXT,
    announced_first_login_at TEXT,
    FOREIGN KEY (upload_id) REFERENCES upload_history(id)
);

CREATE INDEX IF NOT EXISTS idx_sql_refresh_runs_started_at
    ON sql_refresh_runs(run_started_at DESC);

CREATE INDEX IF NOT EXISTS idx_sql_refresh_runs_refresh_day
    ON sql_refresh_runs(refresh_day);

CREATE INDEX IF NOT EXISTS idx_sql_refresh_runs_notice
    ON sql_refresh_runs(refresh_day, announced_first_login_at, trigger_source);
