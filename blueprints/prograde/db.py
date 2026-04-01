import sqlite3
import os
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
DB_PATH = Path(
    os.environ.get(
        "PROGRADE_DB_PATH",
        str(ROOT_DIR / "data" / "db" / "prograde.db"),
    )
)
FALLBACK_SEED_DB_PATH = ROOT_DIR / "archive" / "prograde_source_drop_v03" / "prograde.db"
DEFAULT_BT_DATA_WORKBOOK_PATH = Path(
    os.environ.get(
        "PROGRADE_BT_DATA_WORKBOOK_PATH",
        r"c:\Users\gabramowitz\OneDrive - Council Advisors\Bain Capital - ATW - ATW Operations Value Creation\03 - Phase 2\04 - Carry On MFO\PG Freight Tool\BT - Load Sheets\Stacking Guide Master.xlsx",
    )
)
FALLBACK_BT_DATA_WORKBOOK_PATH = ROOT_DIR / "data" / "reference" / "Stacking Guide Master.xlsx"
FALLBACK_BT_TEMP_WORKBOOK_PATH = ROOT_DIR / ".tmp" / "stacking_guide_master.xlsx"


def _ensure_db_file():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        return
    if FALLBACK_SEED_DB_PATH.exists():
        shutil.copyfile(FALLBACK_SEED_DB_PATH, DB_PATH)


def get_db():
    _ensure_db_file()
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


def has_seed_data():
    with get_db() as conn:
        carrier_count = conn.execute("SELECT COUNT(*) FROM carrier_configs").fetchone()[0]
        pj_count = conn.execute("SELECT COUNT(*) FROM pj_skus").fetchone()[0]
        bt_count = conn.execute("SELECT COUNT(*) FROM bigtex_skus").fetchone()[0]
    return carrier_count > 0 and (pj_count > 0 or bt_count > 0)


def _coerce_float(value, default=None):
    if value is None:
        return default
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return default
        value = raw
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value, default=None):
    if value is None:
        return default
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return default
        value = raw
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _normalize_header(value):
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum())


def get_bigtex_workbook_path(preferred_path=None):
    candidates = []
    if preferred_path:
        candidates.append(Path(str(preferred_path)))
    env_path = os.environ.get("PROGRADE_BT_DATA_WORKBOOK_PATH")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(DEFAULT_BT_DATA_WORKBOOK_PATH)
    candidates.append(FALLBACK_BT_DATA_WORKBOOK_PATH)
    candidates.append(FALLBACK_BT_TEMP_WORKBOOK_PATH)
    for path in candidates:
        try:
            if path and path.exists():
                with open(path, "rb"):
                    pass
                return path
        except OSError:
            continue
    return None


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


def get_bigtex_sku(item_number):
    with get_db() as conn:
        return conn.execute("SELECT * FROM bigtex_skus WHERE item_number=?", (item_number,)).fetchone()


def update_bigtex_sku_field(item_number, field, value):
    with get_db() as conn:
        conn.execute(
            f"UPDATE bigtex_skus SET {field}=?, updated_at=? WHERE item_number=?",
            (value, datetime.utcnow().isoformat(), item_number)
        )


def recompute_bigtex_footprint(item_number):
    row = get_bigtex_sku(item_number)
    if not row:
        return None
    bed_length = _coerce_float(row["bed_length"], 0.0) or 0.0
    tongue = _coerce_float(row["tongue"], 0.0) or 0.0
    total_footprint = round(bed_length + tongue, 2)
    with get_db() as conn:
        conn.execute(
            "UPDATE bigtex_skus SET total_footprint=?, updated_at=? WHERE item_number=?",
            (total_footprint, datetime.utcnow().isoformat(), item_number),
        )
    return {
        "item_number": item_number,
        "bed_length": round(bed_length, 2),
        "tongue": round(tongue, 2),
        "total_footprint": total_footprint,
    }


