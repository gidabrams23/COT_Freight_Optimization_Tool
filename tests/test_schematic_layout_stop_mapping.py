import os
import unittest

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module


def _position_item_signature(schematic):
    signature = []
    for position in (schematic or {}).get("positions") or []:
        items = []
        for item in position.get("items") or []:
            items.append(
                (
                    item.get("order_id"),
                    int(item.get("stop_sequence") or 0),
                    item.get("sku"),
                    int(item.get("units") or 0),
                )
            )
        signature.append(items)
    return signature


class SchematicLayoutStopMappingTests(unittest.TestCase):
    def test_layout_from_schematic_keeps_stop_assignment_for_same_order_and_sku(self):
        assumptions = {
            "stack_overflow_max_height": 5,
            "max_back_overhang_ft": 4.0,
            "upper_two_across_max_length_ft": 7.0,
            "upper_deck_exception_max_length_ft": 16.0,
            "upper_deck_exception_overhang_allowance_ft": 6.0,
            "upper_deck_exception_categories": ["USA", "UTA"],
            "equal_length_deck_length_order_enabled": True,
        }
        sku_specs = {
            "5X8GW2K": {
                "sku": "5X8GW2K",
                "max_stack_step_deck": 4,
                "max_stack_flat_bed": 4,
                "category": "CARGO",
            },
            "6X14GW": {
                "sku": "6X14GW",
                "max_stack_step_deck": 2,
                "max_stack_flat_bed": 2,
                "category": "CARGO",
            },
        }
        lines = [
            # Same order/SKU appears at two different stops.
            {
                "id": 11,
                "order_line_id": 11,
                "so_num": "SO-100",
                "item": "5X8GWE2K",
                "item_desc": "5X8GWE2K",
                "sku": "5X8GW2K",
                "qty": 8,
                "unit_length_ft": 7.0,
                "state": "VA",
                "zip": "23456",
            },
            {
                "id": 12,
                "order_line_id": 12,
                "so_num": "SO-100",
                "item": "5X8GWE2K",
                "item_desc": "5X8GWE2K",
                "sku": "5X8GW2K",
                "qty": 4,
                "unit_length_ft": 7.0,
                "state": "VA",
                "zip": "23434",
            },
            {
                "id": 13,
                "order_line_id": 13,
                "so_num": "SO-200",
                "item": "6X14SF7K",
                "item_desc": "6X14SF7K",
                "sku": "6X14GW",
                "qty": 2,
                "unit_length_ft": 14.0,
                "state": "VA",
                "zip": "23061",
            },
        ]
        ordered_stops = [
            {"state": "VA", "zip": "23061"},
            {"state": "VA", "zip": "23434"},
            {"state": "VA", "zip": "23456"},
        ]
        stop_sequence_map = app_module._stop_sequence_map_from_ordered_stops(ordered_stops)
        order_colors = app_module._build_order_colors_for_lines(
            lines,
            stop_sequence_map=stop_sequence_map,
            stop_palette=["#6FAD47", "#52AFFF", "#EE933A", "#C5CBD5"],
        )

        base_schematic, _, _ = app_module._calculate_load_schematic(
            lines,
            sku_specs,
            "FLATBED_48",
            stop_sequence_map=stop_sequence_map,
            assumptions=assumptions,
        )
        units = app_module._build_schematic_units(
            lines,
            sku_specs,
            "FLATBED_48",
            stop_sequence_map=stop_sequence_map,
            order_colors=order_colors,
        )
        layout = app_module._layout_from_schematic(base_schematic, units)
        remapped_schematic, _warnings = app_module._build_schematic_from_layout(
            layout,
            {unit["unit_id"]: unit for unit in units},
            "FLATBED_48",
            assumptions=assumptions,
        )

        self.assertEqual(
            _position_item_signature(remapped_schematic),
            _position_item_signature(base_schematic),
        )


if __name__ == "__main__":
    unittest.main()
