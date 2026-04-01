"""
Pattern suggestion engine — Sprint 4 implementation target.
Stub: returns empty list until pattern library is seeded.
"""
import db


def suggest_patterns(session_id: str, brand: str, limit: int = 5) -> list:
    """
    Given the current session state, return known-good load patterns
    that match the brand and unit count/type mix.
    Stub for Sprint 1 — returns all patterns for the brand, unsorted.
    """
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM load_patterns WHERE brand=? ORDER BY confidence DESC LIMIT ?",
            (brand, limit)
        ).fetchall()
    return [dict(r) for r in rows]
