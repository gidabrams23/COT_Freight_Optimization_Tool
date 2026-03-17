import os
import unittest
from unittest import mock

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module


class SchematicEditPayloadReturnHintTests(unittest.TestCase):
    def test_edit_payload_uses_return_hint_when_building_stops(self):
        load_id = 8
        captured = {}

        with (
            mock.patch.object(
                app_module.db,
                "get_load",
                side_effect=lambda _load_id: {
                    "id": load_id,
                    "origin_plant": "ATL",
                    "trailer_type": "STEP_DECK",
                    "status": "DRAFT",
                }
                if _load_id == load_id
                else None,
            ),
            mock.patch.object(
                app_module.db,
                "list_load_lines",
                return_value=[
                    {
                        "id": 1,
                        "order_line_id": 1,
                        "so_num": "SO-1",
                        "item": "ITEM-1",
                        "item_desc": "Item 1",
                        "sku": "SKU-1",
                        "qty": 1,
                        "unit_length_ft": 10.0,
                        "state": "TX",
                        "zip": "73301",
                    }
                ],
            ),
            mock.patch.object(app_module.db, "list_sku_specs", return_value=[]),
            mock.patch.object(app_module.db, "get_load_schematic_override", return_value=None),
            mock.patch.object(app_module.geo_utils, "load_zip_coordinates", return_value={}),
            mock.patch.object(app_module, "_requires_return_to_origin", return_value=False),
            mock.patch.object(app_module, "_alternate_requires_return_hint", return_value=True),
            mock.patch.object(app_module, "_load_has_lowes_order", return_value=False),
            mock.patch.object(app_module, "_build_load_carrier_pricing_context", return_value={}),
            mock.patch.object(
                app_module,
                "_ordered_stops_for_lines",
                side_effect=lambda lines, origin_plant, zip_coords, return_to_origin=None: captured.update(
                    {"return_to_origin": return_to_origin}
                )
                or [],
            ),
            mock.patch.object(
                app_module,
                "_apply_load_route_direction",
                side_effect=lambda ordered_stops, load=None, reverse_route=None: ordered_stops,
            ),
        ):
            payload = app_module._build_load_schematic_edit_payload(load_id)

        self.assertIsNotNone(payload)
        self.assertTrue(captured.get("return_to_origin"))


if __name__ == "__main__":
    unittest.main()
