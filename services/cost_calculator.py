import db
from services import geo_utils, tsp_solver
from services.routing_service import get_routing_service

DEFAULT_RATE_PER_MILE = 3.12
DEFAULT_STOP_FEE = 55.0
DEFAULT_MIN_LOAD_COST = 800.0
STOP_FEE = DEFAULT_STOP_FEE
MIN_LOAD_COST = DEFAULT_MIN_LOAD_COST
FUEL_SURCHARGE_SETTING_KEY = "fuel_surcharge_per_mile"
STOP_FEE_SETTING_KEY = "stop_fee"
MIN_LOAD_COST_SETTING_KEY = "min_load_cost"
DEFAULT_FUEL_SURCHARGE_PER_MILE = 0.40


def _coerce_non_negative(raw_value, default_value):
    try:
        parsed = float(raw_value)
    except (TypeError, ValueError):
        parsed = float(default_value)
    if parsed < 0:
        return 0.0
    return parsed


def _coerce_fuel_surcharge(raw_value):
    return _coerce_non_negative(raw_value, DEFAULT_FUEL_SURCHARGE_PER_MILE)


def resolve_fuel_surcharge(value=None):
    if value is not None:
        return _coerce_fuel_surcharge(value)
    setting = db.get_planning_setting(FUEL_SURCHARGE_SETTING_KEY) or {}
    return _coerce_fuel_surcharge(setting.get("value_text"))


def resolve_stop_fee(value=None):
    if value is not None:
        return _coerce_non_negative(value, DEFAULT_STOP_FEE)
    setting = db.get_planning_setting(STOP_FEE_SETTING_KEY) or {}
    return _coerce_non_negative(setting.get("value_text"), DEFAULT_STOP_FEE)


def resolve_min_load_cost(value=None):
    if value is not None:
        return _coerce_non_negative(value, DEFAULT_MIN_LOAD_COST)
    setting = db.get_planning_setting(MIN_LOAD_COST_SETTING_KEY) or {}
    return _coerce_non_negative(setting.get("value_text"), DEFAULT_MIN_LOAD_COST)


def build_rate_lookup(rates=None, fuel_surcharge=None):
    surcharge = resolve_fuel_surcharge(fuel_surcharge)
    rates = rates if rates is not None else db.list_rate_matrix()
    lookup = {}
    for rate in rates:
        origin = (rate.get("origin_plant") or "").strip().upper()
        destination = (rate.get("destination_state") or "").strip().upper()
        if not origin or not destination:
            continue
        key = (origin, destination)
        if key not in lookup:
            lookup[key] = float(rate.get("rate_per_mile") or 0) + surcharge
    return lookup


