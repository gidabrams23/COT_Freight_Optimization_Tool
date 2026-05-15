import os

import db

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module


def _set_authenticated_session(client):
    profiles = db.list_access_profiles()
    assert profiles
    with client.session_transaction() as session_state:
        session_state[app_module.SESSION_PROFILE_ID_KEY] = profiles[0]["id"]


def test_resolve_upload_discrepancy_releases_so_and_preserves_approved_load(monkeypatch):
    client = app_module.app.test_client()
    _set_authenticated_session(client)

    calls = {"feedback": 0, "released": 0, "status_updates": [], "resolve": 0}

    monkeypatch.setattr(
        app_module.db,
        "get_upload_load_discrepancy",
        lambda discrepancy_id: {
            "id": discrepancy_id,
            "upload_id": 101,
            "so_num": "SO-1",
            "discrepancy_type": app_module.UPLOAD_DISCREPANCY_SOURCE_UNASSIGNED_TOOL_ASSIGNED,
            "tool_load_id": 11,
            "tool_load_number": "GA26-1111",
            "resolved_at": None,
        },
    )
    monkeypatch.setattr(
        app_module.db,
        "get_load",
        lambda load_id: {
            "id": load_id,
            "status": app_module.STATUS_APPROVED,
            "origin_plant": "GA",
            "load_number": "GA26-1111",
        },
    )
    monkeypatch.setattr(app_module, "_load_access_failure_reason", lambda _load: None)
    monkeypatch.setattr(app_module.db, "list_load_lines", lambda _load_id: [{"so_num": "SO-1"}])
    monkeypatch.setattr(
        app_module.db,
        "add_load_feedback",
        lambda *args, **kwargs: calls.update({"feedback": calls["feedback"] + 1}),
    )
    monkeypatch.setattr(
        app_module.db,
        "upsert_load_order_release_override",
        lambda *_args, **_kwargs: calls.update({"released": calls["released"] + 1}),
    )
    monkeypatch.setattr(
        app_module.db,
        "update_load_status",
        lambda load_id, status, load_number=None: calls["status_updates"].append(
            (load_id, status, load_number)
        ),
    )
    monkeypatch.setattr(
        app_module.db,
        "resolve_upload_load_discrepancy",
        lambda *_args, **_kwargs: calls.update({"resolve": calls["resolve"] + 1}),
    )

    response = client.post("/api/uploads/101/discrepancies/9001/resolve-remove")

    assert response.status_code == 200
    payload = response.get_json() or {}
    assert payload.get("ok") is True
    assert payload.get("released") is True
    assert payload.get("preserved_load") is True
    assert calls["feedback"] == 1
    assert calls["released"] == 1
    assert calls["resolve"] == 1
    assert calls["status_updates"] == []


def test_resolve_upload_discrepancy_is_idempotent_when_already_resolved(monkeypatch):
    client = app_module.app.test_client()
    _set_authenticated_session(client)

    monkeypatch.setattr(
        app_module.db,
        "get_upload_load_discrepancy",
        lambda discrepancy_id: {
            "id": discrepancy_id,
            "upload_id": 101,
            "so_num": "SO-1",
            "discrepancy_type": app_module.UPLOAD_DISCREPANCY_SOURCE_UNASSIGNED_TOOL_ASSIGNED,
            "tool_load_id": 11,
            "resolved_at": "2026-01-01T12:00:00",
        },
    )

    response = client.post("/api/uploads/101/discrepancies/9002/resolve-remove")

    assert response.status_code == 200
    payload = response.get_json() or {}
    assert payload.get("ok") is True
    assert payload.get("already_resolved") is True


def test_resolve_upload_discrepancy_rejects_non_approved_load(monkeypatch):
    client = app_module.app.test_client()
    _set_authenticated_session(client)

    monkeypatch.setattr(
        app_module.db,
        "get_upload_load_discrepancy",
        lambda discrepancy_id: {
            "id": discrepancy_id,
            "upload_id": 101,
            "so_num": "SO-1",
            "discrepancy_type": app_module.UPLOAD_DISCREPANCY_SOURCE_UNASSIGNED_TOOL_ASSIGNED,
            "tool_load_id": 11,
            "resolved_at": None,
        },
    )
    monkeypatch.setattr(
        app_module.db,
        "get_load",
        lambda load_id: {
            "id": load_id,
            "status": app_module.STATUS_DRAFT,
            "origin_plant": "GA",
            "load_number": "GA26-1111",
        },
    )
    monkeypatch.setattr(app_module, "_load_access_failure_reason", lambda _load: None)

    response = client.post("/api/uploads/101/discrepancies/9003/resolve-remove")

    assert response.status_code == 409
    payload = response.get_json() or {}
    assert "approved loads" in (payload.get("error") or "").lower()


def test_resolve_order_discrepancy_remove_without_persisted_upload_discrepancy(monkeypatch):
    client = app_module.app.test_client()
    _set_authenticated_session(client)

    calls = {"feedback": 0, "released": 0, "resolve": 0}

    monkeypatch.setattr(
        app_module.db,
        "get_load",
        lambda load_id: {
            "id": load_id,
            "status": app_module.STATUS_APPROVED,
            "origin_plant": "GA",
            "load_number": "GA26-1111",
        },
    )
    monkeypatch.setattr(app_module, "_load_access_failure_reason", lambda _load: None)
    monkeypatch.setattr(app_module.db, "list_load_lines", lambda _load_id: [{"so_num": "SO-1"}])
    monkeypatch.setattr(
        app_module.db,
        "add_load_feedback",
        lambda *args, **kwargs: calls.update({"feedback": calls["feedback"] + 1}),
    )
    monkeypatch.setattr(
        app_module.db,
        "upsert_load_order_release_override",
        lambda *_args, **_kwargs: calls.update({"released": calls["released"] + 1}),
    )
    monkeypatch.setattr(
        app_module.db,
        "resolve_upload_load_discrepancy",
        lambda *_args, **_kwargs: calls.update({"resolve": calls["resolve"] + 1}),
    )

    response = client.post(
        "/api/orders/discrepancies/resolve-remove",
        json={"so_num": "SO-1", "tool_load_id": 11},
    )

    assert response.status_code == 200
    payload = response.get_json() or {}
    assert payload.get("ok") is True
    assert payload.get("released") is True
    assert payload.get("discrepancy_id") is None
    assert calls["feedback"] == 1
    assert calls["released"] == 1
    assert calls["resolve"] == 0


def test_recover_discrepancy_load_history_endpoint_returns_summary(monkeypatch):
    client = app_module.app.test_client()
    _set_authenticated_session(client)

    monkeypatch.setattr(
        app_module.db,
        "recover_upload_discrepancy_removed_orders",
        lambda recovered_by=None: {
            "candidate_pairs": 5,
            "restored_pairs": 4,
            "reactivated_loads": 1,
        },
    )

    response = client.post("/api/orders/discrepancies/recover-load-history")
    assert response.status_code == 200
    payload = response.get_json() or {}
    assert payload.get("ok") is True
    assert payload.get("candidate_pairs") == 5
    assert payload.get("restored_pairs") == 4
