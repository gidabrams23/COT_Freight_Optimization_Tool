import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "prograde.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    # Add new columns to existing tables (safe to run multiple times)
    migrations = [
        "ALTER TABLE load_sessions ADD COLUMN acknowledged_violations TEXT DEFAULT '[]'",
    ]
    for m in migrations:
        try:
            c.execute(m)
        except Exception:
            pass

    c.executescript("""
    CREATE TABLE IF NOT EXISTS carrier_configs (
        carrier_type TEXT PRIMARY KEY,
        brand TEXT,
        total_length_ft REAL,
        max_height_ft REAL,
        lower_deck_length_ft REAL,
        upper_deck_length_ft REAL,
        lower_deck_ground_height_ft REAL,
        upper_deck_ground_height_ft REAL,
        gn_max_lower_deck_ft REAL,
        notes TEXT,
        updated_at DATETIME
    );

    CREATE TABLE IF NOT EXISTS pj_tongue_groups (
        group_id TEXT PRIMARY KEY,
        group_label TEXT,
        tongue_feet REAL,
        model_codes TEXT,
        notes TEXT,
        updated_at DATETIME
    );

    CREATE TABLE IF NOT EXISTS pj_height_reference (
        category TEXT PRIMARY KEY,
        label TEXT,
        height_mid_ft REAL,
        height_top_ft REAL,
        gn_axle_dropped_ft REAL,
        notes TEXT,
        updated_at DATETIME
    );

    CREATE TABLE IF NOT EXISTS pj_measurement_offsets (
        rule_key TEXT PRIMARY KEY,
        label TEXT,
        offset_ft REAL,
        notes TEXT,
        updated_at DATETIME
    );

    CREATE TABLE IF NOT EXISTS pj_skus (
        item_number TEXT PRIMARY KEY,
        model TEXT,
        pj_category TEXT,
        description TEXT,
        gvwr INTEGER,
        bed_length_stated REAL,
        bed_length_measured REAL,
        tongue_group TEXT,
        tongue_feet REAL,
        total_footprint REAL,
        dump_side_height_ft REAL,
        can_nest_inside_dump INTEGER DEFAULT 0,
        gn_axle_droppable INTEGER DEFAULT 0,
        tongue_overlap_allowed INTEGER DEFAULT 0,
        pairing_rule TEXT,
        notes TEXT,
        updated_at DATETIME
    );

    CREATE TABLE IF NOT EXISTS bigtex_skus (
        item_number TEXT PRIMARY KEY,
        mcat TEXT,
        tier INTEGER,
        model TEXT,
        gvwr INTEGER,
        floor_type TEXT,
        bed_length REAL,
        width REAL,
        tongue REAL,
        stack_height REAL,
        total_footprint REAL,
        updated_at DATETIME
    );

    CREATE TABLE IF NOT EXISTS bt_stack_configs (
        config_id TEXT PRIMARY KEY,
        label TEXT,
        load_type TEXT,
        stack_position TEXT,
        max_length_ft REAL,
        max_height_ft REAL,
        notes TEXT,
        updated_at DATETIME
    );

    CREATE TABLE IF NOT EXISTS load_sessions (
        session_id TEXT PRIMARY KEY,
        brand TEXT,
        carrier_type TEXT,
        status TEXT DEFAULT 'draft',
        planner_name TEXT,
        session_label TEXT,
        created_at DATETIME,
        updated_at DATETIME,
        approved_by TEXT,
        approved_at DATETIME,
        notes TEXT
    );

    CREATE TABLE IF NOT EXISTS load_positions (
        position_id TEXT PRIMARY KEY,
        session_id TEXT,
        brand TEXT,
        item_number TEXT,
        deck_zone TEXT,
        layer INTEGER,
        sequence INTEGER,
        is_nested INTEGER DEFAULT 0,
        nested_inside TEXT,
        gn_axle_dropped INTEGER DEFAULT 0,
        override_reason TEXT,
        added_at DATETIME
    );

    CREATE TABLE IF NOT EXISTS load_patterns (
        pattern_id TEXT PRIMARY KEY,
        brand TEXT,
        pattern_name TEXT,
        load_type TEXT,
        carrier_type TEXT,
        source TEXT,
        confidence INTEGER DEFAULT 3,
        positions_json TEXT,
        unit_count INTEGER,
        notes TEXT,
        created_at DATETIME
    );
    """)

    conn.commit()
    conn.close()


