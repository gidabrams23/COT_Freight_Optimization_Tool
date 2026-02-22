-- Road-aware routing storage fields

ALTER TABLE loads ADD COLUMN route_provider TEXT;
ALTER TABLE loads ADD COLUMN route_profile TEXT;
ALTER TABLE loads ADD COLUMN route_total_miles REAL;
ALTER TABLE loads ADD COLUMN route_legs_json TEXT;
ALTER TABLE loads ADD COLUMN route_geometry_json TEXT;
ALTER TABLE loads ADD COLUMN route_fallback INTEGER DEFAULT 0;

CREATE TABLE IF NOT EXISTS route_cache (
    cache_key TEXT PRIMARY KEY,
    provider TEXT,
    profile TEXT,
    objective TEXT,
    response_json TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_route_cache_expires ON route_cache(expires_at);
