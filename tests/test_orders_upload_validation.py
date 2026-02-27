import io
import os
import unittest

import db

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module


def _set_authenticated_session(client):
    profiles = db.list_access_profiles()
    assert profiles
    with client.session_transaction() as session_state:
        session_state[app_module.SESSION_PROFILE_ID_KEY] = profiles[0]["id"]


class OrdersUploadValidationTests(unittest.TestCase):
    def test_upload_blocks_when_unmapped_skus_exist(self):
        client = app_module.app.test_client()
        _set_authenticated_session(client)

        csv_body = (
            "shipvia,plant,item,qty,state,zip,bin,sonum,cname\n"
            "2026-02-01,ATL,ZZZ_UNMAPPED_TEST_ITEM_999,1,GA,30301,GEN,SO-UNMAPPED-VALIDATION-1,Test Customer\n"
        )
        response = client.post(
            "/api/orders/upload",
            data={"file": (io.BytesIO(csv_body.encode("utf-8")), "orders.csv")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.get_json() or {}
        self.assertTrue(payload.get("blocked"))
        self.assertGreater(payload.get("unmapped_count", 0), 0)
        self.assertIn("Upload blocked", payload.get("error", ""))
        self.assertIsInstance(payload.get("unmapped_items"), list)
        self.assertGreaterEqual(len(payload.get("unmapped_items")), 1)

        persisted = db.list_orders_by_so_nums_any(["SO-UNMAPPED-VALIDATION-1"])
        self.assertEqual(persisted, [])


if __name__ == "__main__":
    unittest.main()
