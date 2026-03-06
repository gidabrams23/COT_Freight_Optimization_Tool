import unittest

from services import order_categories
from services.optimizer import Optimizer


class OrderCategoryScopeHelpersTests(unittest.TestCase):
    def test_order_category_scope_from_tokens(self):
        self.assertEqual(
            order_categories.order_category_scope_from_tokens(["USA", "UTA"]),
            order_categories.ORDER_CATEGORY_SCOPE_UTILITIES,
        )
        self.assertEqual(
            order_categories.order_category_scope_from_tokens(["CARGO | 14 FT"]),
            order_categories.ORDER_CATEGORY_SCOPE_CARGO,
        )
        self.assertEqual(
            order_categories.order_category_scope_from_tokens(["DUMP"]),
            order_categories.ORDER_CATEGORY_SCOPE_DUMP,
        )
        self.assertEqual(
            order_categories.order_category_scope_from_tokens(["HDEQ"]),
            order_categories.ORDER_CATEGORY_SCOPE_OTHER,
        )
        self.assertEqual(
            order_categories.order_category_scope_from_tokens(["USA", "DUMP"]),
            order_categories.ORDER_CATEGORY_SCOPE_MIXED,
        )

    def test_normalize_order_category_scope(self):
        self.assertEqual(
            order_categories.normalize_order_category_scope("UTILITIES"),
            order_categories.ORDER_CATEGORY_SCOPE_UTILITIES,
        )
        self.assertEqual(
            order_categories.normalize_order_category_scope("not-a-scope"),
            order_categories.ORDER_CATEGORY_SCOPE_ALL,
        )


class OptimizerOrderCategoryScopeFilterTests(unittest.TestCase):
    def setUp(self):
        self.optimizer = Optimizer.__new__(Optimizer)
        self.grouped = [
            {
                "key": "U-1",
                "categories": ["USA"],
                "order_category_scope": "utilities",
                "state": "TX",
                "cust_name": "A",
                "ignore_for_optimization": False,
            },
            {
                "key": "C-1",
                "categories": ["CARGO"],
                "order_category_scope": "cargo",
                "state": "TX",
                "cust_name": "A",
                "ignore_for_optimization": False,
            },
            {
                "key": "M-1",
                "categories": ["USA", "DUMP"],
                "order_category_scope": "mixed",
                "state": "TX",
                "cust_name": "A",
                "ignore_for_optimization": False,
            },
        ]

    def _base_params(self):
        return {
            "optimize_mode": "auto",
            "batch_max_due_date": None,
            "state_filters": [],
            "customer_filters": [],
            "selected_so_nums": [],
            "order_category_scope": "all",
        }

    def test_apply_order_group_filters_respects_category_scope(self):
        params = self._base_params()
        params["order_category_scope"] = "cargo"
        filtered = self.optimizer._apply_order_group_filters(self.grouped, params)
        self.assertEqual([group["key"] for group in filtered], ["C-1"])

    def test_apply_order_group_filters_supports_mixed_scope(self):
        params = self._base_params()
        params["order_category_scope"] = "mixed"
        filtered = self.optimizer._apply_order_group_filters(self.grouped, params)
        self.assertEqual([group["key"] for group in filtered], ["M-1"])


if __name__ == "__main__":
    unittest.main()
