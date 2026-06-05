CREATE TABLE IF NOT EXISTS bwise_inventory_snapshot (
    item_number TEXT PRIMARY KEY,
    source_part_number TEXT,
    match_method TEXT,
    normalized_model TEXT,
    normalized_category TEXT,
    total_count INTEGER DEFAULT 0,
    available_count INTEGER DEFAULT 0,
    assigned_count INTEGER DEFAULT 0,
    updated_at DATETIME
);

CREATE TABLE IF NOT EXISTS bwise_inventory_upload_log (
    upload_id TEXT PRIMARY KEY,
    source_filename TEXT,
    source_format TEXT DEFAULT 'bwise_workbook',
    processed_rows INTEGER DEFAULT 0,
    valid_rows INTEGER DEFAULT 0,
    distinct_items INTEGER DEFAULT 0,
    matched_rows INTEGER DEFAULT 0,
    matched_items INTEGER DEFAULT 0,
    unmatched_items INTEGER DEFAULT 0,
    uploaded_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_bwise_inventory_snapshot_available
    ON bwise_inventory_snapshot(available_count DESC, item_number);

CREATE INDEX IF NOT EXISTS idx_bwise_inventory_upload_log_uploaded_at
    ON bwise_inventory_upload_log(uploaded_at DESC);