class CostCalculator:
    def __init__(
        self,
        rate_lookup=None,
        default_rate=DEFAULT_RATE_PER_MILE,
        fuel_surcharge=None,
        stop_fee=None,
        min_load_cost=None,
        lookup_includes_fuel_surcharge=True,
        zip_coords=None,
        distance_cache=None,
        route_cache=None,
    ):
        self.rate_lookup = rate_lookup or {}
        self.default_rate = float(default_rate)
        self.fuel_surcharge = resolve_fuel_surcharge(fuel_surcharge)
        self.stop_fee = resolve_stop_fee(stop_fee)
        self.min_load_cost = resolve_min_load_cost(min_load_cost)
        self.lookup_includes_fuel_surcharge = bool(lookup_includes_fuel_surcharge)
        self.zip_coords = zip_coords or geo_utils.load_zip_coordinates()
        self.distance_cache = distance_cache if distance_cache is not None else {}
        self.route_cache = route_cache if route_cache is not None else {}
        self.routing_service = get_routing_service()

    def rate_for(self, origin_plant, destination_state):
        origin = (origin_plant or "").strip().upper()
        destination = (destination_state or "").strip().upper()
        if not origin or not destination:
            return self.default_rate + self.fuel_surcharge
        rate = self.rate_lookup.get((origin, destination))
        if rate is not None:
            resolved = float(rate)
            if self.lookup_includes_fuel_surcharge:
                return resolved
            return resolved + self.fuel_surcharge
        db_rate = db.get_rate(origin, destination)
        if db_rate:
            return float(db_rate) + self.fuel_surcharge
        return self.default_rate + self.fuel_surcharge

    def _distance_key(self, coords_a, coords_b):
        a = (coords_a[0], coords_a[1])
        b = (coords_b[0], coords_b[1])
        return (a, b) if a <= b else (b, a)

    def _origin_key(self, origin_coords):
        if not origin_coords:
            return None
        return (origin_coords[0], origin_coords[1])

    def _stop_signature(self, stop):
        coords = stop.get("coords") or ()
        lat = coords[0] if len(coords) > 0 else None
        lng = coords[1] if len(coords) > 1 else None
        return (
            (stop.get("state") or "").strip().upper(),
            (stop.get("zip") or "").strip(),
            lat,
            lng,
        )

    def distance(self, coords_a, coords_b):
        if not coords_a or not coords_b:
            return 0.0
        key = self._distance_key(coords_a, coords_b)
        if key in self.distance_cache:
            return self.distance_cache[key]
        distance = geo_utils.haversine_distance_coords(coords_a, coords_b)
        self.distance_cache[key] = distance
        return distance

    def order_stops(self, origin_coords, stops, return_to_origin=False, objective="distance"):
        if not origin_coords:
            return list(stops or [])

        route_data = self.routing_service.build_route(
            origin_coords,
            stops or [],
            return_to_origin=return_to_origin,
            objective=objective,
            include_geometry=False,
        )
        ordered_stops = route_data.get("ordered_stops") or []
        if ordered_stops:
            return ordered_stops

        with_coords = [stop for stop in (stops or []) if stop.get("coords")]
        without_coords = [stop for stop in (stops or []) if not stop.get("coords")]
        if len(with_coords) <= 1:
            return with_coords + without_coords

        signatures = [self._stop_signature(stop) for stop in with_coords]
        route_key = (
            self._origin_key(origin_coords),
            tuple(sorted(signatures)),
            bool(return_to_origin),
            str(objective or "distance").strip().lower(),
        )
        cached_order = self.route_cache.get(route_key)
        if cached_order:
            pools = {}
            for stop in with_coords:
                sig = self._stop_signature(stop)
                pools.setdefault(sig, []).append(stop)
            ordered = []
            for sig in cached_order:
                bucket = pools.get(sig) or []
                if bucket:
                    ordered.append(bucket.pop(0))
            for bucket in pools.values():
                if bucket:
                    ordered.extend(bucket)
            return ordered + without_coords

        ordered = tsp_solver.solve_route(
            origin_coords,
            with_coords,
            distance_fn=self.distance,
            return_to_origin=return_to_origin,
        )
        self.route_cache[route_key] = tuple(self._stop_signature(stop) for stop in ordered)
        return ordered + without_coords

    def calculate(self, origin_plant, stops, origin_coords=None, return_to_origin=False, objective="distance"):
        if not stops:
            return {
                "ordered_stops": [],
                "route_legs": [],
                "route_geometry": [],
                "total_miles": 0.0,
                "total_cost": 0.0,
                "stop_count": 0,
                "return_to_origin": bool(return_to_origin),
                "return_miles": 0.0,
                "return_cost": 0.0,
                "route_provider": "none",
                "route_profile": "",
                "route_fallback": True,
            }

        origin_coords = origin_coords or geo_utils.plant_coords_for_code(origin_plant)
        route_data = (
            self.routing_service.build_route(
                origin_coords,
                stops,
                return_to_origin=return_to_origin,
                objective=objective,
                include_geometry=False,
            )
            if origin_coords
            else {
                "ordered_stops": list(stops),
                "leg_miles": [],
                "total_miles": 0.0,
                "geometry_latlng": [],
                "provider": "none",
                "profile": "",
                "used_fallback": True,
            }
        )
        ordered_stops = route_data.get("ordered_stops") or list(stops)
        route_legs = [float(value or 0.0) for value in (route_data.get("leg_miles") or [])]

        total_miles = 0.0
        total_cost = 0.0
        current = origin_coords

        for index, stop in enumerate(ordered_stops):
            coords = stop.get("coords")
            if index < len(route_legs):
                segment_miles = route_legs[index]
            else:
                segment_miles = self.distance(current, coords) if coords and current else 0.0
            total_miles += segment_miles
            total_cost += segment_miles * self.rate_for(origin_plant, stop.get("state"))
            if coords:
                current = coords

        stop_count = len(ordered_stops)
        total_cost += self.stop_fee * stop_count

        return_miles = 0.0
        return_cost = 0.0
        if return_to_origin and origin_coords and current and current != origin_coords:
            if len(route_legs) > len(ordered_stops):
                return_miles = float(route_legs[len(ordered_stops)] or 0.0)
            else:
                return_miles = self.distance(current, origin_coords)
            # Treat the return leg as inbound to the origin plant's state code (plant codes are state-like today).
            return_cost = return_miles * self.rate_for(origin_plant, origin_plant)
            total_miles += return_miles
            total_cost += return_cost

        route_total_miles = float(route_data.get("total_miles") or 0.0)
        if route_total_miles > 0:
            total_miles = route_total_miles

        if total_cost < self.min_load_cost:
            total_cost = self.min_load_cost

        return {
            "ordered_stops": ordered_stops,
            "route_legs": route_legs,
            "route_geometry": route_data.get("geometry_latlng") or [],
            "total_miles": total_miles,
            "total_cost": total_cost,
            "stop_count": stop_count,
            "return_to_origin": bool(return_to_origin),
            "return_miles": return_miles,
            "return_cost": return_cost,
            "route_provider": route_data.get("provider") or "none",
            "route_profile": route_data.get("profile") or "",
            "route_fallback": bool(route_data.get("used_fallback")),
        }
