import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

pd = pytest.importorskip("pandas")

from cot_utilization.scorer import (
    UtilizationScorer,
    _parse_sku_dimensions,
    _resolve_sku,
    _normalize_sku_lookup,
)


SKU_LOOKUP = {
    "5X8GW": {
        "length_with_tongue_ft": 12.0,
        "max_stack_step_deck": 5,
        "max_stack_flat_bed": 4,
        "category": "USA",
    },
    "5.5X10GWE2K": {
        "length_with_tongue_ft": 14.0,
        "max_stack_step_deck": 5,
        "max_stack_flat_bed": 4,
        "category": "USA",
    },
    "7X16TA": {
        "length_with_tongue_ft": 22.0,
        "max_stack_step_deck": 2,
        "max_stack_flat_bed": 2,
        "category": "CARGO",
    },
}


class TestSKUDimensionParsing(unittest.TestCase):
    def test_parses_simple_dimensions(self):
        self.assertEqual(_parse_sku_dimensions("5X8GW"), 8.0)

    def test_parses_decimal_dimensions(self):
        self.assertEqual(_parse_sku_dimensions("5.5X10GWE2K"), 10.0)

    def test_returns_none_for_unparseable(self):
        self.assertIsNone(_parse_sku_dimensions("CUSTOM-ITEM"))

    def test_returns_second_dimension(self):
        self.assertEqual(_parse_sku_dimensions("10X5"), 5.0)


class TestSKUResolution(unittest.TestCase):
    def setUp(self):
        self._lookup = _normalize_sku_lookup(SKU_LOOKUP)

    def test_exact_match(self):
        spec, unmapped = _resolve_sku("5X8GW", self._lookup, "STEP_DECK")
        self.assertFalse(unmapped)
        self.assertEqual(spec["unit_length_ft"], 12.0)
        self.assertEqual(spec["max_stack_height"], 5)

    def test_case_insensitive_match(self):
        spec, unmapped = _resolve_sku("5x8gw", self._lookup, "STEP_DECK")
        self.assertFalse(unmapped)
        self.assertEqual(spec["unit_length_ft"], 12.0)

    def test_flatbed_uses_flatbed_max_stack(self):
        spec, unmapped = _resolve_sku("5X8GW", self._lookup, "FLATBED")
        self.assertFalse(unmapped)
        self.assertEqual(spec["max_stack_height"], 4)

    def test_fallback_to_dimension_parsing(self):
        spec, unmapped = _resolve_sku("6X12NEWSKU", self._lookup, "STEP_DECK")
        self.assertFalse(unmapped)
        self.assertEqual(spec["unit_length_ft"], 12.0)
        self.assertEqual(spec["max_stack_height"], 1)

    def test_unmapped_when_no_match_and_no_dimensions(self):
        spec, unmapped = _resolve_sku("CUSTOM", self._lookup, "STEP_DECK")
        self.assertTrue(unmapped)
        self.assertIsNone(spec)


