import io
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module


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


if __name__ == "__main__":
    unittest.main()
