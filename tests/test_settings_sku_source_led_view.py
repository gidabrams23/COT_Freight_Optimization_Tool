import os
import unittest

import db

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module
from blueprints.cot import routes as cot_routes


def _set_authenticated_session(client):
    profiles = db.list_access_profiles()
    assert profiles
    with client.session_transaction() as session_state:
        session_state[app_module.SESSION_PROFILE_ID_KEY] = profiles[0]["id"]


class SettingsSkuSourceLedViewTests(unittest.TestCase):
    def test_settings_sku_tab_renders_source_led_section(self):
        client = app_module.app.test_client()
        _set_authenticated_session(client)

        response = client.get("/settings?tab=skus")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Source-Led Cheat Sheet", body)
        self.assertIn("Item Num", body)
        self.assertIn("Item Desc", body)
        self.assertIn("Unique Mapped SKUs", body)
        self.assertIn("+ Columns", body)
        self.assertNotIn("<th>Plant</th>", body)

    def test_full_sku_list_sorts_newest_added_first_and_shows_added_timestamp(self):
        older_sku = "ZZZ-SKU-OLDER-ADDED-TEST"
        newer_sku = "ZZZ-SKU-NEWER-ADDED-TEST"
        older_label = cot_routes._format_est_datetime_label("2026-06-01T12:00:00")
        newer_label = cot_routes._format_est_datetime_label("2026-06-03T18:15:00")

        def _delete_sku(sku):
            existing = db.get_sku_spec_by_sku(sku)
            if existing and existing.get("id"):
                db.delete_sku_spec(int(existing["id"]))

        _delete_sku(older_sku)
        _delete_sku(newer_sku)
        try:
            db.upsert_sku_spec(
                {
                    "sku": older_sku,
                    "description": "Older added row",
                    "category": "USA",
                    "length_with_tongue_ft": 10.0,
                    "max_stack_step_deck": 1,
                    "max_stack_flat_bed": 1,
                    "notes": "",
                    "source": "planner",
                    "added_at": "2026-06-01T12:00:00",
                }
            )
            db.upsert_sku_spec(
                {
                    "sku": newer_sku,
                    "description": "Newer added row",
                    "category": "CARGO",
                    "length_with_tongue_ft": 12.0,
                    "max_stack_step_deck": 2,
                    "max_stack_flat_bed": 2,
                    "notes": "",
                    "source": "planner",
                    "added_at": "2026-06-03T18:15:00",
                }
            )

            client = app_module.app.test_client()
            _set_authenticated_session(client)

            response = client.get("/settings?tab=skus")

            self.assertEqual(response.status_code, 200)
            body = response.get_data(as_text=True)
            self.assertIn("Source</th>", body)
            self.assertIn(older_label, body)
            self.assertIn(newer_label, body)
            self.assertLess(body.index(newer_sku), body.index(older_sku))
        finally:
            _delete_sku(older_sku)
            _delete_sku(newer_sku)


if __name__ == "__main__":
    unittest.main()
