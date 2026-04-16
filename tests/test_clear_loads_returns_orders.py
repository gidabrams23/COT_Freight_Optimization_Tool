import os
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module


def test_clear_loads_returns_only_non_finalized_loads_in_scope(monkeypatch):
    deleted_ids = []
    archived_session_ids = []

    monkeypatch.setattr(app_module, "_require_session", lambda: None)
    monkeypatch.setattr(app_module, "_resolve_plant_filters", lambda _selected: ["GA", "NV"])
    monkeypatch.setattr(app_module, "_get_allowed_plants", lambda: ["GA", "NV", "TX"])
    monkeypatch.setattr(app_module, "_is_session_sandbox", lambda: False)
    monkeypatch.setattr(
        app_module.db,
        "list_loads",
        lambda origin_plant=None, session_id=None: [
            {"id": 1, "origin_plant": "GA", "status": "PROPOSED"},
            {"id": 2, "origin_plant": "GA", "status": "APPROVED"},
            {"id": 3, "origin_plant": "TX", "status": "PROPOSED"},
            {"id": 4, "origin_plant": "NV", "status": "DRAFT"},
            {"id": 5, "origin_plant": "NV", "status": None},
        ],
    )
    monkeypatch.setattr(app_module.db, "delete_load", lambda load_id: deleted_ids.append(int(load_id)))
    monkeypatch.setattr(app_module, "_sync_planning_session_status", lambda _session_id: None)
    monkeypatch.setattr(
        app_module,
        "_archive_session_and_release_loads",
        lambda session_id: archived_session_ids.append(session_id),
    )

    with app_module.app.test_request_context(
        "/loads/clear",
        method="POST",
        data={"plants": "GA,NV", "tab": "draft", "sort": "flow"},
    ):
        response = app_module.clear_loads()

    assert response.status_code == 302
    assert sorted(deleted_ids) == [1, 4, 5]
    assert archived_session_ids == []
    parsed = urlparse(response.location)
    query = parse_qs(parsed.query)
    assert query.get("manual_success") == ["Returned orders from 3 draft loads to the pool."]


def test_clear_loads_with_session_id_keeps_finalized_loads_and_syncs_status(monkeypatch):
    deleted_ids = []
    synced_session_ids = []
    archived_session_ids = []
    listed_session_ids = []

    monkeypatch.setattr(app_module, "_require_session", lambda: None)
    monkeypatch.setattr(app_module, "_resolve_plant_filters", lambda _selected: ["GA"])
    monkeypatch.setattr(app_module, "_get_allowed_plants", lambda: ["GA", "NV"])
    monkeypatch.setattr(app_module, "_is_session_sandbox", lambda: False)
    monkeypatch.setattr(
        app_module.db,
        "get_planning_session",
        lambda session_id: {"id": int(session_id), "plant_code": "GA", "status": "DRAFT"},
    )
    monkeypatch.setattr(app_module, "_can_access_planning_session", lambda _session: True)

    def _list_loads(_origin_plant=None, session_id=None):
        listed_session_ids.append(session_id)
        return [
            {"id": 10, "origin_plant": "GA", "status": "PROPOSED"},
            {"id": 11, "origin_plant": "GA", "status": "APPROVED"},
            {"id": 12, "origin_plant": "NV", "status": "PROPOSED"},
        ]

    monkeypatch.setattr(app_module.db, "list_loads", _list_loads)
    monkeypatch.setattr(app_module.db, "delete_load", lambda load_id: deleted_ids.append(int(load_id)))
    monkeypatch.setattr(
        app_module,
        "_sync_planning_session_status",
        lambda session_id: synced_session_ids.append(int(session_id)),
    )
    monkeypatch.setattr(
        app_module,
        "_archive_session_and_release_loads",
        lambda session_id: archived_session_ids.append(session_id),
    )

    with app_module.app.test_request_context(
        "/loads/clear",
        method="POST",
        data={"plants": "GA", "session_id": "22", "tab": "draft", "sort": "flow"},
    ):
        response = app_module.clear_loads()

    assert response.status_code == 302
    assert listed_session_ids == [22]
    assert deleted_ids == [10]
    assert synced_session_ids == [22]
    assert archived_session_ids == []
    parsed = urlparse(response.location)
    query = parse_qs(parsed.query)
    assert query.get("manual_success") == ["Returned orders from 1 draft load to the pool."]
