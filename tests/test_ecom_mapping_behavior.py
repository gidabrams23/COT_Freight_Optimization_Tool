import os
import unittest

import db

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module
from blueprints.cot import routes as cot_routes
from services.order_importer import OrderImporter


def _set_authenticated_session(client):
    profiles = db.list_access_profiles()
    assert profiles
    with client.session_transaction() as session_state:
        session_state[app_module.SESSION_PROFILE_ID_KEY] = profiles[0]["id"]


class EcomMappingBehaviorTests(unittest.TestCase):
    def test_unmapped_suggestions_for_ecom_prefer_non_ecom_category(self):
        original = cot_routes.db.list_sku_specs
        try:
            cot_routes.db.list_sku_specs = lambda: [
                {
                    "sku": "6X12CGR",
                    "description": "6X12CGR-ENCLOSED CARGO",
                    "category": "ECOM",
                    "length_with_tongue_ft": 18.0,
                    "max_stack_step_deck": 4,
                    "max_stack_flat_bed": 5,
                },
                {
                    "sku": "6X12CTD3K-BLUE",
                    "description": "6X12CGR ENCLOSED FLAT ROOF-3K",
                    "category": "CARGO",
                    "length_with_tongue_ft": 16.0,
                    "max_stack_step_deck": 1,
                    "max_stack_flat_bed": 1,
                },
            ]
            suggestions = cot_routes._build_unmapped_suggestions(
                [{"item": "6X12CGR", "desc": "6X12CGR-ENCLOSED CARGO", "bin": "ECOM"}]
            )
        finally:
            cot_routes.db.list_sku_specs = original

        self.assertEqual(len(suggestions), 1)
        suggested = suggestions[0].get("suggested") or {}
        self.assertEqual(suggested.get("sku"), "6X12CTD3K-BLUE")
        self.assertEqual((suggested.get("category") or "").upper(), "CARGO")

    def test_order_importer_remaps_ecom_direct_sku_to_non_ecom_candidate(self):
        importer = OrderImporter()
        importer.sku_lookup = {"exact": {}, "patterns": {}}
        importer.sku_specs = {
            "6X12CGR": {
                "sku": "6X12CGR",
                "description": "6X12CGR-ENCLOSED CARGO",
                "category": "ECOM",
                "length_with_tongue_ft": 18.0,
                "max_stack_step_deck": 4,
                "max_stack_flat_bed": 5,
            },
            "6X12CTD3K-BLUE": {
                "sku": "6X12CTD3K-BLUE",
                "description": "6X12CGR ENCLOSED FLAT ROOF-3K",
                "category": "CARGO",
                "length_with_tongue_ft": 16.0,
                "max_stack_step_deck": 1,
                "max_stack_flat_bed": 1,
            },
        }

        resolved = importer.lookup_sku("6X12CGR", plant="GA", bin_code="ECOM", bin_raw="ECOM")
        self.assertEqual(resolved, "6X12CTD3K-BLUE")

    def test_source_led_save_accepts_category_update(self):
        test_sku = "ZZZ-ECOM-CAT-EDIT-TEST"
        db.upsert_sku_spec(
            {
                "sku": test_sku,
                "description": "test",
                "category": "ECOM",
                "length_with_tongue_ft": 12.0,
                "max_stack_step_deck": 1,
                "max_stack_flat_bed": 1,
                "notes": "",
                "source": "planner",
            }
        )

        client = app_module.app.test_client()
        _set_authenticated_session(client)
        response = client.post(
            "/skus/source-led/save",
            json={
                "sku": test_sku,
                "category": "CARGO",
                "length_with_tongue_ft": 12.0,
                "max_stack_step_deck": 1,
                "max_stack_flat_bed": 1,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json() or {}
        self.assertTrue(payload.get("ok"))
        self.assertEqual((payload.get("category") or "").upper(), "CARGO")

        refreshed = db.get_sku_spec_by_sku(test_sku) or {}
        self.assertEqual((refreshed.get("category") or "").upper(), "CARGO")

        if refreshed.get("id"):
            db.delete_sku_spec(int(refreshed["id"]))


if __name__ == "__main__":
    unittest.main()
