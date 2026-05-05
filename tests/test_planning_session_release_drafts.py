import os
from datetime import date

import db

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module


def _set_authenticated_session(client):
    profiles = db.list_access_profiles()
    assert profiles
    with client.session_transaction() as session_state:
        session_state[app_module.SESSION_PROFILE_ID_KEY] = profiles[0]["id"]


def test_release_draft_loads_for_session_skips_approved(monkeypatch):
    deleted_ids = []
    synced = []

    monkeypatch.setattr(
        app_module.db,
        "list_loads",
        lambda _origin_plant=None, session_id=None: [
            {"id": 11, "status": "DRAFT"},
            {"id": 12, "status": "PROPOSED"},
            {"id": 13, "status": "APPROVED"},
            {"id": 14, "status": None},
        ],
    )
    monkeypatch.setattr(app_module.db, "delete_load", lambda load_id: deleted_ids.append(int(load_id)))
    monkeypatch.setattr(
        app_module,
        "_sync_planning_session_status",
        lambda session_id: synced.append(int(session_id)),
    )

    released = app_module._release_draft_loads_for_session(77)

    assert released == [11, 12, 14]
    assert deleted_ids == [11, 12, 14]
    assert synced == [77]


def test_release_draft_loads_endpoint_returns_json_summary(monkeypatch):
    client = app_module.app.test_client()
    _set_authenticated_session(client)

    monkeypatch.setattr(
        app_module,
        "_get_scoped_planning_session_or_404",
        lambda session_id: {"id": session_id, "status": "DRAFT"},
    )
    monkeypatch.setattr(
        app_module,
        "_release_draft_loads_for_session",
        lambda session_id: [401, 402] if session_id == 9 else [],
    )

    response = client.post(
        "/planning-sessions/9/release-draft-loads",
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload == {
        "ok": True,
        "session_id": 9,
        "released_count": 2,
        "released_load_ids": [401, 402],
        "message": "Released 2 draft loads back to the pool.",
    }


def test_planning_sessions_view_auto_releases_active_draft_session(monkeypatch):
    client = app_module.app.test_client()
    _set_authenticated_session(client)

    released_sessions = []
    monkeypatch.setattr(app_module, "_get_active_planning_session_id", lambda: 55)
    monkeypatch.setattr(app_module, "_get_allowed_plants", lambda: ["ATL"])
    monkeypatch.setattr(app_module, "_can_access_planning_session", lambda _session: True)
    monkeypatch.setattr(
        app_module.db,
        "get_planning_session",
        lambda session_id: {"id": session_id, "status": "DRAFT", "plant_code": "ATL"},
    )
    monkeypatch.setattr(
        app_module,
        "_release_draft_loads_for_session",
        lambda session_id: released_sessions.append(int(session_id)) or [],
    )
    monkeypatch.setattr(
        app_module,
        "_auto_release_stale_unplanned_sessions",
        lambda reference_day=None: {},
    )
    monkeypatch.setattr(app_module.db, "list_planning_sessions", lambda _filters=None: [])

    response = client.get("/planning-sessions")

    assert response.status_code == 200
    assert released_sessions == [55]


def test_auto_release_stale_unplanned_sessions_releases_and_archives(monkeypatch):
    archived_ids = []
    cleared_active_ids = []

    monkeypatch.setattr(
        app_module.db,
        "list_stale_planning_sessions",
        lambda _before_date: [
            {"id": 101, "status": "DRAFT"},
            {"id": 102, "status": "DRAFT"},
            {"id": 103, "status": "COMPLETED"},
        ],
    )
    monkeypatch.setattr(
        app_module,
        "_release_draft_loads_for_session",
        lambda session_id: [session_id * 10] if session_id in {101, 102} else [],
    )
    monkeypatch.setattr(
        app_module.db,
        "list_loads",
        lambda _origin_plant=None, session_id=None: (
            [] if session_id == 101 else [{"id": 9001, "status": "APPROVED"}]
        ),
    )
    monkeypatch.setattr(
        app_module.db,
        "archive_planning_session",
        lambda session_id: archived_ids.append(int(session_id)),
    )
    monkeypatch.setattr(app_module, "_get_active_planning_session_id", lambda: 101)
    monkeypatch.setattr(
        app_module,
        "_set_active_planning_session_id",
        lambda session_id: cleared_active_ids.append(session_id),
    )

    summary = app_module._auto_release_stale_unplanned_sessions(reference_day=date(2026, 5, 5))

    assert summary["reference_day"] == "2026-05-05"
    assert summary["inspected_sessions"] == 3
    assert summary["released_sessions"] == 2
    assert summary["released_loads"] == 2
    assert summary["archived_sessions"] == 1
    assert summary["archived_session_ids"] == [101]
    assert archived_ids == [101]
    assert cleared_active_ids == [None]


def test_internal_planning_sessions_eod_cleanup_returns_summary(monkeypatch):
    client = app_module.app.test_client()
    monkeypatch.setattr(app_module, "_verify_sql_refresh_internal_token", lambda: True)
    monkeypatch.setattr(
        app_module,
        "_auto_release_stale_unplanned_sessions",
        lambda reference_day=None: {
            "reference_day": "2026-05-05",
            "inspected_sessions": 4,
            "released_loads": 3,
            "released_sessions": 2,
            "archived_sessions": 1,
            "archived_session_ids": [88],
        },
    )

    response = client.post("/internal/planning-sessions/eod-cleanup")

    assert response.status_code == 200
    assert response.get_json() == {
        "ok": True,
        "message": "Stale draft sessions were cleaned up.",
        "reference_day": "2026-05-05",
        "inspected_sessions": 4,
        "released_loads": 3,
        "released_sessions": 2,
        "archived_sessions": 1,
        "archived_session_ids": [88],
    }
