-- ProGrade-only account profiles and session builder attribution.
-- Intended for PROGRADE_DB_PATH databases.

ALTER TABLE load_sessions ADD COLUMN created_by_profile_id INTEGER;
ALTER TABLE load_sessions ADD COLUMN created_by_name TEXT;

CREATE TABLE IF NOT EXISTS prograde_access_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME,
    updated_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_prograde_access_profiles_name
    ON prograde_access_profiles(name COLLATE NOCASE);

UPDATE load_sessions
SET created_by_name = COALESCE(NULLIF(created_by_name, ''), planner_name)
WHERE COALESCE(created_by_name, '') = '' AND COALESCE(planner_name, '') != '';
