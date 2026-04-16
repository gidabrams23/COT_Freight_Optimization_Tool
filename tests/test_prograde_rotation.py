import importlib
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from flask import Flask


class ProgradeRotationTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "prograde_rotation_test.db"
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
            # Windows can transiently hold SQLite WAL files open at teardown.
            pass

    def _create_active_profile(self, name="Rotation Tester"):
        profile_id = int(self.db.create_access_profile(name=name, is_admin=False))
        with self.client.session_transaction() as sess:
            sess["prograde_profile_id"] = profile_id
        return profile_id

    def _seed_bigtex_sku(self, item_number="BT-DEFAULT-LEFT"):
        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, mcat, tier, model, bed_length, tongue, stack_height, total_footprint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (item_number, "utility", 1, "UT", 16.0, 4.0, 2.0, 20.0),
            )
        return item_number

    def test_load_positions_schema_includes_rotation_flag(self):
        with self.db.get_db() as conn:
            cols = [row["name"] for row in conn.execute("PRAGMA table_info(load_positions)").fetchall()]
        self.assertIn("is_rotated", cols)

    def test_rotate_endpoint_toggles_position_flag(self):
        profile_id = self._create_active_profile()
        session_id = str(uuid.uuid4())
        position_id = str(uuid.uuid4())
        self.db.create_session(
            session_id=session_id,
            brand="bigtex",
            carrier_type="53_step_deck",
            planner_name="Rotation Tester",
            session_label="rotation",
            created_by_profile_id=profile_id,
            created_by_name="Rotation Tester",
        )
        self.db.add_position(
            position_id=position_id,
            session_id=session_id,
            brand="bigtex",
            item_number="UNMAPPED-ROTATE",
            deck_zone="lower_deck",
            layer=1,
            sequence=1,
        )

        first = self.client.post(
            f"/prograde/api/session/{session_id}/rotate",
            json={"position_id": position_id},
        )
        self.assertEqual(first.status_code, 200)
        first_payload = first.get_json()
        self.assertTrue(first_payload["ok"])
        self.assertEqual(first_payload["is_rotated"], 1)
        self.assertEqual(int(self.db.get_position(position_id)["is_rotated"]), 1)

        second = self.client.post(
            f"/prograde/api/session/{session_id}/rotate",
            json={"position_id": position_id},
        )
        self.assertEqual(second.status_code, 200)
        second_payload = second.get_json()
        self.assertTrue(second_payload["ok"])
        self.assertEqual(second_payload["is_rotated"], 0)
        self.assertEqual(int(self.db.get_position(position_id)["is_rotated"]), 0)

    def test_bigtex_add_endpoint_defaults_new_units_to_left_facing_tongue(self):
        profile_id = self._create_active_profile("BT Orientation Tester")
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id=session_id,
            brand="bigtex",
            carrier_type="53_step_deck",
            planner_name="BT Orientation Tester",
            session_label="bt-default-orientation",
            created_by_profile_id=profile_id,
            created_by_name="BT Orientation Tester",
        )
        item_number = self._seed_bigtex_sku()

        add_resp = self.client.post(
            f"/prograde/api/session/{session_id}/add",
            json={"item_number": item_number, "deck_zone": "lower_deck"},
        )
        self.assertEqual(add_resp.status_code, 200)
        payload = add_resp.get_json()
        self.assertTrue(payload["ok"])

        added = self.db.get_position(payload["position_id"])
        self.assertIsNotNone(added)
        self.assertEqual(int(added["is_rotated"]), 1)


if __name__ == "__main__":
    unittest.main()
