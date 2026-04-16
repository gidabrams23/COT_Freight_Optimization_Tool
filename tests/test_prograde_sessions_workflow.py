import importlib
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from flask import Flask


class ProgradeSessionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "prograde_sessions_workflow_test.db"
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

    def _set_active_profile(self, profile_id):
        with self.client.session_transaction() as sess:
            sess["prograde_profile_id"] = int(profile_id)

    def _create_planner_profile(self, name):
        return int(self.db.create_access_profile(name=name, is_admin=False))

    def test_session_hidden_from_all_sessions_until_saved(self):
        profile_id = self._create_planner_profile("Workflow Tester")
        self._set_active_profile(profile_id)
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "bigtex",
            "53_step_deck",
            "Workflow Tester",
            "Unsaved session",
            created_by_profile_id=profile_id,
            created_by_name="Workflow Tester",
        )

        resp_before = self.client.get("/prograde/sessions?brand=bigtex")
        self.assertEqual(resp_before.status_code, 200)
        self.assertNotIn(session_id, resp_before.get_data(as_text=True))

        save_resp = self.client.post(f"/prograde/api/session/{session_id}/save", json={})
        self.assertEqual(save_resp.status_code, 200)
        save_payload = save_resp.get_json()
        self.assertTrue(save_payload["ok"])
        self.assertEqual(save_payload["session_id"], session_id)

        resp_after = self.client.get("/prograde/sessions?brand=bigtex")
        self.assertEqual(resp_after.status_code, 200)
        self.assertIn(session_id, resp_after.get_data(as_text=True))

    def test_all_sessions_filters_by_selected_brand(self):
        profile_id = self._create_planner_profile("Brand Tester")
        self._set_active_profile(profile_id)
        bt_session_id = str(uuid.uuid4())
        pj_session_id = str(uuid.uuid4())
        self.db.create_session(
            bt_session_id,
            "bigtex",
            "53_step_deck",
            "Brand Tester",
            "BT Saved",
            is_saved=True,
            created_by_profile_id=profile_id,
            created_by_name="Brand Tester",
        )
        self.db.create_session(
            pj_session_id,
            "pj",
            "53_step_deck",
            "Brand Tester",
            "PJ Saved",
            is_saved=True,
            created_by_profile_id=profile_id,
            created_by_name="Brand Tester",
        )

        bt_resp = self.client.get("/prograde/sessions?brand=bigtex")
        self.assertEqual(bt_resp.status_code, 200)
        bt_html = bt_resp.get_data(as_text=True)
        self.assertIn(bt_session_id, bt_html)
        self.assertNotIn(pj_session_id, bt_html)
        self.assertIn('href="/prograde/session/new?brand=bigtex"', bt_html)

        pj_resp = self.client.get("/prograde/sessions?brand=pj")
        self.assertEqual(pj_resp.status_code, 200)
        pj_html = pj_resp.get_data(as_text=True)
        self.assertIn(pj_session_id, pj_html)
        self.assertNotIn(bt_session_id, pj_html)
        self.assertIn('href="/prograde/session/new?brand=pj"', pj_html)

    def test_all_sessions_shows_qty_column_from_position_count(self):
        profile_id = self._create_planner_profile("Qty Tester")
        self._set_active_profile(profile_id)
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "bigtex",
            "53_step_deck",
            "Qty Tester",
            "QTY Session",
            is_saved=True,
            created_by_profile_id=profile_id,
            created_by_name="Qty Tester",
        )
        self.db.add_position(
            position_id=str(uuid.uuid4()),
            session_id=session_id,
            brand="bigtex",
            item_number="UNMAPPED-QTY-1",
            deck_zone="lower_deck",
            layer=1,
            sequence=1,
        )
        self.db.add_position(
            position_id=str(uuid.uuid4()),
            session_id=session_id,
            brand="bigtex",
            item_number="UNMAPPED-QTY-2",
            deck_zone="upper_deck",
            layer=1,
            sequence=2,
        )

        resp = self.client.get("/prograde/sessions?brand=bigtex")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("<th>QTY</th>", html)
        self.assertIn('class="td-mono td-qty">2</td>', html)

    def test_session_can_be_deleted_from_all_sessions(self):
        profile_id = self._create_planner_profile("Delete Tester")
        self._set_active_profile(profile_id)
        session_id = str(uuid.uuid4())
        position_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "bigtex",
            "53_step_deck",
            "Delete Tester",
            "Delete me",
            is_saved=True,
            created_by_profile_id=profile_id,
            created_by_name="Delete Tester",
        )
        self.db.add_position(
            position_id=position_id,
            session_id=session_id,
            brand="bigtex",
            item_number="UNMAPPED-DELETE",
            deck_zone="lower_deck",
            layer=1,
            sequence=1,
        )

        delete_resp = self.client.post(
            f"/prograde/session/{session_id}/delete?brand=bigtex",
            follow_redirects=True,
        )
        self.assertEqual(delete_resp.status_code, 200)
        delete_html = delete_resp.get_data(as_text=True)
        self.assertNotIn(session_id, delete_html)
        self.assertIsNone(self.db.get_session(session_id))
        self.assertEqual(len(self.db.get_positions(session_id)), 0)

    def test_root_route_renders_account_landing_for_selected_brand(self):
        resp = self.client.get("/prograde/?brand=pj", follow_redirects=False)
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Select Account", html)
        self.assertIn('name="brand" value="pj"', html)

    def test_account_landing_shows_account_selection(self):
        resp = self.client.get("/prograde/account?brand=bigtex")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Select Account", html)
        self.assertIn("Add New Account", html)

    def test_account_select_sets_active_profile_for_session(self):
        profile_id = self._create_planner_profile("Session Selection Tester")
        resp = self.client.post(
            "/prograde/account/select",
            data={"brand": "bigtex", "profile_id": str(profile_id)},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/prograde/sessions?brand=bigtex", resp.headers.get("Location", ""))

        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("prograde_profile_id"), profile_id)
            self.assertEqual(sess.get("prograde_profile_name"), "Session Selection Tester")
            self.assertEqual(sess.get("prograde_profile_is_admin"), 0)

        sessions_resp = self.client.get("/prograde/sessions?brand=bigtex", follow_redirects=False)
        self.assertEqual(sessions_resp.status_code, 200)

    def test_account_create_sets_active_profile_for_session(self):
        resp = self.client.post(
            "/prograde/account/create",
            data={"brand": "pj", "name": "Quick Add Planner"},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/prograde/sessions?brand=pj", resp.headers.get("Location", ""))

        with self.client.session_transaction() as sess:
            created_profile_id = sess.get("prograde_profile_id")
            self.assertIsNotNone(created_profile_id)
            self.assertEqual(sess.get("prograde_profile_name"), "Quick Add Planner")
            self.assertEqual(sess.get("prograde_profile_is_admin"), 0)

        profile = self.db.get_access_profile(created_profile_id)
        self.assertIsNotNone(profile)
        self.assertEqual(profile["name"], "Quick Add Planner")

    def test_sessions_requires_active_account(self):
        resp = self.client.get("/prograde/sessions?brand=bigtex", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/prograde/account?", resp.headers.get("Location", ""))

    def test_sessions_scope_planner_vs_admin(self):
        planner_one_id = self._create_planner_profile("Planner One")
        planner_two_id = self._create_planner_profile("Planner Two")
        admin_id = int(self.db.create_access_profile(name="Workflow Admin", is_admin=True))

        planner_one_session_id = str(uuid.uuid4())
        planner_two_session_id = str(uuid.uuid4())
        self.db.create_session(
            planner_one_session_id,
            "bigtex",
            "53_step_deck",
            "Planner One",
            "Planner One Session",
            is_saved=True,
            created_by_profile_id=planner_one_id,
            created_by_name="Planner One",
        )
        self.db.create_session(
            planner_two_session_id,
            "bigtex",
            "53_step_deck",
            "Planner Two",
            "Planner Two Session",
            is_saved=True,
            created_by_profile_id=planner_two_id,
            created_by_name="Planner Two",
        )

        self._set_active_profile(planner_one_id)
        planner_resp = self.client.get("/prograde/sessions?brand=bigtex")
        self.assertEqual(planner_resp.status_code, 200)
        planner_html = planner_resp.get_data(as_text=True)
        self.assertIn(planner_one_session_id, planner_html)
        self.assertNotIn(planner_two_session_id, planner_html)

        self._set_active_profile(admin_id)
        admin_resp = self.client.get("/prograde/sessions?brand=bigtex")
        self.assertEqual(admin_resp.status_code, 200)
        admin_html = admin_resp.get_data(as_text=True)
        self.assertIn(planner_one_session_id, admin_html)
        self.assertIn(planner_two_session_id, admin_html)

    def test_session_new_stamps_builder_from_selected_account(self):
        profile_id = self.db.create_access_profile("Builder User")
        self._set_active_profile(profile_id)

        create_resp = self.client.get("/prograde/session/new?brand=bigtex", follow_redirects=False)
        self.assertEqual(create_resp.status_code, 302)
        location = create_resp.headers.get("Location", "")
        self.assertIn("/prograde/session/", location)
        self.assertIn("/load", location)

        session_id = location.split("/session/")[1].split("/load")[0]
        row = self.db.get_session(session_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["created_by_profile_id"], profile_id)
        self.assertEqual(row["created_by_name"], "Builder User")
        self.assertEqual(row["planner_name"], "Builder User")

    def test_pj_add_stack_can_enable_gooseneck_crisscross(self):
        profile_id = self.db.create_access_profile("PJ Crisscross Planner")
        self._set_active_profile(profile_id)

        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "pj",
            "53_step_deck",
            "PJ Crisscross Planner",
            "PJ Crisscross Session",
            created_by_profile_id=profile_id,
            created_by_name="PJ Crisscross Planner",
        )

        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pj_skus
                (item_number, model, pj_category, bed_length_stated, bed_length_measured, tongue_feet, total_footprint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("PJ-GN-A", "LS", "gooseneck", 30.0, 30.0, 9.0, 39.0),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO pj_skus
                (item_number, model, pj_category, bed_length_stated, bed_length_measured, tongue_feet, total_footprint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("PJ-GN-B", "DL", "gooseneck", 14.0, 14.0, 9.0, 23.0),
            )

        add_base = self.client.post(
            f"/prograde/api/session/{session_id}/add",
            json={"item_number": "PJ-GN-A", "deck_zone": "lower_deck", "pj_tongue_profile": "gooseneck"},
        )
        self.assertEqual(add_base.status_code, 200)
        base_payload = add_base.get_json()
        self.assertTrue(base_payload["ok"])
        base_position_id = base_payload["position_id"]

        add_top = self.client.post(
            f"/prograde/api/session/{session_id}/add",
            json={
                "item_number": "PJ-GN-B",
                "deck_zone": "lower_deck",
                "stack_on": base_position_id,
                "pj_tongue_profile": "gooseneck",
            },
        )
        self.assertEqual(add_top.status_code, 200)
        top_payload = add_top.get_json()
        self.assertTrue(top_payload["ok"])
        self.assertTrue(top_payload["gn_crisscross_applied"])

        rows = [dict(r) for r in self.db.get_positions(session_id)]
        self.assertEqual(len(rows), 2)
        rows.sort(key=lambda r: int(r["layer"]))
        lower, upper = rows
        self.assertEqual(int(lower["sequence"]), int(upper["sequence"]))
        self.assertEqual(bool(lower["is_rotated"]), bool(upper["is_rotated"]))
        self.assertIn("gn_crisscross:1", str(lower.get("override_reason") or ""))
        self.assertIn("gn_crisscross:1", str(upper.get("override_reason") or ""))


if __name__ == "__main__":
    unittest.main()
