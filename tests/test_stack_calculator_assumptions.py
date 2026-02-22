import unittest
from unittest.mock import patch

from services import stack_calculator


class StackCalculatorAssumptionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
