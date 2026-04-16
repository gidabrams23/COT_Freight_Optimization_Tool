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

    def _build_inventory_csv_report(self):
        csv_path = Path(self._tmpdir.name) / "bt_inventory.csv"
        csv_path.write_text(
            "\n".join(
                [
                    "conum,itemnum,serid,whse,transtype,cost,physdate,memo,onhand,committed_,wip,intransit,discrepant,lastusedate,TS_LastUpdated,description1,majorcat,majorcatdesc",
                    "001,70BT14,SER-001,501,4,100,1/1/1900 0:00,,1,0,0,0,0,1/1/1900 0:00,4/8/2026 2:34,desc,060,UTILITY",
                    "001,70BT14,SER-001,501,4,100,1/1/1900 0:00,,1,0,0,0,0,1/1/1900 0:00,4/8/2026 2:34,desc,060,UTILITY",
                    "001,70BT14,SER-002,501,4,100,1/1/1900 0:00,,1,1,0,0,0,1/1/1900 0:00,4/8/2026 2:34,desc,060,UTILITY",
                    "001,22PH20,SER-003,601,4,100,1/1/1900 0:00,,1,0,0,0,0,1/1/1900 0:00,4/8/2026 2:34,desc,060,EQUIPMENT",
                    "001,UNMAPPED01,SER-004,601,4,100,1/1/1900 0:00,,1,0,0,0,0,1/1/1900 0:00,4/8/2026 2:34,desc,060,UNKNOWN",
                ]
            ),
            encoding="utf-8",
        )
        return csv_path

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
        self.assertEqual(upload_meta["source_format"], "workbook")
        self.assertEqual(upload_meta["sheet_name"], "All.Orders.Quick")
        self.assertEqual(upload_meta["valid_rows"], 5)
        self.assertEqual(upload_meta["distinct_items"], 3)

    def test_import_inventory_csv_dedupes_serial_rows_and_tracks_warehouse(self):
        csv_path = self._build_inventory_csv_report()

        result = self.db.import_bigtex_inventory_orders_workbook(workbook_path=csv_path)

        self.assertEqual(result["source_format"], "csv_inventory")
        self.assertEqual(result["processed_rows"], 5)
        self.assertEqual(result["valid_rows"], 5)
        self.assertEqual(result["deduped_rows"], 4)
        self.assertEqual(result["duplicate_rows"], 1)
        self.assertEqual(result["distinct_items"], 3)
        self.assertEqual(result["warehouse_count"], 2)
        self.assertEqual(result["available_total"], 3)
        self.assertEqual(result["unmatched_item_count"], 1)
        self.assertIn("UNMAPPED01", result["unmatched_items"])

        snapshot_rows = [dict(r) for r in self.db.get_bt_inventory_snapshot_rows(limit=20)]
        by_item = {row["item_number"]: row for row in snapshot_rows}
        self.assertEqual(by_item["70BT14"]["total_count"], 2)
        self.assertEqual(by_item["70BT14"]["assigned_count"], 1)
        self.assertEqual(by_item["70BT14"]["available_count"], 1)
        self.assertEqual(by_item["22PH20"]["available_count"], 1)
        self.assertEqual(by_item["UNMAPPED01"]["available_count"], 1)

        whse_codes = self.db.get_bt_inventory_whse_codes()
        self.assertEqual(whse_codes, ["501", "601"])

        snapshot_501 = [dict(r) for r in self.db.get_bt_inventory_snapshot_rows(limit=20, whse_code="501")]
        self.assertEqual(len(snapshot_501), 1)
        self.assertEqual(snapshot_501[0]["item_number"], "70BT14")
        self.assertEqual(snapshot_501[0]["available_count"], 1)

        upload_meta = dict(self.db.get_bt_inventory_upload_meta())
        self.assertEqual(upload_meta["source_format"], "csv_inventory")
        self.assertEqual(upload_meta["deduped_rows"], 4)
        self.assertEqual(upload_meta["duplicate_rows"], 1)
        self.assertEqual(upload_meta["warehouse_count"], 2)


if __name__ == "__main__":
    unittest.main()
