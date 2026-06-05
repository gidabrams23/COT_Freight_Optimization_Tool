from collections import Counter
from datetime import datetime, date
import heapq
import json
import math
import re

import db
from services import geo_utils, order_categories, stack_calculator
from services import customer_rules
from services.cost_calculator import (
    CostCalculator,
    build_rate_lookup,
    resolve_fuel_surcharge,
    DEFAULT_RATE_PER_MILE,
)

LOW_UTIL_THRESHOLD_PCT = 70.0
DEFAULT_V2_LAMBDA_LOW_UTIL_COUNT = 560.0
DEFAULT_V2_LAMBDA_LOW_UTIL_DEPTH = 24.0
DEFAULT_V2_RESCUE_PASSES = 4
DEFAULT_V2_RESCUE_DETOUR_FLOOR = 35.0
DEFAULT_V2_GEO_ESCAPE_THRESHOLD = 40.0
DEFAULT_V2_DETOUR_ESCAPE_FLOOR = 80.0
DEFAULT_V2_GRADE_RESCUE_PASSES = 4
DEFAULT_V2_GRADE_RESCUE_MIN_SAVINGS = -60.0
DEFAULT_V2_GRADE_RESCUE_MIN_GAIN = 0.0
DEFAULT_V2_GRADE_RESCUE_DETOUR_FLOOR = 160.0
DEFAULT_V2_GRADE_REPAIR_LIMIT = 12
DEFAULT_V2_GRADE_REPAIR_MIN_SAVINGS = -350.0
DEFAULT_V2_FD_REBALANCE_PASSES = 3
DEFAULT_V2_FD_TARGET_UTIL = 55.0
DEFAULT_V2_FD_ABSORB_MAX_COST_INCREASE_F = 5000.0
DEFAULT_V2_FD_ABSORB_MAX_COST_INCREASE_D = 2200.0
DEFAULT_V2_FD_ABSORB_DETOUR_CAP = 999.0
DEFAULT_V2_FD_CANDIDATE_LIMIT = 120
DEFAULT_V2_WEAK_CANNIBALIZE_PASSES = 3
DEFAULT_V2_WEAK_CANNIBALIZE_TARGET_UTIL = 65.0
DEFAULT_V2_WEAK_CANNIBALIZE_CANDIDATE_LIMIT = 180
DEFAULT_V2_GROUP_REASSIGN_PASSES = 3
DEFAULT_V2_GROUP_REASSIGN_MIN_SAVINGS = 25.0
DEFAULT_V2_GROUP_REASSIGN_CANDIDATE_LIMIT = 16
DEFAULT_V2_FULL_LOAD_PROMOTION_MIN_SAVINGS_FLOOR = -350.0
DEFAULT_V2_ALLOW_ORDER_INTERLEAVE = True
DEFAULT_V2_PAIR_NEIGHBORS = 18
DEFAULT_V2_PAIR_NEIGHBORS_LOW_UTIL = 56
DEFAULT_V2_INCREMENTAL_NEIGHBORS = 20
DEFAULT_V2_ONWAY_BEARING_DEG = 35.0
DEFAULT_V2_ONWAY_RADIAL_GAP_MILES = 500.0
DEFAULT_V2_CROSS_STATE_ONWAY_BEARING_DEG = 18.0
DEFAULT_V2_CROSS_STATE_RADIAL_GAP_MILES = 180.0
DEFAULT_V2_CROSS_STATE_MIN_DISTANCE_MILES = 140.0
DEFAULT_V2_DIRECTIONAL_DETOUR_FLOOR = 95.0
DEFAULT_V2_SAME_STATE_GEO_ESCAPE_THRESHOLD = 65.0
DEFAULT_V2_SAME_STATE_DETOUR_ESCAPE_FLOOR = 180.0
DEFAULT_V2_FAST_TUNE_THRESHOLD = 400
DEFAULT_V2_FAST_TUNE_HIGH_THRESHOLD = 800
DEFAULT_V2_MEDIUM_TUNE_THRESHOLD = 40
DEFAULT_V2_HOME_LENGTH_PRIORITY_ENABLED = True
DEFAULT_V2_HOME_LENGTH_PRIORITY_RADIUS_MILES = 250.0
DEFAULT_V2_HOME_LENGTH_PRIORITY_THRESHOLD_FT = 12.0
DEFAULT_V2_HOME_LENGTH_PRIORITY_WEIGHT = 1.0
DEFAULT_V2_HOME_LENGTH_PRIORITY_MAX_BONUS = 12.0
DEFAULT_V2_SHARED_STORE_PRIORITY_BONUS = 80.0
DEFAULT_V2_SHARED_STORE_REASSIGN_BONUS = 220.0
DEFAULT_V2_SHARED_STORE_MIN_SAVINGS_FLOOR = -150.0
DEFAULT_V2_SHORT_UPPER_PAIR_PRIORITY_BONUS = 18.0
DEFAULT_V2_STACK_AWARE_PREBATCH_ENABLED = True
DEFAULT_V2_PREBATCH_TARGET_UTIL = 92.0
DEFAULT_V2_PREBATCH_CANDIDATE_LIMIT = 18
DEFAULT_V2_PREBATCH_SHARED_STORE_BONUS = 320.0
DEFAULT_V2_PREBATCH_STORE_DUPLICATE_SEED_BONUS = 24.0
DEFAULT_V2_PREBATCH_SAVINGS_WEIGHT = 0.35
DEFAULT_V2_PREBATCH_UTIL_GAIN_WEIGHT = 18.0
DEFAULT_V2_PREBATCH_TWO_ACROSS_GAIN_BONUS = 80.0
DEFAULT_V2_PREBATCH_STOP_DELTA_PENALTY = 8.0
DEFAULT_V2_MULTI_START_ENABLED = True
DEFAULT_V2_MULTI_START_MAX_GROUPS = 220
DEFAULT_V2_MULTI_START_INCLUDE_AGGRESSIVE_FILL = True
DEFAULT_V2_AGGRESSIVE_PREBATCH_TARGET_UTIL = 98.0
DEFAULT_V2_AGGRESSIVE_PREBATCH_CANDIDATE_LIMIT = 24
DEFAULT_V2_AGGRESSIVE_PREBATCH_SAVINGS_WEIGHT = 0.2
DEFAULT_V2_AGGRESSIVE_SHARED_STORE_PRIORITY_BONUS = 120.0
DEFAULT_V2_AGGRESSIVE_SHARED_STORE_REASSIGN_BONUS = 320.0
DEFAULT_V2_AGGRESSIVE_SHORT_UPPER_PAIR_PRIORITY_BONUS = 28.0
DEFAULT_V2_AGGRESSIVE_PREBATCH_SHARED_STORE_BONUS = 420.0
DEFAULT_V2_AGGRESSIVE_PREBATCH_STORE_DUPLICATE_SEED_BONUS = 40.0
DEFAULT_V2_AGGRESSIVE_PREBATCH_UTIL_GAIN_WEIGHT = 24.0
DEFAULT_V2_AGGRESSIVE_PREBATCH_TWO_ACROSS_GAIN_BONUS = 120.0
DEFAULT_V2_AGGRESSIVE_PREBATCH_STOP_DELTA_PENALTY = 5.0
UNIFIED_OPTIMIZER_PROFILE = "planner_approval"
TRAILER_ASSIGNMENT_RULES_SETTING_KEY = "trailer_assignment_rules"
PLANNER_TRAILER_RULES_OVERRIDE_SETTING_PREFIX = "planner_trailer_assignment_rules_override::"
DEFAULT_TRACTOR_SUPPLY_CARGO_WEDGE_MIN_ITEM_LENGTH_FT = 20.0
DEFAULT_TRACTOR_SUPPLY_UTA_WEDGE_MIN_ITEM_LENGTH_FT = 22.0
DEFAULT_TRACTOR_SUPPLY_CARGO_CATEGORY_TOKENS = ("CARGO",)
DEFAULT_TRACTOR_SUPPLY_UTA_CATEGORY_TOKENS = ("UTA",)
DEFAULT_TRACTOR_SUPPLY_WEDGE_CATEGORY_TOKENS = (
    *DEFAULT_TRACTOR_SUPPLY_CARGO_CATEGORY_TOKENS,
    *DEFAULT_TRACTOR_SUPPLY_UTA_CATEGORY_TOKENS,
)
DEFAULT_LIVESTOCK_CATEGORY_TOKENS = ("LIVESTOCK",)


