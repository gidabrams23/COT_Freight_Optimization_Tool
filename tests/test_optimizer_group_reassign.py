import unittest

from services.optimizer import Optimizer


class GroupReassignTests(unittest.TestCase):
    def setUp(self):
        self.optimizer = Optimizer.__new__(Optimizer)

        cost_map = {
            "A": 170.0,
            "B": 120.0,
            "C": 160.0,
            "D": 180.0,
            "AB": 300.0,
            "CD": 300.0,
            "ACD": 220.0,
            "BCD": 500.0,
        }
        util_map = {
            "A": 48.0,
            "B": 72.0,
            "C": 51.0,
            "D": 54.0,
            "AB": 63.0,
            "CD": 61.0,
            "ACD": 79.0,
            "BCD": 64.0,
        }

        def fake_build_load(groups, _params, standalone_cost=None):
            keys = "".join(sorted(str((group or {}).get("key") or "") for group in (groups or [])))
            if not keys:
                keys = "EMPTY"
            cost = float(cost_map.get(keys, 9999.0))
            util = float(util_map.get(keys, 50.0))
            return {
                "_merge_id": keys,
                "groups": list(groups or []),
                "origin_plant": "GA",
                "estimated_cost": cost,
                "utilization_pct": util,
                "standalone_cost": float(standalone_cost if standalone_cost is not None else cost),
            }

        self.optimizer._build_load = fake_build_load
        self.optimizer._load_is_multi_order_capacity_violation = lambda _load: False
        self.optimizer._recipient_candidates_for_target = (
            lambda _target, _group_load, recipients, _params, _time_window_days, _limit: list(recipients or [])
        )
        self.optimizer._loads_date_compatible = lambda _a, _b, _window: True
        self.optimizer._detour_allowed = lambda *_args, **_kwargs: True

    @staticmethod
    def _load(group_keys):
        keys = "".join(sorted(group_keys))
        return {
            "_merge_id": keys,
            "groups": [{"key": key} for key in group_keys],
            "origin_plant": "GA",
            "estimated_cost": 300.0,
            "utilization_pct": 60.0,
            "standalone_cost": 300.0,
        }

    def test_reassigns_single_group_when_savings_are_material(self):
        active = {
            "AB": self._load(["A", "B"]),
            "CD": self._load(["C", "D"]),
        }
        params = {
            "max_detour_pct": 15.0,
            "v2_group_reassign_passes": 1,
            "v2_group_reassign_min_savings": 25.0,
            "v2_group_reassign_candidate_limit": 8,
        }

        updated = self.optimizer._reassign_single_group_outliers(
            dict(active),
            params,
            time_window_days=None,
        )

        updated_keys = set(updated.keys())
        self.assertEqual(updated_keys, {"B", "ACD"})
        updated_total = sum((load.get("estimated_cost") or 0) for load in updated.values())
        self.assertLess(updated_total, 600.0)

    def test_skips_reassign_when_savings_threshold_not_met(self):
        active = {
            "AB": self._load(["A", "B"]),
            "CD": self._load(["C", "D"]),
        }
        params = {
            "max_detour_pct": 15.0,
            "v2_group_reassign_passes": 1,
            "v2_group_reassign_min_savings": 9999.0,
            "v2_group_reassign_candidate_limit": 8,
        }

        updated = self.optimizer._reassign_single_group_outliers(
            dict(active),
            params,
            time_window_days=None,
        )

        self.assertEqual(set(updated.keys()), {"AB", "CD"})


if __name__ == "__main__":
    unittest.main()
