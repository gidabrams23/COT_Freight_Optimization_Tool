import importlib
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from flask import Flask


class ProgradeInventoryGapFinderTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "prograde_inventory_gap_test.db"
        self._previous_db_path = os.environ.get("PROGRADE_DB_PATH")
        os.environ["PROGRADE_DB_PATH"] = str(self._db_path)

        import blueprints.prograde.db as prograde_db
        import blueprints.prograde.routes as prograde_routes
        import blueprints.prograde.services.inventory_gap_finder as gap_finder

        self.db = importlib.reload(prograde_db)
        self.routes = importlib.reload(prograde_routes)
        self.gap_finder = importlib.reload(gap_finder)
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

    def _activate_profile(self, name="Gap Tester"):
        profile_id = int(self.db.create_access_profile(name=name, is_admin=False))
        with self.client.session_transaction() as sess:
            sess["prograde_profile_id"] = profile_id
        return profile_id

    def _create_session(self, brand, planner_name="Gap Tester"):
        profile_id = int(self.db.create_access_profile(name=f"{planner_name}-{uuid.uuid4().hex[:6]}", is_admin=False))
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            brand,
            "53_step_deck",
            planner_name,
            f"{brand}-gap",
            created_by_profile_id=profile_id,
            created_by_name=planner_name,
        )
        return session_id

    def _build_canvas_and_carrier(self, session_id):
        session_row = self.db.get_session(session_id)
        session = dict(session_row)
        carrier = self.db.get_carrier_config(session["carrier_type"])
        zones = ["lower_deck", "upper_deck"]
        positions = self.db.get_positions(session_id)
        canvas = self.routes._build_canvas_data(
            session_id,
            session,
            carrier,
            zones,
            positions,
            session["brand"],
        )
        return session, carrier, canvas

    def _insert_bigtex_sku(self, item_number, *, model, mcat, total_footprint, stack_height):
        now = "2026-04-13T00:00:00"
        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bigtex_skus
                (item_number, model, mcat, bed_length, tongue, total_footprint, stack_height, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_number,
                    model,
                    mcat,
                    float(total_footprint) - 2.0,
                    2.0,
                    float(total_footprint),
                    float(stack_height),
                    now,
                ),
            )

    def _insert_bt_snapshot(self, item_number, *, available_count, total_count=None):
        now = "2026-04-13T00:00:00"
        total = int(total_count if total_count is not None else available_count)
        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bt_inventory_snapshot
                (item_number, total_count, available_count, assigned_count, built_count, future_build_count,
                 available_built_count, available_future_count, updated_at)
                VALUES (?, ?, ?, 0, 0, 0, 0, 0, ?)
                """,
                (
                    item_number,
                    total,
                    int(available_count),
                    now,
                ),
            )

    def _insert_bt_snapshot_whse(self, item_number, *, whse_code, available_count, total_count=None):
        now = "2026-04-13T00:00:00"
        total = int(total_count if total_count is not None else available_count)
        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bt_inventory_snapshot_whse
                (item_number, whse_code, total_count, available_count, assigned_count, built_count,
                 future_build_count, available_built_count, available_future_count, updated_at)
                VALUES (?, ?, ?, ?, 0, 0, 0, 0, 0, ?)
                """,
                (
                    item_number,
                    whse_code,
                    total,
                    int(available_count),
                    now,
                ),
            )

    def _insert_pj_sku(
        self,
        item_number,
        *,
        model,
        category,
        bed_length_measured,
        tongue_feet,
        total_footprint,
    ):
        now = "2026-04-13T00:00:00"
        with self.db.get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pj_skus
                (item_number, model, pj_category, description, bed_length_stated, bed_length_measured,
                 tongue_feet, total_footprint, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_number,
                    model,
                    category,
                    f"{model} test",
                    float(bed_length_measured),
                    float(bed_length_measured),
                    float(tongue_feet),
                    float(total_footprint),
                    now,
                ),
            )

    def _get_gap_data(self, session_id, *, bt_whse=""):
        session, carrier, canvas = self._build_canvas_and_carrier(session_id)
        return self.gap_finder.build_inventory_gap_data(
            session_id=session_id,
            brand=session["brand"],
            carrier=carrier,
            canvas=canvas,
            bt_whse=bt_whse,
        )

    def test_bt_prefers_stack_top_when_vertical_fit_is_best(self):
        session_id = self._create_session("bigtex")
        self._insert_bigtex_sku("BT-BASE-10", model="B10", mcat="utility", total_footprint=10.0, stack_height=2.0)
        self._insert_bigtex_sku("BT-CAND-TOP", model="CTOP", mcat="utility", total_footprint=7.0, stack_height=1.0)
        self._insert_bt_snapshot("BT-CAND-TOP", available_count=10)

        self.db.add_position(
            position_id=str(uuid.uuid4()),
            session_id=session_id,
            brand="bigtex",
            item_number="BT-BASE-10",
            deck_zone="lower_deck",
            layer=1,
            sequence=1,
        )

        gap_data = self._get_gap_data(session_id)
        rows = {row["item_number"]: row for row in gap_data["rows"]}
        row = rows["BT-CAND-TOP"]
        matching_fits = [fit for fit in row["stack_fits"] if fit.get("fits")]

        self.assertGreaterEqual(len(gap_data["stack_slots"]), 1)
        self.assertTrue(row["fits_gap"])
        self.assertGreaterEqual(len(matching_fits), 1)
        self.assertEqual(matching_fits[0]["target_sequence"], 1)
        self.assertGreaterEqual(matching_fits[0]["suggested_qty"], 1)

    def test_bt_no_longer_falls_back_to_horizontal_fit(self):
        session_id = self._create_session("bigtex")
        self._insert_bigtex_sku("BT-TALL-BASE", model="TALL", mcat="utility", total_footprint=10.0, stack_height=10.0)
        self._insert_bigtex_sku("BT-CAND-HZ", model="HZ", mcat="utility", total_footprint=5.0, stack_height=2.0)
        self._insert_bt_snapshot("BT-CAND-HZ", available_count=4)

        self.db.add_position(
            position_id=str(uuid.uuid4()),
            session_id=session_id,
            brand="bigtex",
            item_number="BT-TALL-BASE",
            deck_zone="lower_deck",
            layer=1,
            sequence=1,
        )

        gap_data = self._get_gap_data(session_id)
        rows = {row["item_number"]: row for row in gap_data["rows"]}
        row = rows["BT-CAND-HZ"]

        self.assertFalse(row["fits_gap"])
        self.assertFalse(any(fit.get("fits") for fit in row["stack_fits"]))

    def test_bt_dimension_fit_ignores_additional_constraint_signatures(self):
        session_id = self._create_session("bigtex")
        self._insert_bigtex_sku("BT-LOW-26", model="L26", mcat="utility", total_footprint=26.0, stack_height=1.0)
        self._insert_bigtex_sku("BT-UP-20", model="U20", mcat="utility", total_footprint=20.0, stack_height=1.0)
        self._insert_bigtex_sku("BT-ERR-CAND", model="EC2", mcat="utility", total_footprint=2.0, stack_height=1.0)
        self._insert_bt_snapshot("BT-ERR-CAND", available_count=8)

        self.db.add_position(
            position_id=str(uuid.uuid4()),
            session_id=session_id,
            brand="bigtex",
            item_number="BT-LOW-26",
            deck_zone="lower_deck",
            layer=1,
            sequence=1,
        )
        self.db.add_position(
            position_id=str(uuid.uuid4()),
            session_id=session_id,
            brand="bigtex",
            item_number="BT-UP-20",
            deck_zone="upper_deck",
            layer=1,
            sequence=1,
        )

        gap_data = self._get_gap_data(session_id)
        rows = {row["item_number"]: row for row in gap_data["rows"]}
        row = rows["BT-ERR-CAND"]

        self.assertTrue(row["fits_gap"])
        self.assertTrue(any(fit.get("fits") for fit in row["stack_fits"]))

    def test_pj_utility_vertical_fit_uses_top_mid_stacking_deltas(self):
        session_id = self._create_session("pj")
        self._insert_pj_sku(
            "PJ-U40",
            model="U4",
            category="utility",
            bed_length_measured=21.0,
            tongue_feet=4.0,
            total_footprint=25.0,
        )
        self.db.update_carrier_config("53_step_deck", "max_height_ft", 6.8)  # lower clearance = 3.3

        self.db.add_position(
            position_id=str(uuid.uuid4()),
            session_id=session_id,
            brand="pj",
            item_number="PJ-U40",
            deck_zone="lower_deck",
            layer=1,
            sequence=1,
        )

        gap_data = self._get_gap_data(session_id)
        rows = {row["item_number"]: row for row in gap_data["rows"]}
        row = rows["PJ-U40"]
        matching_fits = [fit for fit in row["stack_fits"] if fit.get("fits")]

        self.assertTrue(row["fits_gap"])
        self.assertGreaterEqual(len(matching_fits), 1)
        self.assertEqual(matching_fits[0]["target_sequence"], 1)
        self.assertEqual(matching_fits[0]["suggested_qty"], 1)

    def test_pj_rejects_candidates_that_violate_constraints(self):
        session_id = self._create_session("pj")
        self._insert_pj_sku(
            "PJ-D5-TEST",
            model="D5",
            category="dump_small",
            bed_length_measured=6.0,
            tongue_feet=4.0,
            total_footprint=10.0,
        )

        gap_data = self._get_gap_data(session_id)
        rows = {row["item_number"]: row for row in gap_data["rows"]}
        row = rows["PJ-D5-TEST"]

        self.assertFalse(row["fits_gap"])
        self.assertFalse(any(fit.get("fits") for fit in row["stack_fits"]))

    def test_bt_inventory_gap_supports_warehouse_filter(self):
        session_id = self._create_session("bigtex")
        self._insert_bigtex_sku("BT-WHSE-1", model="WH1", mcat="utility", total_footprint=12.0, stack_height=1.0)
        self._insert_bt_snapshot("BT-WHSE-1", available_count=7)
        self._insert_bt_snapshot_whse("BT-WHSE-1", whse_code="501", available_count=2)
        self._insert_bt_snapshot_whse("BT-WHSE-1", whse_code="601", available_count=5)

        all_data = self._get_gap_data(session_id)
        whse_501_data = self._get_gap_data(session_id, bt_whse="501")
        whse_601_data = self._get_gap_data(session_id, bt_whse="601")

        all_row = {row["item_number"]: row for row in all_data["rows"]}["BT-WHSE-1"]
        row_501 = {row["item_number"]: row for row in whse_501_data["rows"]}["BT-WHSE-1"]
        row_601 = {row["item_number"]: row for row in whse_601_data["rows"]}["BT-WHSE-1"]

        self.assertEqual(all_row["available_count"], 7)
        self.assertEqual(row_501["available_count"], 2)
        self.assertEqual(row_601["available_count"], 5)
        self.assertEqual(whse_501_data["selected_warehouse"], "501")
        self.assertEqual(whse_601_data["selected_warehouse"], "601")
        self.assertEqual(all_data["selected_warehouse"], "ALL")
        self.assertEqual(
            [opt["value"] for opt in all_data["warehouse_options"]],
            ["ALL", "501", "601"],
        )

    def test_bt_load_page_keeps_upload_controls(self):
        profile_id = self._activate_profile()
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "bigtex",
            "53_step_deck",
            "Gap Tester",
            "bt-page",
            created_by_profile_id=profile_id,
            created_by_name="Gap Tester",
        )
        self._insert_bigtex_sku("BT-PAGE-1", model="P1", mcat="utility", total_footprint=12.0, stack_height=1.0)
        self._insert_bt_snapshot("BT-PAGE-1", available_count=3)
        self.db.add_position(
            position_id=str(uuid.uuid4()),
            session_id=session_id,
            brand="bigtex",
            item_number="BT-PAGE-1",
            deck_zone="lower_deck",
            layer=1,
            sequence=1,
        )

        resp = self.client.get(f"/prograde/session/{session_id}/load")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("BT Inventory Gap Finder", html)
        self.assertIn("Upload Inventory", html)
        self.assertIn("Stack 1 Fit", html)
        self.assertIn("remaining height", html)

    def test_pj_load_page_uses_catalog_mode_and_tongue_metadata(self):
        profile_id = self._activate_profile(name="PJ Gap Tester")
        session_id = str(uuid.uuid4())
        self.db.create_session(
            session_id,
            "pj",
            "53_step_deck",
            "PJ Gap Tester",
            "pj-page",
            created_by_profile_id=profile_id,
            created_by_name="PJ Gap Tester",
        )
        self._insert_pj_sku(
            "PJ-PAGE-U1",
            model="U1",
            category="utility",
            bed_length_measured=12.0,
            tongue_feet=4.0,
            total_footprint=16.0,
        )

        resp = self.client.get(f"/prograde/session/{session_id}/load")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("PJ Inventory Gap Finder", html)
        self.assertNotIn("Upload Orders", html)
        self.assertIn("Available Qty", html)


if __name__ == "__main__":
    unittest.main()