def import_bigtex_skus_from_workbook(workbook_path=None, sheet_name="Data"):
    source_path = get_bigtex_workbook_path(workbook_path)
    if not source_path:
        raise FileNotFoundError("Big Tex workbook not found. Set PROGRADE_BT_DATA_WORKBOOK_PATH or provide a valid path.")

    # OneDrive files can be reparse points. Read from a local temp copy for reliable workbook access.
    temp_copy = Path(tempfile.gettempdir()) / f"prograde_bigtex_import_{uuid.uuid4().hex}.xlsx"
    shutil.copyfile(source_path, temp_copy)

    try:
        from openpyxl import load_workbook

        workbook = load_workbook(temp_copy, read_only=True, data_only=True)
        selected_sheet = None
        if sheet_name in workbook.sheetnames:
            selected_sheet = sheet_name
        else:
            for name in workbook.sheetnames:
                if str(name).strip().lower() == "data":
                    selected_sheet = name
                    break
        if selected_sheet is None:
            selected_sheet = workbook.sheetnames[0]

        sheet = workbook[selected_sheet]
        rows_iter = sheet.iter_rows(min_row=1, values_only=True)
        header_row = next(rows_iter, None)
        if not header_row:
            raise ValueError("Workbook sheet has no header row.")

        header_map = {idx: _normalize_header(value) for idx, value in enumerate(header_row)}
        aliases = {
            "mcat": {"mcat", "category", "loadcategory"},
            "tier": {"tier"},
            "model": {"model"},
            "item_number": {"itemnumber", "item", "itemno", "itemnum", "sku", "itemid"},
            "gvwr": {"gvwr"},
            "floor_type": {"floortype", "floor"},
            "bed_length": {"bedlength", "bed", "bedlen", "decklength"},
            "width": {"width"},
            "tongue": {"tongue", "tonguelength", "tongueft"},
            "stack_height": {"stackheight", "stackht", "height"},
        }

        def _find_col(key):
            valid = aliases[key]
            for idx, name in header_map.items():
                if name in valid:
                    return idx
            return None

        required = {"item_number", "bed_length", "tongue"}
        col_idx = {key: _find_col(key) for key in aliases}
        missing = [key for key in required if col_idx.get(key) is None]
        if missing:
            raise ValueError(f"Data sheet missing required columns: {', '.join(missing)}")

        parsed = {}
        for row in rows_iter:
            if not row:
                continue
            item_raw = row[col_idx["item_number"]] if col_idx["item_number"] is not None and col_idx["item_number"] < len(row) else None
            item_number = str(item_raw or "").strip()
            if not item_number:
                continue
            bed_length_raw = row[col_idx["bed_length"]] if col_idx["bed_length"] is not None and col_idx["bed_length"] < len(row) else None
            tongue_raw = row[col_idx["tongue"]] if col_idx["tongue"] is not None and col_idx["tongue"] < len(row) else None
            bed_length = _coerce_float(bed_length_raw, 0.0) or 0.0
            tongue = _coerce_float(tongue_raw, 0.0) or 0.0
            total_footprint = round(bed_length + tongue, 2)

            mcat_raw = row[col_idx["mcat"]] if col_idx["mcat"] is not None and col_idx["mcat"] < len(row) else None
            tier_raw = row[col_idx["tier"]] if col_idx["tier"] is not None and col_idx["tier"] < len(row) else None
            model_raw = row[col_idx["model"]] if col_idx["model"] is not None and col_idx["model"] < len(row) else None
            gvwr_raw = row[col_idx["gvwr"]] if col_idx["gvwr"] is not None and col_idx["gvwr"] < len(row) else None
            floor_raw = row[col_idx["floor_type"]] if col_idx["floor_type"] is not None and col_idx["floor_type"] < len(row) else None
            width_raw = row[col_idx["width"]] if col_idx["width"] is not None and col_idx["width"] < len(row) else None
            stack_height_raw = row[col_idx["stack_height"]] if col_idx["stack_height"] is not None and col_idx["stack_height"] < len(row) else None

            parsed[item_number] = (
                item_number,
                str(mcat_raw).strip() if mcat_raw is not None else None,
                _coerce_int(tier_raw, None),
                str(model_raw).strip() if model_raw is not None else None,
                _coerce_int(gvwr_raw, None),
                str(floor_raw).strip() if floor_raw is not None else None,
                round(bed_length, 2),
                _coerce_float(width_raw, None),
                round(tongue, 2),
                _coerce_float(stack_height_raw, None),
                total_footprint,
                datetime.utcnow().isoformat(),
            )

        if not parsed:
            raise ValueError("No Big Tex rows were parsed from workbook Data tab.")

        with get_db() as conn:
            conn.execute("DELETE FROM bigtex_skus")
            conn.executemany(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, mcat, tier, model, gvwr, floor_type, bed_length, width, tongue, stack_height, total_footprint, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                list(parsed.values()),
            )

        return {
            "source_path": str(source_path),
            "sheet_name": selected_sheet,
            "row_count": len(parsed),
        }
    finally:
        try:
            temp_copy.unlink(missing_ok=True)
        except OSError:
            pass


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


