import unittest
from unittest.mock import patch

from services.order_importer import OrderImporter


class OrderImporterLookupScopeTests(unittest.TestCase):
    def _build_importer(self, lookups, known_skus=None):
        importer = OrderImporter.__new__(OrderImporter)
        importer.sku_specs = {
            str(sku).strip().upper(): {"sku": str(sku).strip().upper()}
            for sku in (known_skus or [])
        }
        with patch("services.order_importer.db.list_item_lookups", return_value=lookups):
            importer.sku_lookup = OrderImporter._load_sku_lookup(importer)
        return importer

    def test_prefers_plant_bin_exact_before_wildcards(self):
        importer = self._build_importer(
            [
                {"plant": "*", "bin": "*", "item_pattern": "ABC123", "sku": "SKU_GLOBAL"},
                {"plant": "*", "bin": "CARGO", "item_pattern": "ABC123", "sku": "SKU_ANY_PLANT"},
                {"plant": "GA", "bin": "*", "item_pattern": "ABC123", "sku": "SKU_ANY_BIN_GA"},
                {"plant": "GA", "bin": "CARGO", "item_pattern": "ABC123", "sku": "SKU_GA_CARGO"},
            ]
        )

        sku = importer.lookup_sku("ABC123", plant="GA", bin_code="CARGO", bin_raw="CARGO")
        self.assertEqual(sku, "SKU_GA_CARGO")

    def test_prefers_plant_wildcard_bin_before_any_any(self):
        importer = self._build_importer(
            [
                {"plant": "*", "bin": "*", "item_pattern": "ABC123", "sku": "SKU_GLOBAL"},
                {"plant": "GA", "bin": "*", "item_pattern": "ABC123", "sku": "SKU_GA_ANY_BIN"},
            ]
        )

        sku = importer.lookup_sku("ABC123", plant="GA", bin_code="USA", bin_raw="USA")
        self.assertEqual(sku, "SKU_GA_ANY_BIN")

    def test_prefers_raw_bin_before_extracted_bin(self):
        importer = self._build_importer(
            [
                {"plant": "GA", "bin": "USA", "item_pattern": "ABC123", "sku": "SKU_USA"},
                {"plant": "GA", "bin": "USA-NG", "item_pattern": "ABC123", "sku": "SKU_USA_NG"},
            ]
        )

        sku = importer.lookup_sku("ABC123", plant="GA", bin_code="USA", bin_raw="USA-NG")
        self.assertEqual(sku, "SKU_USA_NG")

    def test_prefers_longest_prefix_pattern_in_scope(self):
        importer = self._build_importer(
            [
                {"plant": "GA", "bin": "CARGO", "item_pattern": "5X%", "sku": "SKU_SHORT"},
                {"plant": "GA", "bin": "CARGO", "item_pattern": "5X10%", "sku": "SKU_LONG"},
            ]
        )

        sku = importer.lookup_sku("5X10GW2K", plant="GA", bin_code="CARGO", bin_raw="CARGO")
        self.assertEqual(sku, "SKU_LONG")

    def test_supports_asterisk_suffix_as_prefix_pattern(self):
        importer = self._build_importer(
            [
                {"plant": "GA", "bin": "CARGO", "item_pattern": "7X14*", "sku": "SKU_7X14"},
            ]
        )

        sku = importer.lookup_sku("7X14CTD14K", plant="GA", bin_code="CARGO", bin_raw="CARGO")
        self.assertEqual(sku, "SKU_7X14")

    def test_item_matching_known_sku_short_circuits(self):
        importer = self._build_importer([], known_skus=["5X8GW2K"])
        sku = importer.lookup_sku("5x8gw2k", plant="GA", bin_code="USA", bin_raw="USA-NG")
        self.assertEqual(sku, "5X8GW2K")


if __name__ == "__main__":
    unittest.main()
