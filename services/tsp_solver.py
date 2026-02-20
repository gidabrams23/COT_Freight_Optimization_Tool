from itertools import permutations

from services import geo_utils


def _route_distance(origin_coords, stops, distance_fn, return_to_origin=False):
    total = 0.0
    current = origin_coords
    for stop in stops:
        coords = stop.get("coords")
        if not coords or not current:
            continue
        total += distance_fn(current, coords)
        current = coords
    if return_to_origin and current and origin_coords and stops:
        total += distance_fn(current, origin_coords)
    return total


def _nearest_neighbor_route(origin_coords, stops, distance_fn, start_index=0):
    if not stops:
        return []

    remaining = list(stops)
    ordered = []
    current = origin_coords

    start_index = max(0, min(start_index, len(remaining) - 1))
    if remaining:
        first = remaining.pop(start_index)
        ordered.append(first)
        current = first["coords"]

    while remaining:
        next_stop = min(
            remaining,
            key=lambda stop: distance_fn(current, stop["coords"]),
        )
        ordered.append(next_stop)
        current = next_stop["coords"]
        remaining.remove(next_stop)
    return ordered


def _two_opt_improve(origin_coords, route, distance_fn, return_to_origin=False, max_passes=4):
    if len(route) <= 2:
        return route

    best = list(route)
    best_distance = _route_distance(
        origin_coords,
        best,
        distance_fn,
        return_to_origin=return_to_origin,
    )

    passes = 0
    improved = True
    while improved and passes < max_passes:
        improved = False
        passes += 1
        for left in range(len(best) - 1):
            for right in range(left + 1, len(best)):
                candidate = best[:left] + list(reversed(best[left : right + 1])) + best[right + 1 :]
                candidate_distance = _route_distance(
                    origin_coords,
                    candidate,
                    distance_fn,
                    return_to_origin=return_to_origin,
                )
                if candidate_distance + 1e-9 < best_distance:
                    best = candidate
                    best_distance = candidate_distance
                    improved = True
                    break
            if improved:
                break
    return best


def _held_karp_open_path(origin_coords, stops, distance_fn, return_to_origin=False):
    n = len(stops)
    if n <= 1:
        return list(stops)

    dist_origin = [distance_fn(origin_coords, stop["coords"]) for stop in stops]
    dist = [
        [distance_fn(stops[i]["coords"], stops[j]["coords"]) for j in range(n)]
        for i in range(n)
    ]

    dp = {}
    parent = {}

    for idx in range(n):
        mask = 1 << idx
        dp[(mask, idx)] = dist_origin[idx]
        parent[(mask, idx)] = None

    full_mask = (1 << n) - 1
    for mask in range(1, full_mask + 1):
        for end_idx in range(n):
            if not (mask & (1 << end_idx)):
                continue
            prev_mask = mask ^ (1 << end_idx)
            if prev_mask == 0:
                continue

            best_cost = None
            best_prev = None
            for prev_idx in range(n):
                if not (prev_mask & (1 << prev_idx)):
                    continue
                prev_cost = dp.get((prev_mask, prev_idx))
                if prev_cost is None:
                    continue
                candidate_cost = prev_cost + dist[prev_idx][end_idx]
                if best_cost is None or candidate_cost < best_cost:
                    best_cost = candidate_cost
                    best_prev = prev_idx

            if best_cost is not None:
                dp[(mask, end_idx)] = best_cost
                parent[(mask, end_idx)] = best_prev

    best_end = None
    best_total = None
    for end_idx in range(n):
        path_cost = dp.get((full_mask, end_idx))
        if path_cost is None:
            continue
        total_cost = path_cost
        if return_to_origin:
            total_cost += dist_origin[end_idx]
        if best_total is None or total_cost < best_total:
            best_total = total_cost
            best_end = end_idx

    if best_end is None:
        return list(stops)

    order_indices = []
    mask = full_mask
    cursor = best_end
    while cursor is not None:
        order_indices.append(cursor)
        next_cursor = parent.get((mask, cursor))
        mask ^= (1 << cursor)
        cursor = next_cursor
    order_indices.reverse()
    return [stops[idx] for idx in order_indices]


def solve_route(
    origin_coords,
    stops,
    distance_fn=None,
    brute_force_limit=6,
    exact_limit=11,
    return_to_origin=False,
):
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
            distance = _route_distance(
                origin_coords,
                perm,
                distance_fn,
                return_to_origin=return_to_origin,
            )
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_order = perm
        ordered = list(best_order) if best_order is not None else with_coords
    elif len(with_coords) <= exact_limit:
        ordered = _held_karp_open_path(
            origin_coords,
            with_coords,
            distance_fn,
            return_to_origin=return_to_origin,
        )
    else:
        nearest_origin = sorted(
            range(len(with_coords)),
            key=lambda idx: distance_fn(origin_coords, with_coords[idx]["coords"]),
        )
        best_order = None
        best_distance = None
        for seed_idx in nearest_origin[: min(4, len(nearest_origin))]:
            candidate = _nearest_neighbor_route(
                origin_coords,
                with_coords,
                distance_fn,
                start_index=seed_idx,
            )
            candidate = _two_opt_improve(
                origin_coords,
                candidate,
                distance_fn,
                return_to_origin=return_to_origin,
            )
            candidate_distance = _route_distance(
                origin_coords,
                candidate,
                distance_fn,
                return_to_origin=return_to_origin,
            )
            if best_distance is None or candidate_distance < best_distance:
                best_distance = candidate_distance
                best_order = candidate
        ordered = best_order if best_order is not None else with_coords

    return ordered + without_coords