def get_position(position_id):
    with get_db() as conn:
        return conn.execute("SELECT * FROM load_positions WHERE position_id=?", (position_id,)).fetchone()

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


def _load_zone_columns(connection, session_id, deck_zone):
    rows = connection.execute(
        """
        SELECT position_id, sequence, layer
        FROM load_positions
        WHERE session_id=? AND deck_zone=?
        ORDER BY sequence ASC, layer ASC
        """,
        (session_id, deck_zone),
    ).fetchall()
    grouped = []
    for row in rows:
        sequence = int(row["sequence"])
        if not grouped or grouped[-1]["sequence"] != sequence:
            grouped.append({"sequence": sequence, "ids": []})
        grouped[-1]["ids"].append(row["position_id"])
    return grouped


def _find_column_index(columns, sequence):
    for idx, col in enumerate(columns):
        if int(col["sequence"]) == int(sequence):
            return idx
    return -1


def _apply_zone_columns_layout(connection, session_id, deck_zone, columns):
    for seq_idx, col in enumerate(columns, start=1):
        for layer_idx, position_id in enumerate(col["ids"], start=1):
            connection.execute(
                """
                UPDATE load_positions
                SET deck_zone=?, sequence=?, layer=?
                WHERE session_id=? AND position_id=?
                """,
                (deck_zone, seq_idx, layer_idx, session_id, position_id),
            )


def move_column(session_id, from_zone, sequence, to_zone, insert_index=None):
    with get_db() as conn:
        from_columns = _load_zone_columns(conn, session_id, from_zone)
        if not from_columns:
            return None

        src_idx = _find_column_index(from_columns, sequence)
        if src_idx < 0:
            return None

        source_column = from_columns.pop(src_idx)

        if from_zone == to_zone:
            target_columns = from_columns
            target_idx = len(target_columns) if insert_index is None else int(insert_index)
            target_idx = max(0, min(target_idx, len(target_columns)))
            target_columns.insert(target_idx, source_column)
            _apply_zone_columns_layout(conn, session_id, from_zone, target_columns)
            return {
                "from_zone": from_zone,
                "to_zone": to_zone,
                "insert_index": target_idx,
                "sequence": target_idx + 1,
                "count": len(source_column["ids"]),
            }

        to_columns = _load_zone_columns(conn, session_id, to_zone)
        target_idx = len(to_columns) if insert_index is None else int(insert_index)
        target_idx = max(0, min(target_idx, len(to_columns)))
        to_columns.insert(target_idx, source_column)

        _apply_zone_columns_layout(conn, session_id, from_zone, from_columns)
        _apply_zone_columns_layout(conn, session_id, to_zone, to_columns)
        return {
            "from_zone": from_zone,
            "to_zone": to_zone,
            "insert_index": target_idx,
            "sequence": target_idx + 1,
            "count": len(source_column["ids"]),
        }


