import unittest

from services import customer_rules
from services.optimizer import Optimizer


class TrailerAssignmentRuleTests(unittest.TestCase):
    def _optimizer_stub(self):
        optimizer = Optimizer.__new__(Optimizer)
        optimizer.zip_coords = {}
        optimizer.sku_specs = {}
        optimizer.strategic_customers = []
        optimizer.trailer_assignment_rules = {
            "livestock_wedge_enabled": True,
            "livestock_category_tokens": ["LIVESTOCK"],
            "auto_assign_hotshot_enabled": True,
            "auto_assign_hotshot_utilization_threshold_pct": 45.0,
        }
        optimizer._strategic_customer_cache = {}
        optimizer._stack_cache = {}
        return optimizer

    def test_strategic_customer_wedge_length_round_trip(self):
        parsed = customer_rules.parse_strategic_customers(
            '[{"label":"Tractor Supply","patterns":["TRACTOR SUPPLY"],"wedge_min_item_length_ft":16}]'
        )
        self.assertEqual(parsed[0]["wedge_min_item_length_ft"], 16.0)

        serialized = customer_rules.serialize_strategic_customers(parsed)
        parsed_again = customer_rules.parse_strategic_customers(serialized)
        self.assertEqual(parsed_again[0]["wedge_min_item_length_ft"], 16.0)

    def test_tractor_supply_long_item_forces_wedge(self):
        optimizer = self._optimizer_stub()
        optimizer.sku_specs = {
            "SKU-16": {
                "sku": "SKU-16",
                "category": "CARGO",
                "max_stack_step_deck": 1,
                "max_stack_flat_bed": 1,
            }
        }
        optimizer.strategic_customers = customer_rules.parse_strategic_customers(
            '[{"label":"Tractor Supply","patterns":["TRACTOR SUPPLY"]}]'
        )

        group = optimizer._build_group(
            "SO1",
            [
                {
                    "id": 1,
                    "so_num": "SO1",
                    "cust_name": "Tractor Supply Co",
                    "sku": "SKU-16",
                    "unit_length_ft": 16.0,
                    "total_length_ft": 16.0,
                    "state": "TX",
                    "zip": "75001",
                }
            ],
            order_summary={"cust_name": "Tractor Supply Co", "state": "TX", "zip": "75001"},
        )
        self.assertTrue(group["default_wedge_51"])
        self.assertEqual(group["wedge_min_item_length_ft"], 16.0)

    def test_tractor_supply_long_non_cargo_item_does_not_force_wedge(self):
        optimizer = self._optimizer_stub()
        optimizer.sku_specs = {
            "SKU-16": {
                "sku": "SKU-16",
                "category": "USA",
                "max_stack_step_deck": 1,
                "max_stack_flat_bed": 1,
            }
        }
        optimizer.strategic_customers = customer_rules.parse_strategic_customers(
            '[{"label":"Tractor Supply","patterns":["TRACTOR SUPPLY"]}]'
        )

        group = optimizer._build_group(
            "SO1",
            [
                {
                    "id": 1,
                    "so_num": "SO1",
                    "cust_name": "Tractor Supply Co",
                    "sku": "SKU-16",
                    "unit_length_ft": 16.0,
                    "total_length_ft": 16.0,
                    "state": "TX",
                    "zip": "75001",
                }
            ],
            order_summary={"cust_name": "Tractor Supply Co", "state": "TX", "zip": "75001"},
        )
        self.assertFalse(group["default_wedge_51"])
        self.assertIsNone(group["wedge_min_item_length_ft"])

    def test_tractor_supply_long_item_with_bin_cargo_forces_wedge_without_sku_spec_category(self):
        optimizer = self._optimizer_stub()
        optimizer.sku_specs = {
            "SKU-16": {
                "sku": "SKU-16",
                "category": "",
                "max_stack_step_deck": 1,
                "max_stack_flat_bed": 1,
            }
        }
        optimizer.strategic_customers = customer_rules.parse_strategic_customers(
            '[{"label":"Tractor Supply","patterns":["TRACTOR SUPPLY"]}]'
        )

        group = optimizer._build_group(
            "SO1",
            [
                {
                    "id": 1,
                    "so_num": "SO1",
                    "cust_name": "Tractor Supply Co",
                    "sku": "SKU-16",
                    "unit_length_ft": 20.0,
                    "total_length_ft": 20.0,
                    "bin": "CARGO",
                    "state": "TX",
                    "zip": "75001",
                }
            ],
            order_summary={"cust_name": "Tractor Supply Co", "state": "TX", "zip": "75001"},
        )
        self.assertTrue(group["default_wedge_51"])
        self.assertEqual(group["wedge_min_item_length_ft"], 16.0)

    def test_livestock_category_forces_wedge_for_all_customers(self):
        optimizer = self._optimizer_stub()
        optimizer.sku_specs = {
            "LIV-1": {
                "sku": "LIV-1",
                "category": "LIVESTOCK",
                "max_stack_step_deck": 1,
                "max_stack_flat_bed": 1,
            }
        }

        group = optimizer._build_group(
            "SO2",
            [
                {
                    "id": 2,
                    "so_num": "SO2",
                    "cust_name": "Any Customer",
                    "sku": "LIV-1",
                    "unit_length_ft": 8.0,
                    "total_length_ft": 8.0,
                    "state": "OK",
                    "zip": "73101",
                }
            ],
            order_summary={"cust_name": "Any Customer", "state": "OK", "zip": "73101"},
        )
        self.assertTrue(group["contains_livestock"])
        self.assertTrue(group["default_wedge_51"])

    def test_auto_hotshot_assignment_applies_to_any_non_wedge_load_that_fits(self):
        optimizer = self._optimizer_stub()

        def fake_stack_config(groups, params, trailer_type=None, stop_sequence_map=None):
            if trailer_type == "HOTSHOT":
                return {
                    "trailer_type": "HOTSHOT",
                    "utilization_pct": 62.0,
                    "exceeds_capacity": False,
                }
            return {"trailer_type": "STEP_DECK", "utilization_pct": 30.0, "exceeds_capacity": False}

        optimizer._stack_config_for_groups = fake_stack_config
        optimizer._groups_require_wedge = lambda groups: any(
            bool((group or {}).get("default_wedge_51")) for group in (groups or [])
        )

        active_loads = {
            1: {
                "_merge_id": 1,
                "utilization_pct": 30.0,
                "trailer_type": "STEP_DECK",
                "groups": [{"default_wedge_51": False}],
                "lines": [{"so_num": "SO1"}],
                "exceeds_capacity": False,
            },
            2: {
                "_merge_id": 2,
                "utilization_pct": 28.0,
                "trailer_type": "STEP_DECK",
                "groups": [{"default_wedge_51": True}],
                "lines": [{"so_num": "SO2"}],
                "exceeds_capacity": False,
            },
            3: {
                "_merge_id": 3,
                "utilization_pct": 70.0,
                "trailer_type": "STEP_DECK",
                "groups": [{"default_wedge_51": False}],
                "lines": [{"so_num": "SO3"}],
                "exceeds_capacity": False,
            },
        }

        updated = optimizer._apply_auto_hotshot_tail_assignments(active_loads, params={})
        self.assertEqual(updated[1]["trailer_type"], "HOTSHOT")
        self.assertEqual(updated[2]["trailer_type"], "STEP_DECK")
        self.assertEqual(updated[3]["trailer_type"], "HOTSHOT")

    def test_auto_hotshot_assignment_skips_when_hotshot_has_overhang_warning(self):
        optimizer = self._optimizer_stub()

        def fake_stack_config(groups, params, trailer_type=None, stop_sequence_map=None):
            if trailer_type == "HOTSHOT":
                return {
                    "trailer_type": "HOTSHOT",
                    "utilization_pct": 62.0,
                    "exceeds_capacity": False,
                    "warnings": [{"code": "ITEM_HANGS_OVER_DECK"}],
                    "positions": [
                        {
                            "deck": "lower",
                            "length_ft": 60.0,
                        }
                    ],
                    "lower_deck_length": 40.0,
                    "upper_deck_length": 0.0,
                    "max_back_overhang_ft": 4.0,
                    "trailer_type": "HOTSHOT",
                }
            return {"trailer_type": "STEP_DECK", "utilization_pct": 30.0, "exceeds_capacity": False}

        optimizer._stack_config_for_groups = fake_stack_config
        optimizer._groups_require_wedge = lambda groups: any(
            bool((group or {}).get("default_wedge_51")) for group in (groups or [])
        )

        active_loads = {
            1: {
                "_merge_id": 1,
                "utilization_pct": 30.0,
                "trailer_type": "STEP_DECK",
                "groups": [{"default_wedge_51": False}],
                "lines": [{"so_num": "SO1"}],
                "exceeds_capacity": False,
            },
        }

        updated = optimizer._apply_auto_hotshot_tail_assignments(active_loads, params={})
        self.assertEqual(updated[1]["trailer_type"], "STEP_DECK")

    def test_hotshot_stack_check_uses_hotshot_native_capacity_not_global_capacity(self):
        optimizer = self._optimizer_stub()
        optimizer.sku_specs = {
            "LONG": {
                "sku": "LONG",
                "category": "USA",
                "max_stack_step_deck": 1,
                "max_stack_flat_bed": 1,
            }
        }

        groups = [
            {
                "key": "SO1",
                "lines": [
                    {
                        "item": "LONG",
                        "sku": "LONG",
                        "item_desc": "LONG",
                        "qty": 6,
                        "unit_length_ft": 10.0,
                        "order_id": "SO1",
                        "so_num": "SO1",
                    }
                ],
            }
        ]
        config = optimizer._stack_config_for_groups(
            groups,
            params={
                "capacity_feet": 53.0,
                "max_back_overhang_ft": 4.0,
                "stack_overflow_max_height": 5,
                "upper_two_across_max_length_ft": 7.0,
                "upper_deck_exception_max_length_ft": 16.0,
                "upper_deck_exception_overhang_allowance_ft": 6.0,
                "upper_deck_exception_categories": ["USA", "UTA"],
            },
            trailer_type="HOTSHOT",
        )
        self.assertTrue(config.get("exceeds_capacity"))


if __name__ == "__main__":
    unittest.main()
