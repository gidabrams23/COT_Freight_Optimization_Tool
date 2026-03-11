import io
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module
import db


def _set_authenticated_session(client):
    profiles = db.list_access_profiles()
    assert profiles
    with client.session_transaction() as session_state:
        session_state[app_module.SESSION_PROFILE_ID_KEY] = profiles[0]["id"]


class OrdersLoadReportSnapshotTests(unittest.TestCase):
    def test_build_orders_snapshot_uses_load_report_assignments(self):
        orders = [
            {"so_num": "1001", "due_date": "2026-01-01", "is_excluded": 0},
            {"so_num": "1002", "due_date": "2026-01-10", "is_excluded": 0},
            {"so_num": "1003", "due_date": "2026-02-10", "is_excluded": 0},
            {"so_num": "1004", "due_date": "2026-01-05", "is_excluded": 1},
        ]

        snapshot = app_module._build_orders_snapshot(
            orders,
            today=app_module.date(2026, 1, 15),
            load_assignment_map={"1002": "GA26-1001"},
        )

        self.assertEqual(snapshot["total"], 3)
        self.assertEqual(snapshot["on_load"], 1)
        self.assertEqual(snapshot["unassigned"], 2)
        self.assertEqual(snapshot["past_due"], 1)
        self.assertEqual(snapshot["due_next_14"], 0)
        self.assertEqual(snapshot["due_14_plus"], 1)
        self.assertEqual(snapshot["timeline_total"], 2)

    def test_handle_load_report_upload_tracks_duplicates_and_conflicts(self):
        fake_rows = [
            {"order_number": "SO-1", "load_number": "GA26-1001"},
            {"order_number": "SO-1", "load_number": "GA26-1001"},
            {"order_number": "SO-2", "load_number": "GA26-1002"},
            {"order_number": "SO-2", "load_number": "GA26-1003"},
            {"order_number": "SO-3", "load_number": "GA26-1004"},
        ]
        fake_file = SimpleNamespace(filename="loads-report.xlsx")

        with patch.object(
            app_module.replay_evaluator,
            "parse_report",
            return_value={"rows": fake_rows, "issues": []},
        ), patch.object(
            app_module.db,
            "list_orders_by_so_nums_any",
            return_value=[{"so_num": "SO-1"}, {"so_num": "SO-3"}],
        ), patch.object(
            app_module.db,
            "add_load_report_upload",
            return_value=42,
        ) as add_upload, patch.object(
            app_module.db,
            "replace_latest_load_report_assignments",
        ) as replace_assignments:
            summary = app_module._handle_load_report_upload(fake_file)

        self.assertEqual(summary["unique_orders"], 3)
        self.assertEqual(summary["duplicate_rows"], 2)
        self.assertEqual(summary["conflicting_orders"], 1)
        self.assertEqual(summary["matched_open_orders"], 2)
        self.assertEqual(summary["unmatched_open_orders"], 1)
        self.assertEqual(summary["upload_id"], 42)

        add_upload.assert_called_once()
        replace_assignments.assert_called_once()
        args = replace_assignments.call_args[0]
        self.assertEqual(args[0], 42)
        self.assertEqual(len(args[1]), 3)

    def test_parse_load_report_rows_handles_trailing_unclosed_quote(self):
        csv_body = (
            "Load Number,Date Created,Name\n"
            "GA26-2306,03/06/2026 07:00am,12607204\n"
            "VA26-1499,03/06/2026 07:29am,\"12608040\n"
        )
        fake_file = SimpleNamespace(
            filename="report.csv",
            stream=io.BytesIO(csv_body.encode("utf-8")),
        )

        rows = app_module._parse_load_report_rows(fake_file)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["load_number"], "GA26-2306")
        self.assertEqual(rows[0]["order_number"], "12607204")
        self.assertEqual(rows[1]["load_number"], "VA26-1499")
        self.assertEqual(rows[1]["order_number"], "12608040")

    def test_build_load_assignments_from_order_lines_presence_based(self):
        payload = app_module._build_load_assignments_from_order_lines(
            [
                {"so_num": "SO-1", "load_num": "GA26-1001"},
                {"so_num": "SO-1", "load_num": "GA26-1002"},
                {"so_num": "SO-2", "load_num": "Not On Load"},
                {"so_num": "SO-3", "load_num": "#N/A"},
                {"so_num": "SO-4", "load_num": "GA26-2001"},
                {"so_num": "SO-4", "load_num": "GA26-2001"},
            ]
        )

        assignments = {
            entry["so_num"]: entry["load_number"]
            for entry in (payload.get("assignments") or [])
        }
        self.assertEqual(assignments, {"SO-1": "GA26-1001", "SO-4": "GA26-2001"})
        self.assertEqual(payload["unique_orders"], 2)
        self.assertEqual(payload["unique_loads"], 2)
        self.assertEqual(payload["assignment_rows"], 4)
        self.assertEqual(payload["duplicate_rows"], 2)
        self.assertEqual(payload["conflicting_orders"], 1)

    def test_handle_order_upload_refreshes_assignments_from_order_file(self):
        parse_summary = {
            "orders": [
                {"so_num": "SO-1", "plant": "GA"},
                {"so_num": "SO-2", "plant": "GA"},
                {"so_num": "SO-3", "plant": "GA"},
            ],
            "order_lines": [
                {"so_num": "SO-1", "load_num": "GA26-1001"},
                {"so_num": "SO-2", "load_num": "Not On Load"},
                {"so_num": "SO-3", "load_num": "GA26-2001"},
                {"so_num": "SO-3", "load_num": "GA26-2002"},
            ],
            "unmapped_items": [],
            "total_rows": 4,
            "mapping_rate": 100.0,
        }
        fake_file = SimpleNamespace(filename="orders.csv")
        fake_importer = SimpleNamespace(parse_csv=lambda _stream: parse_summary)

        with patch.object(app_module, "OrderImporter", return_value=fake_importer), patch.object(
            app_module.db,
            "list_orders_by_so_nums_any",
            return_value=[],
        ), patch.object(
            app_module.db,
            "upsert_order_lines",
        ), patch.object(
            app_module.db,
            "upsert_orders",
        ), patch.object(
            app_module.db,
            "mark_orders_seen",
        ), patch.object(
            app_module.db,
            "list_open_order_so_nums",
            return_value=[],
        ), patch.object(
            app_module.db,
            "mark_orders_closed",
        ), patch.object(
            app_module.db,
            "purge_closed_orders",
        ), patch.object(
            app_module.db,
            "add_upload_history",
            return_value=101,
        ), patch.object(
            app_module.db,
            "add_upload_order_changes",
        ), patch.object(
            app_module.db,
            "update_orders_upload_meta",
        ), patch.object(
            app_module.db,
            "add_upload_unmapped_items",
        ), patch.object(
            app_module.db,
            "add_load_report_upload",
            return_value=202,
        ) as add_load_upload, patch.object(
            app_module.db,
            "replace_latest_load_report_assignments",
        ) as replace_assignments:
            summary = app_module._handle_order_upload(fake_file)

        self.assertEqual(summary["load_assignment_upload_id"], 202)
        self.assertEqual(summary["load_assignment_summary"]["unique_orders"], 2)
        self.assertEqual(summary["load_assignment_summary"]["conflicting_orders"], 1)
        self.assertEqual(summary["load_assignment_summary"]["matched_open_orders"], 2)
        self.assertEqual(summary["load_assignment_summary"]["unmatched_open_orders"], 0)

        add_load_upload.assert_called_once()
        replace_assignments.assert_called_once()
        call_args = replace_assignments.call_args[0]
        self.assertEqual(call_args[0], 202)
        assignment_pairs = {
            (entry.get("so_num"), entry.get("load_number")) for entry in call_args[1]
        }
        self.assertEqual(
            assignment_pairs,
            {("SO-1", "GA26-1001"), ("SO-3", "GA26-2001")},
        )

    def test_orders_load_report_upload_route_redirects_to_single_file_notice(self):
        client = app_module.app.test_client()
        _set_authenticated_session(client)

        response = client.post("/orders/load-report/upload")
        self.assertEqual(response.status_code, 302)
        location = response.headers.get("Location") or ""
        self.assertIn("/orders", location)
        self.assertIn("intake_notice=load-report-deprecated", location)


if __name__ == "__main__":
    unittest.main()
