import os
import unittest

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module


class SchematicLayoutNormalizationTests(unittest.TestCase):
    def test_missing_units_are_repaired_as_single_lower_positions(self):
        units_by_id = {
            "ol100-u1": {"unit_id": "ol100-u1"},
            "ol200-u1": {"unit_id": "ol200-u1"},
            "ol300-u1": {"unit_id": "ol300-u1"},
        }
        layout = {
            "positions": [
                {"position_id": "p1", "deck": "lower", "unit_ids": ["ol100-u1"]},
            ]
        }

        normalized = app_module._normalize_edit_layout(layout, units_by_id, "COTT")
        positions = normalized.get("positions") or []
        flattened = [unit_id for pos in positions for unit_id in (pos.get("unit_ids") or [])]

        self.assertEqual(set(flattened), set(units_by_id.keys()))
        self.assertEqual(len(flattened), len(units_by_id))

        repaired_positions = [
            pos
            for pos in positions
            if (pos.get("unit_ids") or [None])[0] in {"ol200-u1", "ol300-u1"}
        ]
        self.assertTrue(repaired_positions)
        self.assertTrue(all((pos.get("deck") or "").lower() == "lower" for pos in repaired_positions))

    def test_duplicate_units_still_raise_validation_error(self):
        units_by_id = {
            "ol100-u1": {"unit_id": "ol100-u1"},
            "ol200-u1": {"unit_id": "ol200-u1"},
        }
        layout = {
            "positions": [
                {"position_id": "p1", "deck": "lower", "unit_ids": ["ol100-u1", "ol200-u1"]},
                {"position_id": "p2", "deck": "upper", "unit_ids": ["ol100-u1"]},
            ]
        }

        with self.assertRaises(ValueError):
            app_module._normalize_edit_layout(layout, units_by_id, "COTT")


if __name__ == "__main__":
    unittest.main()
