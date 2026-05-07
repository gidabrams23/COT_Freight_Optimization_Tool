-- 008_bwise_skus.sql
-- Adds B-Wise SKU standards table for ProGrade brand pathway.

CREATE TABLE IF NOT EXISTS bwise_skus (
    item_number TEXT PRIMARY KEY,
    mcat TEXT,
    model TEXT,
    old_model TEXT,
    bed_length REAL,
    tongue REAL,
    stack_height REAL,
    total_footprint REAL,
    stack_height_is_placeholder INTEGER DEFAULT 0,
    updated_at DATETIME
);
