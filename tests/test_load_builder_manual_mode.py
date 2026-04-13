from werkzeug.datastructures import MultiDict

from services import load_builder


def test_manual_mode_missing_time_window_uses_default_without_time_window_error():
    form = MultiDict(
        {
            "opt_toggles": "1",
            "origin_plant": "",
            "capacity_feet": "48",
            "trailer_type": "STEP_DECK_48",
            "max_detour_pct": "15",
            "geo_radius": "100",
            "enforce_time_window": "1",
            "optimize_mode": "manual",
            "manual_order_input": "12606113\n12611231",
        }
    )

    result = load_builder.build_loads(form)

    assert "origin_plant" in result["errors"]
    assert "time_window_days" not in result["errors"]
    assert result["form_data"]["time_window_days"] == str(
        load_builder.DEFAULT_BUILD_PARAMS.get("time_window_days", "7")
    )
