-- Pilot Ready schema updates

CREATE TABLE IF NOT EXISTS sku_specifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    length_with_tongue_ft REAL NOT NULL,
    max_stack_step_deck INTEGER DEFAULT 1,
    max_stack_flat_bed INTEGER DEFAULT 1,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS item_sku_lookup (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plant TEXT NOT NULL,
    bin TEXT NOT NULL,
    item_pattern TEXT,
    sku TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (sku) REFERENCES sku_specifications(sku)
);

CREATE TABLE IF NOT EXISTS rate_matrix (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin_plant TEXT NOT NULL,
    destination_state TEXT NOT NULL,
    rate_per_mile REAL NOT NULL,
    effective_year INTEGER DEFAULT 2026,
    notes TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(origin_plant, destination_state, effective_year)
);

ALTER TABLE order_lines ADD COLUMN due_date TEXT;
ALTER TABLE order_lines ADD COLUMN plant TEXT;
ALTER TABLE order_lines ADD COLUMN item TEXT;
ALTER TABLE order_lines ADD COLUMN qty INTEGER;
ALTER TABLE order_lines ADD COLUMN sales REAL;
ALTER TABLE order_lines ADD COLUMN so_num TEXT;
ALTER TABLE order_lines ADD COLUMN cust_name TEXT;
ALTER TABLE order_lines ADD COLUMN cpo TEXT;
ALTER TABLE order_lines ADD COLUMN salesman TEXT;
ALTER TABLE order_lines ADD COLUMN cust_num TEXT;
ALTER TABLE order_lines ADD COLUMN bin TEXT;
ALTER TABLE order_lines ADD COLUMN load_num TEXT;
ALTER TABLE order_lines ADD COLUMN address1 TEXT;
ALTER TABLE order_lines ADD COLUMN address2 TEXT;
ALTER TABLE order_lines ADD COLUMN city TEXT;
ALTER TABLE order_lines ADD COLUMN state TEXT;
ALTER TABLE order_lines ADD COLUMN zip TEXT;
ALTER TABLE order_lines ADD COLUMN sku TEXT;
ALTER TABLE order_lines ADD COLUMN unit_length_ft REAL;
ALTER TABLE order_lines ADD COLUMN total_length_ft REAL;
ALTER TABLE order_lines ADD COLUMN max_stack_height INTEGER;
ALTER TABLE order_lines ADD COLUMN stack_position INTEGER DEFAULT 1;
ALTER TABLE order_lines ADD COLUMN utilization_pct REAL;
ALTER TABLE order_lines ADD COLUMN is_excluded INTEGER DEFAULT 0;

ALTER TABLE loads ADD COLUMN origin_plant TEXT;
ALTER TABLE loads ADD COLUMN destination_state TEXT;
ALTER TABLE loads ADD COLUMN estimated_miles REAL;
ALTER TABLE loads ADD COLUMN rate_per_mile REAL;
ALTER TABLE loads ADD COLUMN estimated_cost REAL;
ALTER TABLE loads ADD COLUMN status TEXT DEFAULT 'DRAFT';
ALTER TABLE loads ADD COLUMN utilization_pct REAL DEFAULT 0.0;
ALTER TABLE loads ADD COLUMN optimization_score REAL DEFAULT 0.0;
ALTER TABLE loads ADD COLUMN created_by TEXT;
