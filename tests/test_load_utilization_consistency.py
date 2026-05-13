import os

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module


def test_sync_load_utilization_from_report_rows_updates_load_and_persists(monkeypatch):
    writes = []

    def fake_update(load_id, utilization_pct):
        writes.append((load_id, utilization_pct))

    monkeypatch.setattr(app_module.db, "update_load_utilization_pct", fake_update)

    loads = [{"id": 11, "utilization_pct": 70.0, "schematic": {}}]
    report_rows = [{"id": 11, "display_utilization_pct": 82.4, "schematic_grade": "B"}]

    app_module._sync_load_utilization_from_report_rows(loads, report_rows, persist=True)

    assert abs(float(loads[0]["utilization_pct"]) - 82.4) < 1e-6
    assert abs(float(loads[0]["display_utilization_pct"]) - 82.4) < 1e-6
    assert loads[0]["schematic"]["utilization_grade"] == "B"
    assert writes == [(11, 82.4)]


def test_sync_load_utilization_from_report_rows_skips_persist_when_unchanged(monkeypatch):
    writes = []

    def fake_update(load_id, utilization_pct):
        writes.append((load_id, utilization_pct))

    monkeypatch.setattr(app_module.db, "update_load_utilization_pct", fake_update)

    loads = [{"id": 12, "utilization_pct": 82.4, "schematic": {}}]
    report_rows = [{"id": 12, "display_utilization_pct": 82.4, "schematic_grade": "B"}]

    app_module._sync_load_utilization_from_report_rows(loads, report_rows, persist=True)

    assert writes == []
