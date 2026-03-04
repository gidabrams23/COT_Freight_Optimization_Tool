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


class SettingsPlantTrailerDefaultsTests(unittest.TestCase):
    def test_settings_overview_renders_plant_trailer_defaults_table(self):
        client = app_module.app.test_client()
        _set_authenticated_session(client)

        response = client.get("/settings?tab=overview")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Plant Trailer Defaults", body)
        self.assertIn("name=\"plant_default_trailer_NV\"", body)
        self.assertIn("name=\"plant_auto_hotshot_enabled_NV\"", body)


if __name__ == "__main__":
    unittest.main()