# ── Carrier configs ──────────────────────────────────────────────────────────

def get_carrier_configs():
    with get_db() as conn:
        return conn.execute("SELECT * FROM carrier_configs ORDER BY brand, carrier_type").fetchall()

def get_carrier_config(carrier_type):
    with get_db() as conn:
        return conn.execute("SELECT * FROM carrier_configs WHERE carrier_type=?", (carrier_type,)).fetchone()

def update_carrier_config(carrier_type, field, value):
    with get_db() as conn:
        conn.execute(
            f"UPDATE carrier_configs SET {field}=?, updated_at=? WHERE carrier_type=?",
            (value, datetime.utcnow().isoformat(), carrier_type)
        )


# ── PJ tongue groups ─────────────────────────────────────────────────────────

def get_pj_tongue_groups():
    with get_db() as conn:
        return conn.execute("SELECT * FROM pj_tongue_groups ORDER BY group_id").fetchall()

def update_pj_tongue_group(group_id, field, value):
    with get_db() as conn:
        conn.execute(
            f"UPDATE pj_tongue_groups SET {field}=?, updated_at=? WHERE group_id=?",
            (value, datetime.utcnow().isoformat(), group_id)
        )


# ── PJ height reference ──────────────────────────────────────────────────────

def get_pj_height_reference():
    with get_db() as conn:
        return conn.execute("SELECT * FROM pj_height_reference ORDER BY category").fetchall()

def update_pj_height_reference(category, field, value):
    with get_db() as conn:
        conn.execute(
            f"UPDATE pj_height_reference SET {field}=?, updated_at=? WHERE category=?",
            (value, datetime.utcnow().isoformat(), category)
        )


# ── PJ measurement offsets ───────────────────────────────────────────────────

def get_pj_measurement_offsets():
    with get_db() as conn:
        return conn.execute("SELECT * FROM pj_measurement_offsets ORDER BY rule_key").fetchall()

def get_pj_offsets_dict():
    rows = get_pj_measurement_offsets()
    return {r["rule_key"]: r["offset_ft"] for r in rows}

def update_pj_measurement_offset(rule_key, field, value):
    with get_db() as conn:
        conn.execute(
            f"UPDATE pj_measurement_offsets SET {field}=?, updated_at=? WHERE rule_key=?",
            (value, datetime.utcnow().isoformat(), rule_key)
        )


def get_pj_height_ref_dict():
    """Return {category: dict} for use in constraint engine (no inner-loop DB calls)."""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM pj_height_reference").fetchall()
    return {r["category"]: dict(r) for r in rows}


# ── PJ SKUs ──────────────────────────────────────────────────────────────────

def get_pj_skus(search=None, category=None):
    sql = "SELECT * FROM pj_skus"
    params = []
    clauses = []
    if search:
        clauses.append("(model LIKE ? OR item_number LIKE ? OR description LIKE ?)")
        params += [f"%{search}%", f"%{search}%", f"%{search}%"]
    if category:
        clauses.append("pj_category=?")
        params.append(category)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY model, bed_length_stated"
    with get_db() as conn:
        return conn.execute(sql, params).fetchall()

def get_pj_sku(item_number):
    with get_db() as conn:
        return conn.execute("SELECT * FROM pj_skus WHERE item_number=?", (item_number,)).fetchone()

def update_pj_sku_field(item_number, field, value):
    with get_db() as conn:
        conn.execute(
            f"UPDATE pj_skus SET {field}=?, updated_at=? WHERE item_number=?",
            (value, datetime.utcnow().isoformat(), item_number)
        )


# ── Big Tex SKUs ─────────────────────────────────────────────────────────────

