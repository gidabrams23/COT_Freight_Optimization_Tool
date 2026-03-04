import os
import sqlite3

import db

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module


def _build_assignment_fixture_db():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE planning_sessions (
            id INTEGER PRIMARY KEY,
            status TEXT
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
            status TEXT,
            cust_name TEXT,
            due_date TEXT
        );

        CREATE TABLE order_lines (
            id INTEGER PRIMARY KEY,
            so_num TEXT,
            city TEXT,
            is_excluded INTEGER,
            plant TEXT
        );
        """
    )

    connection.executemany(
        "INSERT INTO planning_sessions (id, status) VALUES (?, ?)",
        [
            (1, "ARCHIVED"),
            (2, "DRAFT"),
        ],
    )
    connection.executemany(
        "INSERT INTO loads (id, origin_plant, status, build_source, planning_session_id) VALUES (?, ?, ?, ?, ?)",
        [
            (10, "ATL", "DRAFT", "OPTIMIZED", 1),   # archived session (should be ignored for active draft pool)
            (11, "ATL", "DRAFT", "OPTIMIZED", 2),   # active draft session
            (12, "ATL", "DRAFT", "OPTIMIZED", None),  # unsessioned draft load
            (13, "ATL", "APPROVED", "OPTIMIZED", 1),  # approved load remains assigned
        ],
    )
    connection.executemany(
        "INSERT INTO orders (id, so_num, plant, is_excluded, status, cust_name, due_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "SO-A", "ATL", 0, "OPEN", "Customer A", "2026-03-10"),
            (2, "SO-B", "ATL", 0, "OPEN", "Customer B", "2026-03-11"),
            (3, "SO-C", "ATL", 0, "OPEN", "Customer C", "2026-03-12"),
            (4, "SO-D", "ATL", 0, "OPEN", "Customer D", "2026-03-13"),
        ],
    )
    connection.executemany(
        "INSERT INTO order_lines (id, so_num, city, is_excluded, plant) VALUES (?, ?, ?, ?, ?)",
        [
            (101, "SO-A", "Atlanta", 0, "ATL"),
            (102, "SO-B", "Atlanta", 0, "ATL"),
            (103, "SO-C", "Atlanta", 0, "ATL"),
            (104, "SO-D", "Atlanta", 0, "ATL"),
        ],
    )
    connection.executemany(
        "INSERT INTO load_lines (id, load_id, order_line_id) VALUES (?, ?, ?)",
        [
            (201, 10, 101),
            (202, 11, 102),
            (203, 12, 103),
            (204, 13, 104),
        ],
    )
    connection.commit()
    return connection


def test_archive_session_releases_orders_and_preserves_history(monkeypatch):
    calls = []
    active_session_updates = []

    monkeypatch.setattr(
        app_module.db,
        "get_planning_session",
        lambda session_id: {"id": session_id, "status": "DRAFT"},
    )
    monkeypatch.setattr(app_module, "_session_plant_scope", lambda session_id: ["ATL"])
    monkeypatch.setattr(
        app_module,
        "_reintroduce_orders_to_pool",
        lambda plants: calls.append(("reintroduce", tuple(plants))),
    )
    monkeypatch.setattr(
        app_module.db,
        "archive_planning_session",
        lambda session_id: calls.append(("archive", session_id)),
    )
    monkeypatch.setattr(
        app_module.db,
        "clear_loads_for_session",
        lambda session_id: calls.append(("clear", session_id)),
    )
    monkeypatch.setattr(app_module, "_get_active_planning_session_id", lambda: 42)
    monkeypatch.setattr(
        app_module,
        "_set_active_planning_session_id",
        lambda session_id: active_session_updates.append(session_id),
    )

    archived = app_module._archive_session_and_release_loads(42)

    assert archived is True
    assert ("reintroduce", ("ATL",)) in calls
    assert ("archive", 42) in calls
    assert ("clear", 42) not in calls
    assert active_session_updates == [None]


def test_manual_pool_filters_ignore_archived_sessions(monkeypatch):
    connection = _build_assignment_fixture_db()
    monkeypatch.setattr(db, "get_connection", lambda: connection)
    try:
        so_nums = db.filter_eligible_manual_so_nums("ATL", ["SO-A", "SO-B", "SO-C", "SO-D"])
        assert so_nums == {"SO-B", "SO-C"}

        rows = db.list_eligible_manual_orders("ATL", limit=None)
        returned = {row["so_num"] for row in rows}
        assert returned == {"SO-B", "SO-C"}
    finally:
        connection.close()


def test_order_assignment_ignores_archived_draft_loads(monkeypatch):
    connection = _build_assignment_fixture_db()
    monkeypatch.setattr(db, "get_connection", lambda: connection)
    try:
        assigned = db.list_orders(
            {"plant": "ATL", "assignment_status": "ASSIGNED", "include_closed": True}
        )
        assigned_so_nums = {row["so_num"] for row in assigned}
        assert assigned_so_nums == {"SO-B", "SO-C", "SO-D"}

        unassigned = db.list_orders(
            {"plant": "ATL", "assignment_status": "UNASSIGNED", "include_closed": True}
        )
        unassigned_so_nums = {row["so_num"] for row in unassigned}
        assert unassigned_so_nums == {"SO-A"}
    finally:
        connection.close()
