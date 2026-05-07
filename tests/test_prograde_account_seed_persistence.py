import importlib
import os
import tempfile
import unittest
from pathlib import Path


class ProgradeAccountSeedPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "prograde_seed_accounts_test.db"
        self._seed_path = Path(self._tmpdir.name) / "prograde_access_profiles.csv"
        self._seed_path.write_text(
            "\n".join(
                [
                    "name,is_admin",
                    "Planner One,0",
                    "Planner Two,0",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        self._previous_db_path = os.environ.get("PROGRADE_DB_PATH")
        self._previous_seed_path = os.environ.get("PROGRADE_ACCESS_PROFILES_SEED_PATH")
        os.environ["PROGRADE_DB_PATH"] = str(self._db_path)
        os.environ["PROGRADE_ACCESS_PROFILES_SEED_PATH"] = str(self._seed_path)

        import blueprints.prograde.db as prograde_db

        self.db = importlib.reload(prograde_db)

    def tearDown(self):
        if self._previous_db_path is None:
            os.environ.pop("PROGRADE_DB_PATH", None)
        else:
            os.environ["PROGRADE_DB_PATH"] = self._previous_db_path

        if self._previous_seed_path is None:
            os.environ.pop("PROGRADE_ACCESS_PROFILES_SEED_PATH", None)
        else:
            os.environ["PROGRADE_ACCESS_PROFILES_SEED_PATH"] = self._previous_seed_path

        try:
            self._tmpdir.cleanup()
        except PermissionError:
            pass

    def test_init_db_seeds_prograde_access_profiles_csv(self):
        self.db.init_db()
        rows = list(self.db.list_access_profiles())
        names = {str(row["name"]) for row in rows}
        self.assertIn("Planner One", names)
        self.assertIn("Planner Two", names)
        planner_one = next((row for row in rows if str(row["name"]) == "Planner One"), None)
        self.assertIsNotNone(planner_one)
        self.assertEqual((planner_one["default_brand"] or "").lower(), "bigtex")

        count_before = len(list(self.db.list_access_profiles()))
        self.db.init_db()
        count_after = len(list(self.db.list_access_profiles()))
        self.assertEqual(count_before, count_after)


if __name__ == "__main__":
    unittest.main()
