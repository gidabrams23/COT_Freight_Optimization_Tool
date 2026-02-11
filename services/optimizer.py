from collections import Counter
from datetime import datetime, date
import heapq

import db
from services import geo_utils, stack_calculator
from services import customer_rules
from services.cost_calculator import CostCalculator, build_rate_lookup, DEFAULT_RATE_PER_MILE, STOP_FEE


class Optimizer:
    def __init__(self):
        self.zip_coords = geo_utils.load_zip_coordinates()
        self.sku_specs = {spec["sku"]: spec for spec in db.list_sku_specs()}
        self.rate_lookup = build_rate_lookup()
        self.cost_calculator = CostCalculator(
            rate_lookup=self.rate_lookup,
            zip_coords=self.zip_coords,
        )
        self._stack_cache = {}
        self._merge_id_counter = 0

    def build_optimized_loads(self, params):
        baseline_groups = self._build_baseline_group_sets(params)
        if not baseline_groups:
            return []

        loads = [self._build_load(groups, params) for groups in baseline_groups]
        active = {load["_merge_id"]: load for load in loads}
        time_window_days = params.get("time_window_days") if params.get("enforce_time_window", True) else 0

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

    def build_baseline_loads(self, params):
        baseline_groups = self._build_baseline_group_sets(params)
        if not baseline_groups:
            return []
        return [self._build_load(groups, params) for groups in baseline_groups]

    def _build_baseline_group_sets(self, params):
        orders = db.list_order_lines_for_optimization(params["origin_plant"])
        if not orders:
            return []

        order_summary_map = self._build_order_summary_map(params["origin_plant"])
        grouped = self._group_by_so_num(orders, order_summary_map)
        max_due_date = params.get("batch_max_due_date")
        if max_due_date:
            grouped = [
                group
                for group in grouped
                if not group.get("due_date") or group.get("due_date") <= max_due_date
            ]

        state_filters = {value.strip().upper() for value in (params.get("state_filters") or []) if value}
        if state_filters:
            grouped = [
                group
                for group in grouped
                if (group.get("state") or "").strip().upper() in state_filters
            ]

        customer_filters = {
            value.strip().casefold()
            for value in (params.get("customer_filters") or [])
            if value
        }
        if customer_filters:
            grouped = [
                group
                for group in grouped
                if (group.get("cust_name") or "").strip().casefold() in customer_filters
            ]

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
        capacity = params.get("capacity_feet") or 0
        total_length = sum(g.get("total_length_ft") or 0 for g in current_groups)
        total_length += candidate_group.get("total_length_ft") or 0
        if capacity and total_length > capacity:
            return False

        time_window_days = params.get("time_window_days") or 0
        if not params.get("enforce_time_window", True):
            time_window_days = 0
        if time_window_days and time_window_days > 0:
            dates = [
                group.get("due_date")
                for group in (current_groups + [candidate_group])
                if group.get("due_date")
            ]
            if dates and (max(dates) - min(dates)).days > time_window_days:
                return False

        combined = current_groups + [candidate_group]
        if not self._check_stacking_compatible(combined):
            return False

        stack_config = self._stack_config(
            combined,
            params.get("trailer_type"),
            params.get("capacity_feet"),
        )
        exceeds_capacity = stack_config.get("exceeds_capacity", False)
        utilization_pct = stack_config.get("utilization_pct", 0) or 0
        if (exceeds_capacity or utilization_pct > 100) and len(combined) > 1:
            return False
        return True

    def _build_merge_candidates(
        self,
        active_loads,
        params,
        min_savings,
        radius=None,
        time_window_days=None,
        require_orphan=False,
    ):
        load_list = list(active_loads.values())
        heap = []
        for idx, load_a in enumerate(load_list):
            for load_b in load_list[idx + 1:]:
                if require_orphan and not (self._is_orphan(load_a) or self._is_orphan(load_b)):
                    continue
                if not self._loads_compatible(load_a, load_b, radius, time_window_days, params):
                    continue
                candidate = self._evaluate_merge_candidate(load_a, load_b, params)
                if not candidate:
                    continue
                savings = candidate["savings"]
                if savings < min_savings:
                    continue
                heapq.heappush(
                    heap,
                    (-savings, load_a["_merge_id"], load_b["_merge_id"], candidate),
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
    ):
        while heap:
            neg_savings, load_a_id, load_b_id, candidate = heapq.heappop(heap)
            savings = -neg_savings
            if savings < min_savings:
                break
            if load_a_id not in active_loads or load_b_id not in active_loads:
                continue

            load_a = active_loads[load_a_id]
            load_b = active_loads[load_b_id]
            merged_load = candidate.get("merged_load")
            if not merged_load:
                merged_load = self._merge_loads(load_a, load_b, params)
                if not merged_load:
                    continue

            del active_loads[load_a_id]
            del active_loads[load_b_id]
            active_loads[merged_load["_merge_id"]] = merged_load

            for other in list(active_loads.values()):
                if other["_merge_id"] == merged_load["_merge_id"]:
                    continue
                if require_orphan and not (self._is_orphan(merged_load) or self._is_orphan(other)):
                    continue
                if not self._loads_compatible(
                    merged_load,
                    other,
                    radius,
                    time_window_days,
                    params,
                ):
                    continue
                new_candidate = self._evaluate_merge_candidate(merged_load, other, params)
                if not new_candidate:
                    continue
                new_savings = new_candidate["savings"]
                if new_savings < min_savings:
                    continue
                heapq.heappush(
                    heap,
                    (-new_savings, merged_load["_merge_id"], other["_merge_id"], new_candidate),
                )

        return active_loads

    def _rescue_orphans(self, active_loads, params):
        if not any(self._is_orphan(load) for load in active_loads.values()):
            return active_loads

        rescue_radius = self._expanded_radius(params.get("geo_radius") or 0)
        rescue_window = params.get("time_window_days") if params.get("enforce_time_window", True) else 0
        candidates = self._build_merge_candidates(
            active_loads,
            params,
            min_savings=-50.0,
            radius=rescue_radius,
            time_window_days=rescue_window,
            require_orphan=True,
        )

        return self._merge_candidates(
            active_loads,
            candidates,
            params,
            min_savings=-50.0,
            radius=rescue_radius,
            time_window_days=rescue_window,
            require_orphan=True,
        )

    def _evaluate_merge_candidate(self, load_a, load_b, params):
        merged_load = self._merge_loads(load_a, load_b, params)
        if not merged_load:
            return None
        savings = (load_a.get("estimated_cost") or 0) + (load_b.get("estimated_cost") or 0)
        savings -= merged_load.get("estimated_cost") or 0
        return {"merged_load": merged_load, "savings": savings}

    def _merge_loads(self, load_a, load_b, params):
        combined_groups = list(load_a.get("groups", [])) + list(load_b.get("groups", []))
        if not combined_groups:
            return None

        capacity = params.get("capacity_feet") or 0
        total_length = sum(group.get("total_length_ft") or 0 for group in combined_groups)
        if capacity and total_length > capacity and len(combined_groups) > 1:
            return None

        if not self._check_stacking_compatible(combined_groups):
            return None

        stack_config = self._stack_config(
            combined_groups,
            params.get("trailer_type"),
            params.get("capacity_feet"),
        )
        exceeds_capacity = stack_config.get("exceeds_capacity", False)
        utilization_pct = stack_config.get("utilization_pct", 0) or 0
        if (exceeds_capacity or utilization_pct > 100) and len(combined_groups) > 1:
            return None

        standalone_cost = (load_a.get("standalone_cost") or 0) + (load_b.get("standalone_cost") or 0)
        return self._build_load(combined_groups, params, standalone_cost=standalone_cost)

    def _loads_compatible(self, load_a, load_b, radius, time_window_days, params):
        if load_a.get("origin_plant") != load_b.get("origin_plant"):
            return False
        if not self._loads_date_compatible(load_a, load_b, time_window_days):
            return False
        if not self._loads_geo_compatible(load_a, load_b, radius):
            return False
        capacity = params.get("capacity_feet") or 0
        if capacity:
            total_length = (load_a.get("total_length_ft") or 0) + (load_b.get("total_length_ft") or 0)
            if total_length > capacity and (len(load_a.get("groups", [])) + len(load_b.get("groups", [])) > 1):
                return False
        return True

    def _loads_date_compatible(self, load_a, load_b, time_window_days):
        if not time_window_days or time_window_days <= 0:
            return True
        start_a = load_a.get("due_date_min")
        end_a = load_a.get("due_date_max")
        start_b = load_b.get("due_date_min")
        end_b = load_b.get("due_date_max")
        if not (start_a and end_a and start_b and end_b):
            return True
        combined_start = min(start_a, start_b)
        combined_end = max(end_a, end_b)
        return (combined_end - combined_start).days <= time_window_days

    def _loads_geo_compatible(self, load_a, load_b, radius):
        if not radius:
            return True
        min_distance = self._min_distance_between_loads(load_a, load_b)
        if min_distance is None:
            return True
        return min_distance <= radius

    def _build_load(self, groups, params, standalone_cost=None):
        all_lines = [line for group in groups for line in group.get("lines", [])]
        preferred_trailer = params.get("trailer_type", "STEP_DECK")
        stack_config = self._stack_config(
            groups,
            preferred_trailer,
            params.get("capacity_feet"),
        )
        trailer_type = stack_config.get("trailer_type") or preferred_trailer
        utilization = stack_config.get("utilization_pct", 0) or 0
        exceeds_capacity = stack_config.get("exceeds_capacity", False)

        order_numbers = {line.get("so_num") for line in all_lines if line.get("so_num")}
        single_order = len(order_numbers) <= 1
        over_capacity = (exceeds_capacity or utilization > 100) and single_order

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

        estimated_miles = cost_data["total_miles"]
        estimated_cost = cost_data["total_cost"]
        stop_count = cost_data["stop_count"]

        route = [stop.get("zip") for stop in ordered_stops if stop.get("zip")]
        destination_state = self._select_primary_state(all_lines)
        rate_per_mile = self._average_rate_per_mile(estimated_cost, estimated_miles, stop_count)

        direct_miles = self._max_direct_miles(origin_coords, ordered_stops)
        detour_miles = max(estimated_miles - direct_miles, 0.0)

        standalone_cost = estimated_cost if standalone_cost is None else standalone_cost
        consolidation_savings = standalone_cost - estimated_cost
        fragility_score = (consolidation_savings / standalone_cost) if standalone_cost else 0.0

        due_min, due_max = self._due_date_range(groups)

        load = {
            "_merge_id": self._next_merge_id(),
            "origin_plant": origin_plant,
            "destination_state": destination_state,
            "estimated_miles": estimated_miles,
            "rate_per_mile": rate_per_mile,
            "estimated_cost": estimated_cost,
            "status": "PROPOSED",
            "trailer_type": trailer_type,
            "utilization_pct": utilization,
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
            key = f"{zip_code}|{state}"
            if key in stop_map:
                continue
            coords = self.zip_coords.get(zip_code) if zip_code else None
            stop_map[key] = {"zip": zip_code, "state": state, "coords": coords}
        return list(stop_map.values())

    def _average_rate_per_mile(self, estimated_cost, estimated_miles, stop_count):
        if estimated_miles and estimated_miles > 0:
            base_cost = estimated_cost - (STOP_FEE * stop_count)
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

    def _expanded_radius(self, base_radius):
        if base_radius <= 0:
            return base_radius
        return max(base_radius * 2.0, base_radius + 150)

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
        stop_count = load.get("stop_count") or len(load.get("groups") or [])
        if stop_count <= 1:
            return utilization < 75
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
            "requires_return_to_origin": customer_rules.is_lowes_customer(cust_name),
        }

    def _stack_config(self, groups, trailer_type, capacity_feet):
        group_keys = tuple(group.get("key") for group in groups if group.get("key"))
        trailer_key = (trailer_type or "STEP_DECK").strip().upper()
        cache_key = (group_keys, trailer_key, capacity_feet)
        if cache_key in self._stack_cache:
            return self._stack_cache[cache_key]

        def build_line_items(trailer_choice):
            items = []
            for group in groups:
                for line in group.get("lines", []):
                    sku = line.get("sku")
                    max_stack = self._max_stack_for_trailer(sku, trailer_choice)
                    items.append(
                        {
                            "item": line.get("item"),
                            "sku": sku,
                            "qty": line.get("qty") or 0,
                            "unit_length_ft": line.get("unit_length_ft") or 0,
                            "max_stack_height": max_stack,
                            "category": self._sku_category(line.get("sku")),
                            "order_id": line.get("so_num"),
                        }
                    )
            return items

        config = stack_calculator.calculate_stack_configuration(
            build_line_items(trailer_key),
            trailer_type=trailer_key,
            capacity_feet=capacity_feet,
        )

        # Auto-upgrade step deck -> flatbed when the load doesn't fit the 43' / 10' split.
        if trailer_key == "STEP_DECK" and config.get("exceeds_capacity"):
            flatbed_key = "FLATBED"
            flatbed_cache_key = (group_keys, flatbed_key, capacity_feet)
            flatbed_config = self._stack_cache.get(flatbed_cache_key)
            if flatbed_config is None:
                flatbed_config = stack_calculator.calculate_stack_configuration(
                    build_line_items(flatbed_key),
                    trailer_type=flatbed_key,
                    capacity_feet=capacity_feet,
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
        trailer_key = (trailer_type or "STEP_DECK").strip().upper()
        if trailer_key == "STEP_DECK":
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
