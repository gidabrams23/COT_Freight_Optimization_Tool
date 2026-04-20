import importlib
import os
import unittest
from pathlib import Path


class AppDbPathResolutionTests(unittest.TestCase):
    def setUp(self):
        self._env_keys = (
            "APP_DB_PATH",
            "RENDER",
            "RENDER_SERVICE_ID",
            "WEBSITE_HOSTNAME",
        )
        self._previous_env = {key: os.environ.get(key) for key in self._env_keys}

    def tearDown(self):
        for key, value in self._previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _clear_env(self):
        for key in self._env_keys:
            os.environ.pop(key, None)

    @staticmethod
    def _reload_module():
        import db as app_db

        return importlib.reload(app_db)

    def test_app_db_path_explicit_env_wins(self):
        self._clear_env()
        explicit_path = Path("C:/temp/explicit-app.db")
        os.environ["APP_DB_PATH"] = str(explicit_path)

        module = self._reload_module()

        self.assertEqual(module.DB_PATH, explicit_path)

    def test_defaults_to_render_path_when_render_env_present(self):
        self._clear_env()
        os.environ["RENDER"] = "1"

        module = self._reload_module()

        self.assertEqual(module.DB_PATH, Path("/var/data/app.db"))

    def test_defaults_to_azure_path_when_azure_env_present(self):
        self._clear_env()
        os.environ["WEBSITE_HOSTNAME"] = "example.azurewebsites.net"

        module = self._reload_module()

        self.assertEqual(module.DB_PATH, Path("/home/site/app.db"))

