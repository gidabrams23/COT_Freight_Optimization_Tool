import importlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path


class ProgradeDbPathResolutionTests(unittest.TestCase):
    def setUp(self):
        self._env_keys = (
            "PROGRADE_DB_PATH",
            "APP_DB_PATH",
            "RENDER",
            "RENDER_SERVICE_ID",
            "WEBSITE_HOSTNAME",
            "PROGRADE_BOOTSTRAP_FROM_ARCHIVE_DB",
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
        import blueprints.prograde.db as prograde_db

        return importlib.reload(prograde_db)

    def test_explicit_prograde_db_path_wins(self):
        self._clear_env()
        explicit_path = Path(tempfile.gettempdir()) / "explicit-prograde.db"
        os.environ["PROGRADE_DB_PATH"] = str(explicit_path)
        os.environ["APP_DB_PATH"] = str(Path(tempfile.gettempdir()) / "app.db")

        module = self._reload_module()

        self.assertEqual(module.DB_PATH, explicit_path)

    def test_uses_app_db_sibling_when_prograde_path_missing(self):
        self._clear_env()
        app_db_path = Path(tempfile.gettempdir()) / "app.db"
        os.environ["APP_DB_PATH"] = str(app_db_path)

        module = self._reload_module()

        self.assertEqual(module.DB_PATH, app_db_path.with_name("prograde.db"))

    def test_uses_render_default_when_no_db_paths_set(self):
        self._clear_env()
        os.environ["RENDER"] = "1"

        module = self._reload_module()

        self.assertEqual(module.DB_PATH, Path("/var/data/prograde.db"))

    def test_uses_azure_default_when_no_db_paths_set(self):
        self._clear_env()
        os.environ["WEBSITE_HOSTNAME"] = "example.azurewebsites.net"

        module = self._reload_module()

        self.assertEqual(module.DB_PATH, Path("/home/site/prograde.db"))

    def test_missing_db_touches_empty_file_without_archive_bootstrap_flag(self):
        self._clear_env()
        temp_dir = Path(tempfile.mkdtemp(prefix="prograde-db-path-test-"))
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        db_path = temp_dir / "prograde.db"
        os.environ["PROGRADE_DB_PATH"] = str(db_path)

        module = self._reload_module()

        self.assertFalse(db_path.exists())
        module._ensure_db_file()
        self.assertTrue(db_path.exists())
        self.assertEqual(db_path.stat().st_size, 0)
