-- Iteration 1 schema updates

ALTER TABLE order_lines ADD COLUMN ship_to_zip TEXT NOT NULL DEFAULT '';
ALTER TABLE order_lines ADD COLUMN trailer_category TEXT DEFAULT 'STANDARD';
ALTER TABLE order_lines ADD COLUMN is_excluded INTEGER DEFAULT 0;
ALTER TABLE order_lines ADD COLUMN origin_plant TEXT DEFAULT '';

ALTER TABLE loads ADD COLUMN status TEXT DEFAULT 'DRAFT';
ALTER TABLE loads ADD COLUMN utilization_pct REAL DEFAULT 0.0;
ALTER TABLE loads ADD COLUMN total_miles REAL DEFAULT 0.0;
ALTER TABLE loads ADD COLUMN detour_miles REAL DEFAULT 0.0;
ALTER TABLE loads ADD COLUMN optimization_score REAL DEFAULT 0.0;

CREATE TABLE stacking_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trailer_category TEXT NOT NULL,
    max_stack_height INTEGER DEFAULT 1,
    feet_per_unit REAL DEFAULT 0.0,
    notes TEXT,
    created_at TEXT NOT NULL
);

INSERT INTO stacking_rules (trailer_category, max_stack_height, feet_per_unit, notes, created_at)
VALUES
    ('STANDARD', 2, 4.0, 'Standard dry van trailers, 2-high stack', datetime('now')),
    ('TALL', 1, 5.5, 'Tall trailers, no stacking', datetime('now')),
    ('WIDE', 1, 6.0, 'Wide loads, no mixing', datetime('now')),
    ('MIXED', 1, 4.5, 'Mixed SKUs, conservative stacking', datetime('now'));
