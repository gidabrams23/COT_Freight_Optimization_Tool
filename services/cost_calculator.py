import db
from services import geo_utils, tsp_solver

DEFAULT_RATE_PER_MILE = 3.12
STOP_FEE = 55.0
MIN_LOAD_COST = 800.0


def build_rate_lookup(rates=None):
    rates = rates if rates is not None else db.list_rate_matrix()
    lookup = {}
    for rate in rates:
        origin = (rate.get("origin_plant") or "").strip().upper()
        destination = (rate.get("destination_state") or "").strip().upper()
        if not origin or not destination:
            continue
        key = (origin, destination)
        if key not in lookup:
            lookup[key] = float(rate.get("rate_per_mile") or 0)
    return lookup


class CostCalculator:
    def __init__(
        self,
        rate_lookup=None,
        default_rate=DEFAULT_RATE_PER_MILE,
        zip_coords=None,
        distance_cache=None,
    ):
        self.rate_lookup = rate_lookup or {}
        self.default_rate = float(default_rate)
        self.zip_coords = zip_coords or geo_utils.load_zip_coordinates()
        self.distance_cache = distance_cache if distance_cache is not None else {}

    def rate_for(self, origin_plant, destination_state):
        origin = (origin_plant or "").strip().upper()
        destination = (destination_state or "").strip().upper()
        if not origin or not destination:
            return self.default_rate
        rate = self.rate_lookup.get((origin, destination))
        if rate is None:
            rate = db.get_rate(origin, destination)
        if not rate:
            rate = self.default_rate
        return float(rate)

    def _distance_key(self, coords_a, coords_b):
        a = (round(coords_a[0], 6), round(coords_a[1], 6))
        b = (round(coords_b[0], 6), round(coords_b[1], 6))
        return (a, b) if a <= b else (b, a)

    def distance(self, coords_a, coords_b):
        if not coords_a or not coords_b:
            return 0.0
        key = self._distance_key(coords_a, coords_b)
        if key in self.distance_cache:
            return self.distance_cache[key]
        distance = geo_utils.haversine_distance_coords(coords_a, coords_b)
        self.distance_cache[key] = distance
        return distance

    def order_stops(self, origin_coords, stops):
        return tsp_solver.solve_route(origin_coords, stops, distance_fn=self.distance)

    def calculate(self, origin_plant, stops, origin_coords=None, return_to_origin=False):
        if not stops:
            return {
                "ordered_stops": [],
                "total_miles": 0.0,
                "total_cost": 0.0,
                "stop_count": 0,
                "return_to_origin": bool(return_to_origin),
                "return_miles": 0.0,
                "return_cost": 0.0,
            }

        origin_coords = origin_coords or geo_utils.plant_coords_for_code(origin_plant)
        ordered_stops = (
            self.order_stops(origin_coords, stops) if origin_coords else list(stops)
        )

        total_miles = 0.0
        total_cost = 0.0
        current = origin_coords

        for stop in ordered_stops:
            coords = stop.get("coords")
            segment_miles = self.distance(current, coords) if coords and current else 0.0
            total_miles += segment_miles
            total_cost += segment_miles * self.rate_for(origin_plant, stop.get("state"))
            if coords:
                current = coords

        stop_count = len(ordered_stops)
        total_cost += STOP_FEE * stop_count

        return_miles = 0.0
        return_cost = 0.0
        if return_to_origin and origin_coords and current and current != origin_coords:
            return_miles = self.distance(current, origin_coords)
            # Treat the return leg as inbound to the origin plant's state code (plant codes are state-like today).
            return_cost = return_miles * self.rate_for(origin_plant, origin_plant)
            total_miles += return_miles
            total_cost += return_cost

        if total_cost < MIN_LOAD_COST:
            total_cost = MIN_LOAD_COST

        return {
            "ordered_stops": ordered_stops,
            "total_miles": total_miles,
            "total_cost": total_cost,
            "stop_count": stop_count,
            "return_to_origin": bool(return_to_origin),
            "return_miles": return_miles,
            "return_cost": return_cost,
        }
