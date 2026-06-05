import unittest

from services.optimizer import Optimizer


class GroupReassignTests(unittest.TestCase):
    def setUp(self):
        self.optimizer = Optimizer.__new__(Optimizer)
        self.optimizer.zip_coords = {}
        self.optimizer.cost_calculator = type(
            "CostCalculatorStub",
            (),
            {"distance": staticmethod(lambda _left, _right: 0.0)},
        )()

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

    def test_cross_order_stack_sharing_is_enabled_by_default_for_multi_group_v2(self):
        params = {"algorithm_version": "v2"}
        groups = [{"key": "A"}, {"key": "B"}]

        enabled = self.optimizer._allow_cross_order_stack_sharing(params, groups)

        self.assertTrue(enabled)

    def test_cross_order_stack_sharing_can_only_be_enabled_explicitly(self):
        params = {
            "algorithm_version": "v2",
            "v2_allow_cross_order_stack_sharing": True,
        }
        groups = [{"key": "A"}, {"key": "B"}]

        enabled = self.optimizer._allow_cross_order_stack_sharing(params, groups)

        self.assertTrue(enabled)

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

    def test_recipient_candidates_rank_against_group_being_moved(self):
        target = {"_merge_id": "SOURCE", "destination_state": "NY", "utilization_pct": 60.0}
        group_load = {"_merge_id": "GROUP", "destination_state": "VA", "utilization_pct": 30.0}
        recipient_va = {"_merge_id": "VA", "destination_state": "VA", "utilization_pct": 65.0}
        recipient_ny = {"_merge_id": "NY", "destination_state": "NY", "utilization_pct": 65.0}

        self.optimizer._load_pair_meta = lambda load, _params: {"state": load.get("destination_state"), "utilization": load.get("utilization_pct")}
        self.optimizer._pair_priority_score = lambda meta_a, meta_b, _params: 0.0 if meta_a.get("state") == meta_b.get("state") else 100.0
        self.optimizer._loads_compatible = lambda _a, _b, _r, _t, _p: True

        ranked = Optimizer._recipient_candidates_for_target(
            self.optimizer,
            target,
            group_load,
            [recipient_ny, recipient_va],
            {},
            time_window_days=None,
            limit=2,
        )

        self.assertEqual([load.get("_merge_id") for load in ranked], ["VA", "NY"])

    def test_reassign_can_accept_negative_savings_to_promote_same_state_full_load(self):
        active = {
            "AB": {
                "_merge_id": "AB",
                "groups": [{"key": "A"}, {"key": "B"}],
                "origin_plant": "GA",
                "destination_state": "MI",
                "estimated_cost": 300.0,
                "utilization_pct": 55.0,
                "standalone_cost": 300.0,
            },
            "CD": {
                "_merge_id": "CD",
                "groups": [{"key": "C"}, {"key": "D"}],
                "origin_plant": "GA",
                "destination_state": "MI",
                "estimated_cost": 300.0,
                "utilization_pct": 80.0,
                "standalone_cost": 300.0,
            },
        }
        cost_map = {"A": 170.0, "B": 160.0, "CD": 300.0, "BCD": 320.0}
        util_map = {"A": 28.0, "B": 30.0, "CD": 80.0, "BCD": 93.0}

        def fake_build_load(groups, _params, standalone_cost=None):
            keys = "".join(sorted(str((group or {}).get("key") or "") for group in (groups or [])))
            return {
                "_merge_id": keys,
                "groups": list(groups or []),
                "origin_plant": "GA",
                "destination_state": "MI",
                "estimated_cost": float(cost_map.get(keys, 999.0)),
                "utilization_pct": float(util_map.get(keys, 40.0)),
                "standalone_cost": float(standalone_cost if standalone_cost is not None else cost_map.get(keys, 999.0)),
            }

        self.optimizer._build_load = fake_build_load
        self.optimizer._load_geo_penalty = lambda *_args, **_kwargs: 0.0
        self.optimizer._stop_penalty_units = lambda *_args, **_kwargs: 0.0
        self.optimizer._reassign_directional_bonus = lambda *_args, **_kwargs: 0.0

        updated = self.optimizer._reassign_single_group_outliers(
            dict(active),
            {
                "max_detour_pct": 15.0,
                "v2_group_reassign_passes": 1,
                "v2_group_reassign_min_savings": 25.0,
                "v2_group_reassign_candidate_limit": 8,
                "v2_full_load_promotion_min_savings_floor": -50.0,
            },
            time_window_days=None,
        )

        self.assertEqual(set(updated.keys()), {"A", "BCD"})

    def test_cannibalize_weak_loads_targets_sub_threshold_loads(self):
        active = {
            "LOW": {
                "_merge_id": "LOW",
                "utilization_pct": 22.0,
                "groups": [{"key": "L"}],
            },
            "HIGH": {
                "_merge_id": "HIGH",
                "utilization_pct": 82.0,
                "groups": [{"key": "H"}],
            },
        }
        objective_weights = {}
        params = {
            "v2_weak_cannibalize_passes": 1,
            "v2_weak_cannibalize_target_util": 65.0,
            "v2_fd_target_util": 55.0,
            "v2_fd_candidate_limit": 12,
        }
        seen = {"target_ids": [], "stage_params": []}

        self.optimizer._fd_rebalance_targets = lambda active_loads, _params, _window: [
            active_loads["LOW"],
            active_loads["HIGH"],
        ]

        def fake_absorb(target_id, active_loads, stage_params, _weights, _window):
            seen["target_ids"].append(target_id)
            seen["stage_params"].append(dict(stage_params))
            if target_id != "LOW":
                return None
            return {
                "HIGHMERGED": {
                    "_merge_id": "HIGHMERGED",
                    "utilization_pct": 78.0,
                    "groups": [{"key": "H"}, {"key": "L"}],
                }
            }

        self.optimizer._try_absorb_target_load = fake_absorb

        updated = self.optimizer._cannibalize_weak_loads(
            dict(active),
            params,
            objective_weights,
            time_window_days=None,
        )

        self.assertEqual(seen["target_ids"], ["LOW"])
        self.assertEqual(set(updated.keys()), {"HIGHMERGED"})
        self.assertGreaterEqual(
            int(seen["stage_params"][0].get("v2_fd_candidate_limit") or 0),
            180,
        )
        self.assertEqual(
            float(seen["stage_params"][0].get("v2_fd_target_util") or 0.0),
            65.0,
        )

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

    def test_objective_bonus_rewards_crossing_fill_floor(self):
        objective_weights = {
            "low_util_threshold": 70.0,
            "lambda_low_util_count": 0.0,
            "lambda_low_util_depth": 0.0,
            "lambda_upper_two_across": 0.0,
            "full_load_target_pct": 100.0,
            "lambda_full_load_count": 0.0,
            "lambda_full_load_depth": 0.0,
            "lambda_fill_to_full": 0.0,
            "fill_floor_target_pct": 65.0,
            "lambda_fill_floor_count": 140.0,
            "lambda_fill_floor_depth": 10.0,
            "lambda_fill_floor_progress": 8.0,
            "lambda_weak_tail_absorb": 0.0,
        }
        load_a = {"utilization_pct": 52.0}
        load_b = {"utilization_pct": 18.0}
        merged_61 = {"utilization_pct": 61.0}
        merged_69 = {"utilization_pct": 69.0}

        bonus_61 = self.optimizer._objective_bonus_for_merge(
            load_a,
            load_b,
            merged_61,
            objective_weights,
        )
        bonus_69 = self.optimizer._objective_bonus_for_merge(
            load_a,
            load_b,
            merged_69,
            objective_weights,
        )

        self.assertGreater(bonus_69, bonus_61)

    def test_objective_bonus_rewards_absorbing_weak_tail_into_strong_load(self):
        objective_weights = {
            "low_util_threshold": 70.0,
            "lambda_low_util_count": 0.0,
            "lambda_low_util_depth": 0.0,
            "lambda_upper_two_across": 0.0,
            "full_load_target_pct": 100.0,
            "lambda_full_load_count": 0.0,
            "lambda_full_load_depth": 0.0,
            "lambda_fill_to_full": 0.0,
            "fill_floor_target_pct": 65.0,
            "lambda_fill_floor_count": 0.0,
            "lambda_fill_floor_depth": 0.0,
            "lambda_fill_floor_progress": 0.0,
            "lambda_weak_tail_absorb": 160.0,
        }
        weak_tail = {"utilization_pct": 25.0}
        strong_anchor = {"utilization_pct": 72.0}
        medium_anchor = {"utilization_pct": 58.0}
        merged = {"utilization_pct": 78.0}

        strong_bonus = self.optimizer._objective_bonus_for_merge(
            strong_anchor,
            weak_tail,
            merged,
            objective_weights,
        )
        medium_bonus = self.optimizer._objective_bonus_for_merge(
            medium_anchor,
            weak_tail,
            merged,
            objective_weights,
        )

        self.assertGreater(strong_bonus, medium_bonus)

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
            {
                "optimize_focus": "utilization_first",
                "v2_multi_start_include_aggressive_fill": True,
            }
        )

        self.assertEqual([name for name, _params in profiles], ["planner_approval", "aggressive_fill"])
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
            ("planner_approval", {"seed_profile": "planner_approval"}),
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
            if params.get("seed_profile") == "planner_approval":
                return [{"_merge_id": "planner", "utilization_pct": 82.0, "groups": [{"key": "BAL"}]}]
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

    def test_select_best_v2_solution_prefers_more_clean_high_util_loads(self):
        cleaner = [
            {"_merge_id": "C1", "utilization_pct": 96.0, "groups": [{"key": "A"}]},
            {"_merge_id": "C2", "utilization_pct": 92.0, "groups": [{"key": "B"}]},
            {"_merge_id": "C3", "utilization_pct": 88.0, "groups": [{"key": "C"}]},
            {"_merge_id": "C4", "utilization_pct": 86.0, "groups": [{"key": "D"}]},
            {"_merge_id": "C5", "utilization_pct": 84.0, "groups": [{"key": "E"}]},
        ]
        messier = [
            {"_merge_id": "M1", "utilization_pct": 97.0, "groups": [{"key": "A"}]},
            {"_merge_id": "M2", "utilization_pct": 89.0, "groups": [{"key": "B"}]},
            {"_merge_id": "M3", "utilization_pct": 88.0, "groups": [{"key": "C"}]},
            {"_merge_id": "M4", "utilization_pct": 76.0, "groups": [{"key": "D"}]},
            {"_merge_id": "M5", "utilization_pct": 69.0, "groups": [{"key": "E"}]},
        ]

        selected = self.optimizer._select_best_v2_solution(
            [cleaner, messier],
            {"optimize_focus": "planner_approval"},
        )

        self.assertEqual(len(selected), 5)
        self.assertEqual({load.get("_merge_id") for load in selected}, {"C1", "C2", "C3", "C4", "C5"})

    def test_seed_profile_variants_default_include_aggressive_fill_when_enabled(self):
        profiles = self.optimizer._seed_profile_variants_v2(
            {"v2_multi_start_include_aggressive_fill": True}
        )

        self.assertEqual([name for name, _params in profiles], ["planner_approval", "aggressive_fill"])

    def test_effective_fill_prefers_practical_fill_when_deck_is_nearly_full(self):
        load = {
            "utilization_pct": 22.6,
            "practical_fill_pct": 98.1,
            "total_linear_feet": 52.0,
            "capacity_feet": 53.0,
        }

        self.assertAlmostEqual(self.optimizer._effective_fill_pct(load), 98.1, places=1)

    def test_state_cohort_seed_loads_keep_cohorts_separate(self):
        groups = [
            {"key": "MI1", "state": "MI", "cust_name": "TSC", "max_unit_length_ft": 20.0, "total_length_ft": 20.0},
            {"key": "MI2", "state": "MI", "cust_name": "TSC", "max_unit_length_ft": 20.0, "total_length_ft": 20.0},
            {"key": "NC1", "state": "NC", "cust_name": "TSC", "max_unit_length_ft": 20.0, "total_length_ft": 20.0},
            {"key": "NC2", "state": "NC", "cust_name": "TSC", "max_unit_length_ft": 20.0, "total_length_ft": 20.0},
        ]
        singleton_loads = [
            {"_merge_id": group["key"], "groups": [group], "estimated_cost": 100.0, "standalone_cost": 100.0, "utilization_pct": 20.0, "effective_fill_pct": 20.0}
            for group in groups
        ]

        def fake_build_load(selected_groups, _params, standalone_cost=None):
            states = sorted({group["state"] for group in selected_groups})
            keys = [group["key"] for group in selected_groups]
            return {
                "_merge_id": "".join(keys),
                "groups": list(selected_groups),
                "estimated_cost": 100.0,
                "standalone_cost": float(standalone_cost if standalone_cost is not None else 100.0),
                "utilization_pct": 20.0 * len(selected_groups),
                "effective_fill_pct": 20.0 * len(selected_groups),
                "unique_states": states,
            }

        self.optimizer._build_load = fake_build_load
        self.optimizer._prebatch_should_keep_filling = lambda load, _params: len(load.get("groups") or []) < 2
        self.optimizer._can_add_group = lambda current_groups, candidate_group, _params: current_groups[0]["state"] == candidate_group["state"]
        self.optimizer._load_is_multi_order_capacity_violation = lambda _load: False
        self.optimizer._prebatch_candidate_score = lambda _current_load, _candidate_load, merged_load, _params: len(merged_load.get("groups") or [])

        seed_loads = self.optimizer._build_state_cohort_seed_loads_v2(
            groups,
            singleton_loads,
            {"v2_state_cohort_prebatch_enabled": True},
        )

        self.assertEqual(len(seed_loads), 2)
        self.assertEqual(
            sorted(tuple(load.get("unique_states") or []) for load in seed_loads),
            [("MI",), ("NC",)],
        )

    def test_loads_compatible_blocks_cross_state_rescue_for_decent_seed_loads(self):
        load_a = {
            "origin_plant": "GA",
            "destination_state": "MI",
            "unique_states_count": 1,
            "lines": [{"so_num": "A1"}, {"so_num": "A2"}],
            "effective_fill_pct": 75.0,
            "stop_coords": [],
        }
        load_b = {
            "origin_plant": "GA",
            "destination_state": "NC",
            "unique_states_count": 1,
            "lines": [{"so_num": "B1"}, {"so_num": "B2"}],
            "effective_fill_pct": 60.0,
            "stop_coords": [],
        }

        self.optimizer._loads_mix_compatible = lambda _a, _b: True
        self.optimizer._loads_date_compatible = lambda _a, _b, _window: True
        self.optimizer._loads_geo_compatible = lambda _a, _b, _radius: True

        compatible = self.optimizer._loads_compatible(
            load_a,
            load_b,
            radius=100.0,
            time_window_days=None,
            params={"v2_cross_state_rescue_fill_threshold": 40.0},
        )

        self.assertFalse(compatible)

    def test_cross_state_pair_allowed_blocks_far_apart_states(self):
        load_a = {"destination_state": "MI"}
        load_b = {"destination_state": "NC"}

        self.optimizer._is_directionally_on_way_pair = lambda _a, _b, _params: False
        self.optimizer._min_distance_between_loads = lambda _a, _b: 600.0

        allowed = self.optimizer._cross_state_pair_allowed(load_a, load_b, {})

        self.assertFalse(allowed)

    def test_cross_state_pair_allowed_permits_nearby_on_way_states(self):
        load_a = {"destination_state": "NJ"}
        load_b = {"destination_state": "PA"}

        self.optimizer._is_directionally_on_way_pair = lambda _a, _b, _params: True
        self.optimizer._min_distance_between_loads = lambda _a, _b: 42.0

        allowed = self.optimizer._cross_state_pair_allowed(load_a, load_b, {})

        self.assertTrue(allowed)

    def test_cross_state_pair_allowed_blocks_expanding_already_mixed_load(self):
        mixed_load = {
            "destination_state": "MI,OH",
            "unique_states_count": 2,
            "lines": [{"state": "MI"}, {"state": "OH"}],
        }
        single_load = {
            "destination_state": "SC",
            "unique_states_count": 1,
            "lines": [{"state": "SC"}],
        }

        allowed = self.optimizer._cross_state_pair_allowed(mixed_load, single_load, {})

        self.assertFalse(allowed)

    def test_same_state_geo_escape_allows_weak_same_state_pair(self):
        load_a = {"destination_state": "MI", "utilization_pct": 45.0}
        load_b = {"destination_state": "MI", "utilization_pct": 60.0}

        allowed = self.optimizer._allow_v2_geo_escape(
            load_a,
            load_b,
            {"algorithm_version": "v2", "v2_same_state_geo_escape_threshold": 65.0},
        )

        self.assertTrue(allowed)

    def test_can_add_group_respects_cross_state_compatibility(self):
        current_groups = [{"key": "MI1", "state": "MI"}]
        candidate_group = {"key": "NC1", "state": "NC"}

        def fake_build_load(groups, _params, standalone_cost=None):
            state = (groups or [{}])[0].get("state") or ""
            return {
                "_merge_id": "".join(group.get("key") or "" for group in (groups or [])),
                "groups": list(groups or []),
                "origin_plant": "GA",
                "destination_state": state,
                "estimated_cost": 100.0,
                "utilization_pct": 40.0,
                "standalone_cost": float(standalone_cost if standalone_cost is not None else 100.0),
            }

        self.optimizer._build_load = fake_build_load
        self.optimizer._groups_mix_compatible = lambda _groups: True
        self.optimizer._effective_time_window_days = lambda **_kwargs: None
        self.optimizer._loads_compatible = lambda _a, _b, _r, _t, _p: False
        self.optimizer._check_stacking_compatible = lambda _groups: True
        self.optimizer._stack_config_for_groups = lambda _groups, _params: {}
        self.optimizer._is_multi_order_capacity_violation = lambda _groups, _stack: False

        allowed = self.optimizer._can_add_group(current_groups, candidate_group, {"geo_radius": 100.0})

        self.assertFalse(allowed)

    def test_recipient_candidates_filter_out_incompatible_cross_state_recipient(self):
        target = {"_merge_id": "SOURCE", "destination_state": "MI", "utilization_pct": 60.0}
        group_load = {"_merge_id": "GROUP", "destination_state": "NC", "utilization_pct": 30.0}
        recipient = {"_merge_id": "R1", "destination_state": "MI", "utilization_pct": 65.0}

        self.optimizer._load_pair_meta = lambda load, _params: {"state": load.get("destination_state"), "utilization": load.get("utilization_pct")}
        self.optimizer._pair_priority_score = lambda _a, _b, _params: 0.0
        self.optimizer._loads_compatible = lambda _a, _b, _r, _t, _p: False

        ranked = Optimizer._recipient_candidates_for_target(
            self.optimizer,
            target,
            group_load,
            [recipient],
            {"geo_radius": 100.0},
            time_window_days=None,
            limit=2,
        )

        self.assertEqual(ranked, [])


if __name__ == "__main__":
    unittest.main()