def get_bigtex_skus(search=None, mcat=None):
    sql = "SELECT * FROM bigtex_skus"
    params = []
    clauses = []
    if search:
        clauses.append("(model LIKE ? OR item_number LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]
    if mcat:
        clauses.append("mcat=?")
        params.append(mcat)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY mcat, model, bed_length"
    with get_db() as conn:
        return conn.execute(sql, params).fetchall()


# ── BT stack configs ─────────────────────────────────────────────────────────

def get_bt_stack_configs():
    with get_db() as conn:
        return conn.execute("SELECT * FROM bt_stack_configs ORDER BY load_type, stack_position").fetchall()

def update_bt_stack_config(config_id, field, value):
    with get_db() as conn:
        conn.execute(
            f"UPDATE bt_stack_configs SET {field}=?, updated_at=? WHERE config_id=?",
            (value, datetime.utcnow().isoformat(), config_id)
        )


# ── Load sessions ─────────────────────────────────────────────────────────────

def create_session(session_id, brand, carrier_type, planner_name, session_label):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO load_sessions
               (session_id, brand, carrier_type, status, planner_name, session_label, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (session_id, brand, carrier_type, "draft", planner_name, session_label,
             datetime.utcnow().isoformat(), datetime.utcnow().isoformat())
        )

def get_session(session_id):
    with get_db() as conn:
        return conn.execute("SELECT * FROM load_sessions WHERE session_id=?", (session_id,)).fetchone()

def get_all_sessions():
    with get_db() as conn:
        return conn.execute("SELECT * FROM load_sessions ORDER BY created_at DESC").fetchall()

def flag_session_stale(session_id):
    with get_db() as conn:
        conn.execute(
            "UPDATE load_sessions SET status='stale', updated_at=? WHERE session_id=?",
            (datetime.utcnow().isoformat(), session_id)
        )

def flag_all_draft_sessions_stale():
    """Called when settings change — flags all open sessions for revalidation."""
    with get_db() as conn:
        conn.execute(
            "UPDATE load_sessions SET status='stale', updated_at=? WHERE status IN ('draft','review')",
            (datetime.utcnow().isoformat(),)
        )

def mark_session_active(session_id):
    """Clear stale flag after revalidation."""
    with get_db() as conn:
        conn.execute(
            "UPDATE load_sessions SET status='draft', updated_at=? WHERE session_id=? AND status='stale'",
            (datetime.utcnow().isoformat(), session_id)
        )

def get_acknowledged_violations(session_id):
    """Return list of acknowledged rule_codes for this session."""
    import json
    with get_db() as conn:
        row = conn.execute(
            "SELECT acknowledged_violations FROM load_sessions WHERE session_id=?",
            (session_id,)
        ).fetchone()
    if not row or not row["acknowledged_violations"]:
        return []
    try:
        return json.loads(row["acknowledged_violations"])
    except Exception:
        return []

def set_acknowledged_violations(session_id, ack_list):
    """Persist updated acknowledgment list (list of rule_code strings)."""
    import json
    with get_db() as conn:
        conn.execute(
            "UPDATE load_sessions SET acknowledged_violations=? WHERE session_id=?",
            (json.dumps(ack_list), session_id)
        )


# ── Load positions ────────────────────────────────────────────────────────────

def get_positions(session_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM load_positions WHERE session_id=? ORDER BY deck_zone, sequence, layer",
            (session_id,)
        ).fetchall()

def add_position(position_id, session_id, brand, item_number, deck_zone, layer, sequence):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO load_positions
               (position_id, session_id, brand, item_number, deck_zone, layer, sequence, added_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (position_id, session_id, brand, item_number, deck_zone, layer, sequence,
             datetime.utcnow().isoformat())
        )

def remove_position(position_id):
    with get_db() as conn:
        conn.execute("DELETE FROM load_positions WHERE position_id=?", (position_id,))

def update_position_field(position_id, field, value):
    with get_db() as conn:
        conn.execute(
            f"UPDATE load_positions SET {field}=? WHERE position_id=?",
            (value, position_id)
        )

def get_pj_skus_for_tongue_group(group_id):
    """Return all PJ SKUs belonging to a tongue group."""
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM pj_skus WHERE tongue_group=?", (group_id,)
        ).fetchall()
