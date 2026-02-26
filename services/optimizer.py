from collections import Counter
from datetime import datetime, date
import heapq
import math

import db
from services import geo_utils, stack_calculator
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
DEFAULT_V2_ALLOW_ORDER_INTERLEAVE = True
DEFAULT_V2_PAIR_NEIGHBORS = 18
DEFAULT_V2_PAIR_NEIGHBORS_LOW_UTIL = 56
DEFAULT_V2_INCREMENTAL_NEIGHBORS = 20
DEFAULT_V2_ONWAY_BEARING_DEG = 35.0
DEFAULT_V2_ONWAY_RADIAL_GAP_MILES = 500.0
DEFAULT_V2_DIRECTIONAL_DETOUR_FLOOR = 95.0
DEFAULT_V2_FAST_TUNE_THRESHOLD = 400
DEFAULT_V2_FAST_TUNE_HIGH_THRESHOLD = 800
DEFAULT_V2_HOME_LENGTH_PRIORITY_ENABLED = True
DEFAULT_V2_HOME_LENGTH_PRIORITY_RADIUS_MILES = 250.0
DEFAULT_V2_HOME_LENGTH_PRIORITY_THRESHOLD_FT = 12.0
DEFAULT_V2_HOME_LENGTH_PRIORITY_WEIGHT = 1.0
DEFAULT_V2_HOME_LENGTH_PRIORITY_MAX_BONUS = 12.0


