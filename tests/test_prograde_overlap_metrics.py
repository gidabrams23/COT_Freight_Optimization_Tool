import sqlite3
import unittest

from blueprints.prograde.services.bt_rules import _bt_total_length, compute_bt_length_metrics
from blueprints.prograde.services.pj_rules import compute_pj_length_metrics


class ProgradeOverlapMetricTests(unittest.TestCase):
    def _sqlite_rows(self, rows):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE positions (
                item_number TEXT,
                deck_zone TEXT,
                sequence INTEGER,
                layer INTEGER,
                is_rotated INTEGER,
                is_nested INTEGER
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO positions (item_number, deck_zone, sequence, layer, is_rotated, is_nested)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        fetched = conn.execute("SELECT * FROM positions ORDER BY sequence, layer").fetchall()
        conn.close()
        return fetched

    def test_pj_metrics_accept_sqlite_row_objects(self):
        positions = self._sqlite_rows(
            [
                ("PJ-L-1", "lower_deck", 1, 1, 1, 0),
                ("PJ-U-1", "upper_deck", 1, 1, 0, 0),
            ]
        )
        skus = {
            "PJ-L-1": {"total_footprint": 25.0, "tongue_feet": 4.0, "pj_category": "utility", "model": "UL"},
            "PJ-U-1": {"total_footprint": 20.0, "tongue_feet": 6.0, "pj_category": "utility", "model": "UL"},
        }
        metrics = compute_pj_length_metrics(positions, skus=skus, offsets={"gn_in_dump_hidden_ft": 7.0})

        self.assertEqual(metrics["legacy_total_ft"], 45.0)
        self.assertEqual(metrics["overlap_credit_ft"], 4.0)
        self.assertEqual(metrics["effective_total_ft"], 41.0)
        self.assertEqual(metrics["blocked_lower_ft"], 4.0)
        self.assertEqual(metrics["blocked_upper_ft"], 0.0)

    def test_bigtex_metrics_and_length_rule_use_seam_overlap(self):
        positions = [
            {
                "position_id": "L1",
                "item_number": "BT-L-1",
                "deck_zone": "lower_deck",
                "sequence": 1,
                "layer": 1,
                "is_rotated": 1,
            },
            {
                "position_id": "U1",
                "item_number": "BT-U-1",
                "deck_zone": "upper_deck",
                "sequence": 1,
                "layer": 1,
                "is_rotated": 0,
            },
        ]
        sku_map = {
            "BT-L-1": {"total_footprint": 25.0, "tongue": 4.0},
            "BT-U-1": {"total_footprint": 20.0, "tongue": 6.0},
        }
        metrics = compute_bt_length_metrics(positions, sku_map=sku_map)
        self.assertEqual(metrics["legacy_total_ft"], 45.0)
        self.assertEqual(metrics["overlap_credit_ft"], 4.0)
        self.assertEqual(metrics["effective_total_ft"], 41.0)
        self.assertEqual(metrics["adjusted_lower_usage_ft"], 29.0)
        self.assertEqual(metrics["adjusted_upper_usage_ft"], 20.0)

        violations = _bt_total_length(
            positions=positions,
            sku_map=sku_map,
            carrier={"lower_deck_length_ft": 26.0, "upper_deck_length_ft": 12.0},
            _stack_configs={},
        )
        self.assertGreaterEqual(len(violations), 1)
        self.assertTrue(all(v.rule_code == "BT_TOTAL_LENGTH" for v in violations))
        self.assertTrue(any("Lower Deck" in v.message for v in violations))
        self.assertTrue(any("blocked at step seam" in v.message for v in violations))

    def test_pj_low_profile_lower_column_gets_step_clearance_credit(self):
        positions = self._sqlite_rows(
            [
                ("PJ-L-LOW", "lower_deck", 1, 1, 0, 0),
                ("PJ-U-OVR", "upper_deck", 1, 1, 0, 0),
            ]
        )
        skus = {
            "PJ-L-LOW": {"total_footprint": 25.0, "tongue_feet": 0.0, "pj_category": "utility", "model": "UL"},
            "PJ-U-OVR": {"total_footprint": 20.0, "tongue_feet": 0.0, "pj_category": "utility", "model": "UL"},
        }
        height_ref = {"utility": {"height_mid_ft": 1.25, "height_top_ft": 1.25, "gn_axle_dropped_ft": 1.0}}

        metrics = compute_pj_length_metrics(
            positions,
            skus=skus,
            offsets={"gn_in_dump_hidden_ft": 7.0},
            lower_cap_ft=41.0,
            upper_cap_ft=12.0,
            height_ref=height_ref,
            step_gap_ft=1.5,
        )

        self.assertEqual(metrics["upper_seam_span_ft"], 8.0)
        self.assertEqual(metrics["seam_clearance_credit_ft"], 8.0)
        self.assertEqual(metrics["blocked_lower_ft"], 0.0)
        self.assertEqual(metrics["effective_total_ft"], 37.0)

    def test_bigtex_low_profile_lower_column_gets_step_clearance_credit(self):
        positions = [
            {
                "position_id": "L1",
                "item_number": "BT-L-LOW",
                "deck_zone": "lower_deck",
                "sequence": 1,
                "layer": 1,
                "is_rotated": 0,
            },
            {
                "position_id": "U1",
                "item_number": "BT-U-OVR",
                "deck_zone": "upper_deck",
                "sequence": 1,
                "layer": 1,
                "is_rotated": 1,
            },
        ]
        sku_map = {
            "BT-L-LOW": {"total_footprint": 25.0, "tongue": 0.0, "stack_height": 1.25},
            "BT-U-OVR": {"total_footprint": 20.0, "tongue": 0.0, "stack_height": 2.0},
        }

        metrics = compute_bt_length_metrics(
            positions,
            sku_map=sku_map,
            lower_cap_ft=41.0,
            upper_cap_ft=12.0,
            step_gap_ft=1.5,
        )

        self.assertEqual(metrics["upper_seam_span_ft"], 8.0)
        self.assertEqual(metrics["seam_clearance_credit_ft"], 8.0)
        self.assertEqual(metrics["blocked_lower_ft"], 0.0)
        self.assertEqual(metrics["adjusted_lower_usage_ft"], 25.0)
        self.assertEqual(metrics["effective_total_ft"], 37.0)


if __name__ == "__main__":
    unittest.main()
