-- ProGrade PJ SKU per-row height source-of-truth migration
-- Adds per-SKU bottom/mid and top height fields so PJ dimensions live in pj_skus.

ALTER TABLE pj_skus ADD COLUMN height_mid_ft REAL;
ALTER TABLE pj_skus ADD COLUMN height_top_ft REAL;

-- Best-effort backfill for existing environments that still have category-level rows.
UPDATE pj_skus
SET
    height_mid_ft = COALESCE(
        height_mid_ft,
        (
            SELECT h.height_mid_ft
            FROM pj_height_reference h
            WHERE h.category = pj_skus.pj_category
            LIMIT 1
        )
    ),
    height_top_ft = COALESCE(
        height_top_ft,
        (
            SELECT h.height_top_ft
            FROM pj_height_reference h
            WHERE h.category = pj_skus.pj_category
            LIMIT 1
        )
    );
