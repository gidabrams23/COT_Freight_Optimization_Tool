import importlib
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook


class ProgradeInventoryUploadTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "prograde_test.db"
        self._previous_db_path = os.environ.get("PROGRADE_DB_PATH")
        os.environ["PROGRADE_DB_PATH"] = str(self._db_path)

        import blueprints.prograde.db as prograde_db

        self.db = importlib.reload(prograde_db)
        self.db.init_db()

        now = datetime.utcnow().isoformat()
        with self.db.get_db() as conn:
            conn.executemany(
                """
                INSERT INTO bigtex_skus
                (item_number, mcat, model, total_footprint, updated_at)
                VALUES (?,?,?,?,?)
                """,
                [
                    ("70BT14", "utility", "70BT", 18.5, now),
                    ("22PH20", "equipment", "22PH", 24.0, now),
                ],
            )

    def tearDown(self):
        if self._previous_db_path is None:
            os.environ.pop("PROGRADE_DB_PATH", None)
        else:
            os.environ["PROGRADE_DB_PATH"] = self._previous_db_path
        try:
            self._tmpdir.cleanup()
        except PermissionError:
            # Windows can transiently hold SQLite WAL files open at teardown.
            pass

    def _build_orders_workbook(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "All.Orders.Quick"
        headers = [""] * 18
        headers[2] = "Name"
        headers[12] = "Item #"
        headers[17] = "Days Old"
        sheet.append(headers)

        def make_row(name, item_number, days_old):
            row = [""] * 18
            row[2] = name
            row[12] = item_number
            row[17] = days_old
            return row

        sheet.append(make_row("", "70BT14", 21))
        sheet.append(make_row("Assigned Customer", "70BT14", 4))
        sheet.append(make_row("", "70BT14", ""))
        sheet.append(make_row("", "22PH20", None))
        sheet.append(make_row("", "UNMAPPED01", 2))

        workbook_path = Path(self._tmpdir.name) / "orders.xlsx"
        workbook.save(workbook_path)
        return workbook_path

    def test_import_orders_workbook_aggregates_inventory_statuses(self):
        workbook_path = self._build_orders_workbook()

        result = self.db.import_bigtex_inventory_orders_workbook(
            workbook_path=workbook_path,
            sheet_name="All.Orders.Quick",
        )

        self.assertEqual(result["valid_rows"], 5)
        self.assertEqual(result["distinct_items"], 3)
        self.assertEqual(result["available_total"], 4)
        self.assertEqual(result["built_total"], 3)
        self.assertEqual(result["future_build_total"], 2)
        self.assertEqual(result["unmatched_item_count"], 1)
        self.assertIn("UNMAPPED01", result["unmatched_items"])

        snapshot_rows = [dict(r) for r in self.db.get_bt_inventory_snapshot_rows(limit=20)]
        by_item = {row["item_number"]: row for row in snapshot_rows}

        item_70bt = by_item["70BT14"]
        self.assertEqual(item_70bt["total_count"], 3)
        self.assertEqual(item_70bt["available_count"], 2)
        self.assertEqual(item_70bt["assigned_count"], 1)
        self.assertEqual(item_70bt["built_count"], 2)
        self.assertEqual(item_70bt["future_build_count"], 1)
        self.assertEqual(item_70bt["available_built_count"], 1)
        self.assertEqual(item_70bt["available_future_count"], 1)
        self.assertEqual(item_70bt["sku_model"], "70BT")
        self.assertEqual(item_70bt["sku_mcat"], "utility")

        item_22ph = by_item["22PH20"]
        self.assertEqual(item_22ph["available_count"], 1)
        self.assertEqual(item_22ph["built_count"], 0)
        self.assertEqual(item_22ph["future_build_count"], 1)

        item_unmapped = by_item["UNMAPPED01"]
        self.assertIsNone(item_unmapped["sku_model"])
        self.assertIsNone(item_unmapped["sku_mcat"])

        upload_meta = dict(self.db.get_bt_inventory_upload_meta())
        self.assertEqual(upload_meta["sheet_name"], "All.Orders.Quick")
        self.assertEqual(upload_meta["valid_rows"], 5)
        self.assertEqual(upload_meta["distinct_items"], 3)


if __name__ == "__main__":
    unittest.main()
