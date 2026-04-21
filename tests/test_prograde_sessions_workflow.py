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

    def _set_cot_profile(self, name, role="planner"):
        with self.client.session_transaction() as sess:
            sess["profile_name"] = str(name)
            sess["role"] = str(role)

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

    def test_root_route_redirects_to_sessions_when_cot_profile_present(self):
        self._set_cot_profile("COT Auto Planner", role="planner")
        resp = self.client.get("/prograde/?brand=bigtex", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/prograde/sessions?brand=bigtex", resp.headers.get("Location", ""))

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

    def test_sessions_auto_activates_profile_from_cot_session(self):
        self._set_cot_profile("COT Planner", role="planner")
        resp = self.client.get("/prograde/sessions?brand=bigtex", follow_redirects=False)
        self.assertEqual(resp.status_code, 200)
        with self.client.session_transaction() as sess:
            self.assertIsNotNone(sess.get("prograde_profile_id"))
            self.assertEqual(sess.get("prograde_profile_name"), "COT Planner")
            self.assertEqual(int(sess.get("prograde_profile_is_admin") or 0), 0)

    def test_session_carrier_can_be_switched_via_api(self):
        profile_id = self._create_planner_profile("Carrier API Tester")
        self._set_active_profile(profile_id)
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
                ("53_flatbed", "bigtex", 53.0, 13.5, 53.0, 0.0, 4.0, 0.0, 0.0, "test flatbed"),
            )
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "bigtex",
            "53_step_deck",
            "Carrier API Tester",
            "Carrier Switch Session",
            created_by_profile_id=profile_id,
            created_by_name="Carrier API Tester",
        )
        resp = self.client.post(
            f"/prograde/api/session/{session_id}/carrier",
            json={"carrier_type": "53_flatbed"},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["carrier_type"], "53_flatbed")
        updated = self.db.get_session(session_id)
        self.assertEqual(updated["carrier_type"], "53_flatbed")

    def test_nest_api_can_nest_and_clear_nested_state(self):
        profile_id = self._create_planner_profile("Nest API Tester")
        self._set_active_profile(profile_id)
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "pj",
            "53_step_deck",
            "Nest API Tester",
            "Nest Session",
            created_by_profile_id=profile_id,
            created_by_name="Nest API Tester",
        )
        host_id = str(uuid.uuid4())
        guest_id = str(uuid.uuid4())
        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pj_skus
                (item_number, model, pj_category, bed_length_stated, bed_length_measured, tongue_feet, total_footprint, can_nest_inside_dump, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("PJ-DUMP-HOST", "DV", "dump_lowside", 16.0, 16.0, 3.0, 19.0, 0),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO pj_skus
                (item_number, model, pj_category, bed_length_stated, bed_length_measured, tongue_feet, total_footprint, can_nest_inside_dump, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("PJ-NEST-GUEST", "D5", "dump_small", 6.0, 6.0, 1.0, 7.0, 1),
            )

        self.db.add_position(
            position_id=host_id,
            session_id=session_id,
            brand="pj",
            item_number="PJ-DUMP-HOST",
            deck_zone="lower_deck",
            layer=1,
            sequence=1,
        )
        self.db.add_position(
            position_id=guest_id,
            session_id=session_id,
            brand="pj",
            item_number="PJ-NEST-GUEST",
            deck_zone="lower_deck",
            layer=2,
            sequence=1,
        )

        nest_resp = self.client.post(
            f"/prograde/api/session/{session_id}/nest",
            json={"position_id": guest_id, "nested_inside": host_id},
        )
        self.assertEqual(nest_resp.status_code, 200)
        nested_row = self.db.get_position(guest_id)
        self.assertEqual(int(nested_row["is_nested"] or 0), 1)
        self.assertEqual(nested_row["nested_inside"], host_id)

        clear_resp = self.client.post(
            f"/prograde/api/session/{session_id}/nest",
            json={"position_id": guest_id, "action": "clear"},
        )
        self.assertEqual(clear_resp.status_code, 200)
        cleared_row = self.db.get_position(guest_id)
        self.assertEqual(int(cleared_row["is_nested"] or 0), 0)
        self.assertIsNone(cleared_row["nested_inside"])

    def test_bigtex_nest_api_can_nest_and_clear_nested_state(self):
        profile_id = self._create_planner_profile("BT Nest API Tester")
        self._set_active_profile(profile_id)
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "bigtex",
            "53_step_deck",
            "BT Nest API Tester",
            "BT Nest Session",
            created_by_profile_id=profile_id,
            created_by_name="BT Nest API Tester",
        )
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
                ("BT-NEST-GUEST", "UTILITY", 1, "UT", 6.0, 2.0, 2.0, 8.0, "flat"),
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
            item_number="BT-NEST-GUEST",
            deck_zone="lower_deck",
            layer=2,
            sequence=1,
        )

        nest_resp = self.client.post(
            f"/prograde/api/session/{session_id}/nest",
            json={"position_id": guest_id, "nested_inside": host_id},
        )
        self.assertEqual(nest_resp.status_code, 200)
        nested_row = self.db.get_position(guest_id)
        self.assertEqual(int(nested_row["is_nested"] or 0), 1)
        self.assertEqual(nested_row["nested_inside"], host_id)

        clear_resp = self.client.post(
            f"/prograde/api/session/{session_id}/nest",
            json={"position_id": guest_id, "action": "clear"},
        )
        self.assertEqual(clear_resp.status_code, 200)
        cleared_row = self.db.get_position(guest_id)
        self.assertEqual(int(cleared_row["is_nested"] or 0), 0)
        self.assertIsNone(cleared_row["nested_inside"])

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
                ("53_step_deck", "generic", 53.0, 13.5, 41.5, 11.5, 3.5, 5.0, 39.0, "test step deck"),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, mcat, tier, model, bed_length, tongue, stack_height, total_footprint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("BT-SEED-001", "utility", 1, "UT", 16.0, 4.0, 2.0, 20.0),
            )

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

    def test_bigtex_same_direction_stack_uses_half_tongue_occupancy_in_canvas(self):
        profile_id = self.db.create_access_profile("BT Tongue Half Tester")
        self._set_active_profile(profile_id)
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "bigtex",
            "53_step_deck",
            "BT Tongue Half Tester",
            "BT Half Tongue Session",
            created_by_profile_id=profile_id,
            created_by_name="BT Tongue Half Tester",
        )

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
                ("53_step_deck", "generic", 53.0, 13.5, 41.5, 11.5, 3.5, 5.0, 39.0, "test step deck"),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, mcat, tier, model, bed_length, tongue, stack_height, total_footprint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("BT-HALF-001", "utility", 1, "UT", 16.0, 4.0, 2.0, 20.0),
            )

        left_position_id = str(uuid.uuid4())
        right_position_id = str(uuid.uuid4())
        self.db.add_position(
            position_id=left_position_id,
            session_id=session_id,
            brand="bigtex",
            item_number="BT-HALF-001",
            deck_zone="lower_deck",
            layer=1,
            sequence=1,
            is_rotated=1,
        )
        self.db.add_position(
            position_id=right_position_id,
            session_id=session_id,
            brand="bigtex",
            item_number="BT-HALF-001",
            deck_zone="lower_deck",
            layer=1,
            sequence=2,
            is_rotated=1,
        )

        session_row = dict(self.db.get_session(session_id) or {})
        carrier_row = self.db.get_carrier_config("53_step_deck")
        zones = self.routes.brand_config.DECK_ZONES.get("bigtex", [])
        canvas = self.routes._build_canvas_data(
            session_id=session_id,
            session=session_row,
            carrier=carrier_row,
            zones=zones,
            positions=self.db.get_positions(session_id),
            brand="bigtex",
        )
        units_by_id = {str(row.get("position_id")): row for row in canvas.get("enriched_positions") or []}
        left_unit = units_by_id[left_position_id]
        right_unit = units_by_id[right_position_id]

        self.assertAlmostEqual(float(left_unit.get("render_tongue_length_ft") or 0.0), 4.0, places=2)
        self.assertAlmostEqual(float(left_unit.get("occupied_tongue_length_ft") or 0.0), 4.0, places=2)
        self.assertFalse(bool(left_unit.get("bt_half_tongue_stuffed")))
        self.assertAlmostEqual(float(right_unit.get("render_tongue_length_ft") or 0.0), 4.0, places=2)
        self.assertAlmostEqual(float(right_unit.get("occupied_tongue_length_ft") or 0.0), 2.0, places=2)
        self.assertTrue(bool(right_unit.get("bt_half_tongue_stuffed")))

    def test_bigtex_half_tongue_rule_applies_to_consecutive_stacks(self):
        profile_id = self.db.create_access_profile("BT Tongue Chain Tester")
        self._set_active_profile(profile_id)
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "bigtex",
            "53_step_deck",
            "BT Tongue Chain Tester",
            "BT Half Tongue Chain Session",
            created_by_profile_id=profile_id,
            created_by_name="BT Tongue Chain Tester",
        )

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
                ("53_step_deck", "generic", 53.0, 13.5, 41.5, 11.5, 3.5, 5.0, 39.0, "test step deck"),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, mcat, tier, model, bed_length, tongue, stack_height, total_footprint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("BT-HALF-CHAIN", "utility", 1, "UT", 16.0, 4.0, 2.0, 20.0),
            )

        pos_ids = []
        for seq in (1, 2, 3):
            pos_id = str(uuid.uuid4())
            pos_ids.append(pos_id)
            self.db.add_position(
                position_id=pos_id,
                session_id=session_id,
                brand="bigtex",
                item_number="BT-HALF-CHAIN",
                deck_zone="lower_deck",
                layer=1,
                sequence=seq,
                is_rotated=1,
            )

        session_row = dict(self.db.get_session(session_id) or {})
        carrier_row = self.db.get_carrier_config("53_step_deck")
        zones = self.routes.brand_config.DECK_ZONES.get("bigtex", [])
        canvas = self.routes._build_canvas_data(
            session_id=session_id,
            session=session_row,
            carrier=carrier_row,
            zones=zones,
            positions=self.db.get_positions(session_id),
            brand="bigtex",
        )
        units_by_id = {str(row.get("position_id")): row for row in canvas.get("enriched_positions") or []}
        first = units_by_id[pos_ids[0]]
        second = units_by_id[pos_ids[1]]
        third = units_by_id[pos_ids[2]]

        self.assertAlmostEqual(float(first.get("occupied_tongue_length_ft") or 0.0), 4.0, places=2)
        self.assertFalse(bool(first.get("bt_half_tongue_stuffed")))
        self.assertAlmostEqual(float(second.get("occupied_tongue_length_ft") or 0.0), 2.0, places=2)
        self.assertTrue(bool(second.get("bt_half_tongue_stuffed")))
        self.assertAlmostEqual(float(third.get("occupied_tongue_length_ft") or 0.0), 2.0, places=2)
        self.assertTrue(bool(third.get("bt_half_tongue_stuffed")))

    def test_bigtex_add_on_stack_persists_column_alignment_on_base_stack(self):
        profile_id = self.db.create_access_profile("BT Alignment Tester")
        self._set_active_profile(profile_id)
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "bigtex",
            "53_step_deck",
            "BT Alignment Tester",
            "BT Alignment Session",
            created_by_profile_id=profile_id,
            created_by_name="BT Alignment Tester",
        )

        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, mcat, tier, model, bed_length, tongue, stack_height, total_footprint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("BT-ALIGN-001", "utility", 1, "UT", 16.0, 4.0, 2.0, 20.0),
            )

        add_base = self.client.post(
            f"/prograde/api/session/{session_id}/add",
            json={"item_number": "BT-ALIGN-001", "deck_zone": "lower_deck"},
        )
        self.assertEqual(add_base.status_code, 200)
        add_base_payload = add_base.get_json()
        self.assertTrue(add_base_payload["ok"])
        base_position_id = str(add_base_payload["position_id"])

        add_stacked = self.client.post(
            f"/prograde/api/session/{session_id}/add",
            json={
                "item_number": "BT-ALIGN-001",
                "deck_zone": "lower_deck",
                "stack_on": base_position_id,
                "column_alignment": "left",
            },
        )
        self.assertEqual(add_stacked.status_code, 200)
        add_stacked_payload = add_stacked.get_json()
        self.assertTrue(add_stacked_payload["ok"])

        base_row = dict(self.db.get_position(base_position_id) or {})
        self.assertIn("column_alignment:left", str(base_row.get("override_reason") or ""))

    def test_bigtex_stack_on_does_not_flip_existing_left_anchor(self):
        profile_id = self.db.create_access_profile("BT Anchor Persistence Tester")
        self._set_active_profile(profile_id)
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "bigtex",
            "53_step_deck",
            "BT Anchor Persistence Tester",
            "BT Anchor Session",
            created_by_profile_id=profile_id,
            created_by_name="BT Anchor Persistence Tester",
        )

        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, mcat, tier, model, bed_length, tongue, stack_height, total_footprint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("BT-ANCHOR-001", "utility", 1, "UT", 16.0, 4.0, 2.0, 20.0),
            )

        add_base = self.client.post(
            f"/prograde/api/session/{session_id}/add",
            json={"item_number": "BT-ANCHOR-001", "deck_zone": "lower_deck", "column_alignment": "left"},
        )
        self.assertEqual(add_base.status_code, 200)
        add_base_payload = add_base.get_json()
        self.assertTrue(add_base_payload["ok"])
        base_position_id = str(add_base_payload["position_id"])

        add_stacked = self.client.post(
            f"/prograde/api/session/{session_id}/add",
            json={
                "item_number": "BT-ANCHOR-001",
                "deck_zone": "lower_deck",
                "stack_on": base_position_id,
                "column_alignment": "right",
            },
        )
        self.assertEqual(add_stacked.status_code, 200)
        add_stacked_payload = add_stacked.get_json()
        self.assertTrue(add_stacked_payload["ok"])

        base_row = dict(self.db.get_position(base_position_id) or {})
        override = str(base_row.get("override_reason") or "")
        self.assertIn("column_alignment:left", override)
        self.assertNotIn("column_alignment:right", override)

    def test_bigtex_single_lower_stack_auto_stacks_when_no_explicit_placement(self):
        profile_id = self.db.create_access_profile("BT Auto Stack Tester")
        self._set_active_profile(profile_id)
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "bigtex",
            "53_step_deck",
            "BT Auto Stack Tester",
            "BT Auto Stack Session",
            created_by_profile_id=profile_id,
            created_by_name="BT Auto Stack Tester",
        )

        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, mcat, tier, model, bed_length, tongue, stack_height, total_footprint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("BT-AUTO-STACK", "utility", 1, "UT", 16.0, 4.0, 2.0, 20.0),
            )

        add_first = self.client.post(
            f"/prograde/api/session/{session_id}/add",
            json={"item_number": "BT-AUTO-STACK", "deck_zone": "lower_deck", "column_alignment": "left"},
        )
        self.assertEqual(add_first.status_code, 200)
        add_first_payload = add_first.get_json()
        self.assertTrue(add_first_payload["ok"])

        add_second = self.client.post(
            f"/prograde/api/session/{session_id}/add",
            json={"item_number": "BT-AUTO-STACK", "deck_zone": "lower_deck"},
        )
        self.assertEqual(add_second.status_code, 200)
        add_second_payload = add_second.get_json()
        self.assertTrue(add_second_payload["ok"])

        rows = [dict(r) for r in self.db.get_positions(session_id)]
        self.assertEqual(len(rows), 2)
        rows.sort(key=lambda r: int(r["layer"]))
        self.assertEqual(int(rows[0]["sequence"]), 1)
        self.assertEqual(int(rows[1]["sequence"]), 1)
        self.assertEqual(int(rows[1]["layer"]), 2)

    def test_bigtex_single_stack_same_side_insert_index_keeps_existing_stack(self):
        profile_id = self.db.create_access_profile("BT Lone Stack Insert Tester")
        self._set_active_profile(profile_id)
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "bigtex",
            "53_step_deck",
            "BT Lone Stack Insert Tester",
            "BT Lone Stack Insert Session",
            created_by_profile_id=profile_id,
            created_by_name="BT Lone Stack Insert Tester",
        )

        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, mcat, tier, model, bed_length, tongue, stack_height, total_footprint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("BT-LONE-STACK", "utility", 1, "UT", 16.0, 4.0, 2.0, 20.0),
            )

        first_add = self.client.post(
            f"/prograde/api/session/{session_id}/add",
            json={"item_number": "BT-LONE-STACK", "deck_zone": "lower_deck", "column_alignment": "left"},
        )
        self.assertEqual(first_add.status_code, 200)
        self.assertTrue((first_add.get_json() or {}).get("ok"))

        second_add = self.client.post(
            f"/prograde/api/session/{session_id}/add",
            json={
                "item_number": "BT-LONE-STACK",
                "deck_zone": "lower_deck",
                "insert_index": 1,
                "column_alignment": "left",
            },
        )
        self.assertEqual(second_add.status_code, 200)
        self.assertTrue((second_add.get_json() or {}).get("ok"))

        rows = [dict(r) for r in self.db.get_positions(session_id)]
        self.assertEqual(len(rows), 2)
        self.assertEqual({int(r["sequence"]) for r in rows}, {1})
        self.assertEqual(sorted(int(r["layer"]) for r in rows), [1, 2])

    def test_bigtex_single_stack_opposite_side_insert_creates_second_stack(self):
        profile_id = self.db.create_access_profile("BT Opposite Side Tester")
        self._set_active_profile(profile_id)
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "bigtex",
            "53_step_deck",
            "BT Opposite Side Tester",
            "BT Opposite Side Session",
            created_by_profile_id=profile_id,
            created_by_name="BT Opposite Side Tester",
        )

        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, mcat, tier, model, bed_length, tongue, stack_height, total_footprint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("BT-OPPOSITE-STACK", "utility", 1, "UT", 16.0, 4.0, 2.0, 20.0),
            )

        first_add = self.client.post(
            f"/prograde/api/session/{session_id}/add",
            json={"item_number": "BT-OPPOSITE-STACK", "deck_zone": "lower_deck", "column_alignment": "left"},
        )
        self.assertEqual(first_add.status_code, 200)
        self.assertTrue((first_add.get_json() or {}).get("ok"))

        second_add = self.client.post(
            f"/prograde/api/session/{session_id}/add",
            json={
                "item_number": "BT-OPPOSITE-STACK",
                "deck_zone": "lower_deck",
                "insert_index": 1,
                "column_alignment": "right",
            },
        )
        self.assertEqual(second_add.status_code, 200)
        self.assertTrue((second_add.get_json() or {}).get("ok"))

        rows = [dict(r) for r in self.db.get_positions(session_id)]
        self.assertEqual(len(rows), 2)
        self.assertEqual({int(r["sequence"]) for r in rows}, {1, 2})

    def test_bigtex_first_lower_deck_add_defaults_to_left_alignment(self):
        profile_id = self.db.create_access_profile("BT Default Left Tester")
        self._set_active_profile(profile_id)
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "bigtex",
            "53_step_deck",
            "BT Default Left Tester",
            "BT Default Left Session",
            created_by_profile_id=profile_id,
            created_by_name="BT Default Left Tester",
        )

        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, mcat, tier, model, bed_length, tongue, stack_height, total_footprint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("BT-DEFAULT-LEFT", "utility", 1, "UT", 16.0, 4.0, 2.0, 20.0),
            )

        add_resp = self.client.post(
            f"/prograde/api/session/{session_id}/add",
            json={"item_number": "BT-DEFAULT-LEFT", "deck_zone": "lower_deck"},
        )
        self.assertEqual(add_resp.status_code, 200)
        add_payload = add_resp.get_json() or {}
        self.assertTrue(add_payload.get("ok"))
        position_id = str(add_payload.get("position_id") or "")

        row = dict(self.db.get_position(position_id) or {})
        self.assertIn("column_alignment:left", str(row.get("override_reason") or ""))

    def test_bigtex_canvas_only_partial_refresh_renders_after_add(self):
        profile_id = self.db.create_access_profile("BT Canvas Refresh Tester")
        self._set_active_profile(profile_id)
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "bigtex",
            "53_step_deck",
            "BT Canvas Refresh Tester",
            "BT Canvas Refresh Session",
            created_by_profile_id=profile_id,
            created_by_name="BT Canvas Refresh Tester",
        )

        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, mcat, tier, model, bed_length, tongue, stack_height, total_footprint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("BT-CANVAS-REFRESH", "utility", 1, "UT", 16.0, 4.0, 2.0, 20.0),
            )

        add_resp = self.client.post(
            f"/prograde/api/session/{session_id}/add",
            json={"item_number": "BT-CANVAS-REFRESH", "deck_zone": "lower_deck", "include_state": False},
        )
        self.assertEqual(add_resp.status_code, 200)
        self.assertTrue((add_resp.get_json() or {}).get("ok"))

        refresh_resp = self.client.get(
            f"/prograde/session/{session_id}/load?pg_partial=1&pg_canvas_only=1",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(refresh_resp.status_code, 200)
        body = refresh_resp.get_data(as_text=True)
        self.assertIn('id="pg-canvas-column"', body)

    def test_bigtex_left_aligned_columns_skip_lower_deck_right_snap(self):
        profile_id = self.db.create_access_profile("BT Left Snap Tester")
        self._set_active_profile(profile_id)
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "bigtex",
            "53_step_deck",
            "BT Left Snap Tester",
            "BT Left Snap Session",
            created_by_profile_id=profile_id,
            created_by_name="BT Left Snap Tester",
        )

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
                ("53_step_deck", "generic", 53.0, 13.5, 41.5, 11.5, 3.5, 5.0, 39.0, "test step deck"),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, mcat, tier, model, bed_length, tongue, stack_height, total_footprint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("BT-LEFT-SNAP", "utility", 1, "UT", 16.0, 4.0, 2.0, 20.0),
            )

        left_position_id = str(uuid.uuid4())
        right_position_id = str(uuid.uuid4())
        self.db.add_position(
            position_id=left_position_id,
            session_id=session_id,
            brand="bigtex",
            item_number="BT-LEFT-SNAP",
            deck_zone="lower_deck",
            layer=1,
            sequence=1,
            override_reason="column_alignment:left",
            is_rotated=1,
        )
        self.db.add_position(
            position_id=right_position_id,
            session_id=session_id,
            brand="bigtex",
            item_number="BT-LEFT-SNAP",
            deck_zone="lower_deck",
            layer=1,
            sequence=2,
            is_rotated=1,
        )

        session_row = dict(self.db.get_session(session_id) or {})
        carrier_row = self.db.get_carrier_config("53_step_deck")
        zones = self.routes.brand_config.DECK_ZONES.get("bigtex", [])
        canvas = self.routes._build_canvas_data(
            session_id=session_id,
            session=session_row,
            carrier=carrier_row,
            zones=zones,
            positions=self.db.get_positions(session_id),
            brand="bigtex",
        )
        lower_positions = canvas.get("x_positions", {}).get("lower_deck", {}) or {}
        left_start = float(lower_positions.get(1) or 0.0)
        right_start = float(lower_positions.get(2) or 0.0)

        self.assertLess(left_start, 0.25)
        self.assertGreater(right_start, left_start)

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
