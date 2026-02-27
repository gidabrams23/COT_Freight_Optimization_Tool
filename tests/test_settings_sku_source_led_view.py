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


if __name__ == "__main__":
    unittest.main()