class Optimizer:
    def __init__(self, planner_id=None):
        self.zip_coords = geo_utils.load_zip_coordinates()
        self.sku_specs = {spec["sku"]: spec for spec in db.list_sku_specs()}
        self.planner_id = str(planner_id or "").strip().upper()
        self.fuel_surcharge = resolve_fuel_surcharge()
        self.rate_lookup = build_rate_lookup(fuel_surcharge=self.fuel_surcharge)
        self.cost_calculator = CostCalculator(
            rate_lookup=self.rate_lookup,
            fuel_surcharge=self.fuel_surcharge,
            zip_coords=self.zip_coords,
        )
        self.strategic_customers = self._load_strategic_customers()
        self.trailer_assignment_rules = self._load_trailer_assignment_rules()
        self._strategic_customer_cache = {}
        self._stack_cache = {}
        self._cost_data_cache = {}
        self._load_build_cache = {}
        self._plant_optimizer_settings_cache = {}
        self._merge_id_counter = 0

    def _load_strategic_customers(self):
        setting = db.get_planning_setting("strategic_customers") or {}
        raw_value = setting.get("value_text") or ""
        return customer_rules.parse_strategic_customers(raw_value)

    def _load_trailer_assignment_rules(self):
        defaults = {
            "livestock_wedge_enabled": True,
            "livestock_category_tokens": list(DEFAULT_LIVESTOCK_CATEGORY_TOKENS),
        }
        setting = db.get_planning_setting(TRAILER_ASSIGNMENT_RULES_SETTING_KEY) or {}
        raw_value = (setting.get("value_text") or "").strip()
        parsed = None
        if raw_value:
            try:
                parsed = json.loads(raw_value)
            except json.JSONDecodeError:
                parsed = None
        if not isinstance(parsed, dict):
            parsed = {}

        tokens = parsed.get("livestock_category_tokens")
        if isinstance(tokens, str):
            tokens = [tokens]
        if isinstance(tokens, (list, tuple, set)):
            normalized_tokens = []
            for token in tokens:
                text = str(token or "").strip().upper()
                if text and text not in normalized_tokens:
                    normalized_tokens.append(text)
        else:
            normalized_tokens = []
        if not normalized_tokens:
            normalized_tokens = list(DEFAULT_LIVESTOCK_CATEGORY_TOKENS)

        rules = {
            "livestock_wedge_enabled": self._coerce_bool(
                parsed.get("livestock_wedge_enabled"),
                defaults["livestock_wedge_enabled"],
            ),
            "livestock_category_tokens": normalized_tokens,
        }
        if not self.planner_id:
            return rules

        override_key = f"{PLANNER_TRAILER_RULES_OVERRIDE_SETTING_PREFIX}{self.planner_id}"
        override_setting = db.get_planning_setting(override_key) or {}
        override_raw = (override_setting.get("value_text") or "").strip()
        if not override_raw:
            return rules
        try:
            override_parsed = json.loads(override_raw)
        except json.JSONDecodeError:
            return rules
        if not isinstance(override_parsed, dict):
            return rules
        if "livestock_wedge_enabled" in override_parsed:
            rules["livestock_wedge_enabled"] = self._coerce_bool(
                override_parsed.get("livestock_wedge_enabled"),
                rules["livestock_wedge_enabled"],
            )
        return rules

    def _strategic_rule_for_customer(self, customer_name):
        normalized = customer_rules.normalize_customer_text(customer_name)
        if normalized in self._strategic_customer_cache:
            return self._strategic_customer_cache[normalized]
        rule = customer_rules.find_matching_strategic_customer(
            customer_name,
            self.strategic_customers,
        )
        self._strategic_customer_cache[normalized] = rule
        return rule

    def build_optimized_loads(self, params):
        baseline_groups = self._build_baseline_group_sets(params)
        if not baseline_groups:
            return []

        loads = [self._build_load(groups, params) for groups in baseline_groups]
        active = {load["_merge_id"]: load for load in loads}
        time_window_days = (
            params.get("time_window_days")
            if params.get("enforce_time_window", True)
            else None
        )

        candidates = self._build_merge_candidates(
            active,
            params,
            min_savings=0.0,
            radius=params.get("geo_radius"),
            time_window_days=time_window_days,
        )
        active = self._merge_candidates(
            active,
            candidates,
            params,
            min_savings=10.0,
            radius=params.get("geo_radius"),
            time_window_days=time_window_days,
            require_orphan=False,
        )

        active = self._rescue_orphans(active, params)
        active = self._apply_auto_hotshot_tail_assignments(active, params)
        return list(active.values())

    def build_optimized_loads_v2(self, params):
        groups = self._build_order_groups(params)
        if not groups:
            return []

        runtime_params = self._runtime_tuned_params(params, len(groups))
        runtime_params = self._inject_market_shape_params(runtime_params, groups)
        singleton_loads = [self._build_load([group], runtime_params) for group in groups]
        candidate_solutions = [
            self._optimize_load_set_v2(singleton_loads, runtime_params),
        ]
        cohort_solution = self._build_state_cohort_candidate_solution_v2(groups, runtime_params)
        if cohort_solution:
            candidate_solutions.append(cohort_solution)

        multi_start_enabled = self._coerce_bool(
            runtime_params.get("v2_multi_start_enabled"),
            DEFAULT_V2_MULTI_START_ENABLED,
        )
        multi_start_group_cap = int(
            runtime_params.get("v2_multi_start_max_groups", DEFAULT_V2_MULTI_START_MAX_GROUPS)
            or DEFAULT_V2_MULTI_START_MAX_GROUPS
        )
        prebatch_enabled = self._coerce_bool(
            runtime_params.get("v2_stack_aware_prebatch_enabled"),
            DEFAULT_V2_STACK_AWARE_PREBATCH_ENABLED,
        )
        if multi_start_enabled and prebatch_enabled and len(groups) <= max(multi_start_group_cap, 1):
            for _profile_name, seed_params in self._seed_profile_variants_v2(runtime_params):
                seed_loads = self._build_seed_loads_v2(groups, seed_params)
                if not seed_loads:
                    continue
                candidate_solutions.append(
                    self._optimize_load_set_v2(seed_loads, seed_params),
                )
        return self._select_best_v2_solution(candidate_solutions, runtime_params)

    def _build_state_cohort_candidate_solution_v2(self, groups, params):
        enabled = self._coerce_bool(
            params.get("v2_state_cohort_prebatch_enabled"),
            True,
        )
        if not enabled:
            return []
        cohorts = {}
        for group in groups or []:
            cohorts.setdefault(self._state_cohort_key(group), []).append(group)
        if len(cohorts) <= 1:
            return []

        cohort_solution = []
        for cohort_groups in cohorts.values():
            singleton_loads = [self._build_load([group], params) for group in (cohort_groups or [])]
            if not singleton_loads:
                continue
            cohort_solution.extend(self._optimize_load_set_v2(singleton_loads, params))
        return cohort_solution

    def _optimize_load_set_v2(self, initial_loads, runtime_params):
        active = {load["_merge_id"]: load for load in (initial_loads or [])}
        time_window_days = (
            runtime_params.get("time_window_days")
            if runtime_params.get("enforce_time_window", True)
            else None
        )
        objective_weights = self._v2_objective_weights(runtime_params)
        max_detour_pct = runtime_params.get("max_detour_pct")

        candidates = self._build_merge_candidates(
            active,
            runtime_params,
            min_savings=0.0,
            radius=runtime_params.get("geo_radius"),
            time_window_days=time_window_days,
            max_detour_pct=max_detour_pct,
            objective_weights=objective_weights,
            min_gain=0.0,
        )
        active = self._merge_candidates(
            active,
            candidates,
            runtime_params,
            min_savings=0.0,
            radius=runtime_params.get("geo_radius"),
            time_window_days=time_window_days,
            max_detour_pct=max_detour_pct,
            objective_weights=objective_weights,
            min_gain=0.0,
        )

        rescue_radius = self._expanded_radius(runtime_params.get("geo_radius") or 0)
        rescue_detour_pct = self._rescue_detour_pct(runtime_params.get("max_detour_pct"))
        rescue_passes = int(runtime_params.get("v2_rescue_passes") or DEFAULT_V2_RESCUE_PASSES)
        for _ in range(max(rescue_passes, 0)):
            before = len(active)
            rescue_candidates = self._build_merge_candidates(
                active,
                runtime_params,
                min_savings=-50.0,
                radius=rescue_radius,
                time_window_days=time_window_days,
                require_orphan=True,
                max_detour_pct=rescue_detour_pct,
                objective_weights=objective_weights,
                min_gain=0.0,
            )
            active = self._merge_candidates(
                active,
                rescue_candidates,
                runtime_params,
                min_savings=-50.0,
                radius=rescue_radius,
                time_window_days=time_window_days,
                require_orphan=True,
                max_detour_pct=rescue_detour_pct,
                objective_weights=objective_weights,
                min_gain=0.0,
            )
            if len(active) >= before:
                break

        active = self._grade_rescue_low_util(
            active,
            runtime_params,
            objective_weights,
            time_window_days,
        )
        active = self._rebalance_fd_loads(
            active,
            runtime_params,
            objective_weights,
            time_window_days,
        )
        active = self._reassign_single_group_outliers(
            active,
            runtime_params,
            time_window_days,
        )
        active = self._cannibalize_weak_loads(
            active,
            runtime_params,
            objective_weights,
            time_window_days,
        )
        active = self._apply_auto_hotshot_tail_assignments(active, runtime_params)
        return list(active.values())

    def build_baseline_loads(self, params):
        baseline_groups = self._build_baseline_group_sets(params)
        if not baseline_groups:
            return []
        return [self._build_load(groups, params) for groups in baseline_groups]

    def _build_seed_loads_v2(self, groups, params):
        singleton_loads = [self._build_load([group], params) for group in (groups or [])]
        if not singleton_loads:
            return []

        enabled = self._coerce_bool(
            params.get("v2_stack_aware_prebatch_enabled"),
            DEFAULT_V2_STACK_AWARE_PREBATCH_ENABLED,
        )
        if not enabled or len(singleton_loads) <= 2:
            return singleton_loads

        state_cohort_enabled = self._coerce_bool(
            params.get("v2_state_cohort_prebatch_enabled"),
            True,
        )
        if state_cohort_enabled:
            cohort_seed_loads = self._build_state_cohort_seed_loads_v2(groups, singleton_loads, params)
            if cohort_seed_loads:
                return cohort_seed_loads

        group_by_key = {}
        singleton_by_key = {}
        for group, load in zip(groups, singleton_loads):
            key = str((group or {}).get("key") or "").strip()
            if not key:
                continue
            group_by_key[key] = group
            singleton_by_key[key] = load
        if not group_by_key:
            return singleton_loads

        store_frequency = Counter()
        for group in group_by_key.values():
            for store_code in (group.get("store_codes") or []):
                normalized = str(store_code or "").strip().upper()
                if normalized:
                    store_frequency[normalized] += 1

        remaining = dict(group_by_key)
        built_loads = []
        while remaining:
            seed_group = max(
                remaining.values(),
                key=lambda group: self._prebatch_seed_priority(
                    group,
                    singleton_by_key.get(str(group.get("key") or "").strip()),
                    store_frequency,
                    params,
                ),
            )
            current_groups = [seed_group]
            current_key = str(seed_group.get("key") or "").strip()
            current_load = singleton_by_key.get(current_key) or self._build_load([seed_group], params)
            if current_key in remaining:
                del remaining[current_key]

            while remaining and self._prebatch_should_keep_filling(current_load, params):
                candidate_groups = self._prebatch_candidate_groups(
                    current_load,
                    list(remaining.values()),
                    singleton_by_key,
                    params,
                )
                best_choice = None
                for candidate_group in candidate_groups:
                    if not self._can_add_group(current_groups, candidate_group, params):
                        continue
                    candidate_key = str(candidate_group.get("key") or "").strip()
                    candidate_load = singleton_by_key.get(candidate_key) or self._build_load([candidate_group], params)
                    standalone_cost = (
                        (current_load.get("standalone_cost") or current_load.get("estimated_cost") or 0)
                        + (candidate_load.get("standalone_cost") or candidate_load.get("estimated_cost") or 0)
                    )
                    merged_load = self._build_load(
                        current_groups + [candidate_group],
                        params,
                        standalone_cost=standalone_cost,
                    )
                    if self._load_is_multi_order_capacity_violation(merged_load):
                        continue
                    score = self._prebatch_candidate_score(
                        current_load,
                        candidate_load,
                        merged_load,
                        params,
                    )
                    if best_choice is None or score > best_choice[0]:
                        best_choice = (score, candidate_group, merged_load)
                if not best_choice:
                    break

                _, selected_group, selected_load = best_choice
                current_groups.append(selected_group)
                current_load = selected_load
                selected_key = str(selected_group.get("key") or "").strip()
                if selected_key in remaining:
                    del remaining[selected_key]

            built_loads.append(current_load)

        return built_loads

    def _build_state_cohort_seed_loads_v2(self, groups, singleton_loads, params):
        group_by_key = {}
        singleton_by_key = {}
        for group, load in zip(groups or [], singleton_loads or []):
            key = str((group or {}).get("key") or "").strip()
            if not key:
                continue
            group_by_key[key] = group
            singleton_by_key[key] = load
        if not group_by_key:
            return []

        cohorts = {}
        for group in groups or []:
            cohort_key = self._state_cohort_key(group)
            cohorts.setdefault(cohort_key, []).append(group)

        built_loads = []
        for cohort_groups in cohorts.values():
            remaining = sorted(
                list(cohort_groups or []),
                key=lambda group: self._state_cohort_group_priority(
                    group,
                    singleton_by_key.get(str((group or {}).get("key") or "").strip()),
                ),
                reverse=True,
            )
            while remaining:
                seed_group = remaining.pop(0)
                seed_key = str((seed_group or {}).get("key") or "").strip()
                current_groups = [seed_group]
                current_load = singleton_by_key.get(seed_key) or self._build_load([seed_group], params)
                while remaining and self._prebatch_should_keep_filling(current_load, params):
                    best_choice = None
                    for idx, candidate_group in enumerate(remaining):
                        if not self._can_add_group(current_groups, candidate_group, params):
                            continue
                        candidate_key = str((candidate_group or {}).get("key") or "").strip()
                        candidate_load = singleton_by_key.get(candidate_key) or self._build_load([candidate_group], params)
                        standalone_cost = (
                            float(current_load.get("standalone_cost") or current_load.get("estimated_cost") or 0.0)
                            + float(candidate_load.get("standalone_cost") or candidate_load.get("estimated_cost") or 0.0)
                        )
                        merged_load = self._build_load(
                            current_groups + [candidate_group],
                            params,
                            standalone_cost=standalone_cost,
                        )
                        if self._load_is_multi_order_capacity_violation(merged_load):
                            continue
                        score = self._prebatch_candidate_score(
                            current_load,
                            candidate_load,
                            merged_load,
                            params,
                        )
                        if best_choice is None or score > best_choice[0]:
                            best_choice = (score, idx, candidate_group, merged_load)
                    if not best_choice:
                        break
                    _, idx, selected_group, selected_load = best_choice
                    current_groups.append(selected_group)
                    current_load = selected_load
                    remaining.pop(idx)
                built_loads.append(current_load)

        if not built_loads:
            return []
        if len(built_loads) >= len(singleton_loads):
            return []
        return built_loads

    def _state_cohort_key(self, group):
        if not isinstance(group, dict):
            return ("", "", "")
        return (
            str(group.get("cust_name") or "").strip().upper(),
            str(group.get("state") or "").strip().upper(),
            str(group.get("strategic_key") or "").strip().lower(),
        )

    def _state_cohort_group_priority(self, group, singleton_load=None):
        return (
            self._coerce_non_negative_float((group or {}).get("max_unit_length_ft"), 0.0),
            self._coerce_non_negative_float((group or {}).get("total_length_ft"), 0.0),
            self._effective_fill_pct(singleton_load or {}),
            str((group or {}).get("key") or "").strip(),
        )

    def _load_set_signature(self, loads):
        signature = []
        for load in loads or []:
            group_keys = sorted(
                str((group or {}).get("key") or "").strip()
                for group in (load.get("groups") or [])
                if str((group or {}).get("key") or "").strip()
            )
            signature.append(tuple(group_keys))
        return tuple(sorted(signature))

    def _same_store_split_count(self, loads):
        store_to_load_ids = {}
        for idx, load in enumerate(loads or [], start=1):
            load_identifier = load.get("_merge_id") or idx
            for line in (load.get("lines") or []):
                store_code = str(line.get("store") or "").strip().upper()
                if not store_code:
                    continue
                store_to_load_ids.setdefault(store_code, set()).add(load_identifier)
        return sum(1 for load_ids in store_to_load_ids.values() if len(load_ids) > 1)

    def _single_state_code(self, load):
        state_text = str((load or {}).get("destination_state") or "").strip().upper()
        state_tokens = [token.strip() for token in re.split(r"[,+/|;]+", state_text) if token.strip()]
        unique_count = int((load or {}).get("unique_states_count") or (1 if state_text else 0))
        if unique_count == 1 and state_tokens:
            return state_tokens[0]
        if unique_count == 1 and state_text:
            return state_text
        return ""

    def _load_state_codes(self, load):
        states = set()
        for line in (load or {}).get("lines") or []:
            state = str((line or {}).get("state") or "").strip().upper()
            if state:
                states.add(state)
        if states:
            return states
        state_text = str((load or {}).get("destination_state") or "").strip().upper()
        for token in re.split(r"[,+/|;]+", state_text):
            token = token.strip()
            if token:
                states.add(token)
        return states

    def _load_order_count(self, load):
        if not isinstance(load, dict):
            return 0
        order_numbers = {
            (line.get("so_num") or "").strip()
            for line in (load.get("lines") or [])
            if (line.get("so_num") or "").strip()
        }
        if order_numbers:
            return len(order_numbers)
        group_keys = {
            str((group or {}).get("key") or "").strip()
            for group in (load.get("groups") or [])
            if str((group or {}).get("key") or "").strip()
        }
        return len(group_keys)

    def _effective_fill_pct(self, load):
        if not isinstance(load, dict):
            try:
                return float(load or 0.0)
            except (TypeError, ValueError):
                return 0.0
        explicit_effective_fill_pct = float(load.get("effective_fill_pct") or 0.0)
        utilization_pct = float(load.get("utilization_pct") or 0.0)
        practical_fill_pct = float(load.get("practical_fill_pct") or 0.0)
        total_linear_feet = float(load.get("total_linear_feet") or 0.0)
        capacity_feet = float(load.get("capacity_feet") or 0.0)
        linear_fill_pct = (total_linear_feet / capacity_feet) * 100.0 if capacity_feet > 0 else 0.0
        return max(explicit_effective_fill_pct, utilization_pct, practical_fill_pct, linear_fill_pct)

    def _v2_solution_score(self, loads, params):
        solution = list(loads or [])
        if not solution:
            return (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        utilizations = [float(load.get("utilization_pct") or 0.0) for load in solution]
        effective_fills = [self._effective_fill_pct(load) for load in solution]
        high_95 = sum(1 for value in effective_fills if value >= 95.0)
        high_90 = sum(1 for value in effective_fills if value >= 90.0)
        high_85 = sum(1 for value in effective_fills if value >= 85.0)
        low_70 = sum(1 for value in effective_fills if value < 70.0)
        low_85 = sum(1 for value in effective_fills if value < 85.0)
        avg_fill = sum(effective_fills) / len(effective_fills) if effective_fills else 0.0
        avg_util = sum(utilizations) / len(utilizations) if utilizations else 0.0
        two_across_loads = sum(
            1
            for load in solution
            if int(load.get("upper_two_across_applied_count") or 0) > 0
        )
        total_two_across = sum(int(load.get("upper_two_across_applied_count") or 0) for load in solution)
        same_store_split_count = self._same_store_split_count(solution)
        stop_penalty = sum(self._stop_penalty_units(load, params) for load in solution)
        geo_penalty = sum(self._load_geo_penalty(load, params) for load in solution)
        detour_penalty = sum(max(self._detour_pct(load), 0.0) for load in solution)
        total_cost = sum(float(load.get("estimated_cost") or 0.0) for load in solution)
        state_mix_penalty = sum(
            max(int(load.get("unique_states_count") or (1 if load.get("destination_state") else 0)) - 1, 0)
            for load in solution
        )
        return (
            high_90,
            high_95,
            high_85,
            -low_85,
            -low_70,
            -int(round(stop_penalty * 100.0)),
            -int(round(geo_penalty * 100.0)),
            -int(round(detour_penalty * 10.0)),
            -state_mix_penalty,
            int(round(avg_fill * 10.0)),
            int(round(avg_util * 10.0)),
            total_two_across,
            two_across_loads,
            -same_store_split_count,
            -len(solution),
            -int(round(total_cost)),
        )

    def _select_best_v2_solution(self, candidate_solutions, params):
        best = []
        best_score = None
        for loads in candidate_solutions or []:
            score = self._v2_solution_score(loads, params)
            if best_score is None or score > best_score:
                best = list(loads or [])
                best_score = score
        return best

    def _seed_profile_variants_v2(self, runtime_params):
        base_params = dict(runtime_params or {})
        base_params["optimize_focus"] = UNIFIED_OPTIMIZER_PROFILE
        profiles = [(UNIFIED_OPTIMIZER_PROFILE, base_params)]
        include_aggressive = self._coerce_bool(
            runtime_params.get("v2_multi_start_include_aggressive_fill"),
            DEFAULT_V2_MULTI_START_INCLUDE_AGGRESSIVE_FILL,
        )
        if not include_aggressive:
            return profiles

        aggressive = dict(runtime_params or {})
        aggressive["optimize_focus"] = UNIFIED_OPTIMIZER_PROFILE
        aggressive.update(
            {
                "v2_prebatch_target_util": 99.0,
                "v2_prebatch_candidate_limit": DEFAULT_V2_AGGRESSIVE_PREBATCH_CANDIDATE_LIMIT,
                "v2_prebatch_savings_weight": 0.1,
                "v2_shared_store_priority_bonus": DEFAULT_V2_AGGRESSIVE_SHARED_STORE_PRIORITY_BONUS,
                "v2_shared_store_reassign_bonus": DEFAULT_V2_AGGRESSIVE_SHARED_STORE_REASSIGN_BONUS,
                "v2_short_upper_pair_priority_bonus": DEFAULT_V2_AGGRESSIVE_SHORT_UPPER_PAIR_PRIORITY_BONUS,
                "v2_prebatch_shared_store_bonus": DEFAULT_V2_AGGRESSIVE_PREBATCH_SHARED_STORE_BONUS,
                "v2_prebatch_store_duplicate_seed_bonus": DEFAULT_V2_AGGRESSIVE_PREBATCH_STORE_DUPLICATE_SEED_BONUS,
                "v2_prebatch_util_gain_weight": 28.0,
                "v2_prebatch_two_across_gain_bonus": DEFAULT_V2_AGGRESSIVE_PREBATCH_TWO_ACROSS_GAIN_BONUS,
                "v2_prebatch_stop_delta_penalty": DEFAULT_V2_AGGRESSIVE_PREBATCH_STOP_DELTA_PENALTY,
                "v2_fill_floor_target_pct": 70.0,
                "v2_lambda_fill_floor_count": 1500.0,
                "v2_lambda_fill_floor_depth": 80.0,
                "v2_lambda_fill_floor_progress": 60.0,
                "v2_lambda_weak_tail_absorb": 800.0,
                "v2_weak_cannibalize_passes": 6,
                "v2_weak_cannibalize_target_util": 70.0,
                "v2_fd_target_util": 70.0,
                "v2_fd_candidate_limit": 240,
                "v2_group_reassign_passes": 5,
                "v2_group_reassign_candidate_limit": 32,
            }
        )
        profiles.append(("aggressive_fill", aggressive))
        return profiles

    def _build_order_groups(self, params):
        min_due_date = self._resolve_min_due_date(params)
        session_id = params.get("session_id")
        orders = db.list_order_lines_for_optimization(
            params["origin_plant"],
            min_due_date=min_due_date,
            session_id=session_id,
        )
        if not orders:
            return []

        order_summary_map = self._build_order_summary_map(params["origin_plant"])
        grouped = self._group_by_so_num(orders, order_summary_map)
        return self._apply_order_group_filters(
            grouped,
            params,
            min_due_date=min_due_date,
            include_batch=True,
        )

    def describe_order_group_eligibility(self, params):
        origin_plant = params.get("origin_plant")
        diagnostics = {
            "open_orders_total": 0,
            "eligible_order_lines": 0,
            "grouped_orders": 0,
            "groups_after_all_filters": 0,
            "groups_after_all_filters_no_batch": 0,
            "groups_without_customer_filter": 0,
            "groups_without_state_filter": 0,
            "groups_without_order_category_filter": 0,
            "groups_without_order_category_token_filter": 0,
            "groups_without_excluded_sku_filter": 0,
            "first_due_no_batch": None,
        }
        if not origin_plant:
            return diagnostics

        session_id = params.get("session_id")
        diagnostics["open_orders_total"] = len(
            db.list_orders_for_optimization(origin_plant, session_id=session_id)
        )
        min_due_date = self._resolve_min_due_date(params)
        order_lines = db.list_order_lines_for_optimization(
            origin_plant,
            min_due_date=min_due_date,
            session_id=session_id,
        )
        diagnostics["eligible_order_lines"] = len(order_lines)
        if not order_lines:
            return diagnostics

        grouped = self._group_by_so_num(order_lines, self._build_order_summary_map(origin_plant))
        diagnostics["grouped_orders"] = len(grouped)
        if not grouped:
            return diagnostics

        with_batch = self._apply_order_group_filters(
            grouped,
            params,
            min_due_date=min_due_date,
            include_batch=True,
        )
        diagnostics["groups_after_all_filters"] = len(with_batch)

        without_batch = self._apply_order_group_filters(
            grouped,
            params,
            min_due_date=min_due_date,
            include_batch=False,
        )
        diagnostics["groups_after_all_filters_no_batch"] = len(without_batch)
        due_dates = [group.get("due_date") for group in without_batch if group.get("due_date")]
        diagnostics["first_due_no_batch"] = min(due_dates) if due_dates else None

        params_without_customer = dict(params)
        params_without_customer["customer_filters"] = []
        diagnostics["groups_without_customer_filter"] = len(
            self._apply_order_group_filters(
                grouped,
                params_without_customer,
                min_due_date=min_due_date,
                include_batch=True,
            )
        )

        params_without_state = dict(params)
        params_without_state["state_filters"] = []
        diagnostics["groups_without_state_filter"] = len(
            self._apply_order_group_filters(
                grouped,
                params_without_state,
                min_due_date=min_due_date,
                include_batch=True,
            )
        )

        params_without_order_category = dict(params)
        params_without_order_category["order_category_scope"] = order_categories.ORDER_CATEGORY_SCOPE_ALL
        params_without_order_category["order_category_scopes"] = []
        diagnostics["groups_without_order_category_filter"] = len(
            self._apply_order_group_filters(
                grouped,
                params_without_order_category,
                min_due_date=min_due_date,
                include_batch=True,
            )
        )
        params_without_category_tokens = dict(params)
        params_without_category_tokens["order_category_tokens"] = []
        diagnostics["groups_without_order_category_token_filter"] = len(
            self._apply_order_group_filters(
                grouped,
                params_without_category_tokens,
                min_due_date=min_due_date,
                include_batch=True,
            )
        )
        params_without_excluded_skus = dict(params)
        params_without_excluded_skus["excluded_skus"] = []
        diagnostics["groups_without_excluded_sku_filter"] = len(
            self._apply_order_group_filters(
                grouped,
                params_without_excluded_skus,
                min_due_date=min_due_date,
                include_batch=True,
            )
        )
        return diagnostics

    def _resolve_min_due_date(self, params):
        start_date = params.get("orders_start_date")
        if start_date:
            if isinstance(start_date, str):
                try:
                    start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
                except ValueError:
                    start_date = None
            if isinstance(start_date, date):
                return start_date.strftime("%Y-%m-%d")

        if not params.get("ignore_past_due"):
            return None
        reference_date = params.get("reference_date")
        if isinstance(reference_date, str):
            try:
                reference_date = datetime.strptime(reference_date, "%Y-%m-%d").date()
            except ValueError:
                reference_date = None
        if not reference_date:
            reference_date = date.today()
        return reference_date.strftime("%Y-%m-%d")

    def _apply_order_group_filters(self, grouped, params, min_due_date=None, include_batch=True):
        filtered = list(grouped or [])
        if not filtered:
            return []

        optimize_mode = (params.get("optimize_mode") or "auto").strip().lower()
        if optimize_mode != "manual":
            filtered = [
                group
                for group in filtered
                if not bool(group.get("ignore_for_optimization"))
            ]

        max_due_date = params.get("batch_max_due_date")
        if include_batch and max_due_date:
            filtered = [
                group
                for group in filtered
                if not group.get("due_date") or group.get("due_date") <= max_due_date
            ]

        if min_due_date:
            reference_date = datetime.strptime(min_due_date, "%Y-%m-%d").date()
            filtered = [
                group
                for group in filtered
                if not group.get("due_date") or group.get("due_date") >= reference_date
            ]

        state_filters = {value.strip().upper() for value in (params.get("state_filters") or []) if value}
        if state_filters:
            filtered = [
                group
                for group in filtered
                if (group.get("state") or "").strip().upper() in state_filters
            ]

        customer_filters = {
            value.strip().casefold()
            for value in (params.get("customer_filters") or [])
            if value
        }
        if customer_filters:
            filtered = [
                group
                for group in filtered
                if (group.get("cust_name") or "").strip().casefold() in customer_filters
            ]

        selected_so_nums = [
            str(value).strip()
            for value in (params.get("selected_so_nums") or [])
            if str(value or "").strip()
        ]
        if selected_so_nums:
            selected_set = set(selected_so_nums)
            filtered = [group for group in filtered if (group.get("key") or "") in selected_set]
            sequence = {so_num: idx for idx, so_num in enumerate(selected_so_nums)}
            filtered.sort(key=lambda group: sequence.get(group.get("key"), len(sequence)))

        excluded_skus = self._normalize_excluded_skus(params.get("excluded_skus"))
        if excluded_skus:
            filtered = [
                group
                for group in filtered
                if not self._group_has_excluded_sku(group, excluded_skus)
            ]

        order_category_scopes = order_categories.normalize_order_category_scopes(
            params.get("order_category_scopes"),
            default=params.get("order_category_scope"),
        )
        if order_category_scopes:
            allowed_scopes = set(order_category_scopes)
            filtered = [
                group
                for group in filtered
                if self._group_order_category_scope(group) in allowed_scopes
            ]
        selected_category_tokens = set(
            order_categories.normalize_order_category_tokens(params.get("order_category_tokens"))
        )
        if selected_category_tokens:
            filtered = [
                group
                for group in filtered
                if self._group_matches_category_tokens(group, selected_category_tokens)
            ]
        return filtered

    def _build_baseline_group_sets(self, params):
        grouped = self._build_order_groups(params)
        if not grouped:
            return []

        destinations = {}
        for group in grouped:
            state = (group.get("state") or "").strip().upper()
            zip_code = (group.get("zip") or "").strip()
            key = state or zip_code
            destinations.setdefault(key, []).append(group)

        load_groups = []
        for dest_groups in destinations.values():
            load_groups.extend(self._first_fit_decreasing(dest_groups, params))
        return load_groups

    def _first_fit_decreasing(self, groups, params):
        sorted_groups = sorted(
            groups,
            key=lambda g: g.get("total_length_ft") or 0,
            reverse=True,
        )
        loads = []
        for group in sorted_groups:
            placed = False
            for load_groups in loads:
                if self._can_add_group(load_groups, group, params):
                    load_groups.append(group)
                    placed = True
                    break
            if not placed:
                loads.append([group])
        return loads

    def _can_add_group(self, current_groups, candidate_group, params):
        combined = current_groups + [candidate_group]
        if not self._groups_mix_compatible(combined):
            return False

        time_window_days = self._effective_time_window_days(
            groups=combined,
            base_time_window_days=params.get("time_window_days"),
            enforce_time_window=params.get("enforce_time_window", True),
        )
        if time_window_days is not None:
            dates = [
                group.get("due_date")
                for group in combined
                if group.get("due_date")
            ]
            if dates and (max(dates) - min(dates)).days > time_window_days:
                return False

        current_load = self._build_load(current_groups, params)
        candidate_load = self._build_load([candidate_group], params)
        if not self._loads_compatible(
            current_load,
            candidate_load,
            params.get("geo_radius"),
            time_window_days,
            params,
        ):
            return False

        if not self._check_stacking_compatible(combined):
            return False

        stack_config = self._stack_config_for_groups(combined, params)
        if self._is_multi_order_capacity_violation(combined, stack_config):
            return False
        return True

    def _is_multi_order_capacity_violation(self, groups, stack_config):
        if not groups or not stack_config:
            return False
        order_keys = {group.get("key") for group in groups if group.get("key")}
        multi_order = len(order_keys) > 1 or len(groups) > 1
        if not multi_order:
            return False
        exceeds_capacity = bool(stack_config.get("exceeds_capacity", False))
        return exceeds_capacity

    def _load_is_multi_order_capacity_violation(self, load):
        if not load:
            return False
        lines = load.get("lines") or []
        order_numbers = {
            (line.get("so_num") or "").strip()
            for line in lines
            if (line.get("so_num") or "").strip()
        }
        multi_order = len(order_numbers) > 1
        if not multi_order:
            groups = load.get("groups") or []
            group_keys = {group.get("key") for group in groups if group.get("key")}
            multi_order = len(group_keys) > 1 or len(groups) > 1
        if not multi_order:
            return False
        exceeds_capacity = bool(load.get("exceeds_capacity", False))
        return exceeds_capacity

    def _build_merge_candidates(
        self,
        active_loads,
        params,
        min_savings,
        radius=None,
        time_window_days=None,
        require_orphan=False,
        max_detour_pct=None,
        objective_weights=None,
        min_gain=None,
        require_low_util_target=False,
        target_merge_ids=None,
    ):
        load_list = list(active_loads.values())
        heap = []
        for idx, jdx in self._candidate_pair_indices(
            load_list,
            params,
            require_low_util_target=require_low_util_target,
            target_merge_ids=target_merge_ids,
        ):
            load_a = load_list[idx]
            load_b = load_list[jdx]
            if require_orphan and not (self._is_orphan(load_a) or self._is_orphan(load_b)):
                continue
            if require_low_util_target and not self._pair_has_low_util_target(load_a, load_b, params):
                continue
            if not self._loads_compatible(load_a, load_b, radius, time_window_days, params):
                continue
            candidate = self._evaluate_merge_candidate(
                load_a,
                load_b,
                params,
                objective_weights=objective_weights,
            )
            if not candidate:
                continue
            savings = candidate["savings"]
            if savings < min_savings:
                continue
            if not self._detour_allowed(
                load_a,
                load_b,
                candidate.get("merged_load") or {},
                max_detour_pct,
                params,
                savings=savings,
            ):
                continue
            gain = candidate.get("gain", savings)
            if min_gain is not None and gain < min_gain:
                continue
            heapq.heappush(
                heap,
                (-gain, load_a["_merge_id"], load_b["_merge_id"], candidate),
            )
        return heap

    def _merge_candidates(
        self,
        active_loads,
        heap,
        params,
        min_savings,
        radius=None,
        time_window_days=None,
        require_orphan=False,
        max_detour_pct=None,
        objective_weights=None,
        min_gain=None,
        require_low_util_target=False,
    ):
        while heap:
            neg_gain, load_a_id, load_b_id, candidate = heapq.heappop(heap)
            gain = -neg_gain
            if min_gain is None and objective_weights is None and gain < min_savings:
                break
            if min_gain is not None and gain < min_gain:
                break
            if load_a_id not in active_loads or load_b_id not in active_loads:
                continue

            load_a = active_loads[load_a_id]
            load_b = active_loads[load_b_id]
            if require_low_util_target and not self._pair_has_low_util_target(load_a, load_b, params):
                continue
            merged_load = candidate.get("merged_load")
            if not merged_load:
                merged_load = self._merge_loads(load_a, load_b, params)
                if not merged_load:
                    continue
            savings = (
                (load_a.get("estimated_cost") or 0)
                + (load_b.get("estimated_cost") or 0)
                - (merged_load.get("estimated_cost") or 0)
            )
            if savings < min_savings:
                continue
            if not self._detour_allowed(
                load_a,
                load_b,
                merged_load,
                max_detour_pct,
                params,
                savings=savings,
            ):
                continue
            candidate_gain = candidate.get("gain")
            if candidate_gain is None and objective_weights:
                candidate_gain = savings + self._objective_bonus_for_merge(
                    load_a,
                    load_b,
                    merged_load,
                    objective_weights,
                )
            if min_gain is not None and candidate_gain is not None and candidate_gain < min_gain:
                continue

            del active_loads[load_a_id]
            del active_loads[load_b_id]
            active_loads[merged_load["_merge_id"]] = merged_load

            for other in self._candidate_peers_for_load(merged_load, active_loads, params):
                if require_orphan and not (self._is_orphan(merged_load) or self._is_orphan(other)):
                    continue
                if require_low_util_target and not self._pair_has_low_util_target(merged_load, other, params):
                    continue
                if not self._loads_compatible(
                    merged_load,
                    other,
                    radius,
                    time_window_days,
                    params,
                ):
                    continue
                new_candidate = self._evaluate_merge_candidate(
                    merged_load,
                    other,
                    params,
                    objective_weights=objective_weights,
                )
                if not new_candidate:
                    continue
                new_savings = new_candidate["savings"]
                if new_savings < min_savings:
                    continue
                if not self._detour_allowed(
                    merged_load,
                    other,
                    new_candidate.get("merged_load") or {},
                    max_detour_pct,
                    params,
                    savings=new_savings,
                ):
                    continue
                new_gain = new_candidate.get("gain", new_savings)
                if min_gain is not None and new_gain < min_gain:
                    continue
                heapq.heappush(
                    heap,
                    (-new_gain, merged_load["_merge_id"], other["_merge_id"], new_candidate),
                )

        return active_loads

    def _rescue_orphans(self, active_loads, params):
        if not any(self._is_orphan(load) for load in active_loads.values()):
            return active_loads

        rescue_radius = self._expanded_radius(params.get("geo_radius") or 0)
        rescue_window = (
            params.get("time_window_days")
            if params.get("enforce_time_window", True)
            else None
        )
        candidates = self._build_merge_candidates(
            active_loads,
            params,
            min_savings=-50.0,
            radius=rescue_radius,
            time_window_days=rescue_window,
            require_orphan=True,
            max_detour_pct=self._rescue_detour_pct(params.get("max_detour_pct")),
        )

        return self._merge_candidates(
            active_loads,
            candidates,
            params,
            min_savings=-50.0,
            radius=rescue_radius,
            time_window_days=rescue_window,
            require_orphan=True,
            max_detour_pct=self._rescue_detour_pct(params.get("max_detour_pct")),
        )

    def _grade_rescue_low_util(
        self,
        active_loads,
        params,
        objective_weights,
        time_window_days,
    ):
        grade_passes = int(
            params.get("v2_grade_rescue_passes", DEFAULT_V2_GRADE_RESCUE_PASSES)
            or DEFAULT_V2_GRADE_RESCUE_PASSES
        )
        if grade_passes <= 0:
            return active_loads

        low_target_ids = self._non_date_orphan_subgrade_ids(
            active_loads,
            params,
            time_window_days,
        )
        if not low_target_ids:
            return active_loads

        grade_params = dict(params)
        grade_params["v2_pair_neighbors"] = max(
            int(grade_params.get("v2_pair_neighbors", DEFAULT_V2_PAIR_NEIGHBORS) or DEFAULT_V2_PAIR_NEIGHBORS),
            36,
        )
        grade_params["v2_pair_neighbors_low_util"] = max(
            int(
                grade_params.get("v2_pair_neighbors_low_util", DEFAULT_V2_PAIR_NEIGHBORS_LOW_UTIL)
                or DEFAULT_V2_PAIR_NEIGHBORS_LOW_UTIL
            ),
            120,
        )
        grade_params["v2_incremental_neighbors"] = max(
            int(
                grade_params.get("v2_incremental_neighbors", DEFAULT_V2_INCREMENTAL_NEIGHBORS)
                or DEFAULT_V2_INCREMENTAL_NEIGHBORS
            ),
            48,
        )
        grade_params["v2_on_way_bearing_deg"] = max(
            float(
                grade_params.get("v2_on_way_bearing_deg", DEFAULT_V2_ONWAY_BEARING_DEG)
                or DEFAULT_V2_ONWAY_BEARING_DEG
            ),
            50.0,
        )
        grade_params["v2_on_way_radial_gap_miles"] = max(
            float(
                grade_params.get("v2_on_way_radial_gap_miles", DEFAULT_V2_ONWAY_RADIAL_GAP_MILES)
                or DEFAULT_V2_ONWAY_RADIAL_GAP_MILES
            ),
            800.0,
        )

        base_radius = params.get("geo_radius") or 0
        grade_radius = self._expanded_radius(self._expanded_radius(self._expanded_radius(base_radius)))
        base_detour = self._rescue_detour_pct(grade_params.get("max_detour_pct"))
        grade_detour = float(
            grade_params.get(
                "v2_grade_rescue_detour_cap",
                max(base_detour, DEFAULT_V2_GRADE_RESCUE_DETOUR_FLOOR),
            )
            or max(base_detour, DEFAULT_V2_GRADE_RESCUE_DETOUR_FLOOR)
        )
        min_savings = float(
            grade_params.get("v2_grade_rescue_min_savings", DEFAULT_V2_GRADE_RESCUE_MIN_SAVINGS)
            or DEFAULT_V2_GRADE_RESCUE_MIN_SAVINGS
        )
        min_gain = float(
            grade_params.get("v2_grade_rescue_min_gain", DEFAULT_V2_GRADE_RESCUE_MIN_GAIN)
            or DEFAULT_V2_GRADE_RESCUE_MIN_GAIN
        )

        for _ in range(grade_passes):
            before_count = len(active_loads)
            low_target_ids = self._non_date_orphan_subgrade_ids(
                active_loads,
                grade_params,
                time_window_days,
            )
            if not low_target_ids:
                break

            candidates = self._build_merge_candidates(
                active_loads,
                grade_params,
                min_savings=min_savings,
                radius=grade_radius,
                time_window_days=time_window_days,
                require_orphan=False,
                max_detour_pct=grade_detour,
                objective_weights=objective_weights,
                min_gain=min_gain,
                require_low_util_target=True,
                target_merge_ids=low_target_ids,
            )
            if not candidates:
                break

            active_loads = self._merge_candidates(
                active_loads,
                candidates,
                grade_params,
                min_savings=min_savings,
                radius=grade_radius,
                time_window_days=time_window_days,
                require_orphan=False,
                max_detour_pct=grade_detour,
                objective_weights=objective_weights,
                min_gain=min_gain,
                require_low_util_target=True,
            )

            low_after_ids = self._non_date_orphan_subgrade_ids(
                active_loads,
                grade_params,
                time_window_days,
            )
            if len(low_after_ids) >= len(low_target_ids) and len(active_loads) >= before_count:
                break

        active_loads = self._repair_non_date_orphan_subgrade_loads(
            active_loads,
            grade_params,
            objective_weights,
            time_window_days,
            grade_detour,
        )
        return active_loads

    def _non_date_orphan_subgrade_ids(self, active_loads, params, time_window_days):
        loads = list(active_loads.values())
        merge_ids = set()
        for load in loads:
            if not self._is_low_util_for_target(load.get("utilization_pct") or 0, params):
                continue
            if self._is_date_orphan_load(load, loads, time_window_days):
                continue
            merge_id = load.get("_merge_id")
            if merge_id is not None:
                merge_ids.add(merge_id)
        return merge_ids

    def _is_date_orphan_load(self, load, all_loads, time_window_days):
        load_id = load.get("_merge_id")
        for other in all_loads:
            if other.get("_merge_id") == load_id:
                continue
            if load.get("origin_plant") != other.get("origin_plant"):
                continue
            if self._loads_date_compatible(load, other, time_window_days):
                return False
        return True

    def _repair_non_date_orphan_subgrade_loads(
        self,
        active_loads,
        params,
        objective_weights,
        time_window_days,
        repair_detour_cap,
    ):
        repair_limit = int(
            params.get("v2_grade_repair_limit", DEFAULT_V2_GRADE_REPAIR_LIMIT)
            or DEFAULT_V2_GRADE_REPAIR_LIMIT
        )
        min_savings = float(
            params.get("v2_grade_repair_min_savings", DEFAULT_V2_GRADE_REPAIR_MIN_SAVINGS)
            or DEFAULT_V2_GRADE_REPAIR_MIN_SAVINGS
        )
        if repair_limit <= 0:
            return active_loads

        threshold = float(
            params.get("v2_low_util_threshold", LOW_UTIL_THRESHOLD_PCT)
            or LOW_UTIL_THRESHOLD_PCT
        )

        for _ in range(repair_limit):
            loads = list(active_loads.values())
            violating = sorted(
                [
                    load
                    for load in loads
                    if self._is_low_util_for_target(load.get("utilization_pct") or 0, params)
                    and not self._is_date_orphan_load(load, loads, time_window_days)
                ],
                key=lambda load: load.get("utilization_pct") or 0,
            )
            if not violating:
                break

            repaired = False
            for target in violating:
                target_id = target.get("_merge_id")
                if target_id not in active_loads:
                    continue
                best = None
                for other in loads:
                    other_id = other.get("_merge_id")
                    if other_id == target_id or other_id not in active_loads:
                        continue
                    if target.get("origin_plant") != other.get("origin_plant"):
                        continue
                    if not self._loads_date_compatible(target, other, time_window_days):
                        continue
                    candidate = self._evaluate_merge_candidate(
                        target,
                        other,
                        params,
                        objective_weights=objective_weights,
                    )
                    if not candidate:
                        continue
                    merged = candidate.get("merged_load") or {}
                    savings = candidate.get("savings") or 0.0
                    if savings < min_savings:
                        continue
                    if not self._detour_allowed(
                        target,
                        other,
                        merged,
                        repair_detour_cap,
                        params,
                        savings=savings,
                    ):
                        continue
                    target_util = target.get("utilization_pct") or 0.0
                    merged_util = merged.get("utilization_pct") or 0.0
                    if merged_util <= target_util + 0.25:
                        continue
                    bonus = 0.0
                    if merged_util >= threshold:
                        bonus += 450.0
                    bonus += max(merged_util - target_util, 0.0) * 8.0
                    score = (candidate.get("gain") or savings) + bonus
                    if best is None or score > best[0]:
                        best = (score, target_id, other_id, merged)

                if not best:
                    continue
                _, left_id, right_id, merged_load = best
                if left_id not in active_loads or right_id not in active_loads:
                    continue
                del active_loads[left_id]
                del active_loads[right_id]
                active_loads[merged_load["_merge_id"]] = merged_load
                repaired = True
                break

            if not repaired:
                break

        return active_loads

    def _rebalance_fd_loads(
        self,
        active_loads,
        params,
        objective_weights,
        time_window_days,
    ):
        passes = int(
            params.get("v2_fd_rebalance_passes", DEFAULT_V2_FD_REBALANCE_PASSES)
            or DEFAULT_V2_FD_REBALANCE_PASSES
        )
        if passes <= 0:
            return active_loads

        for _ in range(passes):
            targets = self._fd_rebalance_targets(active_loads, params, time_window_days)
            if not targets:
                break

            changed = False
            for target in targets:
                target_id = target.get("_merge_id")
                if target_id not in active_loads:
                    continue
                updated = self._try_absorb_target_load(
                    target_id,
                    active_loads,
                    params,
                    objective_weights,
                    time_window_days,
                )
                if not updated:
                    continue
                active_loads = updated
                changed = True

            if not changed:
                break

        return active_loads

    def _reassign_single_group_outliers(
        self,
        active_loads,
        params,
        time_window_days,
    ):
        passes = int(
            params.get("v2_group_reassign_passes", DEFAULT_V2_GROUP_REASSIGN_PASSES)
            or DEFAULT_V2_GROUP_REASSIGN_PASSES
        )
        if passes <= 0:
            return active_loads

        min_savings = float(
            params.get("v2_group_reassign_min_savings", DEFAULT_V2_GROUP_REASSIGN_MIN_SAVINGS)
            or DEFAULT_V2_GROUP_REASSIGN_MIN_SAVINGS
        )
        full_load_promotion_floor = float(
            params.get(
                "v2_full_load_promotion_min_savings_floor",
                DEFAULT_V2_FULL_LOAD_PROMOTION_MIN_SAVINGS_FLOOR,
            )
            or DEFAULT_V2_FULL_LOAD_PROMOTION_MIN_SAVINGS_FLOOR
        )
        candidate_limit = int(
            params.get("v2_group_reassign_candidate_limit", DEFAULT_V2_GROUP_REASSIGN_CANDIDATE_LIMIT)
            or DEFAULT_V2_GROUP_REASSIGN_CANDIDATE_LIMIT
        )
        detour_cap = float(
            params.get(
                "v2_group_reassign_detour_cap",
                max(
                    self._rescue_detour_pct(params.get("max_detour_pct")),
                    DEFAULT_V2_DIRECTIONAL_DETOUR_FLOOR,
                ),
            )
            or max(
                self._rescue_detour_pct(params.get("max_detour_pct")),
                DEFAULT_V2_DIRECTIONAL_DETOUR_FLOOR,
            )
        )

        for _ in range(passes):
            best = None
            current_loads = list(active_loads.values())
            for source in current_loads:
                source_id = source.get("_merge_id")
                if source_id not in active_loads:
                    continue
                source_groups = list(source.get("groups") or [])
                if len(source_groups) <= 1:
                    continue
                recipients = [
                    load
                    for load in current_loads
                    if load.get("_merge_id") != source_id
                    and load.get("_merge_id") in active_loads
                    and load.get("origin_plant") == source.get("origin_plant")
                ]
                if not recipients:
                    continue

                for group in source_groups:
                    group_key = str(group.get("key") or "").strip()
                    if not group_key:
                        continue
                    remaining_groups = [
                        existing
                        for existing in source_groups
                        if str(existing.get("key") or "").strip() != group_key
                    ]
                    if len(remaining_groups) == len(source_groups) or not remaining_groups:
                        continue
                    source_remainder = self._build_load(remaining_groups, params)
                    if self._load_is_multi_order_capacity_violation(source_remainder):
                        continue

                    group_load = self._build_load([group], params)
                    ranked_recipients = self._recipient_candidates_for_target(
                        source,
                        group_load,
                        recipients,
                        params,
                        time_window_days,
                        candidate_limit,
                    )
                    for recipient in ranked_recipients:
                        recipient_id = recipient.get("_merge_id")
                        if recipient_id not in active_loads:
                            continue
                        if not self._loads_date_compatible(recipient, group_load, time_window_days):
                            continue
                        recipient_groups = list(recipient.get("groups") or [])
                        merged_groups = recipient_groups + [group]
                        standalone_cost = (
                            (recipient.get("standalone_cost") or recipient.get("estimated_cost") or 0)
                            + (group_load.get("standalone_cost") or group_load.get("estimated_cost") or 0)
                        )
                        merged = self._build_load(
                            merged_groups,
                            params,
                            standalone_cost=standalone_cost,
                        )
                        if self._load_is_multi_order_capacity_violation(merged):
                            continue
                        savings = (
                            (source.get("estimated_cost") or 0)
                            + (recipient.get("estimated_cost") or 0)
                            - (source_remainder.get("estimated_cost") or 0)
                            - (merged.get("estimated_cost") or 0)
                        )
                        shared_store_count = self._shared_store_count(group_load, recipient)
                        effective_min_savings = min_savings
                        objective_weights = self._v2_objective_weights(params)
                        full_load_target = float(objective_weights.get("full_load_target_pct", 90.0) or 90.0)
                        merged_util = float(merged.get("utilization_pct") or 0.0)
                        recipient_util = float(recipient.get("utilization_pct") or 0.0)
                        source_util = float(source.get("utilization_pct") or 0.0)
                        remainder_util = float(source_remainder.get("utilization_pct") or 0.0)
                        source_state = str(source.get("destination_state") or "").strip().upper()
                        recipient_state = str(recipient.get("destination_state") or "").strip().upper()
                        same_state_promotion = bool(source_state and source_state == recipient_state)
                        if (
                            same_state_promotion
                            and merged_util >= full_load_target
                            and recipient_util < full_load_target
                            and remainder_util < source_util
                        ):
                            effective_min_savings = min(
                                effective_min_savings,
                                full_load_promotion_floor,
                            )
                        if shared_store_count > 0:
                            shared_store_savings_floor = float(
                                params.get(
                                    "v2_shared_store_min_savings_floor",
                                    DEFAULT_V2_SHARED_STORE_MIN_SAVINGS_FLOOR,
                                )
                                or DEFAULT_V2_SHARED_STORE_MIN_SAVINGS_FLOOR
                            )
                            effective_min_savings = min(
                                effective_min_savings,
                                shared_store_savings_floor,
                            )
                        if savings < effective_min_savings:
                            continue
                        if not self._detour_allowed(
                            recipient,
                            group_load,
                            merged,
                            detour_cap,
                            params,
                            savings=savings,
                        ):
                            continue
                        before_geo_penalty = self._load_geo_penalty(source, params) + self._load_geo_penalty(recipient, params)
                        after_geo_penalty = self._load_geo_penalty(source_remainder, params) + self._load_geo_penalty(merged, params)
                        before_stop_penalty = self._stop_penalty_units(source, params) + self._stop_penalty_units(recipient, params)
                        after_stop_penalty = self._stop_penalty_units(source_remainder, params) + self._stop_penalty_units(merged, params)
                        score = savings
                        score += max(merged_util - recipient_util, 0.0) * 6.0
                        score += max(before_geo_penalty - after_geo_penalty, 0.0) * 140.0
                        score += max(before_stop_penalty - after_stop_penalty, 0.0) * 24.0
                        score += self._reassign_directional_bonus(group_load, recipient, source_remainder, params) * 5.0
                        if merged_util >= full_load_target and recipient_util < full_load_target:
                            score += 180.0 + max(full_load_target - recipient_util, 0.0) * 3.5
                        if merged_util >= full_load_target and remainder_util < source_util:
                            score += min(source_util - remainder_util, 25.0) * 2.5
                        if shared_store_count > 0:
                            shared_store_reassign_bonus = float(
                                params.get(
                                    "v2_shared_store_reassign_bonus",
                                    DEFAULT_V2_SHARED_STORE_REASSIGN_BONUS,
                                )
                                or DEFAULT_V2_SHARED_STORE_REASSIGN_BONUS
                            )
                            score += min(shared_store_count, 3) * shared_store_reassign_bonus
                        if best is None or score > best[0]:
                            best = (
                                score,
                                source_id,
                                recipient_id,
                                source_remainder,
                                merged,
                                savings,
                            )

            if not best:
                break

            _, source_id, recipient_id, source_remainder, merged, _ = best
            if source_id not in active_loads or recipient_id not in active_loads:
                continue
            del active_loads[source_id]
            del active_loads[recipient_id]
            active_loads[source_remainder["_merge_id"]] = source_remainder
            active_loads[merged["_merge_id"]] = merged

        return active_loads

    def _cannibalize_weak_loads(
        self,
        active_loads,
        params,
        objective_weights,
        time_window_days,
    ):
        passes = int(
            params.get("v2_weak_cannibalize_passes", DEFAULT_V2_WEAK_CANNIBALIZE_PASSES)
            or DEFAULT_V2_WEAK_CANNIBALIZE_PASSES
        )
        if passes <= 0:
            return active_loads

        target_util = float(
            params.get("v2_weak_cannibalize_target_util", DEFAULT_V2_WEAK_CANNIBALIZE_TARGET_UTIL)
            or DEFAULT_V2_WEAK_CANNIBALIZE_TARGET_UTIL
        )
        if target_util <= 0:
            return active_loads

        stage_params = dict(params)
        stage_params["v2_fd_target_util"] = max(
            target_util,
            float(
                params.get("v2_fd_target_util", DEFAULT_V2_FD_TARGET_UTIL)
                or DEFAULT_V2_FD_TARGET_UTIL
            ),
        )
        stage_params["v2_fd_candidate_limit"] = max(
            int(
                params.get("v2_fd_candidate_limit", DEFAULT_V2_FD_CANDIDATE_LIMIT)
                or DEFAULT_V2_FD_CANDIDATE_LIMIT
            ),
            DEFAULT_V2_WEAK_CANNIBALIZE_CANDIDATE_LIMIT,
        )

        for _ in range(passes):
            targets = self._fd_rebalance_targets(active_loads, stage_params, time_window_days)
            targets = [
                load for load in targets
                if (load.get("utilization_pct") or 0.0) < target_util
            ]
            if not targets:
                break

            changed = False
            for target in targets:
                target_id = target.get("_merge_id")
                if target_id not in active_loads:
                    continue
                updated = self._try_absorb_target_load(
                    target_id,
                    active_loads,
                    stage_params,
                    objective_weights,
                    time_window_days,
                )
                if not updated:
                    continue
                active_loads = updated
                changed = True

            if not changed:
                break

        return active_loads

    def _apply_auto_hotshot_tail_assignments(self, active_loads, params):
        default_hotshot_enabled = True

        for merge_id, load in sorted(
            list((active_loads or {}).items()),
            key=lambda entry: float((entry[1] or {}).get("utilization_pct") or 0.0),
        ):
            if not load:
                continue
            origin_plant = str(
                load.get("origin_plant")
                or params.get("origin_plant")
                or ""
            ).strip().upper()
            if not self._auto_hotshot_enabled_for_plant(
                origin_plant,
                default_hotshot_enabled,
            ):
                continue
            current_trailer = stack_calculator.normalize_trailer_type(
                load.get("trailer_type"),
                default="STEP_DECK",
            )
            if current_trailer == "HOTSHOT":
                continue
            groups = list(load.get("groups") or [])
            if not groups or self._groups_require_wedge(groups):
                continue

            hotshot_config = self._stack_config_for_groups(
                groups,
                params,
                trailer_type="HOTSHOT",
            )
            if not hotshot_config or hotshot_config.get("exceeds_capacity"):
                continue
            overflow_feet = stack_calculator.capacity_overflow_feet(hotshot_config)
            if overflow_feet > 0.05:
                continue
            warning_codes = {
                str((warning or {}).get("code") or "").strip().upper()
                for warning in (hotshot_config.get("warnings") or [])
            }
            if "ITEM_HANGS_OVER_DECK" in warning_codes:
                continue

            updated = dict(load)
            updated["trailer_type"] = "HOTSHOT"
            updated["utilization_pct"] = float(
                hotshot_config.get("utilization_pct", load.get("utilization_pct") or 0.0) or 0.0
            )
            updated["exceeds_capacity"] = bool(
                hotshot_config.get("exceeds_capacity", load.get("exceeds_capacity"))
            )
            order_numbers = {
                (line.get("so_num") or "").strip()
                for line in (updated.get("lines") or [])
                if (line.get("so_num") or "").strip()
            }
            updated["over_capacity"] = bool(
                updated["exceeds_capacity"] and len(order_numbers) <= 1
            )
            updated["auto_trailer_upgrade"] = True
            updated["auto_trailer_reason"] = "Assigned HOTSHOT because the load fits on a hotshot trailer."
            active_loads[merge_id] = updated

        return active_loads

    def _plant_optimizer_settings(self, plant_code):
        normalized = str(plant_code or "").strip().upper()
        if not normalized:
            return {}
        if not hasattr(self, "_plant_optimizer_settings_cache"):
            self._plant_optimizer_settings_cache = {}
        if normalized not in self._plant_optimizer_settings_cache:
            self._plant_optimizer_settings_cache[normalized] = (
                db.get_optimizer_settings(normalized) or {}
            )
        return self._plant_optimizer_settings_cache.get(normalized) or {}

    def _auto_hotshot_enabled_for_plant(self, plant_code, default_enabled):
        normalized = str(plant_code or "").strip().upper()
        if not normalized:
            return bool(default_enabled)
        settings = self._plant_optimizer_settings(normalized)
        override_value = settings.get("auto_hotshot_enabled")
        if override_value in (None, ""):
            return bool(default_enabled)
        return self._coerce_bool(override_value, bool(default_enabled))

    def _fd_rebalance_targets(self, active_loads, params, time_window_days):
        target_util = float(
            params.get("v2_fd_target_util", DEFAULT_V2_FD_TARGET_UTIL)
            or DEFAULT_V2_FD_TARGET_UTIL
        )
        loads = list(active_loads.values())
        targets = [
            load
            for load in loads
            if (load.get("utilization_pct") or 0) < target_util
            and not self._is_date_orphan_load(load, loads, time_window_days)
        ]
        return sorted(targets, key=lambda load: load.get("utilization_pct") or 0)

    def _try_absorb_target_load(
        self,
        target_id,
        active_loads,
        params,
        objective_weights,
        time_window_days,
    ):
        working = dict(active_loads)
        target = working.get(target_id)
        if not target:
            return None

        target_util = target.get("utilization_pct") or 0
        if target_util < 40.0:
            max_increase = float(
                params.get(
                    "v2_fd_absorb_max_cost_increase_f",
                    DEFAULT_V2_FD_ABSORB_MAX_COST_INCREASE_F,
                )
                or DEFAULT_V2_FD_ABSORB_MAX_COST_INCREASE_F
            )
        else:
            max_increase = float(
                params.get(
                    "v2_fd_absorb_max_cost_increase_d",
                    DEFAULT_V2_FD_ABSORB_MAX_COST_INCREASE_D,
                )
                or DEFAULT_V2_FD_ABSORB_MAX_COST_INCREASE_D
            )
        detour_cap = float(
            params.get("v2_fd_absorb_detour_cap", DEFAULT_V2_FD_ABSORB_DETOUR_CAP)
            or DEFAULT_V2_FD_ABSORB_DETOUR_CAP
        )
        candidate_limit = int(
            params.get("v2_fd_candidate_limit", DEFAULT_V2_FD_CANDIDATE_LIMIT)
            or DEFAULT_V2_FD_CANDIDATE_LIMIT
        )

        total_delta = 0.0
        for group in sorted(
            target.get("groups") or [],
            key=lambda g: g.get("total_length_ft") or 0,
            reverse=True,
        ):
            group_load = self._build_load([group], params)
            recipients = [
                load
                for load in working.values()
                if load.get("_merge_id") != target_id
                and load.get("origin_plant") == target.get("origin_plant")
            ]
            ranked_recipients = self._recipient_candidates_for_target(
                target,
                group_load,
                recipients,
                params,
                time_window_days,
                candidate_limit,
            )

            best = None
            for recipient in ranked_recipients:
                recipient_id = recipient.get("_merge_id")
                if recipient_id not in working:
                    continue
                if not self._loads_date_compatible(recipient, group_load, time_window_days):
                    continue

                combined_groups = list(recipient.get("groups") or []) + [group]
                candidate_stack_options = []
                default_stack_config = self._stack_config_for_groups(combined_groups, params)
                if not self._is_multi_order_capacity_violation(combined_groups, default_stack_config):
                    candidate_stack_options.append(
                        ("DEFAULT", default_stack_config)
                    )

                # Final rescue: allow STEP_DECK recipients to shift to FLATBED if it unlocks
                # absorption of very small trailing groups.
                recipient_trailer = stack_calculator.normalize_trailer_type(
                    recipient.get("trailer_type"),
                    default="STEP_DECK",
                )
                preferred_trailer = stack_calculator.normalize_trailer_type(
                    params.get("trailer_type"),
                    default="STEP_DECK",
                )
                evaluate_flatbed = (
                    preferred_trailer.startswith("STEP_DECK")
                    or recipient_trailer.startswith("STEP_DECK")
                )
                if evaluate_flatbed and not self._groups_require_wedge(combined_groups):
                    flatbed_stack_config = self._stack_config_for_groups(
                        combined_groups,
                        params,
                        trailer_type="FLATBED",
                    )
                    if not self._is_multi_order_capacity_violation(combined_groups, flatbed_stack_config):
                        candidate_stack_options.append(
                            ("FLATBED", flatbed_stack_config)
                        )

                if not candidate_stack_options:
                    continue

                selected_trailer_mode, selected_stack_config = max(
                    candidate_stack_options,
                    key=lambda entry: float((entry[1] or {}).get("utilization_pct") or 0),
                )

                standalone_cost = (
                    (recipient.get("standalone_cost") or recipient.get("estimated_cost") or 0)
                    + (group_load.get("standalone_cost") or group_load.get("estimated_cost") or 0)
                )
                merged = self._build_load(
                    combined_groups,
                    params,
                    standalone_cost=standalone_cost,
                )
                if selected_trailer_mode == "FLATBED":
                    merged = dict(merged)
                    merged["trailer_type"] = "FLATBED"
                    merged["utilization_pct"] = selected_stack_config.get("utilization_pct", merged.get("utilization_pct"))
                    merged["exceeds_capacity"] = bool(
                        selected_stack_config.get("exceeds_capacity", merged.get("exceeds_capacity"))
                    )

                savings = (
                    (recipient.get("estimated_cost") or 0)
                    + (group_load.get("estimated_cost") or 0)
                    - (merged.get("estimated_cost") or 0)
                )
                delta = (merged.get("estimated_cost") or 0) - (recipient.get("estimated_cost") or 0)
                if total_delta + delta > max_increase:
                    continue
                if not self._detour_allowed(
                    recipient,
                    group_load,
                    merged,
                    detour_cap,
                    params,
                    savings=savings,
                ):
                    continue
                if (merged.get("utilization_pct") or 0) + 0.1 < (recipient.get("utilization_pct") or 0) - 3.0:
                    continue

                bonus = 0.0
                merged_util = merged.get("utilization_pct") or 0
                if merged_util >= 70:
                    bonus += 120.0
                elif merged_util >= 55:
                    bonus += 60.0
                if (merged.get("destination_state") or "") == (target.get("destination_state") or ""):
                    bonus += 30.0
                bonus += max(merged_util - (recipient.get("utilization_pct") or 0), 0.0) * 4.0
                score = (savings + bonus)
                if best is None or score > best[0]:
                    best = (score, recipient_id, merged, delta)

            if not best:
                return None

            _, recipient_id, merged_load, delta = best
            if recipient_id not in working:
                return None
            del working[recipient_id]
            working[merged_load["_merge_id"]] = merged_load
            total_delta += delta

        if target_id not in working:
            return None
        del working[target_id]
        return working

    def _recipient_candidates_for_target(
        self,
        target,
        group_load,
        recipients,
        params,
        time_window_days,
        limit,
    ):
        target_meta = self._load_pair_meta(group_load or target, params)
        scored = []
        for recipient in recipients:
            if not self._loads_date_compatible(recipient, group_load, time_window_days):
                continue
            if not self._loads_compatible(
                recipient,
                group_load,
                params.get("geo_radius"),
                time_window_days,
                params,
            ):
                continue
            score = self._pair_priority_score(
                target_meta,
                self._load_pair_meta(recipient, params),
                params,
            )
            if score is None:
                continue
            shared_store_count = self._shared_store_count(group_load, recipient)
            if shared_store_count > 0:
                score -= min(shared_store_count, 3) * DEFAULT_V2_SHARED_STORE_PRIORITY_BONUS
            if (recipient.get("destination_state") or "") == (target.get("destination_state") or ""):
                score -= 20.0
            if (recipient.get("utilization_pct") or 0) < 55:
                score -= 8.0
            scored.append((score, recipient.get("_merge_id"), recipient))

        if not scored:
            return []
        top = heapq.nsmallest(max(limit, 1), scored)
        return [entry[2] for entry in top]

    def _reassign_directional_bonus(self, group_load, recipient, source_remainder, params):
        group_meta = self._load_pair_meta(group_load, params)
        recipient_meta = self._load_pair_meta(recipient, params)
        recipient_score = self._pair_priority_score(group_meta, recipient_meta, params)
        if recipient_score is None:
            return 0.0
        if not source_remainder:
            return 0.0
        remainder_meta = self._load_pair_meta(source_remainder, params)
        remainder_score = self._pair_priority_score(group_meta, remainder_meta, params)
        if remainder_score is None:
            return 0.0
        return max(remainder_score - recipient_score, 0.0)

    def _evaluate_merge_candidate(self, load_a, load_b, params, objective_weights=None):
        merged_load = self._merge_loads(load_a, load_b, params)
        if not merged_load:
            return None
        savings = (load_a.get("estimated_cost") or 0) + (load_b.get("estimated_cost") or 0)
        savings -= merged_load.get("estimated_cost") or 0
        gain = savings
        if objective_weights:
            gain += self._objective_bonus_for_merge(
                load_a,
                load_b,
                merged_load,
                objective_weights,
            )
        return {"merged_load": merged_load, "savings": savings, "gain": gain}

    def _merge_loads(self, load_a, load_b, params):
        combined_groups = list(load_a.get("groups", [])) + list(load_b.get("groups", []))
        if not combined_groups:
            return None
        if not self._groups_mix_compatible(combined_groups):
            return None

        if not self._check_stacking_compatible(combined_groups):
            return None

        ordered_stops = self._ordered_stops_for_groups(combined_groups, params)
        stop_sequence_map = self._stop_sequence_map_for_groups(combined_groups, ordered_stops)
        stack_config = self._stack_config_for_groups(
            combined_groups,
            params,
            stop_sequence_map=stop_sequence_map,
        )
        if self._is_multi_order_capacity_violation(combined_groups, stack_config):
            return None

        standalone_cost = (load_a.get("standalone_cost") or 0) + (load_b.get("standalone_cost") or 0)
        merged_load = self._build_load(combined_groups, params, standalone_cost=standalone_cost)
        if self._load_is_multi_order_capacity_violation(merged_load):
            return None
        return merged_load

    def _candidate_pair_indices(
        self,
        load_list,
        params,
        require_low_util_target=False,
        target_merge_ids=None,
    ):
        total = len(load_list)
        if total <= 1:
            return
        if (params.get("algorithm_version") or "").lower() != "v2":
            for idx in range(total):
                for jdx in range(idx + 1, total):
                    yield idx, jdx
            return

        metas = [self._load_pair_meta(load, params) for load in load_list]
        pair_set = set()
        for idx, load in enumerate(load_list):
            if target_merge_ids and load.get("_merge_id") not in target_merge_ids:
                continue
            if require_low_util_target and not self._is_low_util_for_target(
                load.get("utilization_pct") or 0,
                params,
            ):
                continue
            neighbor_limit = self._v2_neighbor_count_for_load(load, params)
            scored = []
            for jdx, other in enumerate(load_list):
                if idx == jdx:
                    continue
                score = self._pair_priority_score(metas[idx], metas[jdx], params)
                if score is None:
                    continue
                scored.append((score, jdx))
            for _, jdx in heapq.nsmallest(neighbor_limit, scored):
                left, right = (idx, jdx) if idx < jdx else (jdx, idx)
                pair_set.add((left, right))

        for pair in pair_set:
            yield pair

    def _candidate_peers_for_load(self, merged_load, active_loads, params):
        peers = [
            load for load in active_loads.values()
            if load.get("_merge_id") != merged_load.get("_merge_id")
        ]
        if not peers:
            return []
        if (params.get("algorithm_version") or "").lower() != "v2":
            return peers

        neighbor_limit = self._v2_neighbor_count_for_load(
            merged_load,
            params,
            incremental=True,
        )
        merged_meta = self._load_pair_meta(merged_load, params)
        scored = []
        for load in peers:
            score = self._pair_priority_score(
                merged_meta,
                self._load_pair_meta(load, params),
                params,
            )
            if score is None:
                continue
            scored.append((score, load.get("_merge_id"), load))
        return [load for _, __, load in heapq.nsmallest(neighbor_limit, scored)]

    def _v2_neighbor_count_for_load(self, load, params, incremental=False):
        if incremental:
            base = int(
                params.get("v2_incremental_neighbors", DEFAULT_V2_INCREMENTAL_NEIGHBORS)
                or DEFAULT_V2_INCREMENTAL_NEIGHBORS
            )
            expanded = int(
                params.get("v2_incremental_neighbors_low_util", base * 2)
                or base * 2
            )
        else:
            base = int(
                params.get("v2_pair_neighbors", DEFAULT_V2_PAIR_NEIGHBORS)
                or DEFAULT_V2_PAIR_NEIGHBORS
            )
            expanded = int(
                params.get("v2_pair_neighbors_low_util", DEFAULT_V2_PAIR_NEIGHBORS_LOW_UTIL)
                or DEFAULT_V2_PAIR_NEIGHBORS_LOW_UTIL
            )
        if self._is_low_util_for_target(load.get("utilization_pct") or 0, params):
            return max(expanded, base)
        return max(base, 1)

    def _load_pair_meta(self, load, params):
        origin_plant = load.get("origin_plant") or params.get("origin_plant")
        origin_coords = geo_utils.plant_coords_for_code(origin_plant)
        anchor = self._load_anchor_coords(load)
        miles = self.cost_calculator.distance(origin_coords, anchor) if origin_coords and anchor else None
        effective_due_window_days = self._coerce_optional_non_negative_int(
            load.get("effective_due_window_days")
        )
        if effective_due_window_days is None:
            effective_due_window_days = self._effective_time_window_days(
                groups=load.get("groups") or [],
                base_time_window_days=params.get("time_window_days"),
                enforce_time_window=params.get("enforce_time_window", True),
                loads=[load],
            )
        short_upper_threshold_ft = self._coerce_non_negative_float(
            params.get("upper_two_across_max_length_ft"),
            0.0,
        )
        short_upper_group_count = 0
        if short_upper_threshold_ft > 0:
            for group in (load.get("groups") or []):
                max_unit_length_ft = self._coerce_non_negative_float(
                    (group or {}).get("max_unit_length_ft"),
                    0.0,
                )
                if max_unit_length_ft <= (short_upper_threshold_ft + 1e-6):
                    short_upper_group_count += 1
        return {
            "state": (load.get("destination_state") or "").strip().upper(),
            "utilization": self._effective_fill_pct(load),
            "origin_miles": miles,
            "bearing": self._bearing_from_origin(origin_coords, anchor),
            "due_anchor": self._load_due_anchor(load),
            "effective_due_window_days": effective_due_window_days,
            "max_unit_length_ft": self._load_max_unit_length(load),
            "store_codes": list(load.get("store_codes") or []),
            "short_upper_group_count": short_upper_group_count,
        }

    def _pair_priority_score(self, meta_a, meta_b, params):
        score = 0.0
        due_gap = 0
        if meta_a.get("due_anchor") is not None and meta_b.get("due_anchor") is not None:
            due_gap = abs(meta_a["due_anchor"] - meta_b["due_anchor"])

        effective_windows = [
            self._coerce_optional_non_negative_int(meta_a.get("effective_due_window_days")),
            self._coerce_optional_non_negative_int(meta_b.get("effective_due_window_days")),
        ]
        effective_windows = [value for value in effective_windows if value is not None]
        effective_window = min(effective_windows) if effective_windows else None
        if effective_window is not None and due_gap > (effective_window + 3):
            return None

        bearing_a = meta_a.get("bearing")
        bearing_b = meta_b.get("bearing")
        if bearing_a is not None and bearing_b is not None:
            score += self._bearing_delta(bearing_a, bearing_b) * 2.2
        else:
            score += 35.0

        miles_a = meta_a.get("origin_miles")
        miles_b = meta_b.get("origin_miles")
        if miles_a is not None and miles_b is not None:
            score += abs(miles_a - miles_b) * 0.09
        else:
            score += 25.0

        score += due_gap * 5.0

        state_a = meta_a.get("state") or ""
        state_b = meta_b.get("state") or ""
        if state_a and state_b:
            score += -12.0 if state_a == state_b else 8.0

        if self._is_low_util_for_target(meta_a.get("utilization") or 0, params) or self._is_low_util_for_target(meta_b.get("utilization") or 0, params):
            score -= 10.0

        # Near home base, prioritize longer items first so large units are less likely
        # to become stranded after smaller items have already consumed easy slots.
        score -= self._home_length_priority_bonus(meta_a, meta_b, params)

        shared_store_count = self._shared_store_count(meta_a, meta_b)
        if shared_store_count > 0:
            shared_store_bonus = float(
                params.get("v2_shared_store_priority_bonus", DEFAULT_V2_SHARED_STORE_PRIORITY_BONUS)
                or DEFAULT_V2_SHARED_STORE_PRIORITY_BONUS
            )
            score -= min(shared_store_count, 3) * shared_store_bonus

        requested_trailer = stack_calculator.normalize_trailer_type(
            params.get("trailer_type"),
            default="STEP_DECK",
        )
        if requested_trailer.startswith("STEP_DECK"):
            short_upper_groups = int(meta_a.get("short_upper_group_count") or 0) + int(
                meta_b.get("short_upper_group_count") or 0
            )
            if short_upper_groups >= 2:
                short_upper_bonus = float(
                    params.get(
                        "v2_short_upper_pair_priority_bonus",
                        DEFAULT_V2_SHORT_UPPER_PAIR_PRIORITY_BONUS,
                    )
                    or DEFAULT_V2_SHORT_UPPER_PAIR_PRIORITY_BONUS
                )
                score -= min(short_upper_groups, 4) * short_upper_bonus

        if self._is_directional_from_meta(meta_a, meta_b, params):
            score -= 10.0
        return score

    def _home_length_priority_bonus(self, meta_a, meta_b, params):
        enabled_value = params.get(
            "v2_home_length_priority_enabled",
            DEFAULT_V2_HOME_LENGTH_PRIORITY_ENABLED,
        )
        if isinstance(enabled_value, str):
            enabled = enabled_value.strip().lower() in {"1", "true", "yes", "on", "y"}
        else:
            enabled = bool(enabled_value)
        if not enabled:
            return 0.0

        miles_a = meta_a.get("origin_miles")
        miles_b = meta_b.get("origin_miles")
        if miles_a is None or miles_b is None:
            return 0.0

        try:
            radius_miles = float(
                params.get(
                    "v2_home_length_priority_radius_miles",
                    DEFAULT_V2_HOME_LENGTH_PRIORITY_RADIUS_MILES,
                )
                or DEFAULT_V2_HOME_LENGTH_PRIORITY_RADIUS_MILES
            )
            threshold_ft = float(
                params.get(
                    "v2_home_length_priority_threshold_ft",
                    DEFAULT_V2_HOME_LENGTH_PRIORITY_THRESHOLD_FT,
                )
                or DEFAULT_V2_HOME_LENGTH_PRIORITY_THRESHOLD_FT
            )
            weight = float(
                params.get(
                    "v2_home_length_priority_weight",
                    DEFAULT_V2_HOME_LENGTH_PRIORITY_WEIGHT,
                )
                or DEFAULT_V2_HOME_LENGTH_PRIORITY_WEIGHT
            )
            max_bonus = float(
                params.get(
                    "v2_home_length_priority_max_bonus",
                    DEFAULT_V2_HOME_LENGTH_PRIORITY_MAX_BONUS,
                )
                or DEFAULT_V2_HOME_LENGTH_PRIORITY_MAX_BONUS
            )
        except (TypeError, ValueError):
            return 0.0

        if radius_miles <= 0 or weight <= 0 or max_bonus <= 0:
            return 0.0

        max_origin_miles = max(miles_a, miles_b)
        if max_origin_miles >= radius_miles:
            return 0.0

        longest_ft = max(
            float(meta_a.get("max_unit_length_ft") or 0),
            float(meta_b.get("max_unit_length_ft") or 0),
        )
        if longest_ft <= threshold_ft:
            return 0.0

        home_proximity = (radius_miles - max_origin_miles) / radius_miles
        length_excess = longest_ft - threshold_ft
        bonus = length_excess * home_proximity * weight
        return max(0.0, min(bonus, max_bonus))

    def _shared_store_count(self, left, right):
        if not isinstance(left, dict) or not isinstance(right, dict):
            return 0
        left_codes = {
            str(value or "").strip().upper()
            for value in (left.get("store_codes") or [])
            if str(value or "").strip()
        }
        right_codes = {
            str(value or "").strip().upper()
            for value in (right.get("store_codes") or [])
            if str(value or "").strip()
        }
        if not left_codes or not right_codes:
            return 0
        return len(left_codes.intersection(right_codes))

    def _prebatch_seed_priority(self, group, singleton_load, store_frequency, params):
        if not isinstance(group, dict):
            return (0.0, 0.0, 0.0, 0.0)
        duplicate_store_score = 0.0
        for store_code in (group.get("store_codes") or []):
            normalized = str(store_code or "").strip().upper()
            if not normalized:
                continue
            duplicate_store_score += max((store_frequency or {}).get(normalized, 0) - 1, 0)
        max_unit_length_ft = self._coerce_non_negative_float(group.get("max_unit_length_ft"), 0.0)
        total_length_ft = self._coerce_non_negative_float(group.get("total_length_ft"), 0.0)
        short_upper_threshold_ft = self._coerce_non_negative_float(
            params.get("upper_two_across_max_length_ft"),
            0.0,
        )
        short_upper_bonus = 0.0
        if short_upper_threshold_ft > 0 and max_unit_length_ft <= (short_upper_threshold_ft + 1e-6):
            short_upper_bonus = 1.0
        singleton_util = self._coerce_non_negative_float(
            (singleton_load or {}).get("utilization_pct"),
            0.0,
        )
        duplicate_store_seed_bonus = float(
            params.get(
                "v2_prebatch_store_duplicate_seed_bonus",
                DEFAULT_V2_PREBATCH_STORE_DUPLICATE_SEED_BONUS,
            )
            or DEFAULT_V2_PREBATCH_STORE_DUPLICATE_SEED_BONUS
        )
        return (
            duplicate_store_score * duplicate_store_seed_bonus,
            max_unit_length_ft,
            total_length_ft,
            short_upper_bonus,
            singleton_util,
        )

    def _prebatch_should_keep_filling(self, load, params):
        target_util = float(
            params.get("v2_prebatch_target_util", DEFAULT_V2_PREBATCH_TARGET_UTIL)
            or DEFAULT_V2_PREBATCH_TARGET_UTIL
        )
        return float((load or {}).get("utilization_pct") or 0.0) + 1e-6 < target_util

    def _prebatch_candidate_groups(self, current_load, remaining_groups, singleton_by_key, params):
        groups = list(remaining_groups or [])
        if not groups:
            return []
        limit = int(
            params.get("v2_prebatch_candidate_limit", DEFAULT_V2_PREBATCH_CANDIDATE_LIMIT)
            or DEFAULT_V2_PREBATCH_CANDIDATE_LIMIT
        )
        if limit <= 0:
            limit = DEFAULT_V2_PREBATCH_CANDIDATE_LIMIT
        radius = params.get("geo_radius")
        time_window_days = (
            params.get("time_window_days")
            if params.get("enforce_time_window", True)
            else None
        )
        current_meta = self._load_pair_meta(current_load, params)
        scored = []
        fallback = []
        for group in groups:
            key = str((group or {}).get("key") or "").strip()
            if not key:
                continue
            candidate_load = singleton_by_key.get(key) or self._build_load([group], params)
            if not self._loads_compatible(current_load, candidate_load, radius, time_window_days, params):
                continue
            candidate_meta = self._load_pair_meta(candidate_load, params)
            pair_score = self._pair_priority_score(current_meta, candidate_meta, params)
            if pair_score is None:
                continue
            scored.append((pair_score, key, group))
            fallback.append(group)
        if not scored:
            return fallback[:limit]
        top = heapq.nsmallest(max(limit, 1), scored)
        return [entry[2] for entry in top]

    def _prebatch_candidate_score(self, current_load, candidate_load, merged_load, params):
        objective_bonus = self._objective_bonus_for_merge(
            current_load,
            candidate_load,
            merged_load,
            self._v2_objective_weights(params),
        )
        current_util = float((current_load or {}).get("utilization_pct") or 0.0)
        merged_util = float((merged_load or {}).get("utilization_pct") or 0.0)
        util_gain = max(merged_util - current_util, 0.0)
        shared_store_count = self._shared_store_count(current_load, candidate_load)
        savings = (
            float((current_load or {}).get("estimated_cost") or 0.0)
            + float((candidate_load or {}).get("estimated_cost") or 0.0)
            - float((merged_load or {}).get("estimated_cost") or 0.0)
        )
        two_across_gain = max(
            int((merged_load or {}).get("upper_two_across_applied_count") or 0)
            - int((current_load or {}).get("upper_two_across_applied_count") or 0)
            - int((candidate_load or {}).get("upper_two_across_applied_count") or 0),
            0,
        )
        stop_delta = max(
            int((merged_load or {}).get("stop_count") or 0)
            - int((current_load or {}).get("stop_count") or 0),
            0,
        )
        util_gain_weight = float(
            params.get("v2_prebatch_util_gain_weight", DEFAULT_V2_PREBATCH_UTIL_GAIN_WEIGHT)
            or DEFAULT_V2_PREBATCH_UTIL_GAIN_WEIGHT
        )
        shared_store_bonus = float(
            params.get("v2_prebatch_shared_store_bonus", DEFAULT_V2_PREBATCH_SHARED_STORE_BONUS)
            or DEFAULT_V2_PREBATCH_SHARED_STORE_BONUS
        )
        two_across_bonus = float(
            params.get("v2_prebatch_two_across_gain_bonus", DEFAULT_V2_PREBATCH_TWO_ACROSS_GAIN_BONUS)
            or DEFAULT_V2_PREBATCH_TWO_ACROSS_GAIN_BONUS
        )
        stop_delta_penalty = float(
            params.get("v2_prebatch_stop_delta_penalty", DEFAULT_V2_PREBATCH_STOP_DELTA_PENALTY)
            or DEFAULT_V2_PREBATCH_STOP_DELTA_PENALTY
        )
        return (
            objective_bonus
            + (util_gain * util_gain_weight)
            + (shared_store_count * shared_store_bonus)
            + (two_across_gain * two_across_bonus)
            + (savings * float(
                params.get("v2_prebatch_savings_weight", DEFAULT_V2_PREBATCH_SAVINGS_WEIGHT)
                or DEFAULT_V2_PREBATCH_SAVINGS_WEIGHT
            ))
            - (stop_delta * stop_delta_penalty)
        )

    def _load_max_unit_length(self, load):
        max_length = 0.0
        for line in (load.get("lines") or []):
            length = float(line.get("unit_length_ft") or 0)
            if length > max_length:
                max_length = length
        if max_length > 0:
            return max_length
        for group in (load.get("groups") or []):
            for line in (group.get("lines") or []):
                length = float(line.get("unit_length_ft") or 0)
                if length > max_length:
                    max_length = length
        return max_length

    def _load_anchor_coords(self, load):
        centroid = load.get("centroid")
        if centroid:
            return centroid
        for coords in (load.get("stop_coords") or []):
            if coords:
                return coords
        return None

    def _load_due_anchor(self, load):
        due_min = load.get("due_date_min")
        due_max = load.get("due_date_max")
        if due_min and due_max:
            return (due_min.toordinal() + due_max.toordinal()) // 2
        if due_min:
            return due_min.toordinal()
        if due_max:
            return due_max.toordinal()
        return None

    def _bearing_from_origin(self, origin_coords, destination_coords):
        if not origin_coords or not destination_coords:
            return None
        lat1 = math.radians(origin_coords[0])
        lon1 = math.radians(origin_coords[1])
        lat2 = math.radians(destination_coords[0])
        lon2 = math.radians(destination_coords[1])
        delta_lon = lon2 - lon1
        x_axis = math.sin(delta_lon) * math.cos(lat2)
        y_axis = (
            math.cos(lat1) * math.sin(lat2)
            - math.sin(lat1) * math.cos(lat2) * math.cos(delta_lon)
        )
        if x_axis == 0 and y_axis == 0:
            return None
        return (math.degrees(math.atan2(x_axis, y_axis)) + 360.0) % 360.0

    def _bearing_delta(self, bearing_a, bearing_b):
        delta = abs((bearing_a or 0) - (bearing_b or 0)) % 360.0
        return 360.0 - delta if delta > 180.0 else delta

    def _is_directional_from_meta(self, meta_a, meta_b, params):
        if meta_a.get("bearing") is None or meta_b.get("bearing") is None:
            return False
        if meta_a.get("origin_miles") is None or meta_b.get("origin_miles") is None:
            return False

        bearing_limit = float(
            params.get("v2_on_way_bearing_deg", DEFAULT_V2_ONWAY_BEARING_DEG)
            or DEFAULT_V2_ONWAY_BEARING_DEG
        )
        radial_gap_limit = float(
            params.get("v2_on_way_radial_gap_miles", DEFAULT_V2_ONWAY_RADIAL_GAP_MILES)
            or DEFAULT_V2_ONWAY_RADIAL_GAP_MILES
        )
        market_avg_nn = float(params.get("v2_market_avg_nn_miles", 0.0) or 0.0)
        market_avg_origin = float(params.get("v2_market_avg_origin_miles", 0.0) or 0.0)
        adaptive_radial_gap_limit = radial_gap_limit
        if market_avg_nn > 0:
            adaptive_radial_gap_limit = min(
                adaptive_radial_gap_limit,
                max(market_avg_nn * 4.0, 80.0),
            )
        if market_avg_origin > 0:
            adaptive_radial_gap_limit = min(
                adaptive_radial_gap_limit,
                max(market_avg_origin * 0.45, 120.0),
            )
        if self._bearing_delta(meta_a["bearing"], meta_b["bearing"]) > bearing_limit:
            return False
        if abs(meta_a["origin_miles"] - meta_b["origin_miles"]) > adaptive_radial_gap_limit:
            return False
        return min(meta_a["origin_miles"], meta_b["origin_miles"]) >= 40.0

    def _loads_compatible(self, load_a, load_b, radius, time_window_days, params):
        if load_a.get("origin_plant") != load_b.get("origin_plant"):
            return False
        if not self._loads_mix_compatible(load_a, load_b):
            return False
        if not self._loads_date_compatible(load_a, load_b, time_window_days):
            return False
        state_a = self._single_state_code(load_a)
        state_b = self._single_state_code(load_b)
        if state_a and state_b and state_a != state_b and not self._cross_state_pair_allowed(load_a, load_b, params):
            return False
        cross_state_rescue_fill_threshold = float(
            params.get("v2_cross_state_rescue_fill_threshold", 40.0) or 40.0
        )
        if (
            state_a
            and state_b
            and state_a != state_b
            and (
                self._load_order_count(load_a) > 1
                or self._load_order_count(load_b) > 1
            )
            and min(self._effective_fill_pct(load_a), self._effective_fill_pct(load_b)) >= cross_state_rescue_fill_threshold
        ):
            return False
        if not self._loads_geo_compatible(load_a, load_b, radius):
            if not self._allow_v2_geo_escape(load_a, load_b, params):
                return False
        return True

    def _allow_v2_geo_escape(self, load_a, load_b, params):
        if (params.get("algorithm_version") or "").lower() != "v2":
            return False
        same_state_escape_threshold = float(
            params.get(
                "v2_same_state_geo_escape_threshold",
                DEFAULT_V2_SAME_STATE_GEO_ESCAPE_THRESHOLD,
            )
            or DEFAULT_V2_SAME_STATE_GEO_ESCAPE_THRESHOLD
        )
        state_a = self._single_state_code(load_a)
        state_b = self._single_state_code(load_b)
        if (
            state_a
            and state_b
            and state_a == state_b
            and max(self._effective_fill_pct(load_a), self._effective_fill_pct(load_b)) < same_state_escape_threshold
        ):
            return True
        if self._is_very_low_util_pair(load_a, load_b, params):
            return True
        if self._is_directionally_on_way_pair(load_a, load_b, params):
            return (
                self._is_low_util_for_target(load_a, params)
                or self._is_low_util_for_target(load_b, params)
            )
        return False

    def _is_very_low_util_pair(self, load_a, load_b, params):
        threshold = float(
            params.get("v2_geo_escape_threshold", DEFAULT_V2_GEO_ESCAPE_THRESHOLD)
            or DEFAULT_V2_GEO_ESCAPE_THRESHOLD
        )
        util_a = self._effective_fill_pct(load_a)
        util_b = self._effective_fill_pct(load_b)
        return util_a <= threshold and util_b <= threshold

    def _is_low_util_for_target(self, load_or_pct, params):
        target = float(
            params.get("v2_low_util_threshold", LOW_UTIL_THRESHOLD_PCT)
            or LOW_UTIL_THRESHOLD_PCT
        )
        if isinstance(load_or_pct, dict):
            utilization_pct = self._effective_fill_pct(load_or_pct)
        else:
            utilization_pct = load_or_pct or 0
        return utilization_pct < target

    def _pair_has_low_util_target(self, load_a, load_b, params):
        return (
            self._is_low_util_for_target(load_a, params)
            or self._is_low_util_for_target(load_b, params)
        )

    def _count_low_util_target(self, loads, params):
        return sum(
            1
            for load in loads
            if self._is_low_util_for_target(load, params)
        )

    def _is_directionally_on_way_pair(self, load_a, load_b, params):
        return self._is_directional_from_meta(
            self._load_pair_meta(load_a, params),
            self._load_pair_meta(load_b, params),
            params,
        )

    def _cross_state_pair_allowed(self, load_a, load_b, params):
        states_a = self._load_state_codes(load_a)
        states_b = self._load_state_codes(load_b)
        if not states_a or not states_b:
            return True
        if states_a == states_b:
            return True
        if len(states_a) > 1 or len(states_b) > 1:
            return False

        state_a = next(iter(states_a))
        state_b = next(iter(states_b))
        if state_a == state_b:
            return True

        cross_state_params = dict(params or {})
        cross_state_params["v2_on_way_bearing_deg"] = float(
            cross_state_params.get(
                "v2_cross_state_on_way_bearing_deg",
                DEFAULT_V2_CROSS_STATE_ONWAY_BEARING_DEG,
            )
            or DEFAULT_V2_CROSS_STATE_ONWAY_BEARING_DEG
        )
        cross_state_params["v2_on_way_radial_gap_miles"] = float(
            cross_state_params.get(
                "v2_cross_state_radial_gap_miles",
                DEFAULT_V2_CROSS_STATE_RADIAL_GAP_MILES,
            )
            or DEFAULT_V2_CROSS_STATE_RADIAL_GAP_MILES
        )
        if not self._is_directionally_on_way_pair(load_a, load_b, cross_state_params):
            return False

        min_distance_limit = float(
            cross_state_params.get(
                "v2_cross_state_min_distance_miles",
                DEFAULT_V2_CROSS_STATE_MIN_DISTANCE_MILES,
            )
            or DEFAULT_V2_CROSS_STATE_MIN_DISTANCE_MILES
        )
        min_distance = self._min_distance_between_loads(load_a, load_b)
        if min_distance is None:
            return False
        return min_distance <= min_distance_limit

    def _detour_allowed(self, load_a, load_b, merged_load, max_detour_pct, params, savings=None):
        if max_detour_pct is None:
            return True
        detour = self._detour_pct(merged_load)
        if detour <= max_detour_pct:
            return True

        # In v2, permit a controlled detour escape for very-low-util, cost-saving merges.
        if (params.get("algorithm_version") or "").lower() != "v2":
            return False
        if savings is not None and savings < 0:
            return False

        util_a = self._effective_fill_pct(load_a)
        util_b = self._effective_fill_pct(load_b)
        merged_util = self._effective_fill_pct(merged_load)
        if merged_util + 1e-6 < max(util_a, util_b):
            return False

        if self._is_very_low_util_pair(load_a, load_b, params):
            detour_escape_cap = float(
                params.get("v2_detour_escape_cap", max(max_detour_pct * 3.0, DEFAULT_V2_DETOUR_ESCAPE_FLOOR))
                or max(max_detour_pct * 3.0, DEFAULT_V2_DETOUR_ESCAPE_FLOOR)
            )
            return detour <= detour_escape_cap

        same_state_escape_threshold = float(
            params.get(
                "v2_same_state_geo_escape_threshold",
                DEFAULT_V2_SAME_STATE_GEO_ESCAPE_THRESHOLD,
            )
            or DEFAULT_V2_SAME_STATE_GEO_ESCAPE_THRESHOLD
        )
        state_a = self._single_state_code(load_a)
        state_b = self._single_state_code(load_b)
        if (
            state_a
            and state_b
            and state_a == state_b
            and max(util_a, util_b) < same_state_escape_threshold
        ):
            detour_escape_cap = float(
                params.get(
                    "v2_same_state_detour_escape_cap",
                    max(max_detour_pct * 6.0, DEFAULT_V2_SAME_STATE_DETOUR_ESCAPE_FLOOR),
                )
                or max(max_detour_pct * 6.0, DEFAULT_V2_SAME_STATE_DETOUR_ESCAPE_FLOOR)
            )
            return detour <= detour_escape_cap

        if not self._is_directionally_on_way_pair(load_a, load_b, params):
            return False
        if not (self._is_low_util_for_target(util_a, params) or self._is_low_util_for_target(util_b, params)):
            return False

        detour_escape_cap = float(
            params.get(
                "v2_directional_detour_cap",
                max(max_detour_pct * 4.0, DEFAULT_V2_DIRECTIONAL_DETOUR_FLOOR),
            )
            or max(max_detour_pct * 4.0, DEFAULT_V2_DIRECTIONAL_DETOUR_FLOOR)
        )
        return detour <= detour_escape_cap

    def _loads_date_compatible(self, load_a, load_b, time_window_days):
        if not self._loads_mix_compatible(load_a, load_b):
            return False
        effective_window = self._effective_time_window_days(
            groups=[],
            base_time_window_days=time_window_days,
            enforce_time_window=time_window_days is not None,
            loads=[load_a, load_b],
        )
        if effective_window is None:
            return True
        start_a = load_a.get("due_date_min")
        end_a = load_a.get("due_date_max")
        start_b = load_b.get("due_date_min")
        end_b = load_b.get("due_date_max")
        if not (start_a and end_a and start_b and end_b):
            return True
        combined_start = min(start_a, start_b)
        combined_end = max(end_a, end_b)
        return (combined_end - combined_start).days <= effective_window

    def _loads_mix_compatible(self, load_a, load_b):
        combined_groups = list(load_a.get("groups") or []) + list(load_b.get("groups") or [])
        return self._groups_mix_compatible(combined_groups)

    def _groups_mix_compatible(self, groups):
        groups = list(groups or [])
        if len(groups) <= 1:
            return True

        no_mix_groups = [group for group in groups if group.get("no_mix")]
        if not no_mix_groups:
            return True

        mix_keys = {
            (
                (group.get("strategic_key") or "").strip().lower()
                or customer_rules.normalize_customer_text(group.get("cust_name") or "").lower()
            )
            for group in groups
            if (group.get("strategic_key") or group.get("cust_name"))
        }
        return len(mix_keys) <= 1

    def _effective_time_window_days(
        self,
        groups,
        base_time_window_days,
        enforce_time_window=True,
        loads=None,
    ):
        if not enforce_time_window:
            return None

        base_days = self._coerce_optional_non_negative_int(base_time_window_days)
        if base_days is None:
            base_days = 0

        effective_windows = []

        for group in (groups or []):
            customer_days = self._coerce_optional_non_negative_int(
                group.get("default_due_date_flex_days")
            )
            effective_windows.append(base_days if customer_days is None else customer_days)

        for load in (loads or []):
            load_window = self._coerce_optional_non_negative_int(
                load.get("effective_due_window_days")
            )
            if load_window is None:
                load_window = self._coerce_optional_non_negative_int(
                    load.get("due_flex_days")
                )
            if load_window is None and (load.get("groups") or []):
                load_window = self._effective_time_window_days(
                    groups=load.get("groups") or [],
                    base_time_window_days=base_days,
                    enforce_time_window=True,
                    loads=[],
                )
            if load_window is not None:
                effective_windows.append(load_window)

        if not effective_windows:
            return base_days

        # Strictest customer rule governs a mixed set.
        return min(effective_windows)

    def _loads_geo_compatible(self, load_a, load_b, radius):
        if not radius:
            return True
        min_distance = self._min_distance_between_loads(load_a, load_b)
        if min_distance is None:
            return True
        return min_distance <= radius

    def _build_load(self, groups, params, standalone_cost=None):
        base_load = self._build_load_base(groups, params)
        load = self._clone_cached_load(base_load)
        estimated_cost = float(load.get("estimated_cost") or 0.0)
        standalone_cost = estimated_cost if standalone_cost is None else standalone_cost
        consolidation_savings = standalone_cost - estimated_cost
        fragility_score = (consolidation_savings / standalone_cost) if standalone_cost else 0.0
        load["_merge_id"] = self._next_merge_id()
        load["optimization_score"] = consolidation_savings or 0.0
        load["standalone_cost"] = standalone_cost
        load["consolidation_savings"] = consolidation_savings
        load["fragility_score"] = fragility_score
        over_capacity = bool(load.get("exceeds_capacity")) and self._load_order_count(load) <= 1
        load["over_capacity"] = over_capacity
        capacity_feet_value = float(load.get("capacity_feet") or 0.0)
        total_linear_feet_value = float(load.get("total_linear_feet") or 0.0)
        load["practical_fill_pct"] = round(
            ((total_linear_feet_value / capacity_feet_value) * 100.0) if capacity_feet_value > 0 else 0.0,
            1,
        )
        load["effective_fill_pct"] = round(self._effective_fill_pct(load), 1)
        if load.get("auto_trailer_upgrade"):
            load["auto_trailer_upgrade"] = True
            load["auto_trailer_reason"] = load.get("auto_trailer_reason") or ""
        return load

    def _build_load_base(self, groups, params):
        cache_key = self._load_build_cache_key(groups, params)
        cached = self._load_build_cache.get(cache_key)
        if cached is not None:
            return cached

        all_lines = [line for group in groups for line in group.get("lines", [])]
        preferred_trailer = self._preferred_trailer_for_groups(
            groups,
            params.get("trailer_type", "STEP_DECK"),
        )

        cost_data = self._cost_data_for_groups(groups, params)
        ordered_stops = cost_data["ordered_stops"]
        stop_sequence_map = self._stop_sequence_map_for_groups(groups, ordered_stops)

        stack_config = self._stack_config_for_groups(
            groups,
            params,
            trailer_type=preferred_trailer,
            stop_sequence_map=stop_sequence_map,
        )
        trailer_type = stack_config.get("trailer_type") or preferred_trailer
        utilization = stack_config.get("utilization_pct", 0) or 0
        exceeds_capacity = stack_config.get("exceeds_capacity", False)

        estimated_miles = cost_data["total_miles"]
        estimated_cost = cost_data["total_cost"]
        stop_count = cost_data["stop_count"]
        route_legs = list(cost_data.get("route_legs") or [])
        route_geometry = list(cost_data.get("route_geometry") or [])
        route = [stop.get("zip") for stop in ordered_stops if stop.get("zip")]
        destination_state = self._select_primary_state(all_lines)
        unique_states = sorted(
            {
                (line.get("state") or "").strip().upper()
                for line in all_lines
                if (line.get("state") or "").strip()
            }
        )
        rate_per_mile = self._average_rate_per_mile(estimated_cost, estimated_miles, stop_count)
        origin_coords = geo_utils.plant_coords_for_code(params["origin_plant"])
        direct_miles = self._max_direct_miles(origin_coords, ordered_stops)
        detour_miles = max(estimated_miles - direct_miles, 0.0)
        due_min, due_max = self._due_date_range(groups)
        effective_due_window_days = self._effective_time_window_days(
            groups=groups,
            base_time_window_days=params.get("time_window_days"),
            enforce_time_window=params.get("enforce_time_window", True),
            loads=[],
        )
        contains_no_mix = any(bool(group.get("no_mix")) for group in groups)

        base_load = {
            "_merge_id": 0,
            "origin_plant": params["origin_plant"],
            "destination_state": destination_state,
            "unique_states": unique_states,
            "unique_states_count": len(unique_states),
            "estimated_miles": estimated_miles,
            "rate_per_mile": rate_per_mile,
            "estimated_cost": estimated_cost,
            "route_provider": cost_data.get("route_provider"),
            "route_profile": cost_data.get("route_profile"),
            "route_total_miles": estimated_miles,
            "route_legs": route_legs,
            "route_geometry": route_geometry,
            "route_fallback": bool(cost_data.get("route_fallback")),
            "status": "PROPOSED",
            "trailer_type": trailer_type,
            "utilization_pct": utilization,
            "upper_two_across_applied_count": int(
                stack_config.get("upper_two_across_applied_count") or 0
            ),
            "exceeds_capacity": exceeds_capacity,
            "optimization_score": 0.0,
            "lines": all_lines,
            "route": route,
            "detour_miles": detour_miles,
            "over_capacity": False,
            "standalone_cost": estimated_cost,
            "consolidation_savings": 0.0,
            "fragility_score": 0.0,
            "return_to_origin": any(group.get("requires_return_to_origin") for group in groups),
            "return_miles": cost_data.get("return_miles") or 0.0,
            "return_cost": cost_data.get("return_cost") or 0.0,
            "total_length_ft": sum(group.get("total_length_ft") or 0 for group in groups),
            "total_linear_feet": float(stack_config.get("total_linear_feet") or 0.0),
            "capacity_feet": float(stack_config.get("capacity_feet") or 0.0),
            "lower_deck_used_length_ft": float(stack_config.get("lower_deck_used_length_ft") or 0.0),
            "upper_deck_effective_length_ft": float(stack_config.get("upper_deck_effective_length_ft") or 0.0),
            "due_date_min": due_min,
            "due_date_max": due_max,
            "due_flex_days": effective_due_window_days,
            "effective_due_window_days": effective_due_window_days,
            "contains_no_mix_customer": contains_no_mix,
            "centroid": self._centroid(groups),
            "stop_count": stop_count,
            "groups": list(groups),
            "stop_coords": [stop.get("coords") for stop in ordered_stops if stop.get("coords")],
            "store_codes": sorted(
                {
                    str(store_code or "").strip().upper()
                    for group in groups
                    for store_code in (group.get("store_codes") or [])
                    if str(store_code or "").strip()
                }
            ),
            "practical_fill_pct": round(
                ((float(stack_config.get("total_linear_feet") or 0.0) / float(stack_config.get("capacity_feet") or 0.0)) * 100.0)
                if float(stack_config.get("capacity_feet") or 0.0) > 0
                else 0.0,
                1,
            ),
        }
        base_load["effective_fill_pct"] = round(self._effective_fill_pct(base_load), 1)
        if stack_config.get("auto_trailer_upgrade"):
            base_load["auto_trailer_upgrade"] = True
            base_load["auto_trailer_reason"] = stack_config.get("auto_trailer_reason") or ""
        self._load_build_cache[cache_key] = base_load
        return base_load

    def _clone_cached_load(self, load):
        cloned = dict(load or {})
        for key in ("unique_states", "route_legs", "route_geometry", "lines", "route", "stop_coords", "groups", "store_codes"):
            if isinstance(cloned.get(key), list):
                cloned[key] = list(cloned[key])
        return cloned

    def _load_build_cache_key(self, groups, params):
        group_keys = tuple(
            sorted(
                str((group or {}).get("key") or "").strip()
                for group in (groups or [])
                if str((group or {}).get("key") or "").strip()
            )
        )
        categories = tuple(
            stack_calculator.normalize_upper_deck_exception_categories(
                params.get("upper_deck_exception_categories")
            )
        )
        return (
            group_keys,
            str(params.get("origin_plant") or "").strip().upper(),
            stack_calculator.normalize_trailer_type(params.get("trailer_type"), default="STEP_DECK"),
            self._coerce_non_negative_float(params.get("capacity_feet"), 0.0),
            bool(params.get("enforce_time_window", True)),
            self._coerce_optional_non_negative_int(params.get("time_window_days")) or 0,
            self._coerce_optional_non_negative_int(params.get("stack_overflow_max_height")) or 0,
            self._coerce_non_negative_float(params.get("max_back_overhang_ft"), 0.0),
            self._coerce_non_negative_float(params.get("upper_two_across_max_length_ft"), 0.0),
            self._coerce_non_negative_float(params.get("upper_deck_exception_max_length_ft"), 0.0),
            self._coerce_non_negative_float(params.get("upper_deck_exception_overhang_allowance_ft"), 0.0),
            categories,
            bool(self._coerce_bool(params.get("v2_allow_order_interleave"), DEFAULT_V2_ALLOW_ORDER_INTERLEAVE)),
            bool(self._coerce_bool(params.get("v2_allow_cross_order_stack_sharing"), True)),
            bool(params.get("v2_aggressive_upper_two_across_prepack", False)),
            (
                bool(params.get("equal_length_deck_length_order_enabled"))
                if params.get("equal_length_deck_length_order_enabled") is not None
                else None
            ),
        )

    def _cost_data_for_groups(self, groups, params):
        cache_key = (
            tuple(
                sorted(
                    str((group or {}).get("key") or "").strip()
                    for group in (groups or [])
                    if str((group or {}).get("key") or "").strip()
                )
            ),
            str(params.get("origin_plant") or "").strip().upper(),
            bool(any(group.get("requires_return_to_origin") for group in (groups or []))),
        )
        cached = self._cost_data_cache.get(cache_key)
        if cached is not None:
            return cached
        origin_plant = params["origin_plant"]
        origin_coords = geo_utils.plant_coords_for_code(origin_plant)
        requires_return_to_origin = any(group.get("requires_return_to_origin") for group in groups)
        cost_data = self.cost_calculator.calculate(
            origin_plant,
            self._build_stops(groups),
            origin_coords=origin_coords,
            return_to_origin=requires_return_to_origin,
        )
        self._cost_data_cache[cache_key] = cost_data
        return cost_data

    def _build_stops(self, groups):
        stop_map = {}
        for group in groups:
            zip_code = group.get("zip") or ""
            state = group.get("state") or ""
            key = self._stop_key(zip_code, state)
            if key in stop_map:
                continue
            coords = self.zip_coords.get(zip_code) if zip_code else None
            stop_map[key] = {"zip": zip_code, "state": state, "coords": coords}
        return list(stop_map.values())

    def _stop_key(self, zip_code, state):
        normalized_zip = geo_utils.normalize_zip(zip_code) if zip_code else ""
        normalized_state = (state or "").strip().upper()
        return f"{normalized_zip}|{normalized_state}"

    def _ordered_stops_for_groups(self, groups, params):
        if not groups:
            return []
        origin_plant = params.get("origin_plant")
        if not origin_plant:
            return self._build_stops(groups)
        cost_data = self._cost_data_for_groups(groups, params)
        return cost_data.get("ordered_stops") or []

    def _stop_sequence_map_for_groups(self, groups, ordered_stops):
        if not groups:
            return {}
        sequence_by_stop_key = {}
        for index, stop in enumerate(ordered_stops or [], start=1):
            key = self._stop_key(stop.get("zip"), stop.get("state"))
            if key and key not in sequence_by_stop_key:
                sequence_by_stop_key[key] = index
        fallback = len(sequence_by_stop_key) + 1 if sequence_by_stop_key else 1
        sequence_by_group = {}
        for group in groups:
            group_key = group.get("key")
            if not group_key:
                continue
            stop_key = self._stop_key(group.get("zip"), group.get("state"))
            sequence_by_group[group_key] = sequence_by_stop_key.get(stop_key, fallback)
        return sequence_by_group

    def _average_rate_per_mile(self, estimated_cost, estimated_miles, stop_count):
        if estimated_miles and estimated_miles > 0:
            base_cost = estimated_cost - (self.cost_calculator.stop_fee * stop_count)
            return max(base_cost / estimated_miles, 0.0)
        return DEFAULT_RATE_PER_MILE

    def _max_direct_miles(self, origin_coords, stops):
        if not origin_coords:
            return 0.0
        max_distance = 0.0
        for stop in stops:
            coords = stop.get("coords")
            if not coords:
                continue
            distance = self.cost_calculator.distance(origin_coords, coords)
            max_distance = max(max_distance, distance)
        return max_distance

    def _centroid(self, groups):
        coords = [group.get("coords") for group in groups if group.get("coords")]
        if not coords:
            return None
        lat = sum(coord[0] for coord in coords) / len(coords)
        lon = sum(coord[1] for coord in coords) / len(coords)
        return (lat, lon)

    def _due_date_range(self, groups):
        dates = [group.get("due_date") for group in groups if group.get("due_date")]
        if not dates:
            return None, None
        return min(dates), max(dates)

    def _v2_objective_weights(self, params):
        return {
            "low_util_threshold": float(
                params.get("v2_low_util_threshold", LOW_UTIL_THRESHOLD_PCT) or LOW_UTIL_THRESHOLD_PCT
            ),
            "lambda_low_util_count": float(
                params.get("v2_lambda_low_util_count", DEFAULT_V2_LAMBDA_LOW_UTIL_COUNT)
                or DEFAULT_V2_LAMBDA_LOW_UTIL_COUNT
            ),
            "lambda_low_util_depth": float(
                params.get("v2_lambda_low_util_depth", DEFAULT_V2_LAMBDA_LOW_UTIL_DEPTH)
                or DEFAULT_V2_LAMBDA_LOW_UTIL_DEPTH
            ),
            "lambda_upper_two_across": float(
                params.get("v2_lambda_upper_two_across", 24.0) or 24.0
            ),
            "full_load_target_pct": float(
                params.get("v2_full_load_target_pct", 90.0) or 90.0
            ),
            "elite_load_target_pct": float(
                params.get("v2_elite_load_target_pct", 95.0) or 95.0
            ),
            "lambda_full_load_count": float(
                params.get("v2_lambda_full_load_count", 220.0) or 220.0
            ),
            "lambda_elite_load_count": float(
                params.get("v2_lambda_elite_load_count", 320.0) or 320.0
            ),
            "lambda_full_load_depth": float(
                params.get("v2_lambda_full_load_depth", 18.0) or 18.0
            ),
            "lambda_fill_to_full": float(
                params.get("v2_lambda_fill_to_full", 4.0) or 4.0
            ),
            "fill_floor_target_pct": float(
                params.get("v2_fill_floor_target_pct", 65.0) or 65.0
            ),
            "lambda_fill_floor_count": float(
                params.get("v2_lambda_fill_floor_count", 140.0) or 140.0
            ),
            "lambda_fill_floor_depth": float(
                params.get("v2_lambda_fill_floor_depth", 10.0) or 10.0
            ),
            "lambda_fill_floor_progress": float(
                params.get("v2_lambda_fill_floor_progress", 8.0) or 8.0
            ),
            "lambda_weak_tail_absorb": float(
                params.get("v2_lambda_weak_tail_absorb", 160.0) or 160.0
            ),
            "lambda_state_purity": float(
                params.get("v2_lambda_state_purity", 60.0) or 60.0
            ),
            "lambda_same_state_merge": float(
                params.get("v2_lambda_same_state_merge", 40.0) or 40.0
            ),
            "lambda_cross_state_merge": float(
                params.get("v2_lambda_cross_state_merge", 120.0) or 120.0
            ),
            "lambda_stepdeck_two_across": float(
                params.get("v2_lambda_stepdeck_two_across", 0.0) or 0.0
            ),
            "lambda_stop_penalty": float(
                params.get("v2_lambda_stop_penalty", 18.0) or 18.0
            ),
            "stop_soft_limit": int(
                params.get("v2_stop_soft_limit", 4) or 4
            ),
            "stop_hard_limit": int(
                params.get("v2_stop_hard_limit", 6) or 6
            ),
            "lambda_geo_density": float(
                params.get("v2_lambda_geo_density", 18.0) or 18.0
            ),
            "lambda_geo_direction": float(
                params.get("v2_lambda_geo_direction", 26.0) or 26.0
            ),
            "lambda_geo_spread": float(
                params.get("v2_lambda_geo_spread", 20.0) or 20.0
            ),
            "two_across_bonus_step_deck_only": self._coerce_bool(
                params.get("v2_two_across_bonus_step_deck_only"),
                True,
            ),
        }

    def _low_util_penalty(self, load, threshold):
        effective_fill = self._effective_fill_pct(load)
        count_penalty = 1 if effective_fill < threshold else 0
        depth_penalty = max(threshold - effective_fill, 0.0)
        if effective_fill < 55:
            depth_penalty += (55 - effective_fill) * 0.5
        if effective_fill < 40:
            depth_penalty += (40 - effective_fill)
        return count_penalty, depth_penalty

    def _objective_bonus_for_merge(self, load_a, load_b, merged_load, objective_weights):
        def _single_state_code(load):
            state_text = str(load.get("destination_state") or "").strip().upper()
            state_tokens = [token.strip() for token in re.split(r"[,+/|;]+", state_text) if token.strip()]
            unique_count = int(load.get("unique_states_count") or (1 if state_text else 0))
            if unique_count == 1 and state_tokens:
                return state_tokens[0]
            if unique_count == 1 and state_text:
                return state_text
            return ""

        threshold = objective_weights.get("low_util_threshold", LOW_UTIL_THRESHOLD_PCT)
        lambda_count = objective_weights.get("lambda_low_util_count", 0.0)
        lambda_depth = objective_weights.get("lambda_low_util_depth", 0.0)
        lambda_upper_two_across = objective_weights.get("lambda_upper_two_across", 0.0)
        full_load_target = objective_weights.get("full_load_target_pct", 90.0)
        elite_load_target = objective_weights.get("elite_load_target_pct", 95.0)
        lambda_full_load_count = objective_weights.get("lambda_full_load_count", 0.0)
        lambda_elite_load_count = objective_weights.get("lambda_elite_load_count", 0.0)
        lambda_full_load_depth = objective_weights.get("lambda_full_load_depth", 0.0)
        lambda_fill_to_full = objective_weights.get("lambda_fill_to_full", 0.0)
        fill_floor_target = objective_weights.get("fill_floor_target_pct", 65.0)
        lambda_fill_floor_count = objective_weights.get("lambda_fill_floor_count", 0.0)
        lambda_fill_floor_depth = objective_weights.get("lambda_fill_floor_depth", 0.0)
        lambda_fill_floor_progress = objective_weights.get("lambda_fill_floor_progress", 0.0)
        lambda_weak_tail_absorb = objective_weights.get("lambda_weak_tail_absorb", 0.0)
        lambda_state_purity = objective_weights.get("lambda_state_purity", 0.0)
        lambda_same_state_merge = objective_weights.get("lambda_same_state_merge", 0.0)
        lambda_cross_state_merge = objective_weights.get("lambda_cross_state_merge", 0.0)
        lambda_stepdeck_two_across = objective_weights.get("lambda_stepdeck_two_across", 0.0)
        lambda_stop_penalty = objective_weights.get("lambda_stop_penalty", 0.0)
        lambda_geo_density = objective_weights.get("lambda_geo_density", 0.0)
        lambda_geo_direction = objective_weights.get("lambda_geo_direction", 0.0)
        lambda_geo_spread = objective_weights.get("lambda_geo_spread", 0.0)
        two_across_step_deck_only = bool(objective_weights.get("two_across_bonus_step_deck_only", True))
        before_count_a, before_depth_a = self._low_util_penalty(load_a, threshold)
        before_count_b, before_depth_b = self._low_util_penalty(load_b, threshold)
        after_count, after_depth = self._low_util_penalty(merged_load, threshold)
        count_bonus = (before_count_a + before_count_b - after_count) * lambda_count
        depth_bonus = (before_depth_a + before_depth_b - after_depth) * lambda_depth
        before_upper_two_across = int(load_a.get("upper_two_across_applied_count") or 0) + int(
            load_b.get("upper_two_across_applied_count") or 0
        )
        after_upper_two_across = int(merged_load.get("upper_two_across_applied_count") or 0)
        upper_two_across_bonus = (
            (after_upper_two_across - before_upper_two_across) * lambda_upper_two_across
        )
        if (
            self._load_uses_step_deck(merged_load)
            or not two_across_step_deck_only
        ):
            upper_two_across_bonus += (
                max(after_upper_two_across - before_upper_two_across, 0)
                * lambda_stepdeck_two_across
            )
        util_a = self._effective_fill_pct(load_a)
        util_b = self._effective_fill_pct(load_b)
        util_m = self._effective_fill_pct(merged_load)
        before_full_count = (1 if util_a >= full_load_target else 0) + (1 if util_b >= full_load_target else 0)
        after_full_count = 1 if util_m >= full_load_target else 0
        full_count_bonus = (before_full_count - after_full_count) * (-lambda_full_load_count)
        before_elite_count = (1 if util_a >= elite_load_target else 0) + (1 if util_b >= elite_load_target else 0)
        after_elite_count = 1 if util_m >= elite_load_target else 0
        elite_count_bonus = (before_elite_count - after_elite_count) * (-lambda_elite_load_count)

        before_full_depth = max(full_load_target - util_a, 0.0) + max(full_load_target - util_b, 0.0)
        after_full_depth = max(full_load_target - util_m, 0.0)
        full_depth_bonus = (before_full_depth - after_full_depth) * lambda_full_load_depth

        # Continuous "fill as close to 100% as possible" pressure.
        # Uses squared distance-to-full so benefits grow as loads become truly full.
        cap_a = min(max(util_a, 0.0), 100.0)
        cap_b = min(max(util_b, 0.0), 100.0)
        cap_m = min(max(util_m, 0.0), 100.0)
        before_fill_loss = ((100.0 - cap_a) ** 2) + ((100.0 - cap_b) ** 2)
        after_fill_loss = (100.0 - cap_m) ** 2
        fill_to_full_bonus = (before_fill_loss - after_fill_loss) * lambda_fill_to_full

        before_fill_floor_count = (1 if util_a >= fill_floor_target else 0) + (1 if util_b >= fill_floor_target else 0)
        after_fill_floor_count = 1 if util_m >= fill_floor_target else 0
        fill_floor_count_bonus = (before_fill_floor_count - after_fill_floor_count) * (-lambda_fill_floor_count)

        before_fill_floor_depth = max(fill_floor_target - util_a, 0.0) + max(fill_floor_target - util_b, 0.0)
        after_fill_floor_depth = max(fill_floor_target - util_m, 0.0)
        fill_floor_depth_bonus = (before_fill_floor_depth - after_fill_floor_depth) * lambda_fill_floor_depth

        before_best = max(util_a, util_b)
        progress_to_floor = max(min(util_m, fill_floor_target) - min(before_best, fill_floor_target), 0.0)
        fill_floor_progress_bonus = progress_to_floor * lambda_fill_floor_progress

        weak_tail_absorb_bonus = 0.0
        if util_m >= fill_floor_target:
            weak_tail_count = sum(1 for util in (util_a, util_b) if util < 40.0)
            weak_tail_absorb_bonus += weak_tail_count * lambda_weak_tail_absorb
            if before_best >= fill_floor_target and min(util_a, util_b) < 40.0:
                weak_tail_absorb_bonus += lambda_weak_tail_absorb

        states_a = int(load_a.get("unique_states_count") or (1 if load_a.get("destination_state") else 0))
        states_b = int(load_b.get("unique_states_count") or (1 if load_b.get("destination_state") else 0))
        states_m = int(merged_load.get("unique_states_count") or (1 if merged_load.get("destination_state") else 0))
        before_mix_penalty = max(states_a - 1, 0) + max(states_b - 1, 0)
        after_mix_penalty = max(states_m - 1, 0)
        state_purity_bonus = (before_mix_penalty - after_mix_penalty) * lambda_state_purity
        same_state_bonus = 0.0
        cross_state_penalty = 0.0
        state_a = _single_state_code(load_a)
        state_b = _single_state_code(load_b)
        if state_a and state_b:
            if state_a == state_b and states_m == 1:
                same_state_bonus = lambda_same_state_merge
            elif state_a != state_b and states_m > 1:
                cross_state_penalty = lambda_cross_state_merge

        before_stop_penalty = self._stop_penalty_units(load_a, objective_weights) + self._stop_penalty_units(load_b, objective_weights)
        after_stop_penalty = self._stop_penalty_units(merged_load, objective_weights)
        stop_penalty_bonus = (before_stop_penalty - after_stop_penalty) * lambda_stop_penalty

        before_geo_penalty = self._load_geo_penalty(load_a, objective_weights) + self._load_geo_penalty(load_b, objective_weights)
        after_geo_penalty = self._load_geo_penalty(merged_load, objective_weights)
        geo_penalty_bonus = (
            (before_geo_penalty - after_geo_penalty)
            * (lambda_geo_density + lambda_geo_direction + lambda_geo_spread)
        )
        if before_best >= full_load_target and util_m >= full_load_target:
            stop_penalty_bonus *= 1.35
            geo_penalty_bonus *= 1.85

        return (
            count_bonus
            + depth_bonus
            + upper_two_across_bonus
            + full_count_bonus
            + elite_count_bonus
            + full_depth_bonus
            + fill_to_full_bonus
            + fill_floor_count_bonus
            + fill_floor_depth_bonus
            + fill_floor_progress_bonus
            + weak_tail_absorb_bonus
            + state_purity_bonus
            + stop_penalty_bonus
            + geo_penalty_bonus
            + same_state_bonus
            - cross_state_penalty
        )

    def _detour_pct(self, load):
        estimated_miles = load.get("estimated_miles") or 0
        detour_miles = load.get("detour_miles") or 0
        direct_miles = estimated_miles - detour_miles
        if direct_miles <= 0:
            return 0.0
        return (detour_miles / direct_miles) * 100.0

    def _rescue_detour_pct(self, base_detour_pct):
        if base_detour_pct is None:
            return DEFAULT_V2_RESCUE_DETOUR_FLOOR
        return max((base_detour_pct or 0) * 2.0, DEFAULT_V2_RESCUE_DETOUR_FLOOR)

    def _expanded_radius(self, base_radius):
        if base_radius <= 0:
            return base_radius
        return max(base_radius * 2.0, base_radius + 150)

    def _runtime_tuned_params(self, params, group_count):
        tuned = dict(params or {})
        tuned["optimize_focus"] = UNIFIED_OPTIMIZER_PROFILE
        if group_count >= DEFAULT_V2_MEDIUM_TUNE_THRESHOLD:
            def _int_value(key, fallback):
                try:
                    return int(tuned.get(key, fallback) or fallback)
                except (TypeError, ValueError):
                    return int(fallback)

            tuned["v2_group_reassign_passes"] = min(
                _int_value("v2_group_reassign_passes", DEFAULT_V2_GROUP_REASSIGN_PASSES),
                2,
            )
            tuned["v2_group_reassign_candidate_limit"] = min(
                _int_value("v2_group_reassign_candidate_limit", DEFAULT_V2_GROUP_REASSIGN_CANDIDATE_LIMIT),
                10,
            )
            tuned["v2_fd_candidate_limit"] = min(
                _int_value("v2_fd_candidate_limit", DEFAULT_V2_FD_CANDIDATE_LIMIT),
                120,
            )
            tuned["v2_multi_start_include_aggressive_fill"] = False
            if group_count >= (DEFAULT_V2_MEDIUM_TUNE_THRESHOLD + 5):
                tuned["v2_multi_start_enabled"] = False
        if group_count < DEFAULT_V2_FAST_TUNE_THRESHOLD:
            return tuned

        def _int_value(key, fallback):
            try:
                return int(tuned.get(key, fallback) or fallback)
            except (TypeError, ValueError):
                return int(fallback)

        if group_count >= DEFAULT_V2_FAST_TUNE_HIGH_THRESHOLD:
            tuned["v2_pair_neighbors"] = min(_int_value("v2_pair_neighbors", DEFAULT_V2_PAIR_NEIGHBORS), 8)
            tuned["v2_pair_neighbors_low_util"] = min(
                _int_value("v2_pair_neighbors_low_util", DEFAULT_V2_PAIR_NEIGHBORS_LOW_UTIL),
                20,
            )
            tuned["v2_incremental_neighbors"] = min(
                _int_value("v2_incremental_neighbors", DEFAULT_V2_INCREMENTAL_NEIGHBORS),
                8,
            )
            tuned["v2_rescue_passes"] = min(_int_value("v2_rescue_passes", DEFAULT_V2_RESCUE_PASSES), 1)
            tuned["v2_grade_rescue_passes"] = min(
                _int_value("v2_grade_rescue_passes", DEFAULT_V2_GRADE_RESCUE_PASSES),
                1,
            )
            tuned["v2_grade_repair_limit"] = min(
                _int_value("v2_grade_repair_limit", DEFAULT_V2_GRADE_REPAIR_LIMIT),
                4,
            )
            tuned["v2_fd_rebalance_passes"] = min(
                _int_value("v2_fd_rebalance_passes", DEFAULT_V2_FD_REBALANCE_PASSES),
                0,
            )
            tuned["v2_prebatch_candidate_limit"] = min(
                _int_value("v2_prebatch_candidate_limit", DEFAULT_V2_PREBATCH_CANDIDATE_LIMIT),
                10,
            )
            return tuned

        tuned["v2_pair_neighbors"] = min(_int_value("v2_pair_neighbors", DEFAULT_V2_PAIR_NEIGHBORS), 12)
        tuned["v2_pair_neighbors_low_util"] = min(
            _int_value("v2_pair_neighbors_low_util", DEFAULT_V2_PAIR_NEIGHBORS_LOW_UTIL),
            28,
        )
        tuned["v2_incremental_neighbors"] = min(
            _int_value("v2_incremental_neighbors", DEFAULT_V2_INCREMENTAL_NEIGHBORS),
            12,
        )
        tuned["v2_rescue_passes"] = min(_int_value("v2_rescue_passes", DEFAULT_V2_RESCUE_PASSES), 2)
        tuned["v2_grade_rescue_passes"] = min(
            _int_value("v2_grade_rescue_passes", DEFAULT_V2_GRADE_RESCUE_PASSES),
            2,
        )
        tuned["v2_grade_repair_limit"] = min(
            _int_value("v2_grade_repair_limit", DEFAULT_V2_GRADE_REPAIR_LIMIT),
            6,
        )
        tuned["v2_fd_rebalance_passes"] = min(
            _int_value("v2_fd_rebalance_passes", DEFAULT_V2_FD_REBALANCE_PASSES),
            1,
        )
        tuned["v2_prebatch_candidate_limit"] = min(
            _int_value("v2_prebatch_candidate_limit", DEFAULT_V2_PREBATCH_CANDIDATE_LIMIT),
            14,
        )
        return tuned

    def _inject_market_shape_params(self, params, groups):
        tuned = dict(params or {})
        anchors = []
        bearings = []
        origin_coords = geo_utils.plant_coords_for_code(tuned.get("origin_plant"))
        for group in groups or []:
            anchor = self._group_anchor_coords(group)
            if not anchor:
                continue
            anchors.append(anchor)
            bearing = self._bearing_from_origin(origin_coords, anchor)
            if bearing is not None:
                bearings.append(bearing)
        if len(anchors) >= 2:
            nearest_neighbor_miles = []
            for index, anchor in enumerate(anchors):
                distances = []
                for compare_index, compare in enumerate(anchors):
                    if index == compare_index:
                        continue
                    distance = self._distance_between(anchor, compare)
                    if distance is not None:
                        distances.append(distance)
                if distances:
                    nearest_neighbor_miles.append(min(distances))
            if nearest_neighbor_miles:
                tuned["v2_market_avg_nn_miles"] = sum(nearest_neighbor_miles) / len(nearest_neighbor_miles)
        if origin_coords and anchors:
            origin_miles = [
                self._distance_between(origin_coords, anchor)
                for anchor in anchors
                if self._distance_between(origin_coords, anchor) is not None
            ]
            if origin_miles:
                tuned["v2_market_avg_origin_miles"] = sum(origin_miles) / len(origin_miles)
        if len(bearings) >= 2:
            mean_bearing = sum(bearings) / len(bearings)
            tuned["v2_market_directional_spread_deg"] = sum(
                self._bearing_delta(bearing, mean_bearing) for bearing in bearings
            ) / len(bearings)
        return tuned

    def _group_anchor_coords(self, group):
        coords = []
        for line in (group or {}).get("lines") or []:
            zip_code = geo_utils.normalize_zip(line.get("zip"))
            point = self.zip_coords.get(zip_code) if zip_code else None
            if point:
                coords.append(point)
        if not coords:
            return None
        return self._average_coords(coords)

    def _average_coords(self, coords):
        valid = [point for point in (coords or []) if point]
        if not valid:
            return None
        return (
            sum(point[0] for point in valid) / len(valid),
            sum(point[1] for point in valid) / len(valid),
        )

    def _distance_between(self, left, right):
        calculator = getattr(self, "cost_calculator", None)
        if calculator is None:
            return None
        try:
            return calculator.distance(left, right)
        except Exception:
            return None

    def _load_uses_step_deck(self, load):
        trailer = stack_calculator.normalize_trailer_type(
            (load or {}).get("trailer_type"),
            default="STEP_DECK",
        )
        return str(trailer or "").startswith("STEP_DECK")

    def _stop_penalty_units(self, load, params):
        stop_count = int((load or {}).get("stop_count") or 0)
        soft_limit = int((params or {}).get("stop_soft_limit", (params or {}).get("v2_stop_soft_limit", 4)) or 4)
        hard_limit = int((params or {}).get("stop_hard_limit", (params or {}).get("v2_stop_hard_limit", 6)) or 6)
        if stop_count <= soft_limit:
            return 0.0
        soft_excess = max(min(stop_count, hard_limit) - soft_limit, 0)
        hard_excess = max(stop_count - hard_limit, 0)
        return float(soft_excess) + (float(hard_excess) * 2.5)

    def _load_geo_penalty(self, load, params):
        stop_coords = [coords for coords in ((load or {}).get("stop_coords") or []) if coords]
        if len(stop_coords) <= 1:
            return 0.0

        avg_nn = float((params or {}).get("v2_market_avg_nn_miles", 0.0) or 0.0)
        avg_origin = float((params or {}).get("v2_market_avg_origin_miles", 0.0) or 0.0)
        avg_directional_spread = float((params or {}).get("v2_market_directional_spread_deg", 0.0) or 0.0)

        pair_distances = []
        for index, coord in enumerate(stop_coords):
            for compare in stop_coords[index + 1:]:
                distance = self._distance_between(coord, compare)
                if distance is not None:
                    pair_distances.append(distance)
        avg_pair_distance = (sum(pair_distances) / len(pair_distances)) if pair_distances else 0.0

        centroid = self._average_coords(stop_coords)
        spread_distances = []
        if centroid:
            for coord in stop_coords:
                distance = self._distance_between(coord, centroid)
                if distance is not None:
                    spread_distances.append(distance)
        avg_spread = (sum(spread_distances) / len(spread_distances)) if spread_distances else 0.0

        origin_coords = geo_utils.plant_coords_for_code((load or {}).get("origin_plant"))
        bearings = []
        if origin_coords:
            for coord in stop_coords:
                bearing = self._bearing_from_origin(origin_coords, coord)
                if bearing is not None:
                    bearings.append(bearing)
        directional_spread = 0.0
        if len(bearings) >= 2:
            mean_bearing = sum(bearings) / len(bearings)
            directional_spread = sum(
                self._bearing_delta(bearing, mean_bearing) for bearing in bearings
            ) / len(bearings)

        density_factor = (avg_pair_distance / max(avg_nn, 1.0)) if avg_nn > 0 else 0.0
        spread_factor = (avg_spread / max(avg_origin * 0.35, 1.0)) if avg_origin > 0 else avg_spread / 25.0
        direction_factor = (
            directional_spread / max(avg_directional_spread + 10.0, 15.0)
            if avg_directional_spread > 0
            else directional_spread / 20.0
        )
        detour_factor = max(self._detour_pct(load), 0.0) / 100.0

        density_weight = float((params or {}).get("lambda_geo_density", (params or {}).get("v2_lambda_geo_density", 0.0)) or 0.0)
        spread_weight = float((params or {}).get("lambda_geo_spread", (params or {}).get("v2_lambda_geo_spread", 0.0)) or 0.0)
        direction_weight = float((params or {}).get("lambda_geo_direction", (params or {}).get("v2_lambda_geo_direction", 0.0)) or 0.0)
        total_weight = max(density_weight + spread_weight + direction_weight, 1.0)

        return (
            density_factor * (density_weight / total_weight)
            + spread_factor * (spread_weight / total_weight)
            + direction_factor * (direction_weight / total_weight)
            + detour_factor
        )

    def _min_distance_between_loads(self, load_a, load_b):
        coords_a = [coord for coord in (load_a.get("stop_coords") or []) if coord]
        coords_b = [coord for coord in (load_b.get("stop_coords") or []) if coord]
        if coords_a and coords_b:
            min_distance = None
            for coord_a in coords_a:
                for coord_b in coords_b:
                    distance = self._distance_between(coord_a, coord_b)
                    if min_distance is None or distance < min_distance:
                        min_distance = distance
            return min_distance
        centroid_a = load_a.get("centroid")
        centroid_b = load_b.get("centroid")
        if centroid_a and centroid_b:
            return self._distance_between(centroid_a, centroid_b)
        return None

    def _is_orphan(self, load):
        utilization = load.get("utilization_pct") or 0
        return self._effective_fill_pct(load) < 60

    def _group_by_so_num(self, orders, order_summary_map=None):
        order_summary_map = order_summary_map or {}
        grouped = {}
        for line in orders:
            key = line.get("so_num") or f"line-{line['id']}"
            grouped.setdefault(key, []).append(line)

        groups = []
        for key, lines in grouped.items():
            summary = order_summary_map.get(key)
            groups.append(self._build_group(key, lines, summary))
        return groups

    def _build_group(self, key, lines, order_summary=None):
        total_length = sum(line.get("total_length_ft") or 0 for line in lines)
        due_dates = [self._parse_due_date(line.get("due_date")) for line in lines]
        due_dates = [value for value in due_dates if value]
        due_date = min(due_dates) if due_dates else None
        representative_zip = self._select_representative_zip(lines)
        destination_state = self._select_primary_state(lines)
        categories = [self._line_category(line) for line in lines]
        normalized_categories = [
            str(category or "").strip().upper()
            for category in categories
            if str(category or "").strip()
        ]
        order_category_scope = order_categories.order_category_scope_from_tokens(
            normalized_categories
        )
        cust_name = ""
        if order_summary:
            cust_name = (order_summary.get("cust_name") or "").strip()
        if not cust_name:
            cust_name = (next((line.get("cust_name") for line in lines if line.get("cust_name")), "") or "").strip()

        if order_summary:
            summary_length = order_summary.get("total_length_ft")
            if summary_length:
                total_length = summary_length
            summary_due = self._parse_due_date(order_summary.get("due_date"))
            if summary_due:
                due_date = summary_due
            summary_zip = geo_utils.normalize_zip(order_summary.get("zip"))
            if summary_zip:
                representative_zip = summary_zip
            summary_state = order_summary.get("state")
            if summary_state:
                destination_state = summary_state

        strategic_rule = self._strategic_rule_for_customer(cust_name)
        strategic_key = (strategic_rule or {}).get("key") if strategic_rule else ""
        default_due_date_flex_days = self._coerce_optional_non_negative_int(
            (strategic_rule or {}).get("default_due_date_flex_days")
        )
        no_mix = bool((strategic_rule or {}).get("no_mix"))
        explicit_wedge = bool((strategic_rule or {}).get("default_wedge_51"))
        max_unit_length_ft = self._max_unit_length_ft(lines)
        wedge_min_item_length_ft = self._coerce_optional_non_negative_float(
            (strategic_rule or {}).get("wedge_min_item_length_ft")
        )
        tractor_supply_wedge_threshold_ft = None
        if customer_rules.is_tractor_supply_customer(cust_name):
            tractor_supply_wedge_threshold_ft = self._tractor_supply_wedge_threshold_for_lines(lines)
        if (
            wedge_min_item_length_ft is None
            and tractor_supply_wedge_threshold_ft is not None
        ):
            wedge_min_item_length_ft = tractor_supply_wedge_threshold_ft
        wedge_by_length = (
            wedge_min_item_length_ft is not None
            and max_unit_length_ft >= wedge_min_item_length_ft
        )
        wedge_by_livestock = self._group_contains_livestock(normalized_categories)
        default_wedge_51 = explicit_wedge or wedge_by_length or wedge_by_livestock
        requires_return_to_origin = bool(
            (strategic_rule or {}).get("requires_return_to_origin")
        )
        ignore_for_optimization = bool(
            (strategic_rule or {}).get("ignore_for_optimization")
        )

        coords = self.zip_coords.get(representative_zip) if representative_zip else None
        store_codes = sorted(
            {
                str((line or {}).get("store") or "").strip().upper()
                for line in (lines or [])
                if str((line or {}).get("store") or "").strip()
            }
        )

        return {
            "key": key,
            "lines": lines,
            "total_length_ft": total_length,
            "due_date": due_date,
            "zip": representative_zip,
            "state": destination_state,
            "categories": normalized_categories,
            "coords": coords,
            "order_summary": order_summary or {},
            "cust_name": cust_name,
            "strategic_key": strategic_key,
            "default_due_date_flex_days": default_due_date_flex_days,
            "no_mix": no_mix,
            "default_wedge_51": default_wedge_51,
            "wedge_min_item_length_ft": wedge_min_item_length_ft,
            "max_unit_length_ft": max_unit_length_ft,
            "contains_livestock": wedge_by_livestock,
            "requires_return_to_origin": requires_return_to_origin,
            "ignore_for_optimization": ignore_for_optimization,
            "order_category_scope": order_category_scope,
            "store_codes": store_codes,
        }

    def _group_order_category_scope(self, group):
        if not isinstance(group, dict):
            return order_categories.ORDER_CATEGORY_SCOPE_OTHER
        stored_scope = order_categories.normalize_order_category_scope(
            group.get("order_category_scope"),
            default="",
        )
        if stored_scope and stored_scope != order_categories.ORDER_CATEGORY_SCOPE_ALL:
            return stored_scope
        resolved_scope = order_categories.order_category_scope_from_tokens(
            group.get("categories") or []
        )
        group["order_category_scope"] = resolved_scope
        return resolved_scope

    def _normalize_excluded_skus(self, values):
        if values is None:
            source = []
        elif isinstance(values, str):
            source = [part.strip() for part in re.split(r"[\s,;]+", values)]
        elif isinstance(values, (list, tuple, set)):
            source = []
            for value in values:
                source.extend([part.strip() for part in re.split(r"[\s,;]+", str(value or ""))])
        else:
            source = [str(values or "").strip()]
        cleaned = []
        seen = set()
        for raw in source:
            sku = str(raw or "").strip().upper()
            canonical = self._canonicalize_sku_token(sku)
            if not sku or not canonical or canonical in seen:
                continue
            seen.add(canonical)
            cleaned.append(sku)
        return cleaned

    def _canonicalize_sku_token(self, value):
        text = str(value or "").strip().upper()
        if not text:
            return ""
        return re.sub(r"[^A-Z0-9]+", "", text)

    def _group_has_excluded_sku(self, group, excluded_skus):
        if not group or not excluded_skus:
            return False
        excluded = set()
        for value in excluded_skus:
            canonical = self._canonicalize_sku_token(value)
            if canonical:
                excluded.add(canonical)
        if not excluded:
            return False
        for line in (group.get("lines") or []):
            for candidate in (line.get("sku"), line.get("item")):
                token = self._canonicalize_sku_token(candidate)
                if token and token in excluded:
                    return True
        return False

    def _group_matches_category_tokens(self, group, selected_tokens):
        if not selected_tokens:
            return True
        if not isinstance(group, dict):
            return False
        category_tokens = {
            str(token or "").strip().upper()
            for token in (group.get("categories") or [])
            if str(token or "").strip()
        }
        return bool(category_tokens.intersection(selected_tokens))

    def _coerce_optional_non_negative_int(self, value):
        if value is None:
            return None
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError, AttributeError):
            return None
        return max(parsed, 0)

    def _coerce_optional_non_negative_float(self, value):
        if value is None:
            return None
        text = str(value).strip()
        if text == "":
            return None
        try:
            parsed = float(text)
        except (TypeError, ValueError, AttributeError):
            return None
        return max(parsed, 0.0)

    def _coerce_non_negative_float(self, value, default=0.0):
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = float(default)
        return max(parsed, 0.0)

    def _coerce_bool(self, value, default=False):
        if value is None:
            return bool(default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}

    def _max_unit_length_ft(self, lines):
        max_length = 0.0
        for line in lines or []:
            candidates = [
                line.get("unit_length_ft"),
                line.get("length_with_tongue_ft"),
            ]
            sku = line.get("sku")
            if sku and self.sku_specs.get(sku):
                candidates.append(self.sku_specs[sku].get("length_with_tongue_ft"))
            for raw in candidates:
                value = self._coerce_optional_non_negative_float(raw)
                if value is None:
                    continue
                max_length = max(max_length, value)
        return max_length

    def _max_unit_length_ft_for_category(self, lines, category_tokens):
        normalized_tokens = {
            str(token or "").strip().upper()
            for token in (category_tokens or [])
            if str(token or "").strip()
        }
        if not normalized_tokens:
            return 0.0
        max_length = 0.0
        for line in lines or []:
            category = str(self._line_category(line) or "").strip().upper()
            if not category:
                continue
            if not any(category == token or category.startswith(f"{token}-") for token in normalized_tokens):
                continue
            max_length = max(max_length, self._max_unit_length_ft([line]))
        return max_length

    def _tractor_supply_wedge_threshold_for_lines(self, lines):
        thresholds = []
        max_cargo_length_ft = self._max_unit_length_ft_for_category(
            lines,
            DEFAULT_TRACTOR_SUPPLY_CARGO_CATEGORY_TOKENS,
        )
        if max_cargo_length_ft >= DEFAULT_TRACTOR_SUPPLY_CARGO_WEDGE_MIN_ITEM_LENGTH_FT:
            thresholds.append(DEFAULT_TRACTOR_SUPPLY_CARGO_WEDGE_MIN_ITEM_LENGTH_FT)

        max_uta_length_ft = self._max_unit_length_ft_for_category(
            lines,
            DEFAULT_TRACTOR_SUPPLY_UTA_CATEGORY_TOKENS,
        )
        if max_uta_length_ft >= DEFAULT_TRACTOR_SUPPLY_UTA_WEDGE_MIN_ITEM_LENGTH_FT:
            thresholds.append(DEFAULT_TRACTOR_SUPPLY_UTA_WEDGE_MIN_ITEM_LENGTH_FT)

        return min(thresholds) if thresholds else None

    def _group_contains_livestock(self, categories):
        rules = self.trailer_assignment_rules or {}
        if not self._coerce_bool(rules.get("livestock_wedge_enabled"), True):
            return False
        tokens = rules.get("livestock_category_tokens") or list(DEFAULT_LIVESTOCK_CATEGORY_TOKENS)
        normalized_tokens = {
            str(token or "").strip().upper()
            for token in tokens
            if str(token or "").strip()
        }
        if not normalized_tokens:
            normalized_tokens = set(DEFAULT_LIVESTOCK_CATEGORY_TOKENS)
        normalized_categories = {
            str(category or "").strip().upper()
            for category in (categories or [])
            if str(category or "").strip()
        }
        return bool(normalized_categories.intersection(normalized_tokens))

    def _group_contains_tractor_supply_wedge_category(self, categories):
        normalized_categories = [
            str(category or "").strip().upper()
            for category in (categories or [])
            if str(category or "").strip()
        ]
        for category in normalized_categories:
            for token in DEFAULT_TRACTOR_SUPPLY_WEDGE_CATEGORY_TOKENS:
                if category == token or category.startswith(f"{token}-"):
                    return True
        return False

    def _stack_config_for_groups(self, groups, params, trailer_type=None, stop_sequence_map=None):
        trailer_choice = self._preferred_trailer_for_groups(
            groups,
            trailer_type or params.get("trailer_type"),
        )
        capacity_feet = params.get("capacity_feet")
        allow_order_interleave = self._allow_order_interleave(params, groups)
        allow_cross_order_stack_sharing = self._allow_cross_order_stack_sharing(params, groups)
        active_stop_sequence_map = stop_sequence_map
        if allow_order_interleave and len(groups) > 1 and not active_stop_sequence_map:
            ordered_stops = self._ordered_stops_for_groups(groups, params)
            active_stop_sequence_map = self._stop_sequence_map_for_groups(groups, ordered_stops)
        return self._stack_config(
            groups,
            trailer_choice,
            capacity_feet,
            allow_order_interleave=allow_order_interleave,
            allow_cross_order_stack_sharing=allow_cross_order_stack_sharing,
            stop_sequence_map=active_stop_sequence_map,
            stack_overflow_max_height=params.get("stack_overflow_max_height"),
            max_back_overhang_ft=params.get("max_back_overhang_ft"),
            upper_two_across_max_length_ft=params.get("upper_two_across_max_length_ft"),
            upper_deck_exception_max_length_ft=params.get("upper_deck_exception_max_length_ft"),
            upper_deck_exception_overhang_allowance_ft=params.get("upper_deck_exception_overhang_allowance_ft"),
            upper_deck_exception_categories=params.get("upper_deck_exception_categories"),
            equal_length_deck_length_order_enabled=params.get("equal_length_deck_length_order_enabled"),
            aggressive_upper_two_across_prepack=params.get("v2_aggressive_upper_two_across_prepack"),
        )

    def _groups_require_wedge(self, groups):
        return any(bool((group or {}).get("default_wedge_51")) for group in (groups or []))

    def _preferred_trailer_for_groups(self, groups, fallback_trailer):
        if self._groups_require_wedge(groups):
            return "WEDGE"
        return stack_calculator.normalize_trailer_type(
            fallback_trailer,
            default="STEP_DECK",
        )

    def _allow_order_interleave(self, params, groups):
        if len(groups) <= 1:
            return False
        algorithm_version = (params.get("algorithm_version") or "v2").strip().lower()
        if algorithm_version != "v2":
            return False
        value = params.get("v2_allow_order_interleave", DEFAULT_V2_ALLOW_ORDER_INTERLEAVE)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "y"}
        return bool(value)

    def _allow_cross_order_stack_sharing(self, params, groups):
        if len(groups) <= 1:
            return False
        algorithm_version = (params.get("algorithm_version") or "v2").strip().lower()
        if algorithm_version != "v2":
            return False
        value = params.get("v2_allow_cross_order_stack_sharing", True)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "y"}
        return bool(value)

    def _stack_config(
        self,
        groups,
        trailer_type,
        capacity_feet,
        allow_order_interleave=False,
        allow_cross_order_stack_sharing=False,
        stop_sequence_map=None,
        stack_overflow_max_height=None,
        max_back_overhang_ft=None,
        upper_two_across_max_length_ft=None,
        upper_deck_exception_max_length_ft=None,
        upper_deck_exception_overhang_allowance_ft=None,
        upper_deck_exception_categories=None,
        equal_length_deck_length_order_enabled=None,
        aggressive_upper_two_across_prepack=None,
    ):
        group_keys = tuple(group.get("key") for group in groups if group.get("key"))
        trailer_key = stack_calculator.normalize_trailer_type(trailer_type, default="STEP_DECK")
        if trailer_key in {"HOTSHOT", "WEDGE", "STEP_DECK_48"}:
            capacity_for_calc = None
        else:
            capacity_for_calc = capacity_feet
        sequence_signature = ()
        if stop_sequence_map:
            sequence_signature = tuple(
                (key, int(stop_sequence_map.get(key) or 0))
                for key in group_keys
            )
        cache_key = (
            group_keys,
            trailer_key,
            capacity_for_calc,
            bool(allow_order_interleave),
            bool(allow_cross_order_stack_sharing),
            sequence_signature,
            stack_overflow_max_height,
            max_back_overhang_ft,
            upper_two_across_max_length_ft,
            upper_deck_exception_max_length_ft,
            upper_deck_exception_overhang_allowance_ft,
            tuple(
                stack_calculator.normalize_upper_deck_exception_categories(
                    upper_deck_exception_categories
                )
            ),
            bool(aggressive_upper_two_across_prepack),
            (
                bool(equal_length_deck_length_order_enabled)
                if equal_length_deck_length_order_enabled is not None
                else None
            ),
        )
        if cache_key in self._stack_cache:
            return self._stack_cache[cache_key]

        def build_line_items(trailer_choice):
            items = []
            for group in groups:
                group_key = group.get("key")
                stop_sequence = None
                if stop_sequence_map and group_key in stop_sequence_map:
                    stop_sequence = stop_sequence_map.get(group_key)
                for line in group.get("lines", []):
                    sku = line.get("sku")
                    max_stack = self._max_stack_for_trailer(sku, trailer_choice)
                    upper_max_stack = (
                        self._max_stack_for_trailer(sku, "FLATBED")
                        if str(trailer_choice or "").strip().upper().startswith("STEP_DECK")
                        else max_stack
                    )
                    items.append(
                        {
                            "item": line.get("item"),
                            "item_desc": line.get("item_desc"),
                            "sku": sku,
                            "qty": line.get("qty") or 0,
                            "unit_length_ft": line.get("unit_length_ft") or 0,
                            "max_stack_height": max_stack,
                            "upper_deck_max_stack_height": upper_max_stack,
                            "category": self._sku_category(line.get("sku")),
                            "order_id": line.get("so_num"),
                            "stop_sequence": stop_sequence,
                        }
                    )
            return items

        config = stack_calculator.calculate_stack_configuration(
            build_line_items(trailer_key),
            trailer_type=trailer_key,
            capacity_feet=capacity_for_calc,
            preserve_order_contiguity=not allow_order_interleave,
            prefer_order_affinity=not allow_cross_order_stack_sharing,
            stack_overflow_max_height=stack_overflow_max_height,
            max_back_overhang_ft=max_back_overhang_ft,
            upper_two_across_max_length_ft=upper_two_across_max_length_ft,
            upper_deck_exception_max_length_ft=upper_deck_exception_max_length_ft,
            upper_deck_exception_overhang_allowance_ft=upper_deck_exception_overhang_allowance_ft,
            upper_deck_exception_categories=upper_deck_exception_categories,
            equal_length_deck_length_order_enabled=equal_length_deck_length_order_enabled,
            aggressive_upper_two_across_prepack=aggressive_upper_two_across_prepack,
        )

        # Auto-upgrade step deck when deck split constraints make the requested trailer infeasible.
        if trailer_key.startswith("STEP_DECK") and config.get("exceeds_capacity"):
            candidate_trailers = []
            if trailer_key == "STEP_DECK_48":
                candidate_trailers.append(("STEP_DECK", "48-foot step deck constraints"))
            candidate_trailers.append(("FLATBED", "Step deck deck constraints"))

            for candidate_trailer, reason in candidate_trailers:
                if candidate_trailer in {"HOTSHOT", "WEDGE", "STEP_DECK_48"}:
                    candidate_capacity = None
                else:
                    candidate_capacity = capacity_feet
                candidate_cache_key = (
                    group_keys,
                    candidate_trailer,
                    candidate_capacity,
                    bool(allow_order_interleave),
                    bool(allow_cross_order_stack_sharing),
                    sequence_signature,
                    stack_overflow_max_height,
                    max_back_overhang_ft,
                    upper_two_across_max_length_ft,
                    upper_deck_exception_max_length_ft,
                    upper_deck_exception_overhang_allowance_ft,
                    tuple(
                        stack_calculator.normalize_upper_deck_exception_categories(
                            upper_deck_exception_categories
                        )
                    ),
                    bool(aggressive_upper_two_across_prepack),
                    (
                        bool(equal_length_deck_length_order_enabled)
                        if equal_length_deck_length_order_enabled is not None
                        else None
                    ),
                )
                candidate_config = self._stack_cache.get(candidate_cache_key)
                if candidate_config is None:
                    candidate_config = stack_calculator.calculate_stack_configuration(
                        build_line_items(candidate_trailer),
                        trailer_type=candidate_trailer,
                        capacity_feet=candidate_capacity,
                        preserve_order_contiguity=not allow_order_interleave,
                        prefer_order_affinity=not allow_cross_order_stack_sharing,
                        stack_overflow_max_height=stack_overflow_max_height,
                        max_back_overhang_ft=max_back_overhang_ft,
                        upper_two_across_max_length_ft=upper_two_across_max_length_ft,
                        upper_deck_exception_max_length_ft=upper_deck_exception_max_length_ft,
                        upper_deck_exception_overhang_allowance_ft=upper_deck_exception_overhang_allowance_ft,
                        upper_deck_exception_categories=upper_deck_exception_categories,
                        equal_length_deck_length_order_enabled=equal_length_deck_length_order_enabled,
                        aggressive_upper_two_across_prepack=aggressive_upper_two_across_prepack,
                    )
                    self._stack_cache[candidate_cache_key] = candidate_config
                if candidate_config and not candidate_config.get("exceeds_capacity"):
                    upgraded = dict(candidate_config)
                    upgraded["auto_trailer_upgrade"] = True
                    upgraded["auto_trailer_reason"] = reason
                    config = upgraded
                    break

        self._stack_cache[cache_key] = config
        return config

    def _build_order_summary_map(self, origin_plant):
        summary_rows = db.list_orders_for_optimization(origin_plant)
        summary_map = {}
        for order in summary_rows:
            so_num = order.get("so_num")
            if so_num:
                summary_map[so_num] = order
        return summary_map

    def _check_stacking_compatible(self, groups):
        categories = []
        for group in groups:
            categories.extend(group.get("categories") or [])
        categories = [cat for cat in categories if cat]
        if not categories:
            return True
        if "DUMP" in categories and len(set(categories)) > 1:
            return False
        return True

    def _sku_category(self, sku):
        spec = self.sku_specs.get(sku)
        return spec.get("category") if spec else ""

    def _line_category(self, line):
        sku_category = self._sku_category((line or {}).get("sku"))
        if str(sku_category or "").strip():
            return sku_category
        # Fallback when SKU-spec category is missing: use order-line category/bin.
        for key in ("category", "bin"):
            value = (line or {}).get(key)
            if str(value or "").strip():
                return value
        return ""

    def _max_stack_for_trailer(self, sku, trailer_type):
        spec = self.sku_specs.get(sku)
        if not spec:
            return 1
        trailer_key = stack_calculator.normalize_trailer_type(trailer_type, default="STEP_DECK")
        if trailer_key.startswith("STEP_DECK"):
            return spec.get("max_stack_step_deck") or spec.get("max_stack_flat_bed") or 1
        return spec.get("max_stack_flat_bed") or 1

    def _parse_due_date(self, value):
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None

    def _due_date_sort_key(self, group):
        due_date = group.get("due_date")
        if not due_date:
            return date.max
        return due_date

    def _select_primary_state(self, lines):
        states = [line.get("state") for line in lines if line.get("state")]
        if not states:
            return ""
        return Counter(states).most_common(1)[0][0]

    def _select_representative_zip(self, lines):
        zips = [geo_utils.normalize_zip(line.get("zip")) for line in lines if line.get("zip")]
        if not zips:
            return ""
        return Counter(zips).most_common(1)[0][0]

    def _next_merge_id(self):
        self._merge_id_counter += 1
        return self._merge_id_counter
