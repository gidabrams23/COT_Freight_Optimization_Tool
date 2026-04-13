import unittest

from cot_utilization.stack_calculator import (
    TRAILER_CONFIGS,
    FIXED_CAPACITY_TRAILER_TYPES,
    calculate_stack_configuration,
    normalize_trailer_type,
    is_valid_trailer_type,
    capacity_overflow_feet,
    check_stacking_compatibility,
    item_deck_length_ft,
    normalize_upper_deck_exception_categories,
)


class TestCoreTrailerHelpers(unittest.TestCase):
    def test_trailer_configs_has_six_types(self):
        self.assertEqual(len(TRAILER_CONFIGS), 6)

    def test_normalize_trailer_type_valid(self):
        self.assertEqual(normalize_trailer_type("step_deck"), "STEP_DECK")
        self.assertEqual(normalize_trailer_type("FLATBED"), "FLATBED")
        self.assertEqual(normalize_trailer_type("hotshot"), "HOTSHOT")

    def test_normalize_trailer_type_invalid_returns_default(self):
        self.assertEqual(normalize_trailer_type("INVALID"), "STEP_DECK")
        self.assertEqual(normalize_trailer_type("INVALID", default="FLATBED"), "FLATBED")

    def test_is_valid_trailer_type(self):
        self.assertTrue(is_valid_trailer_type("STEP_DECK"))
        self.assertFalse(is_valid_trailer_type("BOGUS"))

    def test_fixed_capacity_trailer_types(self):
        self.assertIn("HOTSHOT", FIXED_CAPACITY_TRAILER_TYPES)
        self.assertNotIn("STEP_DECK", FIXED_CAPACITY_TRAILER_TYPES)


class TestCoreCalculateStackConfiguration(unittest.TestCase):
    def test_empty_order_lines_returns_zero_utilization(self):
        config = calculate_stack_configuration([])
        self.assertEqual(config["utilization_pct"], 0)
        self.assertEqual(config["utilization_grade"], "F")
        self.assertEqual(config["positions"], [])

    def test_single_item_flatbed_utilization(self):
        lines = [
            {
                "item": "5X10GW",
                "sku": "5X10GW",
                "qty": 1,
                "unit_length_ft": 14.0,
                "max_stack_height": 1,
                "category": "USA",
            }
        ]
        config = calculate_stack_configuration(
            lines,
            trailer_type="FLATBED",
            stack_overflow_max_height=0,
        )
        self.assertEqual(len(config["positions"]), 1)
        expected_pct = round((14.0 / 53.0) * 100, 1)
        self.assertAlmostEqual(config["utilization_pct"], expected_pct, places=1)

    def test_stacked_items_increase_utilization_credit(self):
        lines = [
            {
                "item": "4X6G",
                "sku": "4X6G",
                "qty": 6,
                "unit_length_ft": 7.0,
                "max_stack_height": 6,
                "category": "USA",
            }
        ]
        config = calculate_stack_configuration(
            lines,
            trailer_type="FLATBED",
            stack_overflow_max_height=0,
        )
        self.assertEqual(len(config["positions"]), 1)
        self.assertAlmostEqual(config["utilization_pct"], round((7.0 / 53.0) * 100, 1), places=1)

    def test_grade_thresholds_override(self):
        lines = [
            {
                "item": "LONG",
                "sku": "LONG",
                "qty": 1,
                "unit_length_ft": 40.0,
                "max_stack_height": 1,
                "category": "CARGO",
            }
        ]
        strict = {"A": 95, "B": 90, "C": 85, "D": 80}
        config = calculate_stack_configuration(
            lines,
            trailer_type="FLATBED",
            stack_overflow_max_height=0,
            grade_thresholds=strict,
        )
        pct = config["utilization_pct"]
        self.assertGreater(pct, 70)
        self.assertLess(pct, 80)
        self.assertEqual(config["utilization_grade"], "F")

    def test_default_grade_thresholds_produce_expected_grades(self):
        lines = [
            {
                "item": "LONG",
                "sku": "LONG",
                "qty": 1,
                "unit_length_ft": 40.0,
                "max_stack_height": 1,
                "category": "CARGO",
            }
        ]
        config = calculate_stack_configuration(
            lines,
            trailer_type="FLATBED",
            stack_overflow_max_height=0,
        )
        pct = config["utilization_pct"]
        self.assertGreater(pct, 70)
        self.assertEqual(config["utilization_grade"], "B")

    def test_step_deck_upper_deck_credit_normalization(self):
        lines = [
            {
                "item": "4X6G",
                "sku": "4X6G",
                "qty": 6,
                "unit_length_ft": 7.0,
                "max_stack_height": 6,
                "category": "USA",
            }
        ]
        config = calculate_stack_configuration(
            lines,
            trailer_type="STEP_DECK",
            stack_overflow_max_height=0,
        )
        self.assertGreater(config["utilization_pct"], 0)
        upper_positions = [p for p in config["positions"] if p.get("deck") == "upper"]
        self.assertTrue(len(upper_positions) > 0, "Short item should be on upper deck")


class TestCoreItemDeckLength(unittest.TestCase):
    def test_parses_sku_name_dimensions(self):
        item = {"sku": "5X10GW", "unit_length_ft": 14.0}
        self.assertEqual(item_deck_length_ft(item), 10.0)

    def test_fallback_when_no_dimensions_in_name(self):
        item = {"sku": "CUSTOM", "unit_length_ft": 14.0}
        self.assertEqual(item_deck_length_ft(item, fallback_length_ft=14.0), 14.0)


class TestCoreNormalizeUpperDeckExceptionCategories(unittest.TestCase):
    def test_string_input_splits_on_comma(self):
        result = normalize_upper_deck_exception_categories("USA,UTA,CARGO")
        self.assertEqual(result, ["USA", "UTA", "CARGO"])

    def test_none_returns_defaults(self):
        result = normalize_upper_deck_exception_categories(None)
        self.assertIn("USA", result)
        self.assertIn("UTA", result)


if __name__ == "__main__":
    unittest.main()
