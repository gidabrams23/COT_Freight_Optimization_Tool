import sqlite3

import db
from services import load_builder


def _build_optimization_fixture_db():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE planning_sessions (
            id INTEGER PRIMARY KEY,
            status TEXT,
            is_sandbox INTEGER DEFAULT 0
        );

        CREATE TABLE loads (
            id INTEGER PRIMARY KEY,
            origin_plant TEXT,
            status TEXT,
            build_source TEXT,
            planning_session_id INTEGER
        );

        CREATE TABLE load_lines (
            id INTEGER PRIMARY KEY,
            load_id INTEGER,
            order_line_id INTEGER
        );

        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            so_num TEXT,
            plant TEXT,
            is_excluded INTEGER,
            status TEXT
        );

        CREATE TABLE order_lines (
            id INTEGER PRIMARY KEY,
            so_num TEXT,
            plant TEXT,
            due_date TEXT,
            is_excluded INTEGER
        );

        CREATE TABLE load_report_uploads (
            id INTEGER PRIMARY KEY,
            uploaded_at TEXT
        );

        CREATE TABLE load_report_assignments (
            id INTEGER PRIMARY KEY,
            upload_id INTEGER,
            so_num TEXT,
            load_number TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO planning_sessions (id, status, is_sandbox) VALUES (1, 'ACTIVE', 0)"
    )
    connection.executemany(
        "INSERT INTO orders (id, so_num, plant, is_excluded, status) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "SO-1", "ATL", 0, "OPEN"),
            (2, "SO-2", "ATL", 0, "OPEN"),
        ],
    )
    connection.executemany(
        "INSERT INTO order_lines (id, so_num, plant, due_date, is_excluded) VALUES (?, ?, ?, ?, ?)",
        [
            (101, "SO-1", "ATL", "2026-03-20", 0),
            (102, "SO-2", "ATL", "2026-03-20", 0),
        ],
    )
    connection.execute(
        "INSERT INTO loads (id, origin_plant, status, build_source, planning_session_id) VALUES (10, 'ATL', 'DRAFT', 'OPTIMIZED', 1)"
    )
    connection.execute(
        "INSERT INTO load_lines (id, load_id, order_line_id) VALUES (201, 10, 101)"
    )
    connection.commit()
    return connection


def test_list_order_lines_for_optimization_excludes_active_optimized_assignments(monkeypatch):
    connection = _build_optimization_fixture_db()
    monkeypatch.setattr(db, "get_connection", lambda: connection)
    try:
        rows = db.list_order_lines_for_optimization("ATL")
        so_nums = {row.get("so_num") for row in rows}
        assert so_nums == {"SO-2"}

        assigned = set(db.list_assigned_so_nums_for_active_loads("ATL", include_approved=True))
        assert assigned == {"SO-1"}
    finally:
        connection.close()


def test_enforce_singular_order_assignments_dedupes_across_loads_and_blocked_so_nums():
    class _OptimizerStub:
        @staticmethod
        def _build_load(groups, _params, standalone_cost=None):
            keys = [str((group or {}).get("key") or "").strip() for group in (groups or [])]
            lines = []
            for key in keys:
                lines.append({"id": f"line-{key}", "so_num": key})
            return {
                "groups": list(groups or []),
                "lines": lines,
                "standalone_cost": standalone_cost,
            }

    loads = [
        {
            "groups": [{"key": "SO-1"}, {"key": "SO-2"}],
            "lines": [{"id": 1, "so_num": "SO-1"}, {"id": 2, "so_num": "SO-2"}],
            "standalone_cost": 100.0,
        },
        {
            "groups": [{"key": "SO-2"}, {"key": "SO-3"}],
            "lines": [{"id": 3, "so_num": "SO-2"}, {"id": 4, "so_num": "SO-3"}],
            "standalone_cost": 120.0,
        },
        {
            "groups": [{"key": "SO-4"}],
            "lines": [{"id": 5, "so_num": "SO-4"}],
            "standalone_cost": 80.0,
        },
    ]

    sanitized, dropped = load_builder._enforce_singular_order_assignments(
        loads,
        _OptimizerStub(),
        params={"origin_plant": "ATL"},
        blocked_so_nums={"SO-4"},
    )

    assert len(sanitized) == 2
    assert [group.get("key") for group in sanitized[0]["groups"]] == ["SO-1", "SO-2"]
    assert [group.get("key") for group in sanitized[1]["groups"]] == ["SO-3"]
    assert dropped == {"SO-2", "SO-4"}
