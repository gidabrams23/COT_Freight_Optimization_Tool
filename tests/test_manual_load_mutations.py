import os

import db

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module


def _set_authenticated_session(client):
    profiles = db.list_access_profiles()
    assert profiles
    with client.session_transaction() as session_state:
        session_state[app_module.SESSION_PROFILE_ID_KEY] = profiles[0]["id"]


def test_manual_add_orders_removes_selected_orders_from_other_loads(monkeypatch):
    client = app_module.app.test_client()
    _set_authenticated_session(client)

    load = {
        "id": 10,
        "origin_plant": "ATL",
        "planning_session_id": 77,
        "status": "DRAFT",
    }
    called = {
        "strip": None,
        "build_source": None,
        "created_lines": [],
    }

    monkeypatch.setattr(app_module.db, "get_load", lambda load_id: load if load_id == 10 else None)
    monkeypatch.setattr(app_module, "_load_access_failure_reason", lambda _load: None)
    monkeypatch.setattr(app_module.db, "list_load_lines", lambda _load_id: [{"so_num": "EXISTING"}])
    monkeypatch.setattr(app_module.db, "filter_eligible_manual_so_nums", lambda _plant, _nums: {"SO-2"})
    monkeypatch.setattr(
        app_module.db,
        "remove_orders_from_unapproved_loads",
        lambda plant, so_nums, session_id=None, exclude_load_id=None: called.update(
            {
                "strip": {
                    "plant": plant,
                    "so_nums": list(so_nums),
                    "session_id": session_id,
                    "exclude_load_id": exclude_load_id,
                }
            }
        ),
    )
    monkeypatch.setattr(
        app_module.db,
        "list_order_lines_for_so_nums",
        lambda _plant, _nums: [{"id": 501, "total_length_ft": 12.0}],
    )
    monkeypatch.setattr(
        app_module.db,
        "update_load_build_source",
        lambda load_id, source: called.update({"build_source": (load_id, source)}),
    )
    monkeypatch.setattr(
        app_module.db,
        "create_load_line",
        lambda load_id, order_line_id, total_ft: called["created_lines"].append(
            (load_id, order_line_id, total_ft)
        ),
    )
    monkeypatch.setattr(app_module.db, "delete_load_schematic_override", lambda _load_id: None)
    monkeypatch.setattr(app_module, "_start_reopt_job", lambda *_args, **_kwargs: "job-1")

    response = client.post("/loads/10/manual_add", data={"so_nums": ["SO-2"]})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["reopt_job_id"] == "job-1"
    assert called["strip"] == {
        "plant": "ATL",
        "so_nums": ["SO-2"],
        "session_id": 77,
        "exclude_load_id": 10,
    }
    assert called["build_source"] == (10, "MANUAL")
    assert called["created_lines"] == [(10, 501, 12.0)]


def test_remove_order_find_next_best_marks_load_manual_and_reoptimizes(monkeypatch):
    client = app_module.app.test_client()
    _set_authenticated_session(client)

    load = {
        "id": 22,
        "origin_plant": "ATL",
        "planning_session_id": 8,
        "status": "DRAFT",
        "load_number": "ATL26-1001",
    }
    calls = {"set_excluded": 0, "build_source": None, "reopt": None}
    count_values = iter([2, 1])

    monkeypatch.setattr(app_module.db, "get_load", lambda load_id: load if load_id == 22 else None)
    monkeypatch.setattr(app_module, "_load_access_failure_reason", lambda _load: None)
    monkeypatch.setattr(
        app_module.db,
        "list_load_lines",
        lambda _load_id: [{"so_num": "SO-9", "qty": 2, "due_date": "2026-03-10"}],
    )
    monkeypatch.setattr(app_module.db, "add_load_feedback", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        app_module.db,
        "count_load_lines",
        lambda _load_id: next(count_values),
    )
    monkeypatch.setattr(
        app_module.db,
        "update_load_build_source",
        lambda load_id, source: calls.update({"build_source": (load_id, source)}),
    )
    monkeypatch.setattr(app_module.db, "remove_order_from_load", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app_module.db, "delete_load", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        app_module.db,
        "set_order_excluded_by_so_num",
        lambda *_args, **_kwargs: calls.update({"set_excluded": calls["set_excluded"] + 1}),
    )
    monkeypatch.setattr(
        app_module,
        "_reoptimize_for_plant",
        lambda plant_code, session_id=None: calls.update({"reopt": (plant_code, session_id)}),
    )

    response = client.post(
        "/loads/22/remove_order",
        data={
            "order_id": "SO-9",
            "reason_category": "Route mismatch",
            "details": "This order must move to a different load.",
            "removal_action": "find_next_best",
        },
    )

    assert response.status_code in {301, 302}
    assert calls["build_source"] == (22, "MANUAL")
    assert calls["set_excluded"] == 0
    assert calls["reopt"] == ("ATL", 8)