class Optimizer:
    def __init__(self):
        self.zip_coords = geo_utils.load_zip_coordinates()
        self.sku_specs = {spec["sku"]: spec for spec in db.list_sku_specs()}
        self.fuel_surcharge = resolve_fuel_surcharge()
        self.rate_lookup = build_rate_lookup(fuel_surcharge=self.fuel_surcharge)
        self.cost_calculator = CostCalculator(
            rate_lookup=self.rate_lookup,
            fuel_surcharge=self.fuel_surcharge,
            zip_coords=self.zip_coords,
        )
        self.strategic_customers = self._load_strategic_customers()
        self._strategic_customer_cache = {}
        self._stack_cache = {}
        self._merge_id_counter = 0

    def _load_strategic_customers(self):
        setting = db.get_planning_setting("strategic_customers") or {}
        raw_value = setting.get("value_text") or ""
        return customer_rules.parse_strategic_customers(raw_value)

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
        return list(active.values())

    def build_optimized_loads_v2(self, params):
        groups = self._build_order_groups(params)
        if not groups:
            return []

        runtime_params = self._runtime_tuned_params(params, len(groups))

        singleton_loads = [self._build_load([group], runtime_params) for group in groups]
        active = {load["_merge_id"]: load for load in singleton_loads}
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
        return list(active.values())

    def build_baseline_loads(self, params):
        baseline_groups = self._build_baseline_group_sets(params)
        if not baseline_groups:
            return []
        return [self._build_load(groups, params) for groups in baseline_groups]

    def _build_order_groups(self, params):
        min_due_date = self._resolve_min_due_date(params)
        orders = db.list_order_lines_for_optimization(
            params["origin_plant"],
            min_due_date=min_due_date,
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
            "first_due_no_batch": None,
        }
        if not origin_plant:
            return diagnostics

        diagnostics["open_orders_total"] = len(db.list_orders_for_optimization(origin_plant))
        min_due_date = self._resolve_min_due_date(params)
        order_lines = db.list_order_lines_for_optimization(origin_plant, min_due_date=min_due_date)
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
        target_meta = self._load_pair_meta(target, params)
        scored = []
        for recipient in recipients:
            if not self._loads_date_compatible(recipient, group_load, time_window_days):
                continue
            score = self._pair_priority_score(
                target_meta,
                self._load_pair_meta(recipient, params),
                params,
            )
            if score is None:
                continue
            if (recipient.get("destination_state") or "") == (target.get("destination_state") or ""):
                score -= 20.0
            if (recipient.get("utilization_pct") or 0) < 55:
                score -= 8.0
            scored.append((score, recipient.get("_merge_id"), recipient))

        if not scored:
            return []
        top = heapq.nsmallest(max(limit, 1), scored)
        return [entry[2] for entry in top]

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
        return {
            "state": (load.get("destination_state") or "").strip().upper(),
            "utilization": load.get("utilization_pct") or 0,
            "origin_miles": miles,
            "bearing": self._bearing_from_origin(origin_coords, anchor),
            "due_anchor": self._load_due_anchor(load),
            "effective_due_window_days": effective_due_window_days,
            "max_unit_length_ft": self._load_max_unit_length(load),
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
        if self._bearing_delta(meta_a["bearing"], meta_b["bearing"]) > bearing_limit:
            return False
        if abs(meta_a["origin_miles"] - meta_b["origin_miles"]) > radial_gap_limit:
            return False
        return min(meta_a["origin_miles"], meta_b["origin_miles"]) >= 40.0

    def _loads_compatible(self, load_a, load_b, radius, time_window_days, params):
        if load_a.get("origin_plant") != load_b.get("origin_plant"):
            return False
        if not self._loads_mix_compatible(load_a, load_b):
            return False
        if not self._loads_date_compatible(load_a, load_b, time_window_days):
            return False
        if not self._loads_geo_compatible(load_a, load_b, radius):
            if not self._allow_v2_geo_escape(load_a, load_b, params):
                return False
        return True

    def _allow_v2_geo_escape(self, load_a, load_b, params):
        if (params.get("algorithm_version") or "").lower() != "v2":
            return False
        if self._is_very_low_util_pair(load_a, load_b, params):
            return True
        if self._is_directionally_on_way_pair(load_a, load_b, params):
            return (
                self._is_low_util_for_target(load_a.get("utilization_pct") or 0, params)
                or self._is_low_util_for_target(load_b.get("utilization_pct") or 0, params)
            )
        return False

    def _is_very_low_util_pair(self, load_a, load_b, params):
        threshold = float(
            params.get("v2_geo_escape_threshold", DEFAULT_V2_GEO_ESCAPE_THRESHOLD)
            or DEFAULT_V2_GEO_ESCAPE_THRESHOLD
        )
        util_a = load_a.get("utilization_pct") or 0
        util_b = load_b.get("utilization_pct") or 0
        return util_a <= threshold and util_b <= threshold

    def _is_low_util_for_target(self, utilization_pct, params):
        target = float(
            params.get("v2_low_util_threshold", LOW_UTIL_THRESHOLD_PCT)
            or LOW_UTIL_THRESHOLD_PCT
        )
        return (utilization_pct or 0) < target

    def _pair_has_low_util_target(self, load_a, load_b, params):
        return (
            self._is_low_util_for_target(load_a.get("utilization_pct") or 0, params)
            or self._is_low_util_for_target(load_b.get("utilization_pct") or 0, params)
        )

    def _count_low_util_target(self, loads, params):
        return sum(
            1
            for load in loads
            if self._is_low_util_for_target(load.get("utilization_pct") or 0, params)
        )

    def _is_directionally_on_way_pair(self, load_a, load_b, params):
        return self._is_directional_from_meta(
            self._load_pair_meta(load_a, params),
            self._load_pair_meta(load_b, params),
            params,
        )

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

        util_a = load_a.get("utilization_pct") or 0
        util_b = load_b.get("utilization_pct") or 0
        merged_util = merged_load.get("utilization_pct") or 0
        if merged_util + 1e-6 < max(util_a, util_b):
            return False

        if self._is_very_low_util_pair(load_a, load_b, params):
            detour_escape_cap = float(
                params.get("v2_detour_escape_cap", max(max_detour_pct * 3.0, DEFAULT_V2_DETOUR_ESCAPE_FLOOR))
                or max(max_detour_pct * 3.0, DEFAULT_V2_DETOUR_ESCAPE_FLOOR)
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
        all_lines = [line for group in groups for line in group.get("lines", [])]
        preferred_trailer = self._preferred_trailer_for_groups(
            groups,
            params.get("trailer_type", "STEP_DECK"),
        )

        stops = self._build_stops(groups)
        origin_plant = params["origin_plant"]
        origin_coords = geo_utils.plant_coords_for_code(origin_plant)
        requires_return_to_origin = any(group.get("requires_return_to_origin") for group in groups)
        cost_data = self.cost_calculator.calculate(
            origin_plant,
            stops,
            origin_coords=origin_coords,
            return_to_origin=requires_return_to_origin,
        )
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
        order_numbers = {line.get("so_num") for line in all_lines if line.get("so_num")}
        single_order = len(order_numbers) <= 1
        over_capacity = exceeds_capacity and single_order

        estimated_miles = cost_data["total_miles"]
        estimated_cost = cost_data["total_cost"]
        stop_count = cost_data["stop_count"]
        route_legs = cost_data.get("route_legs") or []
        route_geometry = cost_data.get("route_geometry") or []

        route = [stop.get("zip") for stop in ordered_stops if stop.get("zip")]
        destination_state = self._select_primary_state(all_lines)
        rate_per_mile = self._average_rate_per_mile(estimated_cost, estimated_miles, stop_count)

        direct_miles = self._max_direct_miles(origin_coords, ordered_stops)
        detour_miles = max(estimated_miles - direct_miles, 0.0)

        standalone_cost = estimated_cost if standalone_cost is None else standalone_cost
        consolidation_savings = standalone_cost - estimated_cost
        fragility_score = (consolidation_savings / standalone_cost) if standalone_cost else 0.0

        due_min, due_max = self._due_date_range(groups)
        effective_due_window_days = self._effective_time_window_days(
            groups=groups,
            base_time_window_days=params.get("time_window_days"),
            enforce_time_window=params.get("enforce_time_window", True),
            loads=[],
        )
        contains_no_mix = any(bool(group.get("no_mix")) for group in groups)

        load = {
            "_merge_id": self._next_merge_id(),
            "origin_plant": origin_plant,
            "destination_state": destination_state,
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
            "exceeds_capacity": exceeds_capacity,
            "optimization_score": consolidation_savings or 0.0,
            "lines": all_lines,
            "route": route,
            "detour_miles": detour_miles,
            "over_capacity": over_capacity,
            "standalone_cost": standalone_cost,
            "consolidation_savings": consolidation_savings,
            "fragility_score": fragility_score,
            "return_to_origin": requires_return_to_origin,
            "return_miles": cost_data.get("return_miles") or 0.0,
            "return_cost": cost_data.get("return_cost") or 0.0,
            "total_length_ft": sum(group.get("total_length_ft") or 0 for group in groups),
            "due_date_min": due_min,
            "due_date_max": due_max,
            "due_flex_days": effective_due_window_days,
            "effective_due_window_days": effective_due_window_days,
            "contains_no_mix_customer": contains_no_mix,
            "centroid": self._centroid(groups),
            "stop_count": stop_count,
            "groups": groups,
            "stop_coords": [stop.get("coords") for stop in ordered_stops if stop.get("coords")],
        }
        return load

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
        origin_coords = geo_utils.plant_coords_for_code(origin_plant)
        requires_return_to_origin = any(group.get("requires_return_to_origin") for group in groups)
        cost_data = self.cost_calculator.calculate(
            origin_plant,
            self._build_stops(groups),
            origin_coords=origin_coords,
            return_to_origin=requires_return_to_origin,
        )
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
        }

    def _low_util_penalty(self, load, threshold):
        utilization = load.get("utilization_pct") or 0
        count_penalty = 1 if utilization < threshold else 0
        depth_penalty = max(threshold - utilization, 0.0)
        if utilization < 55:
            depth_penalty += (55 - utilization) * 0.5
        if utilization < 40:
            depth_penalty += (40 - utilization)
        return count_penalty, depth_penalty

    def _objective_bonus_for_merge(self, load_a, load_b, merged_load, objective_weights):
        threshold = objective_weights.get("low_util_threshold", LOW_UTIL_THRESHOLD_PCT)
        lambda_count = objective_weights.get("lambda_low_util_count", 0.0)
        lambda_depth = objective_weights.get("lambda_low_util_depth", 0.0)
        before_count_a, before_depth_a = self._low_util_penalty(load_a, threshold)
        before_count_b, before_depth_b = self._low_util_penalty(load_b, threshold)
        after_count, after_depth = self._low_util_penalty(merged_load, threshold)
        count_bonus = (before_count_a + before_count_b - after_count) * lambda_count
        depth_bonus = (before_depth_a + before_depth_b - after_depth) * lambda_depth
        return count_bonus + depth_bonus

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
        return tuned

    def _min_distance_between_loads(self, load_a, load_b):
        coords_a = [coord for coord in (load_a.get("stop_coords") or []) if coord]
        coords_b = [coord for coord in (load_b.get("stop_coords") or []) if coord]
        if coords_a and coords_b:
            min_distance = None
            for coord_a in coords_a:
                for coord_b in coords_b:
                    distance = self.cost_calculator.distance(coord_a, coord_b)
                    if min_distance is None or distance < min_distance:
                        min_distance = distance
            return min_distance
        centroid_a = load_a.get("centroid")
        centroid_b = load_b.get("centroid")
        if centroid_a and centroid_b:
            return self.cost_calculator.distance(centroid_a, centroid_b)
        return None

    def _is_orphan(self, load):
        utilization = load.get("utilization_pct") or 0
        return utilization < 60

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
        categories = [self._sku_category(line.get("sku")) for line in lines]
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
        default_wedge_51 = bool((strategic_rule or {}).get("default_wedge_51"))
        requires_return_to_origin = bool(
            (strategic_rule or {}).get("requires_return_to_origin")
        )
        ignore_for_optimization = bool(
            (strategic_rule or {}).get("ignore_for_optimization")
        )

        coords = self.zip_coords.get(representative_zip) if representative_zip else None

        return {
            "key": key,
            "lines": lines,
            "total_length_ft": total_length,
            "due_date": due_date,
            "zip": representative_zip,
            "state": destination_state,
            "categories": categories,
            "coords": coords,
            "order_summary": order_summary or {},
            "cust_name": cust_name,
            "strategic_key": strategic_key,
            "default_due_date_flex_days": default_due_date_flex_days,
            "no_mix": no_mix,
            "default_wedge_51": default_wedge_51,
            "requires_return_to_origin": requires_return_to_origin,
            "ignore_for_optimization": ignore_for_optimization,
        }

    def _coerce_optional_non_negative_int(self, value):
        if value is None:
            return None
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError, AttributeError):
            return None
        return max(parsed, 0)

    def _stack_config_for_groups(self, groups, params, trailer_type=None, stop_sequence_map=None):
        trailer_choice = self._preferred_trailer_for_groups(
            groups,
            trailer_type or params.get("trailer_type"),
        )
        capacity_feet = params.get("capacity_feet")
        allow_order_interleave = self._allow_order_interleave(params, groups)
        active_stop_sequence_map = stop_sequence_map
        if allow_order_interleave and len(groups) > 1 and not active_stop_sequence_map:
            ordered_stops = self._ordered_stops_for_groups(groups, params)
            active_stop_sequence_map = self._stop_sequence_map_for_groups(groups, ordered_stops)
        return self._stack_config(
            groups,
            trailer_choice,
            capacity_feet,
            allow_order_interleave=allow_order_interleave,
            stop_sequence_map=active_stop_sequence_map,
            stack_overflow_max_height=params.get("stack_overflow_max_height"),
            max_back_overhang_ft=params.get("max_back_overhang_ft"),
            upper_two_across_max_length_ft=params.get("upper_two_across_max_length_ft"),
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

    def _stack_config(
        self,
        groups,
        trailer_type,
        capacity_feet,
        allow_order_interleave=False,
        stop_sequence_map=None,
        stack_overflow_max_height=None,
        max_back_overhang_ft=None,
        upper_two_across_max_length_ft=None,
    ):
        group_keys = tuple(group.get("key") for group in groups if group.get("key"))
        trailer_key = stack_calculator.normalize_trailer_type(trailer_type, default="STEP_DECK")
        sequence_signature = ()
        if stop_sequence_map:
            sequence_signature = tuple(
                (key, int(stop_sequence_map.get(key) or 0))
                for key in group_keys
            )
        cache_key = (
            group_keys,
            trailer_key,
            capacity_feet,
            bool(allow_order_interleave),
            sequence_signature,
            stack_overflow_max_height,
            max_back_overhang_ft,
            upper_two_across_max_length_ft,
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
            capacity_feet=capacity_feet,
            preserve_order_contiguity=not allow_order_interleave,
            stack_overflow_max_height=stack_overflow_max_height,
            max_back_overhang_ft=max_back_overhang_ft,
            upper_two_across_max_length_ft=upper_two_across_max_length_ft,
        )

        # Auto-upgrade step deck -> flatbed when the load doesn't fit the 43' / 10' split.
        if trailer_key.startswith("STEP_DECK") and config.get("exceeds_capacity"):
            flatbed_key = "FLATBED"
            flatbed_cache_key = (
                group_keys,
                flatbed_key,
                capacity_feet,
                bool(allow_order_interleave),
                sequence_signature,
                stack_overflow_max_height,
                max_back_overhang_ft,
                upper_two_across_max_length_ft,
            )
            flatbed_config = self._stack_cache.get(flatbed_cache_key)
            if flatbed_config is None:
                flatbed_config = stack_calculator.calculate_stack_configuration(
                    build_line_items(flatbed_key),
                    trailer_type=flatbed_key,
                    capacity_feet=capacity_feet,
                    preserve_order_contiguity=not allow_order_interleave,
                    stack_overflow_max_height=stack_overflow_max_height,
                    max_back_overhang_ft=max_back_overhang_ft,
                    upper_two_across_max_length_ft=upper_two_across_max_length_ft,
                )
                self._stack_cache[flatbed_cache_key] = flatbed_config
            if flatbed_config and not flatbed_config.get("exceeds_capacity"):
                upgraded = dict(flatbed_config)
                upgraded["auto_trailer_upgrade"] = True
                upgraded["auto_trailer_reason"] = "Step deck deck constraints"
                config = upgraded

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
