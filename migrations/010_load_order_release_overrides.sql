CREATE TABLE IF NOT EXISTS load_order_release_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    load_id INTEGER NOT NULL,
    so_num TEXT NOT NULL,
    source TEXT,
    reason TEXT,
    notes TEXT,
    released_at TEXT NOT NULL DEFAULT (datetime('now')),
    released_by TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(load_id, so_num),
    FOREIGN KEY (load_id) REFERENCES loads(id)
);

CREATE INDEX IF NOT EXISTS idx_load_order_release_overrides_load_so
    ON load_order_release_overrides(load_id, so_num);

CREATE INDEX IF NOT EXISTS idx_load_order_release_overrides_active
    ON load_order_release_overrides(is_active);
