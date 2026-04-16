import csv
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ApplySeedSnapshotsItemLookupTests(unittest.TestCase):
    def test_apply_seed_snapshots_supports_item_lookup_table(self):
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            db_path = tmp_path / "app.db"
            seed_dir = tmp_path / "seed"
            seed_dir.mkdir(parents=True, exist_ok=True)

            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE item_sku_lookup (
                        id INTEGER PRIMARY KEY,
                        plant TEXT NOT NULL,
                        bin TEXT NOT NULL,
                        item_pattern TEXT NOT NULL,
                        sku TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(plant, bin, item_pattern)
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()

            with (seed_dir / "item_sku_lookup.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["plant", "bin", "item_pattern", "sku", "created_at"])
                writer.writerow(["BT", "HDEQ", "7X14%", "7X14GWHS", "2026-04-16T00:00:00"])

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/apply_seed_snapshots.py",
                    "--db-path",
                    str(db_path),
                    "--seed-dir",
                    str(seed_dir),
                    "--tables",
                    "item_sku_lookup",
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("item_sku_lookup: upserted 1 rows", result.stdout)

            connection = sqlite3.connect(db_path)
            try:
                row = connection.execute(
                    "SELECT plant, bin, item_pattern, sku FROM item_sku_lookup"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(row, ("BT", "HDEQ", "7X14%", "7X14GWHS"))


if __name__ == "__main__":
    unittest.main()