def move_position(session_id, position_id, to_zone, to_sequence=None, insert_index=None):
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT position_id, deck_zone, sequence
            FROM load_positions
            WHERE session_id=? AND position_id=?
            """,
            (session_id, position_id),
        ).fetchone()
        if not row:
            return None

        from_zone = row["deck_zone"]
        from_sequence = int(row["sequence"])
        from_columns = _load_zone_columns(conn, session_id, from_zone)
        src_idx = _find_column_index(from_columns, from_sequence)
        if src_idx < 0:
            return None

        source_column = from_columns[src_idx]
        if position_id not in source_column["ids"]:
            return None
        source_column["ids"].remove(position_id)
        if not source_column["ids"]:
            from_columns.pop(src_idx)

        if to_zone == from_zone:
            target_columns = from_columns
        else:
            target_columns = _load_zone_columns(conn, session_id, to_zone)

        if to_sequence is not None:
            target_idx = _find_column_index(target_columns, int(to_sequence))
            if target_idx < 0:
                return None
            target_columns[target_idx]["ids"].append(position_id)
            final_sequence = target_idx + 1
            final_layer = len(target_columns[target_idx]["ids"])
        else:
            target_idx = len(target_columns) if insert_index is None else int(insert_index)
            target_idx = max(0, min(target_idx, len(target_columns)))
            target_columns.insert(target_idx, {"sequence": None, "ids": [position_id]})
            final_sequence = target_idx + 1
            final_layer = 1

        if to_zone == from_zone:
            _apply_zone_columns_layout(conn, session_id, from_zone, target_columns)
        else:
            _apply_zone_columns_layout(conn, session_id, from_zone, from_columns)
            _apply_zone_columns_layout(conn, session_id, to_zone, target_columns)

        return {
            "position_id": position_id,
            "from_zone": from_zone,
            "to_zone": to_zone,
            "sequence": final_sequence,
            "layer": final_layer,
        }


def _next_zone_sequence(connection, session_id, deck_zone):
    row = connection.execute(
        "SELECT COALESCE(MAX(sequence), 0) AS max_seq FROM load_positions WHERE session_id=? AND deck_zone=?",
        (session_id, deck_zone),
    ).fetchone()
    return int(row["max_seq"] or 0) + 1


def duplicate_column(session_id, deck_zone, sequence):
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM load_positions
            WHERE session_id=? AND deck_zone=? AND sequence=?
            ORDER BY layer ASC
            """,
            (session_id, deck_zone, int(sequence)),
        ).fetchall()
        if not rows:
            return None

        new_seq = _next_zone_sequence(conn, session_id, deck_zone)
        id_map = {row["position_id"]: str(uuid.uuid4()) for row in rows}
        now = datetime.utcnow().isoformat()
        for row in rows:
            nested_inside = row["nested_inside"]
            if nested_inside in id_map:
                nested_inside = id_map[nested_inside]
            conn.execute(
                """
                INSERT INTO load_positions
                (position_id, session_id, brand, item_number, deck_zone, layer, sequence, is_nested, nested_inside, gn_axle_dropped, override_reason, added_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    id_map[row["position_id"]],
                    row["session_id"],
                    row["brand"],
                    row["item_number"],
                    row["deck_zone"],
                    row["layer"],
                    new_seq,
                    row["is_nested"],
                    nested_inside,
                    row["gn_axle_dropped"],
                    row["override_reason"],
                    now,
                ),
            )
    return {"new_sequence": new_seq, "count": len(rows)}


def move_column_zone(session_id, from_zone, sequence, to_zone):
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT position_id
            FROM load_positions
            WHERE session_id=? AND deck_zone=? AND sequence=?
            ORDER BY layer ASC
            """,
            (session_id, from_zone, int(sequence)),
        ).fetchall()
        if not rows:
            return None
        new_seq = _next_zone_sequence(conn, session_id, to_zone)
        for row in rows:
            conn.execute(
                """
                UPDATE load_positions
                SET deck_zone=?, sequence=?
                WHERE position_id=?
                """,
                (to_zone, new_seq, row["position_id"]),
            )
    return {"new_sequence": new_seq, "count": len(rows)}


def resequence_column(session_id, deck_zone, sequence, direction):
    step = -1 if str(direction).lower() == "left" else 1
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT sequence
            FROM load_positions
            WHERE session_id=? AND deck_zone=?
            ORDER BY sequence ASC
            """,
            (session_id, deck_zone),
        ).fetchall()
        sequences = [int(row["sequence"]) for row in rows]
        if int(sequence) not in sequences:
            return None
        idx = sequences.index(int(sequence))
        target_idx = idx + step
        if target_idx < 0 or target_idx >= len(sequences):
            return {"moved": False}
        src = sequences[idx]
        dst = sequences[target_idx]
        temp_seq = -999999
        conn.execute(
            "UPDATE load_positions SET sequence=? WHERE session_id=? AND deck_zone=? AND sequence=?",
            (temp_seq, session_id, deck_zone, src),
        )
        conn.execute(
            "UPDATE load_positions SET sequence=? WHERE session_id=? AND deck_zone=? AND sequence=?",
            (src, session_id, deck_zone, dst),
        )
        conn.execute(
            "UPDATE load_positions SET sequence=? WHERE session_id=? AND deck_zone=? AND sequence=?",
            (dst, session_id, deck_zone, temp_seq),
        )
    return {"moved": True, "new_sequence": dst}


def clear_session_positions(session_id):
    with get_db() as conn:
        conn.execute("DELETE FROM load_positions WHERE session_id=?", (session_id,))

def get_pj_skus_for_tongue_group(group_id):
    """Return all PJ SKUs belonging to a tongue group."""
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM pj_skus WHERE tongue_group=?", (group_id,)
        ).fetchall()
