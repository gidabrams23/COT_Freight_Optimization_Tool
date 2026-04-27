import importlib
import importlib.util
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from flask import Flask

PIL_AVAILABLE = importlib.util.find_spec("PIL") is not None


class ProgradeExportSummaryTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "prograde_export_summary_test.db"
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

    def _create_active_profile(self, name="Export Tester"):
        profile_id = int(self.db.create_access_profile(name=name, is_admin=False))
        with self.client.session_transaction() as sess:
            sess["prograde_profile_id"] = profile_id
        return profile_id

    def _seed_bigtex_sku(self, item_number):
        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, mcat, tier, model, bed_length, tongue, stack_height, total_footprint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (item_number, "utility", 1, "UT", 16.0, 4.0, 2.0, 20.0),
            )

    def _seed_stepdeck_carrier(self):
        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO carrier_configs
                (
                  carrier_type, brand, total_length_ft, max_height_ft,
                  lower_deck_length_ft, upper_deck_length_ft,
                  lower_deck_ground_height_ft, upper_deck_ground_height_ft,
                  gn_max_lower_deck_ft, notes, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("53_step_deck", "generic", 53.0, 13.5, 41.5, 11.5, 3.5, 5.0, 39.0, "test seed"),
            )

    def _create_bigtex_session(self, profile_id, planner_name="Export Tester"):
        self._seed_stepdeck_carrier()
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id=session_id,
            brand="bigtex",
            carrier_type="53_step_deck",
            planner_name=planner_name,
            session_label="export-summary",
            created_by_profile_id=profile_id,
            created_by_name=planner_name,
        )
        return session_id

    def _create_pj_session(self, profile_id, planner_name="Export Tester"):
        self._seed_stepdeck_carrier()
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id=session_id,
            brand="pj",
            carrier_type="53_step_deck",
            planner_name=planner_name,
            session_label="export-summary-pj",
            created_by_profile_id=profile_id,
            created_by_name=planner_name,
        )
        return session_id

    def test_load_builder_shows_lower_left_right_drop_targets_and_pdf_export_label(self):
        profile_id = self._create_active_profile()
        session_id = self._create_bigtex_session(profile_id)

        resp = self.client.get(f"/prograde/session/{session_id}/load")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn('data-drop-zone="lower_deck" data-drop-side="left"', html)
        self.assertIn('data-drop-zone="lower_deck" data-drop-side="right"', html)
        self.assertIn("Download PDF", html)
        self.assertIn("Inventory Gap source (+)", html)
        self.assertNotIn("Add SKUs source", html)
        self.assertIn("pg-legend-card", html)
        self.assertIn("window.__pgGlobalDropHandlers", html)

    def test_load_builder_hides_left_right_stack_visual_labels_for_pj(self):
        profile_id = self._create_active_profile("PJ Export Tester")
        session_id = self._create_pj_session(profile_id, planner_name="PJ Export Tester")

        resp = self.client.get(f"/prograde/session/{session_id}/load")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn('data-drop-zone="lower_deck" data-drop-side="left"', html)
        self.assertIn('data-drop-zone="lower_deck" data-drop-side="right"', html)
        self.assertNotIn('id="pg-lower-side-label-left"', html)
        self.assertNotIn('id="pg-lower-side-label-right"', html)
        self.assertNotIn(">Left Stack<", html)
        self.assertNotIn(">Right Stack<", html)

    def test_load_builder_renders_bigtex_dump_with_shared_dump_geometry(self):
        profile_id = self._create_active_profile("BT Dump Geometry Tester")
        session_id = self._create_bigtex_session(profile_id)

        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, mcat, tier, model, bed_length, tongue, stack_height, total_footprint, floor_type, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("BT-DUMP-GEO", "DUMP", 1, "DM", 14.0, 5.0, 3.0, 19.0, "hydraulic"),
            )

        add_resp = self.client.post(
            f"/prograde/api/session/{session_id}/add",
            json={"item_number": "BT-DUMP-GEO", "deck_zone": "lower_deck"},
        )
        self.assertEqual(add_resp.status_code, 200)
        self.assertTrue((add_resp.get_json() or {}).get("ok"))

        resp = self.client.get(f"/prograde/session/{session_id}/load")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn("BT-DUMP-GEO", html)
        self.assertIn("pg-dump-rear-wall", html)

    def test_load_builder_shows_bt_nest_control_for_dump_stack_candidate(self):
        profile_id = self._create_active_profile("BT Nest Control Tester")
        session_id = self._create_bigtex_session(profile_id)
        host_id = str(uuid.uuid4())
        guest_id = str(uuid.uuid4())
        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, mcat, tier, model, bed_length, tongue, stack_height, total_footprint, floor_type, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("BT-DUMP-HOST", "DUMP", 1, "DM", 16.0, 3.0, 2.0, 19.0, "hydraulic"),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, mcat, tier, model, bed_length, tongue, stack_height, total_footprint, floor_type, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("BT-DUMP-GUEST", "UTILITY", 1, "UT", 6.0, 2.0, 2.0, 8.0, "flat"),
            )
        self.db.add_position(
            position_id=host_id,
            session_id=session_id,
            brand="bigtex",
            item_number="BT-DUMP-HOST",
            deck_zone="lower_deck",
            layer=1,
            sequence=1,
        )
        self.db.add_position(
            position_id=guest_id,
            session_id=session_id,
            brand="bigtex",
            item_number="BT-DUMP-GUEST",
            deck_zone="lower_deck",
            layer=2,
            sequence=1,
        )

        resp = self.client.get(f"/prograde/session/{session_id}/load")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("pg-unit-control-nest", html)
        self.assertIn(f"nestUnit('{guest_id}','{host_id}')", html)

    @unittest.skipUnless(PIL_AVAILABLE, "Pillow not installed in this test runtime")
    def test_export_pdf_downloads_single_page_payload_with_full_bigtex_sku(self):
        profile_id = self._create_active_profile("Export Summary Tester")
        session_id = self._create_bigtex_session(profile_id, planner_name="Export Summary Tester")
        sku_item = "BT-LONGSKU-1234"
        self._seed_bigtex_sku(sku_item)

        add_resp = self.client.post(
            f"/prograde/api/session/{session_id}/add",
            json={
                "item_number": sku_item,
                "deck_zone": "lower_deck",
                "add_source": "inventory_gap",
            },
        )
        self.assertEqual(add_resp.status_code, 200)
        self.assertTrue(add_resp.get_json().get("ok"))

        export_resp = self.client.get(f"/prograde/session/{session_id}/export.pdf")
        self.assertEqual(export_resp.status_code, 200)
        self.assertEqual(export_resp.mimetype, "application/pdf")
        content_disposition = export_resp.headers.get("Content-Disposition", "")
        self.assertIn("attachment", content_disposition.lower())
        self.assertIn(".pdf", content_disposition.lower())
        pdf_bytes = export_resp.get_data()

        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertIn(b"/Count 1", pdf_bytes)


if __name__ == "__main__":
    unittest.main()
