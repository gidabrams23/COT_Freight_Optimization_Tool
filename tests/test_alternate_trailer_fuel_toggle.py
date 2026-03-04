import os
import unittest
from unittest.mock import patch

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module


class AlternateTrailerFuelToggleTests(unittest.TestCase):
    def test_get_alternate_trailer_rates_maps_legacy_numeric_fuel_to_checkbox(self):
        payload = {
            "sections": [
                {
                    "code": "HOT_SHOT",
                    "rates_by_plant": {"GA": 3.1},
                    "placeholders_by_plant": {
                        "GA": {
                            "fuel_surcharge_per_mile": 0.55,
                            "per_stop": 12,
                            "load_minimum": 700,
                            "requires_return_miles": False,
                        }
                    },
                }
            ]
        }
        with patch.object(app_module, "_get_json_planning_setting", return_value=payload), patch.object(
            app_module,
            "_context_plants",
            return_value=["GA"],
        ):
            data = app_module._get_alternate_trailer_rates()

        section = next(entry for entry in data["sections"] if entry["code"] == "HOT_SHOT")
        placeholder = section["placeholders_by_plant"]["GA"]
        self.assertTrue(placeholder["apply_fuel_surcharge"])
        self.assertNotIn("fuel_surcharge_per_mile", placeholder)
        self.assertEqual(placeholder["per_stop"], 12.0)
        self.assertEqual(placeholder["load_minimum"], 700.0)
        self.assertFalse(placeholder["requires_return_miles"])

    def test_get_alternate_trailer_rates_excludes_step_deck_section(self):
        with patch.object(app_module, "_get_json_planning_setting", return_value={}), patch.object(
            app_module,
            "_context_plants",
            return_value=["GA"],
        ):
            data = app_module._get_alternate_trailer_rates()

        codes = [entry["code"] for entry in data["sections"]]
        self.assertNotIn("STEP_DECK", codes)

    def test_get_alternate_trailer_rates_defaults_checkbox_true_when_missing(self):
        payload = {
            "sections": [
                {
                    "code": "HOT_SHOT",
                    "rates_by_plant": {"GA": 2.9},
                    "placeholders_by_plant": {"GA": {"per_stop": 0, "load_minimum": 0}},
                }
            ]
        }
        with patch.object(app_module, "_get_json_planning_setting", return_value=payload), patch.object(
            app_module,
            "_context_plants",
            return_value=["GA"],
        ):
            data = app_module._get_alternate_trailer_rates()

        section = next(entry for entry in data["sections"] if entry["code"] == "HOT_SHOT")
        self.assertTrue(section["placeholders_by_plant"]["GA"]["apply_fuel_surcharge"])

    def test_resolve_load_carrier_pricing_uses_top_fuel_surcharge_when_enabled(self):
        carrier_context = {
            "fls_lookup": {},
            "fls_accessorial": {"per_stop": 0.0, "fuel_surcharge": 0.4, "load_minimum": 0.0},
            "lst_lookup": {},
            "lst_accessorial": {},
            "ryder_table": {"rates_by_plant": {}},
            "alternate_sections": {
                "HOT_SHOT": {
                    "rates_by_plant": {"GA": 2.0},
                    "placeholders_by_plant": {
                        "GA": {
                            "per_stop": 0.0,
                            "load_minimum": 0.0,
                            "apply_fuel_surcharge": True,
                            "requires_return_miles": True,
                        }
                    },
                }
            },
        }

        result = app_module._resolve_load_carrier_pricing(
            lines=[],
            trailer_type="HOTSHOT",
            origin_plant="GA",
            ordered_stops=[{"state": "TX"}],
            route_legs=[100.0],
            total_miles=100.0,
            stop_count=0,
            requires_return_to_origin=False,
            carrier_context=carrier_context,
        )

        self.assertEqual(result["carrier_key"], "alternate")
        self.assertAlmostEqual(result["fuel_surcharge_per_mile"], 0.4, places=4)
        self.assertAlmostEqual(result["rate_per_mile"], 2.4, places=4)

    def test_resolve_load_carrier_pricing_skips_top_fuel_surcharge_when_disabled(self):
        carrier_context = {
            "fls_lookup": {},
            "fls_accessorial": {"per_stop": 0.0, "fuel_surcharge": 0.4, "load_minimum": 0.0},
            "lst_lookup": {},
            "lst_accessorial": {},
            "ryder_table": {"rates_by_plant": {}},
            "alternate_sections": {
                "HOT_SHOT": {
                    "rates_by_plant": {"GA": 2.0},
                    "placeholders_by_plant": {
                        "GA": {
                            "per_stop": 0.0,
                            "load_minimum": 0.0,
                            "apply_fuel_surcharge": False,
                            "requires_return_miles": True,
                        }
                    },
                }
            },
        }

        result = app_module._resolve_load_carrier_pricing(
            lines=[],
            trailer_type="HOTSHOT",
            origin_plant="GA",
            ordered_stops=[{"state": "TX"}],
            route_legs=[100.0],
            total_miles=100.0,
            stop_count=0,
            requires_return_to_origin=False,
            carrier_context=carrier_context,
        )

        self.assertEqual(result["carrier_key"], "alternate")
        self.assertAlmostEqual(result["fuel_surcharge_per_mile"], 0.0, places=4)
        self.assertAlmostEqual(result["rate_per_mile"], 2.0, places=4)


if __name__ == "__main__":
    unittest.main()
