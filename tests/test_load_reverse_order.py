import os

import db

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module


def _set_authenticated_session(client):
    profiles = db.list_access_profiles()
    assert profiles
    with client.session_transaction() as session_state:
        session_state[app_module.SESSION_PROFILE_ID_KEY] = profiles[0]["id"]


def test_apply_load_route_direction_reverses_without_mutating_input():
    source = [
        {"zip": "11111", "state": "TX"},
        {"zip": "22222", "state": "OK"},
        {"zip": "33333", "state": "KS"},
    ]

    result = app_module._apply_load_route_direction(source, reverse_route=True)

    assert [row["zip"] for row in result] == ["33333", "22222", "11111"]
    assert [row["zip"] for row in source] == ["11111", "22222", "33333"]


def test_load_route_display_metrics_skips_cached_values_when_requested():
    load = {
        "route_legs": [10, 20],
        "route_total_miles": 30,
        "route_geometry": [[1, 1], [2, 2], [3, 3]],
    }
    route_nodes = [
        {"coords": (30.0, -97.0)},
        {"coords": (30.5, -97.0)},
        {"coords": (31.0, -97.0)},
    ]

    metrics = app_module._load_route_display_metrics(
        load,
        route_nodes,
        use_cached_route=False,
    )

    assert metrics["route_legs"] != [10, 20]
    assert metrics["route_geometry"] == [[30.0, -97.0], [30.5, -97.0], [31.0, -97.0]]
    assert metrics["route_distance"] == round(sum(metrics["route_legs"]))


def test_reverse_load_order_endpoint_toggles_route_flag(monkeypatch):
    client = app_module.app.test_client()
    _set_authenticated_session(client)

    load = {
        "id": 42,
        "origin_plant": "ATL",
        "planning_session_id": 7,
        "status": "DRAFT",
        "route_reversed": 0,
    }
    captured = {}

    monkeypatch.setattr(app_module.db, "get_load", lambda load_id: load if load_id == 42 else None)
    monkeypatch.setattr(app_module, "_get_allowed_plants", lambda: ["ATL"])
    monkeypatch.setattr(
        app_module.db,
        "update_load_route_reversed",
        lambda load_id, route_reversed: captured.update(
            {"load_id": load_id, "route_reversed": route_reversed}
        ),
    )

    response = client.post("/loads/42/reverse-order")

    assert response.status_code in {301, 302}
    assert "/loads" in response.headers.get("Location", "")
    assert captured == {"load_id": 42, "route_reversed": True}


def test_reverse_load_order_endpoint_blocks_approved_loads(monkeypatch):
    client = app_module.app.test_client()
    _set_authenticated_session(client)

    load = {
        "id": 99,
        "origin_plant": "ATL",
        "planning_session_id": 7,
        "status": "APPROVED",
        "route_reversed": 0,
    }
    called = {"updated": False}

    monkeypatch.setattr(app_module.db, "get_load", lambda load_id: load if load_id == 99 else None)
    monkeypatch.setattr(app_module, "_get_allowed_plants", lambda: ["ATL"])
    monkeypatch.setattr(
        app_module.db,
        "update_load_route_reversed",
        lambda *_args, **_kwargs: called.update({"updated": True}),
    )

    response = client.post("/loads/99/reverse-order")

    assert response.status_code in {301, 302}
    assert "/loads" in response.headers.get("Location", "")
    assert called["updated"] is False
