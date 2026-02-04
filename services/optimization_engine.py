from datetime import datetime, date

import db
from services import geo_utils


class OptimizationEngine:
    def __init__(self, zip_coords=None):
        self.zip_coords = zip_coords or geo_utils.load_zip_coordinates()
        self.stacking_rules = db.list_stacking_rules()
        self.stacking_rule_map = {
            rule["trailer_category"]: rule for rule in self.stacking_rules
        }

    def build_optimized_loads(self, params, orders=None):
        orders = orders if orders is not None else self.get_eligible_orders(params["origin_plant"])
        if not orders:
            return []

        clusters = self.cluster_by_geography(orders, params["geo_radius"])
        clusters = self.filter_by_time_window(clusters, params["time_window_days"])

        load_groups = []
        for cluster in clusters:
            load_groups.extend(
                self.greedy_pack(cluster, params["capacity_feet"], respect_stacking=True)
            )

        return [self._build_load_dict(group, params) for group in load_groups]

    def build_baseline_loads(self, params, orders=None):
        orders = orders if orders is not None else self.get_eligible_orders(params["origin_plant"])
        if not orders:
            return []

        load_groups = self.greedy_pack(
            orders, params["capacity_feet"], respect_stacking=False
        )
        return [self._build_load_dict(group, params) for group in load_groups]

    def get_eligible_orders(self, origin_plant):
        return db.list_order_lines_for_optimization(origin_plant)

    def cluster_by_geography(self, orders, radius_miles):
        clusters = []
        for order in orders:
            zip_code = order.get("ship_to_zip")
            coords = self.zip_coords.get(zip_code)
            if not coords:
                clusters.append({"orders": [order], "centroid": None, "count": 0})
                continue

            best_cluster = None
            best_distance = None
            for cluster in clusters:
                centroid = cluster["centroid"]
                if not centroid:
                    continue
                distance = geo_utils.haversine_distance_coords(coords, centroid)
                if distance <= radius_miles and (
                    best_distance is None or distance < best_distance
                ):
                    best_cluster = cluster
                    best_distance = distance

            if not best_cluster:
                clusters.append({"orders": [order], "centroid": coords, "count": 1})
            else:
                best_cluster["orders"].append(order)
                best_cluster["count"] += 1
                count = best_cluster["count"]
                old_lat, old_lon = best_cluster["centroid"]
                new_lat = (old_lat * (count - 1) + coords[0]) / count
                new_lon = (old_lon * (count - 1) + coords[1]) / count
                best_cluster["centroid"] = (new_lat, new_lon)

        return [cluster["orders"] for cluster in clusters]

    def filter_by_time_window(self, clusters, time_window_days):
        if time_window_days <= 0:
            return clusters

        filtered_clusters = []
        for cluster in clusters:
            orders = sorted(cluster, key=self._due_date_sort_key)
            current_group = []
            start_date = None
            for order in orders:
                order_date = self._parse_due_date(order.get("due_date"))
                if not current_group:
                    current_group = [order]
                    start_date = order_date
                    continue

                if start_date and order_date:
                    delta = (order_date - start_date).days
                else:
                    delta = time_window_days + 1

                if delta <= time_window_days:
                    current_group.append(order)
                else:
                    filtered_clusters.append(current_group)
                    current_group = [order]
                    start_date = order_date

            if current_group:
                filtered_clusters.append(current_group)

        return filtered_clusters

    def greedy_pack(self, orders, capacity_feet, respect_stacking=True):
        loads = []
        current_load = []
        current_feet = 0.0

        for order in sorted(orders, key=self._due_date_sort_key):
            order_feet = order["qty"] * order["feet_per_unit"]
            fits_capacity = current_feet + order_feet <= capacity_feet
            fits_stacking = (
                not respect_stacking
                or self.check_stacking_compatibility(current_load + [order])
            )

            if current_load and (not fits_capacity or not fits_stacking):
                loads.append(current_load)
                current_load = []
                current_feet = 0.0

            current_load.append(order)
            current_feet += order_feet

        if current_load:
            loads.append(current_load)

        return loads

    def check_stacking_compatibility(self, order_lines):
        categories = {
            (line.get("trailer_category") or "STANDARD").upper() for line in order_lines
        }
        if "WIDE" in categories and len(categories) > 1:
            return False
        if "MIXED" in categories and "WIDE" in categories:
            return False
        return True

    def _build_load_dict(self, order_lines, params):
        total_feet = sum(line["qty"] * line["feet_per_unit"] for line in order_lines)
        total_miles, detour_miles, route_order, destination, direct_miles = (
            self._calculate_route_metrics(order_lines, params["origin_plant"])
        )

        utilization_pct = (
            (total_feet / params["capacity_feet"]) * 100
            if params["capacity_feet"]
            else 0.0
        )

        if total_miles > 0:
            route_efficiency = 100 - (detour_miles / total_miles * 100)
        else:
            route_efficiency = 100

        consolidation_score = min(len(order_lines) * 10, 50)
        optimization_score = (
            utilization_pct * 0.6
            + consolidation_score * 0.3
            + route_efficiency * 0.1
        )

        detour_pct = (detour_miles / direct_miles * 100) if direct_miles > 0 else 0.0
        exceeds_detour = detour_pct > params["max_detour_pct"]

        return {
            "order_lines": order_lines,
            "origin": params["origin_plant"],
            "destination": destination,
            "capacity_feet": params["capacity_feet"],
            "total_feet": total_feet,
            "utilization_pct": utilization_pct,
            "total_miles": total_miles,
            "detour_miles": detour_miles,
            "detour_pct": detour_pct,
            "optimization_score": optimization_score,
            "status": "PROPOSED",
            "rate_cents": params["rate_per_mile_cents"],
            "miles": int(round(total_miles)),
            "route_order": route_order,
            "exceeds_detour": exceeds_detour,
        }

    def _calculate_route_metrics(self, order_lines, origin_plant):
        origin_coords = geo_utils.plant_coords_for_code(origin_plant)
        destinations = [line.get("ship_to_zip") for line in order_lines if line.get("ship_to_zip")]
        route_order = geo_utils.nearest_neighbor_route(
            origin_coords, destinations, self.zip_coords
        )

        if not origin_coords:
            return 0.0, 0.0, route_order, "", 0.0

        known_destinations = [zip_code for zip_code in destinations if zip_code in self.zip_coords]
        if not known_destinations:
            destination = destinations[-1] if destinations else ""
            return 0.0, 0.0, route_order, destination, 0.0

        total_miles = 0.0
        current_coords = origin_coords
        for zip_code in route_order:
            coords = self.zip_coords.get(zip_code)
            if not coords:
                continue
            total_miles += geo_utils.haversine_distance_coords(current_coords, coords)
            current_coords = coords

        farthest_zip = None
        direct_miles = 0.0
        for zip_code in known_destinations:
            coords = self.zip_coords[zip_code]
            distance = geo_utils.haversine_distance_coords(origin_coords, coords)
            if distance > direct_miles:
                direct_miles = distance
                farthest_zip = zip_code

        detour_miles = max(total_miles - direct_miles, 0.0)
        destination = farthest_zip or (route_order[-1] if route_order else "")
        return total_miles, detour_miles, route_order, destination, direct_miles

    def _parse_due_date(self, due_date_value):
        if not due_date_value:
            return None
        try:
            return datetime.strptime(due_date_value, "%Y-%m-%d").date()
        except ValueError:
            return None

    def _due_date_sort_key(self, order):
        parsed = self._parse_due_date(order.get("due_date"))
        if not parsed:
            return date.max
        return parsed
