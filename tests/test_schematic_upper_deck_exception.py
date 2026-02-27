import unittest

import app as app_module


class SchematicUpperDeckExceptionTests(unittest.TestCase):
    def _assumptions(self):
        return {
            "stack_overflow_max_height": 5,
            "max_back_overhang_ft": 4.0,
            "upper_two_across_max_length_ft": 7.0,
            "upper_deck_exception_max_length_ft": 16.0,
            "upper_deck_exception_overhang_allowance_ft": 6.0,
            "upper_deck_exception_categories": ["USA", "UTA"],
        }

    def _unit(self, category):
        return {
            "unit_id": "u1",
            "order_id": "SO-1",
            "order_line_id": 1,
            "sku": "SKU-1",
            "item": "ITEM-1",
            "item_desc": "Item",
            "unit_length_ft": 16.0,
            "max_stack": 5,
            "upper_max_stack": 5,
            "category": category,
            "stop_sequence": 1,
            "color": "#334155",
        }

    def test_manual_upper_deck_non_exception_16ft_triggers_too_big_warning(self):
        layout = {"positions": [{"position_id": "p1", "deck": "upper", "unit_ids": ["u1"]}]}
        units_by_id = {"u1": self._unit("DUMP")}

        schematic, warnings = app_module._build_schematic_from_layout(
            layout,
            units_by_id,
            "STEP_DECK",
            assumptions=self._assumptions(),
        )

        warning_codes = {warning.get("code") for warning in warnings}
        self.assertIn("ITEM_TOO_BIG_FOR_UPPER_DECK", warning_codes)
        self.assertTrue(schematic.get("exceeds_capacity"))

    def test_manual_upper_deck_usa_16ft_uses_exception_without_too_big_warning(self):
        layout = {"positions": [{"position_id": "p1", "deck": "upper", "unit_ids": ["u1"]}]}
        units_by_id = {"u1": self._unit("USA")}

        schematic, warnings = app_module._build_schematic_from_layout(
            layout,
            units_by_id,
            "STEP_DECK",
            assumptions=self._assumptions(),
        )

        warning_codes = {warning.get("code") for warning in warnings}
        self.assertNotIn("ITEM_TOO_BIG_FOR_UPPER_DECK", warning_codes)
        self.assertIn("BACK_OVERHANG_IN_ALLOWANCE", warning_codes)
        self.assertFalse(schematic.get("exceeds_capacity"))


if __name__ == "__main__":
    unittest.main()