class TestUtilizationScorer(unittest.TestCase):
    def setUp(self):
        self.scorer = UtilizationScorer(SKU_LOOKUP)

    def test_score_single_load(self):
        df = pd.DataFrame(
            [
                {"load_number": "L001", "shippedqty": 5, "itemnum": "5X8GW", "fancy_cat": "Utility"},
                {"load_number": "L001", "shippedqty": 3, "itemnum": "5.5X10GWE2K", "fancy_cat": "Utility"},
            ]
        )
        results = self.scorer.score_loads(
            df,
            column_map={
                "load_number": "load_number",
                "qty": "shippedqty",
                "sku": "itemnum",
                "trailer_hint": "fancy_cat",
            },
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results.iloc[0]["load_number"], "L001")
        self.assertGreater(results.iloc[0]["utilization_pct"], 0)
        self.assertEqual(results.iloc[0]["trailer_type"], "STEP_DECK")

    def test_cargo_triggers_wedge(self):
        df = pd.DataFrame(
            [
                {"load_number": "L002", "shippedqty": 1, "itemnum": "7X16TA", "fancy_cat": "Cargo"},
            ]
        )
        results = self.scorer.score_loads(
            df,
            column_map={
                "load_number": "load_number",
                "qty": "shippedqty",
                "sku": "itemnum",
                "trailer_hint": "fancy_cat",
            },
            trailer_rules={
                "default": "STEP_DECK",
                "overrides": {"Cargo": "WEDGE"},
            },
        )
        self.assertEqual(results.iloc[0]["trailer_type"], "WEDGE")
        self.assertEqual(results.iloc[0]["capacity_ft"], 51.0)

    def test_multiple_loads_grouped(self):
        df = pd.DataFrame(
            [
                {"load_number": "A", "shippedqty": 2, "itemnum": "5X8GW", "fancy_cat": "Utility"},
                {"load_number": "B", "shippedqty": 3, "itemnum": "5X8GW", "fancy_cat": "Utility"},
            ]
        )
        results = self.scorer.score_loads(
            df,
            column_map={
                "load_number": "load_number",
                "qty": "shippedqty",
                "sku": "itemnum",
                "trailer_hint": "fancy_cat",
            },
        )
        self.assertEqual(len(results), 2)
        load_numbers = set(results["load_number"])
        self.assertEqual(load_numbers, {"A", "B"})

    def test_unmapped_skus_flagged(self):
        df = pd.DataFrame(
            [
                {"load_number": "L003", "shippedqty": 1, "itemnum": "UNKNOWN_ITEM", "fancy_cat": "Utility"},
                {"load_number": "L003", "shippedqty": 2, "itemnum": "5X8GW", "fancy_cat": "Utility"},
            ]
        )
        results = self.scorer.score_loads(
            df,
            column_map={
                "load_number": "load_number",
                "qty": "shippedqty",
                "sku": "itemnum",
                "trailer_hint": "fancy_cat",
            },
        )
        self.assertEqual(results.iloc[0]["unmapped_skus"], ["UNKNOWN_ITEM"])
        self.assertGreater(results.iloc[0]["utilization_pct"], 0)

    def test_output_schema_columns(self):
        df = pd.DataFrame(
            [
                {"load_number": "L001", "shippedqty": 1, "itemnum": "5X8GW", "fancy_cat": "Utility"},
            ]
        )
        results = self.scorer.score_loads(
            df,
            column_map={
                "load_number": "load_number",
                "qty": "shippedqty",
                "sku": "itemnum",
                "trailer_hint": "fancy_cat",
            },
        )
        expected_cols = {
            "load_number",
            "utilization_pct",
            "utilization_grade",
            "utilization_credit_ft",
            "total_linear_feet",
            "trailer_type",
            "capacity_ft",
            "position_count",
            "line_count",
            "unmapped_skus",
        }
        self.assertEqual(set(results.columns), expected_cols)

    def test_empty_dataframe(self):
        df = pd.DataFrame(columns=["load_number", "shippedqty", "itemnum", "fancy_cat"])
        results = self.scorer.score_loads(
            df,
            column_map={
                "load_number": "load_number",
                "qty": "shippedqty",
                "sku": "itemnum",
                "trailer_hint": "fancy_cat",
            },
        )
        self.assertEqual(len(results), 0)

    def test_from_csv_skips_leading_metadata_comments(self):
        with TemporaryDirectory() as tmpdir:
            snapshot_path = Path(tmpdir) / "sku_snapshot.csv"
            snapshot_path.write_text(
                "# generated_at: 2026-04-12T12:00:00+00:00\n"
                "# row_count: 1\n"
                "sku,category,description,length_with_tongue_ft,max_stack_step_deck,max_stack_flat_bed\n"
                "5X8GW,USA,,12.0,5,4\n",
                encoding="utf-8",
            )

            scorer = UtilizationScorer.from_csv(snapshot_path)
            df = pd.DataFrame(
                [
                    {"load_number": "L001", "shippedqty": 1, "itemnum": "5X8GW", "fancy_cat": "Utility"},
                ]
            )
            results = scorer.score_loads(
                df,
                column_map={
                    "load_number": "load_number",
                    "qty": "shippedqty",
                    "sku": "itemnum",
                    "trailer_hint": "fancy_cat",
                },
            )

            self.assertEqual(len(results), 1)
            self.assertEqual(results.iloc[0]["unmapped_skus"], [])
            self.assertGreater(results.iloc[0]["utilization_pct"], 0)


if __name__ == "__main__":
    unittest.main()