def test_remove_order_return_to_pool_excludes_order_before_reopt(monkeypatch):
    client = app_module.app.test_client()
    _set_authenticated_session(client)

    load = {
        "id": 23,
        "origin_plant": "ATL",
        "planning_session_id": 9,
        "status": "DRAFT",
        "load_number": "ATL26-1002",
    }
    calls = {"excluded_args": None, "reopt": None}
    count_values = iter([1, 0])

    monkeypatch.setattr(app_module.db, "get_load", lambda load_id: load if load_id == 23 else None)
    monkeypatch.setattr(app_module, "_load_access_failure_reason", lambda _load: None)
    monkeypatch.setattr(
        app_module.db,
        "list_load_lines",
        lambda _load_id: [{"so_num": "SO-88", "qty": 1, "due_date": "2026-03-10"}],
    )
    monkeypatch.setattr(app_module.db, "add_load_feedback", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app_module.db, "count_load_lines", lambda _load_id: next(count_values))
    monkeypatch.setattr(app_module.db, "update_load_build_source", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app_module.db, "remove_order_from_load", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app_module.db, "delete_load", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        app_module.db,
        "set_order_excluded_by_so_num",
        lambda plant_code, so_num, is_excluded: calls.update(
            {"excluded_args": (plant_code, so_num, is_excluded)}
        ),
    )
    monkeypatch.setattr(
        app_module,
        "_reoptimize_for_plant",
        lambda plant_code, session_id=None: calls.update({"reopt": (plant_code, session_id)}),
    )

    response = client.post(
        "/loads/23/remove_order",
        data={
            "order_id": "SO-88",
            "reason_category": "Hold for later wave",
            "details": "Hold this order out of the current planning session.",
            "removal_action": "return_to_pool",
        },
    )

    assert response.status_code in {301, 302}
    assert calls["excluded_args"] == ("ATL", "SO-88", True)
    assert calls["reopt"] == ("ATL", 9)


def test_manual_add_suggestions_reports_stack_aware_fit(monkeypatch):
    client = app_module.app.test_client()
    _set_authenticated_session(client)

    load = {
        "id": 30,
        "origin_plant": "ATL",
        "planning_session_id": 11,
        "status": "DRAFT",
        "trailer_type": "STEP_DECK",
    }

    existing_line = {
        "so_num": "SO-BASE",
        "item": "BASE",
        "item_desc": "Base item",
        "qty": 1,
        "sku": "SKU-BASE",
        "unit_length_ft": 10.0,
        "total_length_ft": 10.0,
        "state": "TX",
        "zip": "75001",
    }
    candidate_line = {
        "so_num": "SO-NEW",
        "item": "NEW",
        "item_desc": "New item",
        "qty": 1,
        "sku": "SKU-NEW",
        "unit_length_ft": 6.0,
        "total_length_ft": 6.0,
        "state": "TX",
        "zip": "75001",
    }

    monkeypatch.setattr(app_module.db, "get_load", lambda load_id: load if load_id == 30 else None)
    monkeypatch.setattr(app_module, "_load_access_failure_reason", lambda _load: None)
    monkeypatch.setattr(app_module.db, "list_load_lines", lambda _load_id: [existing_line])
    monkeypatch.setattr(
        app_module.db,
        "list_orders_by_so_nums",
        lambda _plant, _so_nums: [{"so_num": "SO-BASE", "due_date": "2026-05-14", "total_length_ft": 50.0}],
    )
    monkeypatch.setattr(
        app_module,
        "_build_load_schematic_payload",
        lambda _load_id: {
            "schematic": {
                "capacity_feet": 53.0,
                "total_linear_feet": 50.0,
                "lower_deck_used_length_ft": 50.0,
                "upper_deck_effective_length_ft": 0.0,
                "positions": [],
            }
        },
    )
    monkeypatch.setattr(
        app_module.db,
        "list_eligible_manual_orders",
        lambda _plant, search=None, limit=None: [
            {
                "so_num": "SO-NEW",
                "cust_name": "Stack Fit Customer",
                "due_date": "2026-05-15",
                "city": "Dallas",
                "state": "TX",
                "zip": "75001",
                "total_length_ft": 6.0,
                "utilization_pct": 11.0,
            }
        ],
    )
    monkeypatch.setattr(app_module, "_get_effective_planning_setting", lambda _key: {"value_text": ""})
    monkeypatch.setattr(app_module, "_parse_strategic_customers", lambda _value: [])
    monkeypatch.setattr(
        app_module.db,
        "list_order_lines_for_so_nums",
        lambda _plant, _so_nums: [existing_line, candidate_line],
    )
    monkeypatch.setattr(app_module.db, "list_sku_specs", lambda: [])
    monkeypatch.setattr(app_module.geo_utils, "load_zip_coordinates", lambda: {})
    monkeypatch.setattr(app_module, "_ordered_stops_for_lines", lambda _lines, _plant, _coords: [])
    monkeypatch.setattr(app_module, "_apply_route_stop_order", lambda ordered_stops, load=None, stop_order=None: ordered_stops)
    monkeypatch.setattr(
        app_module,
        "_apply_load_route_direction",
        lambda ordered_stops, load=None, reverse_route=None: ordered_stops,
    )
    monkeypatch.setattr(
        app_module,
        "_calculate_load_schematic",
        lambda *_args, **_kwargs: (
            {
                "exceeds_capacity": False,
                "lower_deck_used_length_ft": 52.0,
                "upper_deck_effective_length_ft": 0.0,
                "total_linear_feet": 52.0,
            },
            [],
            {"SO-BASE", "SO-NEW"},
        ),
    )
    monkeypatch.setattr(app_module.stack_calculator, "capacity_overflow_feet", lambda _schematic: 0.0)

    response = client.get("/loads/30/manual_add/suggestions")

    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload["suggestions"]) == 1
    suggestion = payload["suggestions"][0]
    assert suggestion["so_num"] == "SO-NEW"
    assert suggestion["stack_added_ft"] == 2.0
    assert suggestion["fit_assessment"]["available"] is True
    assert suggestion["fit_assessment"]["fits_in_capacity"] is True
    assert suggestion["fit_assessment"]["over_capacity_by_ft"] == 0.0


