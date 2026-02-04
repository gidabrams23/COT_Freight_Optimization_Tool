from itertools import permutations

from services import geo_utils


def _route_distance(origin_coords, stops, distance_fn):
    total = 0.0
    current = origin_coords
    for stop in stops:
        coords = stop.get("coords")
        if not coords or not current:
            continue
        total += distance_fn(current, coords)
        current = coords
    return total


def solve_route(origin_coords, stops, distance_fn=None, brute_force_limit=6):
    if not origin_coords or not stops:
        return []

    distance_fn = distance_fn or geo_utils.haversine_distance_coords

    with_coords = [stop for stop in stops if stop.get("coords")]
    without_coords = [stop for stop in stops if not stop.get("coords")]

    if len(with_coords) <= 1:
        return with_coords + without_coords

    if len(with_coords) <= brute_force_limit:
        best_order = None
        best_distance = None
        for perm in permutations(with_coords):
            distance = _route_distance(origin_coords, perm, distance_fn)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_order = perm
        ordered = list(best_order) if best_order is not None else with_coords
    else:
        remaining = list(with_coords)
        ordered = []
        current = origin_coords
        while remaining:
            next_stop = min(
                remaining,
                key=lambda stop: distance_fn(current, stop["coords"]),
            )
            ordered.append(next_stop)
            current = next_stop["coords"]
            remaining.remove(next_stop)

    return ordered + without_coords
