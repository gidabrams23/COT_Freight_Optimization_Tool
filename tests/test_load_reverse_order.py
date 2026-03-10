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


def test_reverse_load_order_redirect_keeps_selected_load(monkeypatch):
    client = app_module.app.test_client()
    _set_authenticated_session(client)

    load = {
        "id": 42,
        "origin_plant": "ATL",
        "planning_session_id": 7,
        "status": "DRAFT",
        "route_reversed": 0,
    }

    monkeypatch.setattr(app_module.db, "get_load", lambda load_id: load if load_id == 42 else None)
    monkeypatch.setattr(app_module, "_get_allowed_plants", lambda: ["ATL"])
    monkeypatch.setattr(app_module.db, "update_load_route_reversed", lambda *_args, **_kwargs: None)

    response = client.post(
        "/loads/42/reverse-order",
        data={"next": "/loads?session_id=7", "selected_load": "42"},
    )

    assert response.status_code in {301, 302}
    location = response.headers.get("Location", "")
    assert "/loads" in location
    assert "selected_load=42" in location


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


def test_reverse_route_sequence_updates_order_color_mapping():
    stops = [
        {"zip": "73301", "state": "TX"},
        {"zip": "73008", "state": "OK"},
    ]
    reversed_stops = app_module._apply_load_route_direction(stops, reverse_route=True)
    stop_sequence_map = app_module._stop_sequence_map_from_ordered_stops(reversed_stops)
    palette = ["#111111", "#222222", "#333333"]
    lines = [
        {"so_num": "SO-TX", "state": "TX", "zip": "73301"},
        {"so_num": "SO-OK", "state": "OK", "zip": "73008"},
    ]

    order_colors = app_module._build_order_colors_for_lines(
        lines,
        stop_sequence_map=stop_sequence_map,
        stop_palette=palette,
    )

    # Reversed route means OK is stop 1 and TX is stop 2.
    assert order_colors["SO-OK"] == app_module._color_for_stop_sequence(1, palette)
    assert order_colors["SO-TX"] == app_module._color_for_stop_sequence(2, palette)


def test_progress_snapshot_counts_manual_load_orders_in_total():
    loads = [
        {
            "origin_plant": "ATL",
            "build_source": "MANUAL",
            "status": "DRAFT",
            "lines": [{"so_num": "SO-1"}, {"so_num": "SO-2"}],
        },
        {
            "origin_plant": "ATL",
            "build_source": "OPTIMIZED",
            "status": "PROPOSED",
            "lines": [{"so_num": "SO-3"}],
        },
    ]

    snapshot = app_module._compute_load_progress_snapshot(
        plant_scope=["ATL"],
        all_loads=loads,
        allowed_plants=["ATL"],
    )

    assert snapshot["total_orders"] == 3


def test_build_load_schematic_payload_uses_return_hint_for_ordering(monkeypatch):
    load_id = 7
    captured = {}

    monkeypatch.setattr(
        app_module.db,
        "get_load",
        lambda _load_id: {
            "id": load_id,
            "origin_plant": "ATL",
            "trailer_type": "STEP_DECK",
            "carrier_override_key": "",
            "route_legs": [],
            "route_total_miles": 0.0,
            "estimated_miles": 0.0,
            "utilization_pct": 0.0,
            "estimated_cost": 0.0,
        }
        if _load_id == load_id
        else None,
    )
    monkeypatch.setattr(
        app_module.db,
        "list_load_lines",
        lambda _load_id: [
            {
                "id": 1,
                "so_num": "SO-1",
                "item": "ITEM-1",
                "item_desc": "Item 1",
                "sku": "SKU-1",
                "qty": 1,
                "unit_length_ft": 10.0,
                "state": "TX",
                "zip": "73301",
            }
        ],
    )
    monkeypatch.setattr(app_module.db, "list_sku_specs", lambda: [])
    monkeypatch.setattr(app_module.db, "get_load_schematic_override", lambda _load_id: None)
    monkeypatch.setattr(app_module.geo_utils, "load_zip_coordinates", lambda: {})
    monkeypatch.setattr(app_module, "_requires_return_to_origin", lambda _lines: False)
    monkeypatch.setattr(app_module, "_alternate_requires_return_hint", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(app_module, "_load_has_lowes_order", lambda _lines: False)
    monkeypatch.setattr(
        app_module,
        "_ordered_stops_for_lines",
        lambda lines, origin_plant, zip_coords, return_to_origin=None: captured.update(
            {"return_to_origin": return_to_origin}
        )
        or [],
    )
    monkeypatch.setattr(app_module, "_apply_load_route_direction", lambda ordered_stops, load=None, reverse_route=None: ordered_stops)
    monkeypatch.setattr(
        app_module,
        "_resolve_load_carrier_pricing",
        lambda **_kwargs: {
            "carrier_key": "default",
            "carrier_label": "FLS",
            "rate_source_label": "",
            "selection_reason": "",
            "rate_per_mile": 0.0,
            "total_cost": 0.0,
        },
    )
    monkeypatch.setattr(app_module, "_build_freight_breakdown", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        app_module,
        "_calculate_load_schematic",
        lambda *args, **kwargs: (
            {
                "utilization_pct": 0.0,
                "exceeds_capacity": False,
                "warnings": [],
                "utilization_credit_ft": 0.0,
                "total_linear_feet": 0.0,
            },
            [],
            set(),
        ),
    )
    monkeypatch.setattr(app_module, "_get_effective_trailer_assignment_rules", lambda: {})
    monkeypatch.setattr(app_module, "_resolve_auto_hotshot_enabled_for_plant", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(app_module, "_get_effective_planning_setting", lambda _key: {"value_text": ""})
    monkeypatch.setattr(app_module, "_parse_strategic_customers", lambda _value: [])
    monkeypatch.setattr(app_module, "_auto_trailer_rule_annotation", lambda **_kwargs: ("", ""))
    monkeypatch.setattr(app_module, "_get_stop_fee_amount", lambda: 0.0)
    monkeypatch.setattr(app_module, "_get_fuel_surcharge_per_mile", lambda: 0.0)
    monkeypatch.setattr(app_module, "_get_load_minimum_amount", lambda: 0.0)

    payload = app_module._build_load_schematic_payload(load_id)

    assert payload is not None
    assert captured["return_to_origin"] is True
