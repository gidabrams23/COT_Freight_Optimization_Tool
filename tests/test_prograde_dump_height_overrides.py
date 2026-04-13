import importlib
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from flask import Flask


class ProgradeDumpHeightOverrideTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "prograde_dump_height_override_test.db"
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

    def _create_active_profile(self, name="Dump Tester"):
        profile_id = int(self.db.create_access_profile(name=name, is_admin=False))
        with self.client.session_transaction() as sess:
            sess["prograde_profile_id"] = profile_id
        return profile_id

    def _first_dump_item_number(self):
        for row in self.db.get_pj_skus():
            data = dict(row)
            category = str(data.get("pj_category") or "").strip().lower()
            if "dump" in category:
                return data.get("item_number")
        return None

    def test_add_dump_unit_persists_height_override_and_uses_it_in_rendered_height(self):
        item_number = self._first_dump_item_number()
        self.assertIsNotNone(item_number)

        profile_id = self._create_active_profile("Dump Height Tester")
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "pj",
            "53_step_deck",
            "Dump Height Tester",
            "dump-height-override",
            created_by_profile_id=profile_id,
            created_by_name="Dump Height Tester",
        )

        add_resp = self.client.post(
            f"/prograde/api/session/{session_id}/add",
            json={
                "item_number": item_number,
                "deck_zone": "lower_deck",
                "pj_tongue_profile": "standard",
                "pj_dump_height_ft": 4,
            },
        )
        self.assertEqual(add_resp.status_code, 200)
        payload = add_resp.get_json()
        self.assertTrue(payload["ok"])

        positions = self.db.get_positions(session_id)
        self.assertEqual(len(positions), 1)
        override_reason = str(dict(positions[0]).get("override_reason") or "")
        self.assertIn("dump_height_ft:4.0", override_reason)

        load_html = self.client.get(f"/prograde/session/{session_id}/load").get_data(as_text=True)
        self.assertIn("h=4.0 ft", load_html)

    def test_dump_door_off_hides_rear_wall_line(self):
        item_number = self._first_dump_item_number()
        self.assertIsNotNone(item_number)

        profile_id = self._create_active_profile("Dump Door Tester")
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "pj",
            "53_step_deck",
            "Dump Door Tester",
            "dump-door-toggle",
            created_by_profile_id=profile_id,
            created_by_name="Dump Door Tester",
        )

        add_resp = self.client.post(
            f"/prograde/api/session/{session_id}/add",
            json={
                "item_number": item_number,
                "deck_zone": "lower_deck",
            },
        )
        self.assertEqual(add_resp.status_code, 200)

        html_on = self.client.get(f"/prograde/session/{session_id}/load").get_data(as_text=True)
        self.assertIn("Door On", html_on)
        self.assertIn("pg-dump-rear-wall", html_on)

        positions = self.db.get_positions(session_id)
        position_id = dict(positions[0]).get("position_id")
        toggle_resp = self.client.post(
            f"/prograde/api/session/{session_id}/toggle_dump_door",
            json={"position_id": position_id},
        )
        self.assertEqual(toggle_resp.status_code, 200)

        html_off = self.client.get(f"/prograde/session/{session_id}/load").get_data(as_text=True)
        self.assertIn("Door Off", html_off)
        self.assertNotIn("pg-dump-rear-wall", html_off)


if __name__ == "__main__":
    unittest.main()
