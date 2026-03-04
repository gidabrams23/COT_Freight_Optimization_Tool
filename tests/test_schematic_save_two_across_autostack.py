import unittest

import app as app_module


class SchematicSaveTwoAcrossAutoStackTests(unittest.TestCase):
    def _assumptions(self):
        return {
            "stack_overflow_max_height": 5,
            "max_back_overhang_ft": 4.0,
            "upper_two_across_max_length_ft": 7.0,
            "upper_deck_exception_max_length_ft": 16.0,
            "upper_deck_exception_overhang_allowance_ft": 6.0,
            "upper_deck_exception_categories": ["USA", "UTA"],
        }

    def _unit(self, unit_id, stop_sequence, length_ft=5.0):
        return {
            "unit_id": unit_id,
            "order_id": f"SO-{stop_sequence}",
            "order_line_id": int(stop_sequence),
            "sku": f"SKU-{unit_id}",
            "item": f"ITEM-{unit_id}",
            "item_desc": "Item",
            "unit_length_ft": float(length_ft),
            "max_stack": 1,
            "upper_max_stack": 1,
            "category": "CARGO",
            "stop_sequence": int(stop_sequence),
            "color": "#334155",
        }

    def test_auto_stacks_upper_two_across_by_stop_sequence_on_save(self):
        layout = {"positions": [{"position_id": "p1", "deck": "upper", "unit_ids": ["u1", "u2", "u3"]}]}
        units_by_id = {
            "u1": self._unit("u1", stop_sequence=1, length_ft=5.0),
            "u2": self._unit("u2", stop_sequence=2, length_ft=5.0),
            "u3": self._unit("u3", stop_sequence=3, length_ft=5.0),
        }

        adjusted = app_module._auto_stack_upper_two_across_layout(
            layout,
            units_by_id,
            "STEP_DECK",
            assumptions=self._assumptions(),
        )

        self.assertEqual(
            (adjusted.get("positions") or [])[0].get("unit_ids"),
            ["u3", "u2", "u1"],
        )

    def test_auto_stacking_keeps_longer_units_below_shorter_units(self):
        layout = {"positions": [{"position_id": "p1", "deck": "upper", "unit_ids": ["u1", "u2", "u3"]}]}
        units_by_id = {
            "u1": self._unit("u1", stop_sequence=1, length_ft=5.0),
            "u2": self._unit("u2", stop_sequence=3, length_ft=7.0),
            "u3": self._unit("u3", stop_sequence=2, length_ft=5.0),
        }

        adjusted = app_module._auto_stack_upper_two_across_layout(
            layout,
            units_by_id,
            "STEP_DECK",
            assumptions=self._assumptions(),
        )
        adjusted_ids = (adjusted.get("positions") or [])[0].get("unit_ids") or []
        adjusted_lengths = [float((units_by_id.get(unit_id) or {}).get("unit_length_ft") or 0.0) for unit_id in adjusted_ids]

        self.assertEqual(adjusted_ids[0], "u2")
        self.assertEqual(adjusted_ids[-1], "u1")
        self.assertTrue(
            all(
                adjusted_lengths[idx] >= adjusted_lengths[idx + 1]
                for idx in range(len(adjusted_lengths) - 1)
            )
        )

    def test_non_two_across_upper_stack_is_left_unchanged(self):
        layout = {"positions": [{"position_id": "p1", "deck": "upper", "unit_ids": ["u1", "u2"]}]}
        units_by_id = {
            "u1": self._unit("u1", stop_sequence=1, length_ft=8.0),
            "u2": self._unit("u2", stop_sequence=2, length_ft=8.0),
        }

        adjusted = app_module._auto_stack_upper_two_across_layout(
            layout,
            units_by_id,
            "STEP_DECK",
            assumptions=self._assumptions(),
        )

        self.assertEqual(
            (adjusted.get("positions") or [])[0].get("unit_ids"),
            ["u1", "u2"],
        )

    def test_upper_two_across_reorders_equal_lengths_by_sku_deck_length(self):
        layout = {"positions": [{"position_id": "p1", "deck": "upper", "unit_ids": ["u1", "u2", "u3"]}]}
        units_by_id = {
            "u1": {**self._unit("u1", stop_sequence=1, length_ft=7.0), "sku": "4X5"},
            "u2": {**self._unit("u2", stop_sequence=3, length_ft=7.0), "sku": "5X8G"},
            "u3": {**self._unit("u3", stop_sequence=2, length_ft=7.0), "sku": "4X6G"},
        }

        adjusted = app_module._auto_stack_upper_two_across_layout(
            layout,
            units_by_id,
            "STEP_DECK",
            assumptions=self._assumptions(),
        )

        self.assertEqual(
            (adjusted.get("positions") or [])[0].get("unit_ids"),
            ["u2", "u3", "u1"],
        )


if __name__ == "__main__":
    unittest.main()