def test_manual_add_suggestions_reports_stack_overflow_amount(monkeypatch):
    client = app_module.app.test_client()
    _set_authenticated_session(client)

    load = {
        "id": 31,
        "origin_plant": "ATL",
        "planning_session_id": 12,
        "status": "DRAFT",
        "trailer_type": "STEP_DECK",
    }
    line = {
        "so_num": "SO-BASE",
        "item": "BASE",
        "item_desc": "Base item",
        "qty": 1,
        "sku": "SKU-BASE",
        "unit_length_ft": 10.0,
        "total_length_ft": 10.0,
        "state": "TX",
        "zip": "75001",
    }
    candidate = {
        "so_num": "SO-OVER",
        "item": "OVER",
        "item_desc": "Overflow item",
        "qty": 1,
        "sku": "SKU-OVER",
        "unit_length_ft": 8.0,
        "total_length_ft": 8.0,
        "state": "TX",
        "zip": "75001",
    }

    monkeypatch.setattr(app_module.db, "get_load", lambda load_id: load if load_id == 31 else None)
    monkeypatch.setattr(app_module, "_load_access_failure_reason", lambda _load: None)
    monkeypatch.setattr(app_module.db, "list_load_lines", lambda _load_id: [line])
    monkeypatch.setattr(
        app_module.db,
        "list_orders_by_so_nums",
        lambda _plant, _so_nums: [{"so_num": "SO-BASE", "due_date": "2026-05-14", "total_length_ft": 50.0}],
    )
    monkeypatch.setattr(
        app_module,
        "_build_load_schematic_payload",
        lambda _load_id: {
            "schematic": {
                "capacity_feet": 53.0,
                "total_linear_feet": 50.0,
                "lower_deck_used_length_ft": 50.0,
                "upper_deck_effective_length_ft": 0.0,
                "positions": [],
            }
        },
    )
    monkeypatch.setattr(
        app_module.db,
        "list_eligible_manual_orders",
        lambda _plant, search=None, limit=None: [
            {
                "so_num": "SO-OVER",
                "cust_name": "Overflow Customer",
                "due_date": "2026-05-16",
                "city": "Dallas",
                "state": "TX",
                "zip": "75001",
                "total_length_ft": 8.0,
                "utilization_pct": 15.0,
            }
        ],
    )
    monkeypatch.setattr(app_module, "_get_effective_planning_setting", lambda _key: {"value_text": ""})
    monkeypatch.setattr(app_module, "_parse_strategic_customers", lambda _value: [])
    monkeypatch.setattr(app_module.db, "list_order_lines_for_so_nums", lambda _plant, _so_nums: [line, candidate])
    monkeypatch.setattr(app_module.db, "list_sku_specs", lambda: [])
    monkeypatch.setattr(app_module.geo_utils, "load_zip_coordinates", lambda: {})
    monkeypatch.setattr(app_module, "_ordered_stops_for_lines", lambda _lines, _plant, _coords: [])
    monkeypatch.setattr(app_module, "_apply_route_stop_order", lambda ordered_stops, load=None, stop_order=None: ordered_stops)
    monkeypatch.setattr(
        app_module,
        "_apply_load_route_direction",
        lambda ordered_stops, load=None, reverse_route=None: ordered_stops,
    )
    monkeypatch.setattr(
        app_module,
        "_calculate_load_schematic",
        lambda *_args, **_kwargs: (
            {
                "exceeds_capacity": True,
                "lower_deck_used_length_ft": 55.0,
                "upper_deck_effective_length_ft": 0.0,
                "total_linear_feet": 55.0,
            },
            [],
            {"SO-BASE", "SO-OVER"},
        ),
    )
    monkeypatch.setattr(app_module.stack_calculator, "capacity_overflow_feet", lambda _schematic: 2.3)

    response = client.get("/loads/31/manual_add/suggestions")

    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload["suggestions"]) == 1
    suggestion = payload["suggestions"][0]
    assert suggestion["so_num"] == "SO-OVER"
    assert suggestion["fit_assessment"]["available"] is True
    assert suggestion["fit_assessment"]["fits_in_capacity"] is False
    assert suggestion["fit_assessment"]["over_capacity_by_ft"] == 2.3
