import io
import os
from datetime import date

from openpyxl import load_workbook

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module
import db


def _set_authenticated_session(client):
    profiles = db.list_access_profiles()
    assert profiles
    with client.session_transaction() as session_state:
        session_state[app_module.SESSION_PROFILE_ID_KEY] = profiles[0]["id"]


def test_dashboard_export_xlsx_uses_scope_and_includes_requested_columns(monkeypatch):
    client = app_module.app.test_client()
    _set_authenticated_session(client)

    export_scope = {
        "allowed_plants": ["GA", "TX"],
        "plant_filters": ["GA"],
        "plant_scope": ["GA"],
        "period": "last_30_days",
        "period_label": "Last 30 Days",
        "date_range_label": "Apr 13, 2026 - May 12, 2026",
        "plant_scope_label": "Lavonia",
        "start_date": date(2026, 4, 13),
        "end_date": date(2026, 5, 12),
    }

    captured = {}

    def fake_scope():
        return export_scope

    def fake_list_loads(plant_scope, start_date, end_date, approved_statuses=None):
        captured["plant_scope"] = list(plant_scope)
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        captured["approved_statuses"] = tuple(approved_statuses or ())
        return [{"id": 501}]

    def fake_build_rows(_loads):
        return [
            {
                "id": 501,
                "load_number": "GA26-1000",
                "display_load_id": "L#01",
                "created_at": "2026-05-10T12:00:00",
                "ship_date": "2026-05-12",
                "status": "APPROVED",
                "origin_plant": "GA",
                "trailer_type": "STEP_DECK",
                "order_numbers": ["12610001", "12610002"],
                "lines": [
                    {
                        "so_num": "12610001",
                        "sku": "4x6GW",
                        "qty": 2,
                        "item": "TRAILER-001",
                        "item_desc": "4x6 utility trailer",
                        "due_date": "2026-05-14",
                        "utilization_pct": 12.3,
                        "total_length_ft": 8.0,
                        "unit_length_ft": 4.0,
                    }
                ],
                "total_units": 2.0,
                "display_utilization_pct": 86.8,
                "utilization_pct": 86.8,
                "schematic_grade": "B",
                "schematic": {
                    "utilization_credit_ft": 46.0,
                    "total_linear_feet": 48.0,
                    "capacity_feet": 53.0,
                },
                "stop_count": 2,
                "estimated_miles": 520.0,
                "estimated_cost": 2100.0,
                "customers": ["ACME Trailer"],
                "orders": [
                    {
                        "so_num": "12610001",
                        "stop_order_display": "01",
                        "due_date": "2026-05-14",
                        "destination_label": "Atlanta, GA",
                        "cust_name": "ACME Trailer",
                    }
                ],
                "schematic_warnings": [],
            }
        ]

    monkeypatch.setattr(app_module, "_resolve_dashboard_scope_from_request", fake_scope)
    monkeypatch.setattr(app_module, "_list_dashboard_scoped_loads", fake_list_loads)
    monkeypatch.setattr(app_module, "_build_load_report_rows", fake_build_rows)

    response = client.get("/dashboard/export.xlsx?period=last_30_days&plants=GA")

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    workbook = load_workbook(io.BytesIO(response.data))
    summary = workbook["Load Summary"]
    details = workbook["Load Lines"]
    filters_sheet = workbook["Filters Applied"]

    assert summary["A1"].value == "Load Number"
    assert summary["H1"].value == "SO Numbers"
    assert summary["J1"].value == "SKU Mix"
    assert summary["L1"].value == "Utilized Feet"
    assert summary["P1"].value == "Utilization Grade"

    assert summary["A2"].value == "GA26-1000"
    assert summary["H2"].value == "12610001, 12610002"
    assert "4x6GW (x2)" in str(summary["J2"].value or "")
    assert abs(float(summary["L2"].value) - 46.0) < 1e-6
    assert summary["P2"].value == "B"

    assert details["A1"].value == "Load Number"
    assert details["H1"].value == "SKU"
    assert details["A2"].value == "GA26-1000"
    assert details["H2"].value == "4x6GW"

    assert filters_sheet["A2"].value == "Period"
    assert filters_sheet["B2"].value == "Last 30 Days"
    assert filters_sheet["A4"].value == "Plant Scope"
    assert filters_sheet["B4"].value == "Lavonia"

    assert captured["plant_scope"] == ["GA"]
    assert captured["start_date"] == date(2026, 4, 13)
    assert captured["end_date"] == date(2026, 5, 12)
    assert captured["approved_statuses"] == ("APPROVED",)
