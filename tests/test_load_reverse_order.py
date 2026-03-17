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


def test_apply_route_stop_order_reorders_and_keeps_unlisted_stops():
    stops = [
        {"state": "VA", "zip": "23061"},
        {"state": "VA", "zip": "23456"},
        {"state": "VA", "zip": "20190"},
    ]

    reordered = app_module._apply_route_stop_order(
        stops,
        stop_order=["VA|23456", "VA|23061"],
    )

    assert [app_module._line_stop_key(stop["state"], stop["zip"]) for stop in reordered] == [
        "VA|23456",
        "VA|23061",
        "VA|20190",
    ]


def test_save_manifest_sequence_endpoint_persists_stop_order(monkeypatch):
    client = app_module.app.test_client()
    _set_authenticated_session(client)

    load = {
        "id": 42,
        "origin_plant": "ATL",
        "planning_session_id": 7,
        "status": "DRAFT",
        "trailer_type": "STEP_DECK",
        "route_reversed": 0,
    }
    lines = [
        {"so_num": "SO-1", "state": "VA", "zip": "23061", "cust_name": "A"},
        {"so_num": "SO-2", "state": "VA", "zip": "23456", "cust_name": "B"},
    ]
    captured = {}

    monkeypatch.setattr(app_module.db, "get_load", lambda load_id: dict(load) if load_id == 42 else None)
    monkeypatch.setattr(app_module.db, "list_load_lines", lambda _load_id: list(lines))
    monkeypatch.setattr(app_module, "_load_access_failure_reason", lambda _load: None)
    monkeypatch.setattr(app_module, "_requires_return_to_origin", lambda _lines: False)
    monkeypatch.setattr(app_module, "_alternate_requires_return_hint", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(app_module, "_load_has_lowes_order", lambda _lines: False)
    monkeypatch.setattr(app_module, "_build_load_carrier_pricing_context", lambda: {})
    monkeypatch.setattr(app_module.geo_utils, "load_zip_coordinates", lambda: {})
    monkeypatch.setattr(app_module.geo_utils, "plant_coords_for_code", lambda _plant: (33.0, -84.0))
    monkeypatch.setattr(
        app_module.tsp_solver,
        "solve_route",
        lambda _origin, stops, return_to_origin=False: list(stops),
    )
    monkeypatch.setattr(
        app_module.db,
        "update_load_route_stop_order",
        lambda load_id, stop_order: captured.update(
            {"load_id": load_id, "stop_order": list(stop_order)}
        ),
    )
    monkeypatch.setattr(
        app_module.db,
        "update_load_route_reversed",
        lambda load_id, route_reversed: captured.update(
            {"route_reversed": (load_id, route_reversed)}
        ),
    )
    monkeypatch.setattr(
        app_module.db,
        "delete_load_schematic_override",
        lambda load_id: captured.update({"override_deleted": load_id}),
    )

    response = client.post(
        "/loads/42/manifest-sequence",
        json={"stop_order": ["VA|23456", "VA|23061"]},
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert captured["load_id"] == 42
    assert captured["stop_order"] == ["VA|23456", "VA|23061"]
    assert captured["route_reversed"] == (42, False)
    assert captured["override_deleted"] == 42


def test_approve_lock_status_update_dedupes_orders_from_unapproved_loads(monkeypatch):
    client = app_module.app.test_client()
    _set_authenticated_session(client)

    load = {
        "id": 42,
        "origin_plant": "ATL",
        "planning_session_id": 7,
        "status": "DRAFT",
        "load_number": "ATL26-0007-D",
    }
    captured = {}

    monkeypatch.setattr(app_module.db, "get_load", lambda load_id: dict(load) if load_id == 42 else None)
    monkeypatch.setattr(app_module.db, "get_planning_session", lambda _session_id: {"id": 7, "plant_code": "ATL"})
    monkeypatch.setattr(app_module, "_get_allowed_plants", lambda: ["ATL"])
    monkeypatch.setattr(app_module, "_load_access_failure_reason", lambda _load: None)
    monkeypatch.setattr(app_module, "_is_session_sandbox", lambda: False)
    monkeypatch.setattr(
        app_module.db,
        "update_load_status",
        lambda load_id, status, load_number=None: captured.update(
            {"status_update": (load_id, status, load_number)}
        ),
    )
    monkeypatch.setattr(
        app_module.db,
        "list_load_lines",
        lambda _load_id: [
            {"so_num": "SO-1"},
            {"so_num": "SO-2"},
            {"so_num": "SO-2"},
        ],
    )
    monkeypatch.setattr(
        app_module.db,
        "remove_orders_from_unapproved_loads",
        lambda plant, so_nums, session_id=None, exclude_load_id=None: captured.update(
            {
                "dedupe_args": {
                    "plant": plant,
                    "so_nums": list(so_nums),
                    "session_id": session_id,
                    "exclude_load_id": exclude_load_id,
                }
            }
        )
        or {"removed_lines": 3, "deleted_loads": 1},
    )
    monkeypatch.setattr(app_module, "_sync_planning_session_status", lambda _session_id: "DRAFT")
    monkeypatch.setattr(app_module.load_builder, "list_loads", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        app_module,
        "_compute_load_progress_snapshot",
        lambda **_kwargs: {
            "approved_orders": 2,
            "total_orders": 2,
            "progress_pct": 100.0,
            "draft_tab_count": 0,
            "final_tab_count": 1,
        },
    )

    response = client.post(
        "/loads/42/status",
        data={"action": "approve_lock"},
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert captured["status_update"] == (42, app_module.STATUS_APPROVED, "ATL26-0007")
    assert captured["dedupe_args"] == {
        "plant": "ATL",
        "so_nums": ["SO-1", "SO-2"],
        "session_id": 7,
        "exclude_load_id": 42,
    }
    assert payload["dedupe"] == {"removed_lines": 3, "deleted_loads": 1}


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


def test_build_load_schematic_edit_payload_uses_return_hint_for_ordering(monkeypatch):
    load_id = 8
    captured = {}

    monkeypatch.setattr(
        app_module.db,
        "get_load",
        lambda _load_id: {
            "id": load_id,
            "origin_plant": "ATL",
            "trailer_type": "STEP_DECK",
            "status": "DRAFT",
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
                "order_line_id": 1,
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
    monkeypatch.setattr(app_module, "_build_load_carrier_pricing_context", lambda: {})
    monkeypatch.setattr(
        app_module,
        "_ordered_stops_for_lines",
        lambda lines, origin_plant, zip_coords, return_to_origin=None: captured.update(
            {"return_to_origin": return_to_origin}
        )
        or [],
    )
    monkeypatch.setattr(
        app_module,
        "_apply_load_route_direction",
        lambda ordered_stops, load=None, reverse_route=None: ordered_stops,
    )

    payload = app_module._build_load_schematic_edit_payload(load_id)

    assert payload is not None
    assert captured["return_to_origin"] is True


def test_ordered_stops_for_lines_uses_local_tsp_sequence(monkeypatch):
    lines = [
        {"state": "TX", "zip": "73301", "city": "Austin", "cust_name": "A"},
        {"state": "OK", "zip": "73008", "city": "Bethany", "cust_name": "B"},
    ]
    zip_coords = {
        "73301": (30.2672, -97.7431),
        "73008": (35.5187, -97.6323),
    }
    captured = {"called": False}

    class _RoutingServiceProbe:
        def build_route(self, *_args, **_kwargs):
            raise AssertionError("routing_service should not be used for stop sequencing")

    monkeypatch.setattr(
        app_module.routing_service,
        "get_routing_service",
        lambda: _RoutingServiceProbe(),
    )
    monkeypatch.setattr(app_module.geo_utils, "plant_coords_for_code", lambda _plant: (33.0, -84.0))

    def _fake_solve_route(_origin, stops, return_to_origin=False):
        captured["called"] = True
        captured["return_to_origin"] = return_to_origin
        return list(reversed(stops))

    monkeypatch.setattr(app_module.tsp_solver, "solve_route", _fake_solve_route)

    ordered = app_module._ordered_stops_for_lines(
        lines,
        origin_plant="ATL",
        zip_coords=zip_coords,
        return_to_origin=True,
    )

    assert captured["called"] is True
    assert captured["return_to_origin"] is True
    assert [stop.get("zip") for stop in ordered] == ["73008", "73301"]


def test_schematic_and_edit_payloads_share_reversed_stop_color_mapping(monkeypatch):
    load_id = 55
    load = {
        "id": load_id,
        "origin_plant": "ATL",
        "trailer_type": "STEP_DECK",
        "status": "DRAFT",
        "carrier_override_key": "",
        "route_legs": [],
        "route_total_miles": 0.0,
        "estimated_miles": 0.0,
        "utilization_pct": 0.0,
        "estimated_cost": 0.0,
        "route_reversed": 1,
    }
    lines = [
        {
            "id": 1,
            "so_num": "SO-1",
            "item": "ITEM-1",
            "item_desc": "Item 1",
            "sku": "SKU-1",
            "qty": 1,
            "unit_length_ft": 8.0,
            "state": "VA",
            "zip": "11111",
        },
        {
            "id": 2,
            "so_num": "SO-2",
            "item": "ITEM-2",
            "item_desc": "Item 2",
            "sku": "SKU-2",
            "qty": 1,
            "unit_length_ft": 8.0,
            "state": "MD",
            "zip": "22222",
        },
        {
            "id": 3,
            "so_num": "SO-3",
            "item": "ITEM-3",
            "item_desc": "Item 3",
            "sku": "SKU-3",
            "qty": 1,
            "unit_length_ft": 8.0,
            "state": "NY",
            "zip": "33333",
        },
    ]
    zip_coords = {
        "11111": (37.0, -77.0),
        "22222": (39.0, -76.0),
        "33333": (43.0, -75.0),
    }
    palette = ["#111111", "#222222", "#333333", "#FFFFFF"]

    monkeypatch.setattr(app_module.db, "get_load", lambda _load_id: dict(load) if _load_id == load_id else None)
    monkeypatch.setattr(app_module.db, "list_load_lines", lambda _load_id: list(lines))
    monkeypatch.setattr(app_module.db, "list_sku_specs", lambda: [])
    monkeypatch.setattr(app_module.db, "get_load_schematic_override", lambda _load_id: None)
    monkeypatch.setattr(app_module.geo_utils, "load_zip_coordinates", lambda: dict(zip_coords))
    monkeypatch.setattr(app_module.geo_utils, "plant_coords_for_code", lambda _plant: (34.0, -84.0))
    monkeypatch.setattr(app_module.tsp_solver, "solve_route", lambda _origin, stops, return_to_origin=False: list(stops))
    monkeypatch.setattr(app_module, "_get_stop_color_palette", lambda: list(palette))
    monkeypatch.setattr(app_module, "_requires_return_to_origin", lambda _lines: False)
    monkeypatch.setattr(app_module, "_alternate_requires_return_hint", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(app_module, "_load_has_lowes_order", lambda _lines: False)
    monkeypatch.setattr(app_module, "_build_load_carrier_pricing_context", lambda: {})
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
    monkeypatch.setattr(app_module, "_get_effective_trailer_assignment_rules", lambda: {})
    monkeypatch.setattr(app_module, "_resolve_auto_hotshot_enabled_for_plant", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(app_module, "_get_effective_planning_setting", lambda _key: {"value_text": ""})
    monkeypatch.setattr(app_module, "_parse_strategic_customers", lambda _value: [])
    monkeypatch.setattr(app_module, "_auto_trailer_rule_annotation", lambda **_kwargs: ("", ""))
    monkeypatch.setattr(app_module, "_get_stop_fee_amount", lambda: 0.0)
    monkeypatch.setattr(app_module, "_get_fuel_surcharge_per_mile", lambda: 0.0)
    monkeypatch.setattr(app_module, "_get_load_minimum_amount", lambda: 0.0)

    payload = app_module._build_load_schematic_payload(load_id)
    edit_payload = app_module._build_load_schematic_edit_payload(load_id)

    ordered_stops = app_module._ordered_stops_for_lines(lines, load.get("origin_plant"), zip_coords)
    ordered_stops = app_module._apply_load_route_direction(ordered_stops, load=load)
    stop_sequence_map = app_module._stop_sequence_map_from_ordered_stops(ordered_stops)
    expected_colors = app_module._build_order_colors_for_lines(
        lines,
        stop_sequence_map=stop_sequence_map,
        stop_palette=palette,
    )

    assert payload is not None
    assert edit_payload is not None
    assert payload["order_colors"] == expected_colors
    assert edit_payload["order_colors"] == expected_colors

    unit_stop_by_order = {}
    for unit in edit_payload.get("units") or []:
        order_id = unit.get("order_id")
        if order_id not in unit_stop_by_order:
            unit_stop_by_order[order_id] = unit.get("stop_sequence")
    assert unit_stop_by_order == {
        "SO-1": 3,
        "SO-2": 2,
        "SO-3": 1,
    }


def test_order_colors_after_manual_addition_follow_stop_sequence():
    ordered_stops = [
        {"state": "VA", "zip": "11111"},
        {"state": "MD", "zip": "22222"},
        {"state": "NY", "zip": "33333"},
    ]
    stop_sequence_map = app_module._stop_sequence_map_from_ordered_stops(ordered_stops)
    palette = ["#00AA00", "#0088FF", "#FF9900", "#FFFFFF"]
    lines = [
        {"so_num": "SO-1", "state": "VA", "zip": "11111"},
        {"so_num": "SO-2", "state": "MD", "zip": "22222"},
        # Represents a manually added order to an existing stop.
        {"so_num": "SO-NEW", "state": "MD", "zip": "22222"},
        {"so_num": "SO-3", "state": "NY", "zip": "33333"},
    ]

    order_colors = app_module._build_order_colors_for_lines(
        lines,
        stop_sequence_map=stop_sequence_map,
        stop_palette=palette,
    )

    assert order_colors["SO-1"] == app_module._color_for_stop_sequence(1, palette)
    assert order_colors["SO-2"] == app_module._color_for_stop_sequence(2, palette)
    assert order_colors["SO-NEW"] == app_module._color_for_stop_sequence(2, palette)
    assert order_colors["SO-3"] == app_module._color_for_stop_sequence(3, palette)
