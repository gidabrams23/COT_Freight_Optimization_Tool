import importlib
import os
import tempfile
import unittest
from pathlib import Path


class ProgradePickerCategoryTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "prograde_picker_categories_test.db"
        self._previous_db_path = os.environ.get("PROGRADE_DB_PATH")
        os.environ["PROGRADE_DB_PATH"] = str(self._db_path)

        import blueprints.prograde.db as prograde_db
        import blueprints.prograde.routes as prograde_routes

        self.db = importlib.reload(prograde_db)
        self.routes = importlib.reload(prograde_routes)
        self.db.init_db()

    def tearDown(self):
        if self._previous_db_path is None:
            os.environ.pop("PROGRADE_DB_PATH", None)
        else:
            os.environ["PROGRADE_DB_PATH"] = self._previous_db_path
        try:
            self._tmpdir.cleanup()
        except PermissionError:
            pass

    def test_pj_picker_collapses_detailed_categories_to_simple_groups(self):
        now = "2026-04-07T00:00:00"
        with self.db.get_db() as conn:
            conn.executemany(
                """
                INSERT INTO pj_skus
                (item_number, model, pj_category, description, total_footprint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    ("PJ-D5-1", "D5", "dump_small", "Dump small", 12.0, now),
                    ("PJ-DL-1", "DL", "dump_lowside", "Dump low side", 14.0, now),
                    ("PJ-U6-1", "U6", "utility", "Utility", 10.0, now),
                    ("U7210ABC", "U7", "utility", "Utility special code", 11.0, now),
                    ("C4215XYZ", "C4", "car_hauler", "C4 special code", 20.0, now),
                    ("C5214XYZ", "C5", "car_hauler", "C5 special code", 20.0, now),
                    ("PJ-CH-1", "CH", "car_hauler_deckover", "Car hauler", 16.0, now),
                    ("H7P38S2BTSK", "H7", "car_hauler", "H7 trailer", 43.5, now),
                    ("LDQ34A2BSSK", "LD", "deck_over", "LD trailer", 40.0, now),
                ],
            )

        picker_rows = {row["item_number"]: row for row in self.routes._build_pj_picker_skus()}

        self.assertEqual(picker_rows["PJ-D5-1"]["picker_category"], "dump")
        self.assertEqual(picker_rows["PJ-D5-1"]["picker_category_label"], "Dump")
        self.assertEqual(picker_rows["PJ-DL-1"]["picker_category"], "dump")
        self.assertEqual(picker_rows["PJ-DL-1"]["picker_category_label"], "Dump")
        self.assertEqual(picker_rows["PJ-U6-1"]["picker_category"], "utility")
        self.assertEqual(picker_rows["PJ-U6-1"]["picker_category_label"], "Utility")
        self.assertEqual(picker_rows["PJ-CH-1"]["picker_category"], "car_hauler")
        self.assertEqual(picker_rows["PJ-CH-1"]["picker_category_label"], "Car Hauler")
        self.assertEqual(picker_rows["PJ-CH-1"]["picker_model_code"], "CH")
        self.assertEqual(picker_rows["H7P38S2BTSK"]["picker_item_code"], "H738")
        self.assertEqual(picker_rows["H7P38S2BTSK"]["picker_model_code"], "H7")
        self.assertEqual(picker_rows["H7P38S2BTSK"]["picker_tongue_profile"], "standard")
        self.assertEqual(picker_rows["U7210ABC"]["picker_item_code"], "U710")
        self.assertEqual(picker_rows["C4215XYZ"]["picker_item_code"], "C415")
        self.assertEqual(picker_rows["C5214XYZ"]["picker_item_code"], "C514")
        self.assertEqual(picker_rows["LDQ34A2BSSK"]["picker_item_code"], "LD34")
        self.assertEqual(picker_rows["LDQ34A2BSSK"]["picker_model_code"], "LD")
        self.assertEqual(picker_rows["LDQ34A2BSSK"]["picker_tongue_profile"], "gooseneck")
        self.assertIn("deck_length_ft", picker_rows["H7P38S2BTSK"])
        self.assertIn("deck_height_ft", picker_rows["H7P38S2BTSK"])


if __name__ == "__main__":
    unittest.main()
