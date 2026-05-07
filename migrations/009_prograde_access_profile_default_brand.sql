-- ProGrade profile default brand focus for account-based session landing.
-- Intended for PROGRADE_DB_PATH databases.

ALTER TABLE prograde_access_profiles ADD COLUMN default_brand TEXT;

UPDATE prograde_access_profiles
SET default_brand = 'bigtex'
WHERE lower(COALESCE(default_brand, '')) NOT IN ('bigtex', 'pj', 'bwise');
