import os
from datetime import date

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module
import db


def _set_authenticated_session(client):
    profiles = db.list_access_profiles()
    assert profiles
    with client.session_transaction() as session_state:
        session_state[app_module.SESSION_PROFILE_ID_KEY] = profiles[0]["id"]


def test_normalize_dashboard_granularity_defaults_to_week():
    assert app_module._normalize_dashboard_granularity("") == "week"
    assert app_module._normalize_dashboard_granularity("invalid") == "week"
    assert app_module._normalize_dashboard_granularity("day") == "day"
    assert app_module._normalize_dashboard_granularity("month") == "month"


def test_build_dashboard_summary_buckets_aggregates_weekly():
    rows = {
        "2026-05-01": {"load_count": 2, "utilization_sum": 170.0},
        "2026-05-02": {"load_count": 1, "utilization_sum": 80.0},
        "2026-05-05": {"load_count": 3, "utilization_sum": 252.0},
    }
    buckets = app_module._build_dashboard_summary_buckets(
        rows,
        date(2026, 5, 1),
        date(2026, 5, 10),
        "week",
    )
    assert len(buckets) == 2
    assert buckets[0]["label"] == "May 1-3"
    assert buckets[0]["load_count"] == 3
    assert round(float(buckets[0]["avg_utilization"]), 2) == 83.33
    assert buckets[1]["label"] == "May 4-10"
    assert buckets[1]["load_count"] == 3
    assert round(float(buckets[1]["avg_utilization"]), 1) == 84.0


def test_build_dashboard_summary_buckets_aggregates_monthly():
    rows = {
        "2026-04-29": {"load_count": 1, "utilization_sum": 88.0},
        "2026-04-30": {"load_count": 1, "utilization_sum": 92.0},
        "2026-05-01": {"load_count": 2, "utilization_sum": 170.0},
    }
    buckets = app_module._build_dashboard_summary_buckets(
        rows,
        date(2026, 4, 29),
        date(2026, 5, 2),
        "month",
    )
    assert len(buckets) == 2
    assert buckets[0]["label"] == "Apr 2026"
    assert buckets[0]["load_count"] == 2
    assert round(float(buckets[0]["avg_utilization"]), 1) == 90.0
    assert buckets[1]["label"] == "May 2026"
    assert buckets[1]["load_count"] == 2
    assert round(float(buckets[1]["avg_utilization"]), 1) == 85.0


def test_resolve_dashboard_scope_uses_requested_filters(monkeypatch):
    monkeypatch.setattr(app_module, "_get_allowed_plants", lambda: ["GA", "TX"])
    monkeypatch.setattr(
        app_module,
        "_resolve_plant_filters",
        lambda raw_value: ["GA"] if str(raw_value or "").strip() else [],
    )

    with app_module.app.test_request_context("/dashboard?period=last_30_days&plants=GA&granularity=day"):
        scope = app_module._resolve_dashboard_scope_from_request()

    assert scope["period"] == "last_30_days"
    assert scope["plant_scope"] == ["GA"]
    assert scope["plant_filters"] == ["GA"]
    assert scope["granularity"] == "day"
    assert (scope["end_date"] - scope["start_date"]).days == 29


def test_dashboard_render_removes_py_copy_and_keeps_granularity_state():
    client = app_module.app.test_client()
    _set_authenticated_session(client)

    response = client.get("/dashboard?period=last_30_days&granularity=month")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Loads &amp; Utilization Summary" in html
    assert "PY n/a" not in html
    assert "vs same period" not in html
    assert "Year over Year Impact" not in html
    assert "name=\"granularity\"" in html
    assert "value=\"month\"" in html
