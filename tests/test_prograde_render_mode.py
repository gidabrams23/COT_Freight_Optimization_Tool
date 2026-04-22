import importlib
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from flask import Flask


class ProgradeRenderModeTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "prograde_render_mode_test.db"
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

    def _create_active_profile(self, name="Render Tester"):
        profile_id = int(self.db.create_access_profile(name=name, is_admin=False))
        with self.client.session_transaction() as sess:
            sess["prograde_profile_id"] = profile_id
        return profile_id

    def test_pj_load_builder_css_shows_active_render_mode(self):
        profile_id = self._create_active_profile("Render Mode Tester")
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "pj",
            "53_step_deck",
            "Render Mode Tester",
            "render-mode-regression",
            created_by_profile_id=profile_id,
            created_by_name="Render Mode Tester",
        )

        resp = self.client.get(f"/prograde/session/{session_id}/load")
        self.assertEqual(resp.status_code, 200)

        html = resp.get_data(as_text=True)
        self.assertIn("render-mode-advanced", html)
        self.assertIn(".render-mode-standard .unit-render-standard", html)
        self.assertIn(".render-mode-advanced .unit-render-advanced", html)
        self.assertIn(".render-mode-standard .pg-unit-block[data-has-tongue=\"1\"] .unit-tongue", html)
        self.assertIn(".render-mode-advanced .pg-unit-block .unit-tongue", html)
        self.assertIn(".render-mode-advanced .pg-unit-block {\n  overflow: hidden;", html)
        self.assertIn(".pg-unit-block .unit-render {\n  display: none;\n  position: absolute;", html)
        self.assertIn("display: block;", html)
        self.assertIn("Category -&gt; Model -&gt; Item", html)
        self.assertIn(".sku-row-tongue-btn", html)
        self.assertIn("pj-table-no-dump", html)
        self.assertIn("modelHasDumpProfile", html)
        self.assertIn("<span>Item</span><span>Len</span><span>Ht</span><span>Tongue</span>", html)
        self.assertIn("pj_tongue_profile", html)
        self.assertNotIn('role="radiogroup" aria-label="Rendering mode"', html)

    def test_pj_add_endpoint_persists_selected_tongue_profile_for_rendering(self):
        profile_id = self._create_active_profile("Tongue Profile Tester")
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "pj",
            "53_step_deck",
            "Tongue Profile Tester",
            "tongue-profile-selection",
            created_by_profile_id=profile_id,
            created_by_name="Tongue Profile Tester",
        )
        skus = self.db.get_pj_skus()
        if skus:
            item_number = skus[0]["item_number"]
        else:
            with self.db.get_db() as conn:
                conn.execute(
                    """
                    INSERT INTO pj_skus
                    (item_number, model, pj_category, description, total_footprint, bed_length_measured, tongue_feet, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("TESTSKU01", "TS", "utility", "Test SKU", 12.0, 10.0, 2.0, "2026-04-09T00:00:00"),
                )
            item_number = "TESTSKU01"

        add_resp = self.client.post(
            f"/prograde/api/session/{session_id}/add",
            json={
                "item_number": item_number,
                "deck_zone": "lower_deck",
                "pj_tongue_profile": "gooseneck",
            },
        )
        self.assertEqual(add_resp.status_code, 200)
        payload = add_resp.get_json()
        self.assertTrue(payload["ok"])

        positions = self.db.get_positions(session_id)
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["override_reason"], "tongue_profile:gooseneck")

        html = self.client.get(f"/prograde/session/{session_id}/load").get_data(as_text=True)
        self.assertIn('data-tongue-profile="gooseneck"', html)
        self.assertIn("unit-tongue-pill gn", html)
        self.assertIn("Tongue (render): 9.0 ft", html)

    def test_pj_load_builder_does_not_render_manual_alignment_controls(self):
        profile_id = self._create_active_profile("Auto Alignment Tester")
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "pj",
            "53_step_deck",
            "Auto Alignment Tester",
            "auto-alignment-only",
            created_by_profile_id=profile_id,
            created_by_name="Auto Alignment Tester",
        )
        html = self.client.get(f"/prograde/session/{session_id}/load").get_data(as_text=True)
        self.assertNotIn("setStackAlignment", html)
        self.assertNotIn("pg-unit-control-align", html)

        missing = self.client.post(
            f"/prograde/api/session/{session_id}/stack_alignment",
            json={"position_id": "missing", "stack_alignment": "right"},
        )
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
