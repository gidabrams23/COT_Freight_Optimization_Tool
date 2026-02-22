import unittest

from services.optimizer import Optimizer


class HomeLengthPriorityTests(unittest.TestCase):
    def setUp(self):
        self.optimizer = Optimizer.__new__(Optimizer)

    def _base_meta(self):
        return {
            "state": "TX",
            "utilization": 72.0,
            "origin_miles": 45.0,
            "bearing": None,
            "due_anchor": 750000,
            "effective_due_window_days": 7,
            "max_unit_length_ft": 8.0,
        }

    def _base_params(self):
        return {
            "v2_low_util_threshold": 70.0,
            "v2_home_length_priority_enabled": True,
            "v2_home_length_priority_radius_miles": 250.0,
            "v2_home_length_priority_threshold_ft": 12.0,
            "v2_home_length_priority_weight": 1.0,
            "v2_home_length_priority_max_bonus": 12.0,
        }

    def test_near_home_long_item_is_prioritized(self):
        params = self._base_params()
        meta_a = self._base_meta()
        meta_b = self._base_meta()

        score_short = self.optimizer._pair_priority_score(meta_a, meta_b, params)
        meta_b["max_unit_length_ft"] = 20.0
        score_long = self.optimizer._pair_priority_score(meta_a, meta_b, params)

        self.assertLess(
            score_long,
            score_short,
            "Longer near-home items should produce a better (lower) pair score.",
        )

    def test_far_from_home_has_no_length_bonus(self):
        params = self._base_params()
        params["v2_home_length_priority_radius_miles"] = 80.0
        meta_a = self._base_meta()
        meta_b = self._base_meta()
        meta_a["origin_miles"] = 180.0
        meta_b["origin_miles"] = 190.0

        score_short = self.optimizer._pair_priority_score(meta_a, meta_b, params)
        meta_b["max_unit_length_ft"] = 24.0
        score_long = self.optimizer._pair_priority_score(meta_a, meta_b, params)

        self.assertAlmostEqual(
            score_long,
            score_short,
            places=6,
            msg="No bonus should apply when both loads are outside the home-priority radius.",
        )

    def test_disabled_flag_turns_bonus_off(self):
        params = self._base_params()
        params["v2_home_length_priority_enabled"] = False
        meta_a = self._base_meta()
        meta_b = self._base_meta()

        score_short = self.optimizer._pair_priority_score(meta_a, meta_b, params)
        meta_b["max_unit_length_ft"] = 22.0
        score_long = self.optimizer._pair_priority_score(meta_a, meta_b, params)

        self.assertAlmostEqual(
            score_long,
            score_short,
            places=6,
            msg="Disabling the feature should eliminate length-priority score changes.",
        )


if __name__ == "__main__":
    unittest.main()
