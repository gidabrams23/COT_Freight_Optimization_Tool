-- Upload discrepancy tracking for approved-load SO removals from intake modal

CREATE TABLE IF NOT EXISTS upload_load_discrepancies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER NOT NULL,
    so_num TEXT NOT NULL,
    plant TEXT,
    discrepancy_type TEXT NOT NULL,
    source_prev_load_number TEXT,
    source_current_load_number TEXT,
    tool_load_id INTEGER,
    tool_load_number TEXT,
    tool_load_status TEXT,
    resolution_action TEXT,
    resolution_notes TEXT,
    resolved_at TEXT,
    resolved_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (upload_id) REFERENCES upload_history(id),
    FOREIGN KEY (tool_load_id) REFERENCES loads(id)
);

CREATE INDEX IF NOT EXISTS idx_upload_load_discrepancies_upload
ON upload_load_discrepancies(upload_id);

CREATE INDEX IF NOT EXISTS idx_upload_load_discrepancies_resolved
ON upload_load_discrepancies(resolved_at);

CREATE INDEX IF NOT EXISTS idx_upload_load_discrepancies_so_num
ON upload_load_discrepancies(so_num);
