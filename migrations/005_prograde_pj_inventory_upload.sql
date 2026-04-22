-- ProGrade PJ inventory upload persistence (snapshot + warehouse + upload log)
-- Forward-only migration notes:
-- - Mirrors BT inventory snapshot patterns for PJ workflows.
-- - Warehouse key is inventsiteid from the PJ inventory CSV extract.

CREATE TABLE IF NOT EXISTS pj_inventory_snapshot (
    item_number TEXT PRIMARY KEY,
    source_item_number TEXT,
    match_method TEXT,
    normalized_model TEXT,
    normalized_category TEXT,
    footprint_each REAL DEFAULT 0,
    stack_height_each REAL DEFAULT 0,
    total_count INTEGER DEFAULT 0,
    available_count INTEGER DEFAULT 0,
    assigned_count INTEGER DEFAULT 0,
    updated_at DATETIME
);

CREATE TABLE IF NOT EXISTS pj_inventory_snapshot_whse (
    item_number TEXT NOT NULL,
    whse_code TEXT NOT NULL,
    source_item_number TEXT,
    match_method TEXT,
    normalized_model TEXT,
    normalized_category TEXT,
    footprint_each REAL DEFAULT 0,
    stack_height_each REAL DEFAULT 0,
    total_count INTEGER DEFAULT 0,
    available_count INTEGER DEFAULT 0,
    assigned_count INTEGER DEFAULT 0,
    updated_at DATETIME,
    PRIMARY KEY (item_number, whse_code)
);

CREATE TABLE IF NOT EXISTS pj_inventory_upload_log (
    upload_id TEXT PRIMARY KEY,
    source_filename TEXT,
    source_format TEXT DEFAULT 'csv_inventory',
    processed_rows INTEGER DEFAULT 0,
    valid_rows INTEGER DEFAULT 0,
    deduped_rows INTEGER DEFAULT 0,
    duplicate_rows INTEGER DEFAULT 0,
    distinct_items INTEGER DEFAULT 0,
    warehouse_count INTEGER DEFAULT 0,
    matched_rows INTEGER DEFAULT 0,
    matched_items INTEGER DEFAULT 0,
    unmatched_items INTEGER DEFAULT 0,
    uploaded_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_pj_inventory_snapshot_available
    ON pj_inventory_snapshot(available_count DESC, item_number);

CREATE INDEX IF NOT EXISTS idx_pj_inventory_snapshot_whse_lookup
    ON pj_inventory_snapshot_whse(whse_code, available_count DESC, item_number);

CREATE INDEX IF NOT EXISTS idx_pj_inventory_upload_log_uploaded_at
    ON pj_inventory_upload_log(uploaded_at DESC);
