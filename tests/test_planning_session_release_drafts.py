import os

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
    monkeypatch.setattr(app_module.db, "list_planning_sessions", lambda _filters=None: [])

    response = client.get("/planning-sessions")

    assert response.status_code == 200
    assert released_sessions == [55]
