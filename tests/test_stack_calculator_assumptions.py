import unittest
from unittest.mock import patch

from services import stack_calculator


class StackCalculatorAssumptionTests(unittest.TestCase):
    @patch("services.stack_calculator.db.get_planning_setting", return_value={})
    def test_high_side_item_rises_to_top_within_same_length_stack(self, _mock_get_setting):
        order_lines = [
            {
                "item": "4X5HS",
                "sku": "4X5HS",
                "qty": 1,
                "unit_length_ft": 7.0,
                "max_stack_height": 6,
                "category": "USA",
            },
            {
                "item": "5X8GWE",
                "sku": "5X8GWE",
                "qty": 1,
                "unit_length_ft": 7.0,
                "max_stack_height": 6,
                "category": "USA",
            },
        ]

        config = stack_calculator.calculate_stack_configuration(
            order_lines,
            trailer_type="FLATBED",
            stack_overflow_max_height=0,
            max_back_overhang_ft=4.0,
        )

        self.assertEqual(len(config.get("positions") or []), 1)
        items = (config["positions"][0].get("items") or [])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].get("sku"), "5X8GWE")
        self.assertEqual(items[1].get("sku"), "4X5HS")

    @patch("services.stack_calculator.db.get_planning_setting", return_value={})
    def test_high_side_reordering_does_not_move_shorter_items_above_longer_items(self, _mock_get_setting):
        order_lines = [
            {
                "item": "LONG-ITEM",
                "sku": "LONG-ITEM",
                "qty": 1,
                "unit_length_ft": 10.0,
                "max_stack_height": 6,
                "category": "USA",
            },
            {
                "item": "4X5HS",
                "sku": "4X5HS",
                "qty": 1,
                "unit_length_ft": 7.0,
                "max_stack_height": 6,
                "category": "USA",
            },
            {
                "item": "5X8GWE",
                "sku": "5X8GWE",
                "qty": 1,
                "unit_length_ft": 7.0,
                "max_stack_height": 6,
                "category": "USA",
            },
        ]

        config = stack_calculator.calculate_stack_configuration(
            order_lines,
            trailer_type="FLATBED",
            stack_overflow_max_height=0,
            max_back_overhang_ft=4.0,
        )

        self.assertEqual(len(config.get("positions") or []), 1)
        items = (config["positions"][0].get("items") or [])
        self.assertEqual([item.get("unit_length_ft") for item in items], [10.0, 7.0, 7.0])
        self.assertEqual(items[1].get("sku"), "5X8GWE")
        self.assertEqual(items[2].get("sku"), "4X5HS")

    @patch("services.stack_calculator.db.get_planning_setting", return_value={})
    def test_stop_sequence_priority_trumps_high_side_promotion(self, _mock_get_setting):
        order_lines = [
            {
                "item": "4X5HS",
                "sku": "4X5HS",
                "qty": 1,
                "unit_length_ft": 7.0,
                "max_stack_height": 6,
                "category": "USA",
                "stop_sequence": 2,
            },
            {
                "item": "5X8GWE",
                "sku": "5X8GWE",
                "qty": 1,
                "unit_length_ft": 7.0,
                "max_stack_height": 6,
                "category": "USA",
                "stop_sequence": 1,
            },
        ]

        config = stack_calculator.calculate_stack_configuration(
            order_lines,
            trailer_type="FLATBED",
            preserve_order_contiguity=False,
            stack_overflow_max_height=0,
            max_back_overhang_ft=4.0,
        )

        self.assertEqual(len(config.get("positions") or []), 1)
        items = (config["positions"][0].get("items") or [])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].get("sku"), "4X5HS")
        self.assertEqual(items[1].get("sku"), "5X8GWE")
        self.assertEqual(items[0].get("stop_sequence"), 2)
        self.assertEqual(items[1].get("stop_sequence"), 1)

    @patch("services.stack_calculator.db.get_planning_setting", return_value={})
    def test_stack_overflow_increases_utilization_credit_for_mixed_base_stack(self, _mock_get_setting):
        order_lines = [
            {
                "item": "ITEM-A",
                "sku": "A",
                "qty": 2,
                "unit_length_ft": 10.0,
                "max_stack_height": 6,
                "category": "FLAT",
            },
            {
                "item": "ITEM-C",
                "sku": "C",
                "qty": 2,
                "unit_length_ft": 10.0,
                "max_stack_height": 3,
                "category": "FLAT",
            },
            {
                "item": "ITEM-B",
                "sku": "B",
                "qty": 1,
                "unit_length_ft": 8.0,
                "max_stack_height": 6,
                "category": "FLAT",
            },
        ]

        no_overflow = stack_calculator.calculate_stack_configuration(
            order_lines,
            trailer_type="FLATBED",
            stack_overflow_max_height=0,
            max_back_overhang_ft=4.0,
        )
        with_overflow = stack_calculator.calculate_stack_configuration(
            order_lines,
            trailer_type="FLATBED",
            stack_overflow_max_height=5,
            max_back_overhang_ft=4.0,
        )

        self.assertGreater(
            with_overflow["utilization_credit_ft"],
            no_overflow["utilization_credit_ft"],
        )
        self.assertAlmostEqual(no_overflow["utilization_credit_ft"], 11.3, places=1)
        self.assertAlmostEqual(with_overflow["utilization_credit_ft"], 11.7, places=1)
        self.assertTrue(
            any(
                warning.get("code") == "STACK_OVERFLOW_ALLOWANCE_USED"
                for warning in (with_overflow.get("warnings") or [])
            )
        )

    @patch("services.stack_calculator.db.get_planning_setting", return_value={})
    def test_back_overhang_allowance_controls_exceeds_capacity(self, _mock_get_setting):
        order_lines = [
            {
                "item": "LONG",
                "sku": "LONG",
                "qty": 6,
                "unit_length_ft": 10.0,
                "max_stack_height": 1,
                "category": "FLAT",
            }
        ]

        tight_allowance = stack_calculator.calculate_stack_configuration(
            order_lines,
            trailer_type="FLATBED",
            stack_overflow_max_height=0,
            max_back_overhang_ft=4.0,
        )
        relaxed_allowance = stack_calculator.calculate_stack_configuration(
            order_lines,
            trailer_type="FLATBED",
            stack_overflow_max_height=0,
            max_back_overhang_ft=8.0,
        )

        self.assertTrue(tight_allowance.get("exceeds_capacity"))
        self.assertFalse(relaxed_allowance.get("exceeds_capacity"))
        self.assertTrue(
            any(
                warning.get("code") == "ITEM_HANGS_OVER_DECK"
                for warning in (tight_allowance.get("warnings") or [])
            )
        )
        self.assertTrue(
            any(
                warning.get("code") == "BACK_OVERHANG_IN_ALLOWANCE"
                for warning in (relaxed_allowance.get("warnings") or [])
            )
        )

    @patch("services.stack_calculator.db.get_planning_setting", return_value={})
    def test_overflow_not_applied_when_extra_stack_is_not_singleton(self, _mock_get_setting):
        order_lines = [
            {
                "item": "ITEM-A",
                "sku": "A",
                "qty": 6,
                "unit_length_ft": 10.0,
                "max_stack_height": 6,
                "category": "FLAT",
            },
            {
                "item": "ITEM-B",
                "sku": "B",
                "qty": 2,
                "unit_length_ft": 8.0,
                "max_stack_height": 5,
                "category": "FLAT",
            },
        ]

        with_overflow = stack_calculator.calculate_stack_configuration(
            order_lines,
            trailer_type="FLATBED",
            stack_overflow_max_height=5,
            max_back_overhang_ft=4.0,
        )

        self.assertAlmostEqual(with_overflow["utilization_credit_ft"], 13.2, places=1)
        self.assertFalse(
            any(
                warning.get("code") == "STACK_OVERFLOW_ALLOWANCE_USED"
                for warning in (with_overflow.get("warnings") or [])
            )
        )

    @patch("services.stack_calculator.db.get_planning_setting", return_value={})
    def test_overflow_not_applied_on_homogeneous_full_stack(self, _mock_get_setting):
        order_lines = [
            {
                "item": "ITEM-A",
                "sku": "A",
                "qty": 6,
                "unit_length_ft": 10.0,
                "max_stack_height": 6,
                "category": "FLAT",
            },
            {
                "item": "ITEM-B",
                "sku": "B",
                "qty": 1,
                "unit_length_ft": 8.0,
                "max_stack_height": 6,
                "category": "FLAT",
            },
        ]

        with_overflow = stack_calculator.calculate_stack_configuration(
            order_lines,
            trailer_type="FLATBED",
            stack_overflow_max_height=5,
            max_back_overhang_ft=4.0,
        )

        self.assertAlmostEqual(with_overflow["utilization_credit_ft"], 11.3, places=1)
        self.assertTrue(
            all(not bool(pos.get("overflow_applied")) for pos in (with_overflow.get("positions") or []))
        )
        self.assertFalse(
            any(
                warning.get("code") == "STACK_OVERFLOW_ALLOWANCE_USED"
                for warning in (with_overflow.get("warnings") or [])
            )
        )

    @patch("services.stack_calculator.db.get_planning_setting", return_value={})
    def test_capacity_overflow_feet_reports_excess_length(self, _mock_get_setting):
        order_lines = [
            {
                "item": "LONG",
                "sku": "LONG",
                "qty": 6,
                "unit_length_ft": 10.0,
                "max_stack_height": 1,
                "category": "FLAT",
            }
        ]
        config = stack_calculator.calculate_stack_configuration(
            order_lines,
            trailer_type="FLATBED",
            stack_overflow_max_height=0,
            max_back_overhang_ft=4.0,
        )
        overflow_ft = stack_calculator.capacity_overflow_feet(config)
        self.assertGreater(overflow_ft, 0.0)

    @patch("services.stack_calculator.db.get_planning_setting", return_value={})
    def test_step_deck_upper_deck_full_stack_gets_full_upper_length_credit(self, _mock_get_setting):
        order_lines = [
            {
                "item": "TRAILER-7FT",
                "sku": "T7",
                "qty": 18,
                "unit_length_ft": 7.0,
                "max_stack_height": 6,
                "category": "FLAT",
            }
        ]

        config = stack_calculator.calculate_stack_configuration(
            order_lines,
            trailer_type="STEP_DECK",
            stack_overflow_max_height=0,
            max_back_overhang_ft=4.0,
        )
        upper_positions = [
            pos for pos in (config.get("positions") or []) if (pos.get("deck") or "").lower() == "upper"
        ]
        self.assertEqual(len(upper_positions), 1)
        self.assertAlmostEqual(config.get("upper_deck_length") or 0.0, 10.0, places=1)
        self.assertAlmostEqual(config.get("utilization_credit_ft") or 0.0, 24.0, places=1)

    @patch("services.stack_calculator.db.get_planning_setting", return_value={})
    def test_step_deck_upper_deck_partial_stack_scales_to_upper_length_credit(self, _mock_get_setting):
        order_lines = [
            {
                "item": "TRAILER-7FT",
                "sku": "T7",
                "qty": 3,
                "unit_length_ft": 7.0,
                "max_stack_height": 6,
                "category": "FLAT",
            }
        ]

        config = stack_calculator.calculate_stack_configuration(
            order_lines,
            trailer_type="STEP_DECK",
            stack_overflow_max_height=0,
            max_back_overhang_ft=4.0,
        )
        upper_positions = [
            pos for pos in (config.get("positions") or []) if (pos.get("deck") or "").lower() == "upper"
        ]
        self.assertEqual(len(upper_positions), 1)
        self.assertAlmostEqual(config.get("utilization_credit_ft") or 0.0, 5.0, places=1)

    @patch("services.stack_calculator.db.get_planning_setting", return_value={})
    def test_upper_two_across_auto_distribution_biases_right_stack(self, _mock_get_setting):
        positions = [
            {
                "position_id": "p1",
                "deck": "upper",
                "length_ft": 7.0,
                "capacity_used": 2.5,
                "items": [
                    {
                        "item": "SHORT-UNIT",
                        "sku": "S7",
                        "units": 5,
                        "max_stack": 2,
                        "upper_max_stack": 2,
                        "unit_length_ft": 7.0,
                        "category": "FLAT",
                    }
                ],
            }
        ]
        trailer_config = {"type": "STEP_DECK", "lower": 43.0, "upper": 10.0}
        stack_calculator.apply_upper_usage_metadata(positions, trailer_config, 7.0)

        self.assertTrue(positions[0].get("two_across_applied"))
        item = (positions[0].get("items") or [])[0]
        left_units = int(item.get("two_across_left_units") or 0)
        right_units = int(item.get("two_across_right_units") or 0)
        self.assertEqual(left_units + right_units, int(item.get("units") or 0))
        self.assertGreaterEqual(right_units, left_units)

    @patch("services.stack_calculator.db.get_planning_setting", return_value={})
    def test_upper_two_across_prefers_keep_stop_group_on_single_side(self, _mock_get_setting):
        positions = [
            {
                "position_id": "p1",
                "deck": "upper",
                "length_ft": 7.0,
                "capacity_used": 4.0,
                "items": [
                    {
                        "item": "STOP-1",
                        "sku": "A",
                        "units": 2,
                        "max_stack": 1,
                        "upper_max_stack": 1,
                        "unit_length_ft": 7.0,
                        "category": "FLAT",
                        "stop_sequence": 1,
                    },
                    {
                        "item": "STOP-2",
                        "sku": "B",
                        "units": 2,
                        "max_stack": 1,
                        "upper_max_stack": 1,
                        "unit_length_ft": 7.0,
                        "category": "FLAT",
                        "stop_sequence": 2,
                    },
                ],
            }
        ]
        trailer_config = {"type": "STEP_DECK", "lower": 43.0, "upper": 10.0}
        stack_calculator.apply_upper_usage_metadata(positions, trailer_config, 7.0)

        self.assertTrue(positions[0].get("two_across_applied"))
        for item in positions[0].get("items") or []:
            left_units = int(item.get("two_across_left_units") or 0)
            right_units = int(item.get("two_across_right_units") or 0)
            self.assertEqual(left_units + right_units, int(item.get("units") or 0))
            # Keep each stop grouped in one side when possible.
            self.assertTrue(left_units == 0 or right_units == 0)

    @patch("services.stack_calculator.db.get_planning_setting", return_value={})
    def test_step_deck_upper_exception_places_16ft_usa_on_upper_deck(self, _mock_get_setting):
        order_lines = [
            {
                "item": "USA-16",
                "sku": "USA16",
                "qty": 1,
                "unit_length_ft": 16.0,
                "max_stack_height": 5,
                "upper_deck_max_stack_height": 5,
                "category": "USA",
            }
        ]

        config = stack_calculator.calculate_stack_configuration(
            order_lines,
            trailer_type="STEP_DECK",
            stack_overflow_max_height=0,
            max_back_overhang_ft=4.0,
            upper_two_across_max_length_ft=7.0,
            upper_deck_exception_max_length_ft=16.0,
            upper_deck_exception_overhang_allowance_ft=6.0,
            upper_deck_exception_categories=["USA", "UTA"],
        )

        upper_positions = [
            pos for pos in (config.get("positions") or []) if (pos.get("deck") or "").lower() == "upper"
        ]
        self.assertEqual(len(upper_positions), 1)
        self.assertFalse(config.get("exceeds_capacity"))
        self.assertTrue(
            any(
                warning.get("code") == "BACK_OVERHANG_IN_ALLOWANCE"
                for warning in (config.get("warnings") or [])
            )
        )

    def test_upper_exception_overhang_within_allowance_reports_no_overflow(self):
        stack_config = {
            "trailer_type": "STEP_DECK",
            "positions": [
                {
                    "deck": "upper",
                    "length_ft": 16.0,
                    "effective_length_ft": 16.0,
                    "items": [{"category": "USA", "units": 1, "max_stack": 1}],
                }
            ],
            "lower_deck_length": 43.0,
            "upper_deck_length": 10.0,
            "max_back_overhang_ft": 4.0,
            "upper_deck_exception_max_length_ft": 16.0,
            "upper_deck_exception_overhang_allowance_ft": 6.0,
            "upper_deck_exception_categories": ["USA", "UTA"],
        }
        self.assertEqual(stack_calculator.capacity_overflow_feet(stack_config), 0.0)

    def test_non_exception_upper_overhang_keeps_standard_limit(self):
        stack_config = {
            "trailer_type": "STEP_DECK",
            "positions": [
                {
                    "deck": "upper",
                    "length_ft": 16.0,
                    "effective_length_ft": 16.0,
                    "items": [{"category": "DUMP", "units": 1, "max_stack": 1}],
                }
            ],
            "lower_deck_length": 43.0,
            "upper_deck_length": 10.0,
            "max_back_overhang_ft": 4.0,
            "upper_deck_exception_max_length_ft": 16.0,
            "upper_deck_exception_overhang_allowance_ft": 6.0,
            "upper_deck_exception_categories": ["USA", "UTA"],
        }
        self.assertGreater(stack_calculator.capacity_overflow_feet(stack_config), 0.0)

    def test_mixed_upper_deck_does_not_get_extra_allowance_without_eligible_overhang(self):
        stack_config = {
            "trailer_type": "STEP_DECK",
            "positions": [
                {
                    "deck": "upper",
                    "length_ft": 10.0,
                    "effective_length_ft": 10.0,
                    "items": [{"category": "USA", "units": 1, "max_stack": 1}],
                },
                {
                    "deck": "upper",
                    "length_ft": 5.0,
                    "effective_length_ft": 5.0,
                    "items": [{"category": "DUMP", "units": 1, "max_stack": 1}],
                },
            ],
            "lower_deck_length": 43.0,
            "upper_deck_length": 10.0,
            "max_back_overhang_ft": 4.0,
            "upper_deck_exception_max_length_ft": 16.0,
            "upper_deck_exception_overhang_allowance_ft": 6.0,
            "upper_deck_exception_categories": ["USA", "UTA"],
        }
        self.assertGreater(stack_calculator.capacity_overflow_feet(stack_config), 0.0)

    @patch("services.stack_calculator.db.get_planning_setting", return_value={})
    def test_auto_layout_limits_upper_deck_to_single_side_by_side_stack(self, _mock_get_setting):
        order_lines = [
            {
                "item": "SHORT-A",
                "sku": "SA",
                "qty": 2,
                "unit_length_ft": 7.0,
                "max_stack_height": 2,
                "upper_deck_max_stack_height": 1,
                "category": "USA",
            },
            {
                "item": "SHORT-B",
                "sku": "SB",
                "qty": 2,
                "unit_length_ft": 7.0,
                "max_stack_height": 2,
                "upper_deck_max_stack_height": 1,
                "category": "USA",
            },
        ]

        config = stack_calculator.calculate_stack_configuration(
            order_lines,
            trailer_type="STEP_DECK",
            stack_overflow_max_height=0,
            max_back_overhang_ft=4.0,
            upper_two_across_max_length_ft=7.0,
        )
        upper_positions = [
            pos for pos in (config.get("positions") or []) if (pos.get("deck") or "").lower() == "upper"
        ]
        two_across_upper = [pos for pos in upper_positions if pos.get("two_across_applied")]
        self.assertEqual(len(two_across_upper), 1)
        self.assertEqual(len(upper_positions), 1)

    @patch("services.stack_calculator.db.get_planning_setting", return_value={})
    def test_upper_exception_length_not_mixed_with_side_by_side_mode(self, _mock_get_setting):
        order_lines = [
            {
                "item": "SHORT-SBS",
                "sku": "SS",
                "qty": 2,
                "unit_length_ft": 4.0,
                "max_stack_height": 2,
                "upper_deck_max_stack_height": 1,
                "category": "USA",
            },
            {
                "item": "LONG-EXCEPTION",
                "sku": "LX",
                "qty": 1,
                "unit_length_ft": 11.0,
                "max_stack_height": 1,
                "upper_deck_max_stack_height": 1,
                "category": "USA",
            },
        ]

        config = stack_calculator.calculate_stack_configuration(
            order_lines,
            trailer_type="STEP_DECK",
            stack_overflow_max_height=0,
            max_back_overhang_ft=4.0,
            upper_two_across_max_length_ft=7.0,
        )
        upper_positions = [
            pos for pos in (config.get("positions") or []) if (pos.get("deck") or "").lower() == "upper"
        ]
        two_across_upper = [pos for pos in upper_positions if pos.get("two_across_applied")]
        if two_across_upper:
            self.assertEqual(len(upper_positions), 1)
            self.assertLessEqual(float(upper_positions[0].get("length_ft") or 0.0), 7.0 + 1e-6)


if __name__ == "__main__":
    unittest.main()
