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

    def test_objective_bonus_rewards_additional_upper_two_across_usage(self):
        objective_weights = {
            "low_util_threshold": 70.0,
            "lambda_low_util_count": 560.0,
            "lambda_low_util_depth": 24.0,
            "lambda_upper_two_across": 48.0,
        }
        load_a = {"utilization_pct": 62.0, "upper_two_across_applied_count": 0}
        load_b = {"utilization_pct": 63.0, "upper_two_across_applied_count": 0}
        merged_low = {"utilization_pct": 70.0, "upper_two_across_applied_count": 0}
        merged_high = {"utilization_pct": 70.0, "upper_two_across_applied_count": 2}

        bonus_low = self.optimizer._objective_bonus_for_merge(
            load_a,
            load_b,
            merged_low,
            objective_weights,
        )
        bonus_high = self.optimizer._objective_bonus_for_merge(
            load_a,
            load_b,
            merged_high,
            objective_weights,
        )

        self.assertGreater(bonus_high, bonus_low)

    def test_objective_bonus_rewards_merges_that_create_full_loads(self):
        objective_weights = {
            "low_util_threshold": 70.0,
            "lambda_low_util_count": 560.0,
            "lambda_low_util_depth": 24.0,
            "lambda_upper_two_across": 0.0,
            "full_load_target_pct": 90.0,
            "lambda_full_load_count": 420.0,
            "lambda_full_load_depth": 36.0,
            "lambda_fill_to_full": 8.0,
        }
        load_a = {"utilization_pct": 62.0, "upper_two_across_applied_count": 0}
        load_b = {"utilization_pct": 64.0, "upper_two_across_applied_count": 0}
        merged_not_full = {"utilization_pct": 82.0, "upper_two_across_applied_count": 0}
        merged_full = {"utilization_pct": 93.0, "upper_two_across_applied_count": 0}

        bonus_not_full = self.optimizer._objective_bonus_for_merge(
            load_a,
            load_b,
            merged_not_full,
            objective_weights,
        )
        bonus_full = self.optimizer._objective_bonus_for_merge(
            load_a,
            load_b,
            merged_full,
            objective_weights,
        )

        self.assertGreater(bonus_full, bonus_not_full)

    def test_objective_bonus_continuously_rewards_closer_to_100(self):
        objective_weights = {
            "low_util_threshold": 70.0,
            "lambda_low_util_count": 0.0,
            "lambda_low_util_depth": 0.0,
            "lambda_upper_two_across": 0.0,
            "full_load_target_pct": 100.0,
            "lambda_full_load_count": 0.0,
            "lambda_full_load_depth": 0.0,
            "lambda_fill_to_full": 8.0,
        }
        load_a = {"utilization_pct": 85.0}
        load_b = {"utilization_pct": 85.0}
        merged_93 = {"utilization_pct": 93.0}
        merged_99 = {"utilization_pct": 99.0}

        bonus_93 = self.optimizer._objective_bonus_for_merge(
            load_a,
            load_b,
            merged_93,
            objective_weights,
        )
        bonus_99 = self.optimizer._objective_bonus_for_merge(
            load_a,
            load_b,
            merged_99,
            objective_weights,
        )
        self.assertGreater(bonus_99, bonus_93)

    def test_objective_bonus_rewards_state_purity(self):
        objective_weights = {
            "low_util_threshold": 70.0,
            "lambda_low_util_count": 0.0,
            "lambda_low_util_depth": 0.0,
            "lambda_upper_two_across": 0.0,
            "full_load_target_pct": 100.0,
            "lambda_full_load_count": 0.0,
            "lambda_full_load_depth": 0.0,
            "lambda_fill_to_full": 0.0,
            "lambda_state_purity": 140.0,
        }
        load_a = {"utilization_pct": 80.0, "unique_states_count": 1, "destination_state": "MI"}
        load_b = {"utilization_pct": 80.0, "unique_states_count": 1, "destination_state": "MI"}
        merged_same = {"utilization_pct": 85.0, "unique_states_count": 1, "destination_state": "MI"}
        merged_mixed = {"utilization_pct": 85.0, "unique_states_count": 2, "destination_state": "MI"}

        bonus_same = self.optimizer._objective_bonus_for_merge(
            load_a,
            load_b,
            merged_same,
            objective_weights,
        )
        bonus_mixed = self.optimizer._objective_bonus_for_merge(
            load_a,
            load_b,
            merged_mixed,
            objective_weights,
        )
        self.assertGreater(bonus_same, bonus_mixed)

    def test_objective_bonus_prefers_same_state_and_penalizes_cross_state_merge(self):
        objective_weights = {
            "low_util_threshold": 70.0,
            "lambda_low_util_count": 0.0,
            "lambda_low_util_depth": 0.0,
            "lambda_upper_two_across": 0.0,
            "full_load_target_pct": 100.0,
            "lambda_full_load_count": 0.0,
            "lambda_full_load_depth": 0.0,
            "lambda_fill_to_full": 0.0,
            "lambda_state_purity": 0.0,
            "lambda_same_state_merge": 220.0,
            "lambda_cross_state_merge": 1200.0,
        }
        load_mi = {"utilization_pct": 82.0, "unique_states_count": 1, "destination_state": "MI"}
        load_mi_2 = {"utilization_pct": 80.0, "unique_states_count": 1, "destination_state": "MI"}
        load_tn = {"utilization_pct": 80.0, "unique_states_count": 1, "destination_state": "TN"}
        merged_same_state = {"utilization_pct": 86.0, "unique_states_count": 1, "destination_state": "MI"}
        merged_cross_state = {"utilization_pct": 86.0, "unique_states_count": 2, "destination_state": "MI,TN"}

        same_bonus = self.optimizer._objective_bonus_for_merge(
            load_mi,
            load_mi_2,
            merged_same_state,
            objective_weights,
        )
        cross_bonus = self.optimizer._objective_bonus_for_merge(
            load_mi,
            load_tn,
            merged_cross_state,
            objective_weights,
        )
        self.assertGreater(same_bonus, cross_bonus)

    def test_pair_priority_prefers_shared_store_and_short_upper_candidates(self):
        params = {
            "trailer_type": "STEP_DECK",
            "upper_two_across_max_length_ft": 7.0,
        }
        baseline_a = {
            "state": "MI",
            "utilization": 45.0,
            "origin_miles": 100.0,
            "bearing": 10.0,
            "due_anchor": 1,
            "effective_due_window_days": 0,
            "max_unit_length_ft": 7.0,
            "store_codes": [],
            "short_upper_group_count": 1,
        }
        baseline_b = {
            "state": "MI",
            "utilization": 45.0,
            "origin_miles": 102.0,
            "bearing": 11.0,
            "due_anchor": 1,
            "effective_due_window_days": 0,
            "max_unit_length_ft": 7.0,
            "store_codes": [],
            "short_upper_group_count": 1,
        }
        shared_store_b = dict(baseline_b, store_codes=["TSC00123"])
        shared_store_a = dict(baseline_a, store_codes=["TSC00123"])
        long_item_b = dict(baseline_b, short_upper_group_count=0, max_unit_length_ft=18.0)

        shared_score = self.optimizer._pair_priority_score(shared_store_a, shared_store_b, params)
        no_store_score = self.optimizer._pair_priority_score(baseline_a, baseline_b, params)
        long_item_score = self.optimizer._pair_priority_score(shared_store_a, long_item_b, params)

        self.assertLess(shared_score, no_store_score)
        self.assertLess(shared_score, long_item_score)

    def test_stack_aware_prebatch_builds_multi_group_seed_loads(self):
        util_map = {
            "A": 30.0,
            "AB": 63.0,
            "ABC": 94.0,
            "AC": 58.0,
            "B": 32.0,
            "BC": 60.0,
            "C": 31.0,
        }

        def fake_build_load(groups, _params, standalone_cost=None):
            keys = "".join(sorted(str((group or {}).get("key") or "") for group in (groups or [])))
            if not keys:
                keys = "EMPTY"
            store_codes = sorted(
                {
                    str(store_code or "").strip().upper()
                    for group in (groups or [])
                    for store_code in ((group or {}).get("store_codes") or [])
                    if str(store_code or "").strip()
                }
            )
            return {
                "_merge_id": keys,
                "groups": list(groups or []),
                "origin_plant": "GA",
                "estimated_cost": 100.0,
                "utilization_pct": float(util_map.get(keys, 50.0)),
                "standalone_cost": float(standalone_cost if standalone_cost is not None else 100.0),
                "stop_count": len(list(groups or [])),
                "destination_state": "MI",
                "store_codes": store_codes,
                "upper_two_across_applied_count": 0,
            }

        self.optimizer._build_load = fake_build_load
        self.optimizer._loads_compatible = lambda *_args, **_kwargs: True
        self.optimizer._can_add_group = lambda _current, _candidate, _params: True
        self.optimizer._load_is_multi_order_capacity_violation = lambda _load: False
        self.optimizer._prebatch_candidate_groups = (
            lambda _current_load, remaining_groups, _singleton_by_key, _params: list(remaining_groups)
        )
        self.optimizer._prebatch_candidate_score = (
            lambda _current_load, _candidate_load, merged_load, _params: float(merged_load.get("utilization_pct") or 0.0)
        )

        groups = [
            {"key": "A", "store_codes": ["TSC00123"], "max_unit_length_ft": 18.0, "total_length_ft": 18.0},
            {"key": "B", "store_codes": ["TSC00123"], "max_unit_length_ft": 18.0, "total_length_ft": 18.0},
            {"key": "C", "store_codes": [], "max_unit_length_ft": 7.0, "total_length_ft": 7.0},
        ]
        params = {
            "v2_stack_aware_prebatch_enabled": True,
            "v2_prebatch_target_util": 90.0,
            "upper_two_across_max_length_ft": 7.0,
            "trailer_type": "STEP_DECK",
            "algorithm_version": "v2",
            "enforce_time_window": False,
        }

        loads = self.optimizer._build_seed_loads_v2(groups, params)

        self.assertEqual(len(loads), 1)
        self.assertEqual(loads[0].get("_merge_id"), "ABC")
        self.assertEqual(float(loads[0].get("utilization_pct") or 0.0), 94.0)

    def test_seed_profile_variants_include_aggressive_fill_profile(self):
        profiles = self.optimizer._seed_profile_variants_v2(
            {"v2_multi_start_include_aggressive_fill": True}
        )

        self.assertEqual([name for name, _params in profiles], ["balanced", "aggressive_fill"])
        aggressive_params = profiles[1][1]
        self.assertGreater(
            float(aggressive_params.get("v2_prebatch_target_util") or 0.0),
            92.0,
        )
        self.assertGreater(
            float(aggressive_params.get("v2_shared_store_priority_bonus") or 0.0),
            80.0,
        )

    def test_build_optimized_loads_v2_selects_best_multi_start_profile(self):
        self.optimizer._build_order_groups = lambda _params: [{"key": "A"}, {"key": "B"}]
        self.optimizer._runtime_tuned_params = lambda params, _count: dict(params)
        self.optimizer._build_load = lambda groups, _params, standalone_cost=None: {
            "_merge_id": "".join(sorted(str(group.get("key") or "") for group in (groups or []))),
            "groups": list(groups or []),
            "origin_plant": "GA",
            "estimated_cost": 100.0,
            "utilization_pct": 50.0,
            "standalone_cost": float(standalone_cost if standalone_cost is not None else 100.0),
        }
        self.optimizer._seed_profile_variants_v2 = lambda _params: [
            ("balanced", {"seed_profile": "balanced"}),
            ("aggressive_fill", {"seed_profile": "aggressive_fill"}),
        ]
        self.optimizer._build_seed_loads_v2 = lambda _groups, params: [
            {
                "_merge_id": str(params.get("seed_profile") or "seed"),
                "groups": [{"key": str(params.get("seed_profile") or "seed")}],
                "origin_plant": "GA",
                "estimated_cost": 100.0,
                "utilization_pct": 60.0,
                "standalone_cost": 100.0,
            }
        ]

        def fake_optimize(initial_loads, params):
            if params.get("seed_profile") == "aggressive_fill":
                return [{"_merge_id": "winner", "utilization_pct": 95.0, "groups": [{"key": "WIN"}]}]
            if params.get("seed_profile") == "balanced":
                return [{"_merge_id": "balanced", "utilization_pct": 82.0, "groups": [{"key": "BAL"}]}]
            return [{"_merge_id": "singleton", "utilization_pct": 80.0, "groups": list(initial_loads[0].get("groups") or [])}]

        self.optimizer._optimize_load_set_v2 = fake_optimize

        loads = self.optimizer.build_optimized_loads_v2(
            {
                "origin_plant": "GA",
                "v2_stack_aware_prebatch_enabled": True,
                "v2_multi_start_enabled": True,
                "v2_multi_start_max_groups": 10,
            }
        )

        self.assertEqual(len(loads), 1)
        self.assertEqual(loads[0].get("_merge_id"), "winner")
        self.assertEqual(float(loads[0].get("utilization_pct") or 0.0), 95.0)


if __name__ == "__main__":
    unittest.main()
