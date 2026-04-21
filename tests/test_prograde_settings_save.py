import importlib
import os
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask


class ProgradeSettingsSaveTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "prograde_settings_test.db"
        self._previous_db_path = os.environ.get("PROGRADE_DB_PATH")
        os.environ["PROGRADE_DB_PATH"] = str(self._db_path)

        import blueprints.prograde.db as prograde_db
        import blueprints.prograde.routes as prograde_routes

        self.db = importlib.reload(prograde_db)
        self.routes = importlib.reload(prograde_routes)
        self.db.init_db()

        self.app = Flask(__name__)
        self.app.secret_key = "test-secret-key"
        self.app.register_blueprint(self.routes.prograde_bp)
        self.client = self.app.test_client()

    def tearDown(self):
        if self._previous_db_path is None:
            os.environ.pop("PROGRADE_DB_PATH", None)
        else:
            os.environ["PROGRADE_DB_PATH"] = self._previous_db_path
        try:
            self._tmpdir.cleanup()
        except PermissionError:
            pass

    def test_init_db_seeds_advanced_schematic_links(self):
        rows = [dict(r) for r in self.db.get_advanced_schematic_links()]
        self.assertGreaterEqual(len(rows), 5)
        keys = {row["drawing_key"] for row in rows}
        self.assertIn("utility_profile", keys)
        self.assertIn("dump_profile", keys)

    def test_init_db_seeds_reference_defaults_and_marks_seed_ready(self):
        carriers = [dict(r) for r in self.db.get_carrier_configs()]
        carrier_types = {row["carrier_type"] for row in carriers}
        self.assertIn("53_step_deck", carrier_types)
        self.assertIn("53_flatbed", carrier_types)

        with self.db.get_db() as conn:
            tongue_count = conn.execute("SELECT COUNT(*) FROM pj_tongue_groups").fetchone()[0]
            height_count = conn.execute("SELECT COUNT(*) FROM pj_height_reference").fetchone()[0]
            bt_stack_count = conn.execute("SELECT COUNT(*) FROM bt_stack_configs").fetchone()[0]

        self.assertGreater(tongue_count, 0)
        self.assertGreater(height_count, 0)
        self.assertGreater(bt_stack_count, 0)
        self.assertTrue(self.db.has_seed_data())

    def test_init_db_backfills_unsaved_sessions_when_v2_marker_missing(self):
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "bigtex",
            "53_step_deck",
            "Backfill Tester",
            "Unsaved session",
            is_saved=False,
            created_by_name="Backfill Tester",
        )
        with self.db.get_db() as conn:
            conn.execute("DELETE FROM app_meta WHERE meta_key='is_saved_backfill_v2'")

        self.db.init_db()
        row = self.db.get_session(session_id)
        self.assertIsNotNone(row)
        self.assertEqual(int(row["is_saved"] or 0), 1)

    def test_height_top_save_keeps_mid_synced(self):
        now = datetime.utcnow().isoformat()
        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pj_height_reference
                (category, label, height_mid_ft, height_top_ft, gn_axle_dropped_ft, notes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("utility", "Utility", 2.0, 2.5, 1.5, "", now),
            )

        resp = self.client.post(
            "/prograde/api/settings/save",
            json={
                "table": "pj_height_reference",
                "pk": "utility",
                "field": "height_top_ft",
                "value": 4.25,
            },
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload["ok"])

        with self.db.get_db() as conn:
            row = dict(
                conn.execute(
                    "SELECT * FROM pj_height_reference WHERE category=?",
                    ("utility",),
                ).fetchone()
            )
        self.assertEqual(float(row["height_top_ft"]), 4.25)
        self.assertEqual(float(row["height_mid_ft"]), 4.25)

    def test_pj_bed_length_edit_recomputes_footprint(self):
        now = datetime.utcnow().isoformat()
        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pj_skus
                (item_number, model, pj_category, description, gvwr, bed_length_stated, bed_length_measured,
                 tongue_group, tongue_feet, total_footprint, dump_side_height_ft, can_nest_inside_dump,
                 gn_axle_droppable, tongue_overlap_allowed, pairing_rule, notes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "TEST-PJ-1",
                    "UT",
                    "utility",
                    "Test SKU",
                    None,
                    10.0,
                    10.0,
                    "G1",
                    2.0,
                    12.0,
                    None,
                    0,
                    0,
                    0,
                    None,
                    None,
                    now,
                ),
            )

        resp = self.client.post(
            "/prograde/api/settings/save",
            json={
                "table": "pj_skus",
                "pk": "TEST-PJ-1",
                "field": "bed_length_measured",
                "value": 14.5,
            },
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload["ok"])
        self.assertIsInstance(payload.get("recomputed"), list)
        self.assertEqual(float(payload["recomputed"][0]["total_footprint"]), 16.5)

        updated = dict(self.db.get_pj_sku("TEST-PJ-1"))
        self.assertEqual(float(updated["bed_length_measured"]), 14.5)
        self.assertEqual(float(updated["total_footprint"]), 16.5)

    def test_get_bigtex_skus_merges_ol_categories(self):
        now = datetime.utcnow().isoformat()
        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, mcat, model, bed_length, tongue, total_footprint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("BT-OL-1", "OL CAR HAULER", "TH70", 16.0, 4.0, 20.0, now),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, mcat, model, bed_length, tongue, total_footprint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("BT-BASE-1", "CAR HAULER", "70CH", 16.0, 4.0, 20.0, now),
            )

        rows = self.db.get_bigtex_skus()
        merged = [row for row in rows if row["item_number"] in {"BT-OL-1", "BT-BASE-1"}]
        self.assertEqual(len(merged), 2)
        self.assertTrue(all(row["mcat"] == "CAR HAULER" for row in merged))

    def test_bigtex_category_save_normalizes_ol_input(self):
        now = datetime.utcnow().isoformat()
        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, mcat, model, bed_length, tongue, total_footprint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("BT-SAVE-1", "DUMP", "14LP", 14.0, 5.0, 19.0, now),
            )

        resp = self.client.post(
            "/prograde/api/settings/save",
            json={
                "table": "bigtex_skus",
                "pk": "BT-SAVE-1",
                "field": "mcat",
                "value": "OL DUMP",
            },
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload["ok"])

        with self.db.get_db() as conn:
            row = conn.execute(
                "SELECT mcat FROM bigtex_skus WHERE item_number=?",
                ("BT-SAVE-1",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(str(row["mcat"]), "DUMP")


if __name__ == "__main__":
    unittest.main()
