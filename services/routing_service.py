import hashlib
import json
import logging
import math
import os
from itertools import permutations
from time import perf_counter

import db
from services import geo_utils, tsp_solver
from services.routing_providers.openrouteservice_provider import (
    OpenRouteServiceError,
    OpenRouteServiceProvider,
)

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows platforms
    winreg = None


logger = logging.getLogger(__name__)

_ROUTING_SERVICE = None


def get_routing_service():
    global _ROUTING_SERVICE
    if _ROUTING_SERVICE is None:
        _ROUTING_SERVICE = RoutingService()
    return _ROUTING_SERVICE


def _as_bool(value, default=False):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _read_windows_env_var(name):
    if not winreg:
        return None
    key_name = str(name or "").strip()
    if not key_name:
        return None
    key_paths = [
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    ]
    for hive, path in key_paths:
        try:
            with winreg.OpenKey(hive, path) as key:
                value, _ = winreg.QueryValueEx(key, key_name)
                text = str(value or "").strip()
                if text:
                    return text
        except OSError:
            continue
    return None


def _env(name, default=None):
    value = os.environ.get(name)
    text = str(value).strip() if value is not None else ""
    if text:
        return text
    fallback = _read_windows_env_var(name)
    if fallback:
        os.environ[name] = fallback
        return fallback
    return default


def _miles_from_matrix(distance_matrix, node_path):
    miles = []
    for index in range(len(node_path) - 1):
        start = node_path[index]
        end = node_path[index + 1]
        miles.append(float(distance_matrix[start][end] or 0.0))
    return miles


def _route_distance_matrix(node_path, distance_matrix):
    return sum(float(distance_matrix[node_path[idx]][node_path[idx + 1]] or 0.0) for idx in range(len(node_path) - 1))


def _solve_path_bruteforce(distance_matrix, stop_indices, return_to_origin=False):
    best_path = None
    best_distance = None
    for perm in permutations(stop_indices):
        node_path = [0] + list(perm)
        if return_to_origin:
            node_path.append(0)
        candidate_distance = _route_distance_matrix(node_path, distance_matrix)
        if best_distance is None or candidate_distance < best_distance:
            best_distance = candidate_distance
            best_path = node_path
    return best_path or [0]


def _solve_path_held_karp(distance_matrix, stop_indices, return_to_origin=False):
    if len(stop_indices) <= 1:
        node_path = [0] + list(stop_indices)
        if return_to_origin and len(stop_indices) == 1:
            node_path.append(0)
        return node_path

    n = len(stop_indices)
    to_node = {idx: stop_indices[idx] for idx in range(n)}

    dp = {}
    parent = {}
    for idx in range(n):
        mask = 1 << idx
        stop_node = to_node[idx]
        dp[(mask, idx)] = float(distance_matrix[0][stop_node] or 0.0)
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
                prev_node = to_node[prev_idx]
                end_node = to_node[end_idx]
                candidate_cost = prev_cost + float(distance_matrix[prev_node][end_node] or 0.0)
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
        end_node = to_node[end_idx]
        total_cost = path_cost
        if return_to_origin:
            total_cost += float(distance_matrix[end_node][0] or 0.0)
        if best_total is None or total_cost < best_total:
            best_total = total_cost
            best_end = end_idx

    if best_end is None:
        node_path = [0] + stop_indices
        if return_to_origin:
            node_path.append(0)
        return node_path

    order_positions = []
    mask = full_mask
    cursor = best_end
    while cursor is not None:
        order_positions.append(cursor)
        next_cursor = parent.get((mask, cursor))
        mask ^= (1 << cursor)
        cursor = next_cursor
    order_positions.reverse()

    node_path = [0] + [to_node[idx] for idx in order_positions]
    if return_to_origin:
        node_path.append(0)
    return node_path


def _nearest_neighbor_path(distance_matrix, stop_indices, return_to_origin=False):
    remaining = list(stop_indices)
    if not remaining:
        return [0]
    node_path = [0]
    current = 0
    while remaining:
        next_node = min(remaining, key=lambda node: float(distance_matrix[current][node] or 0.0))
        node_path.append(next_node)
        current = next_node
        remaining.remove(next_node)
    if return_to_origin:
        node_path.append(0)
    return node_path


def _two_opt_path(node_path, distance_matrix, return_to_origin=False, max_passes=4):
    if len(node_path) <= 4:
        return node_path
    best = list(node_path)
    best_distance = _route_distance_matrix(best, distance_matrix)
    passes = 0
    improved = True
    start_index = 1
    end_limit = len(best) - (1 if return_to_origin else 0)
    while improved and passes < max_passes:
        passes += 1
        improved = False
        for left in range(start_index, end_limit - 1):
            for right in range(left + 1, end_limit):
                candidate = best[:left] + list(reversed(best[left : right + 1])) + best[right + 1 :]
                candidate_distance = _route_distance_matrix(candidate, distance_matrix)
                if candidate_distance + 1e-9 < best_distance:
                    best = candidate
                    best_distance = candidate_distance
                    improved = True
                    break
            if improved:
                break
    return best


def _solve_node_path(distance_matrix, stop_count, return_to_origin=False, brute_force_limit=6, exact_limit=11):
    stop_indices = [idx + 1 for idx in range(stop_count)]
    if stop_count <= 1:
        node_path = [0] + stop_indices
        if return_to_origin and stop_count:
            node_path.append(0)
        return node_path
    if stop_count <= brute_force_limit:
        return _solve_path_bruteforce(distance_matrix, stop_indices, return_to_origin=return_to_origin)
    if stop_count <= exact_limit:
        return _solve_path_held_karp(distance_matrix, stop_indices, return_to_origin=return_to_origin)
    path = _nearest_neighbor_path(distance_matrix, stop_indices, return_to_origin=return_to_origin)
    return _two_opt_path(path, distance_matrix, return_to_origin=return_to_origin)


class RoutingService:
    def __init__(self):
        # Default is enabled so the app is road-routing ready without extra config.
        self.routing_enabled = _as_bool(_env("ROUTING_ENABLED"), default=True)
        # Default to geometry-only provider usage to preserve API quota:
        # optimization and costing remain haversine unless geometry is explicitly requested.
        self.geometry_only_mode = _as_bool(_env("ROUTING_GEOMETRY_ONLY"), default=True)
        self.provider_name = (_env("ROUTING_PROVIDER", "ors") or "ors").strip().lower()
        self.profile = (_env("ROUTING_PROFILE", "driving-hgv") or "driving-hgv").strip()
        self.timeout_ms = _as_int(_env("ROUTING_TIMEOUT_MS"), 5000)
        self.snap_radius_m = _as_int(_env("ROUTING_SNAP_RADIUS_M"), 5000)
        self.cache_ttl_days = _as_int(_env("ROUTING_CACHE_TTL_DAYS"), 30)
        self.provider = None
        if self.routing_enabled and self.provider_name == "ors":
            api_key = _env("ORS_API_KEY") or ""
            if api_key.strip():
                self.provider = OpenRouteServiceProvider(
                    api_key=api_key,
                    profile=self.profile,
                    timeout_ms=self.timeout_ms,
                    retries=1,
                    snap_radius_m=self.snap_radius_m,
                )
            else:
                logger.warning("ROUTING_ENABLED is true but ORS_API_KEY is missing. Using fallback routing.")
        self._memory_cache = {}
        self.stats = {
            "requests": 0,
            "success": 0,
            "fallback": 0,
            "cache_hit_memory": 0,
            "cache_hit_db": 0,
            "errors": 0,
        }

    def build_route(
        self,
        origin_coords,
        stops,
        return_to_origin=False,
        objective="distance",
        include_geometry=True,
    ):
        normalized_stops = list(stops or [])
        with_coords = [stop for stop in normalized_stops if stop.get("coords")]
        without_coords = [stop for stop in normalized_stops if not stop.get("coords")]
        route_objective = str(objective or "distance").strip().lower() or "distance"
        return_to_origin = bool(return_to_origin)
        include_geometry = bool(include_geometry)

        if not origin_coords or not with_coords:
            return {
                "ordered_stops": with_coords + without_coords,
                "leg_miles": [],
                "total_miles": 0.0,
                "geometry_latlng": [],
                "leg_geometries_latlng": [],
                "provider": "none",
                "profile": self.profile,
                "used_fallback": True,
            }

        # Keep optimization/costing on haversine by default; only use provider for map geometry.
        if self.geometry_only_mode and not include_geometry:
            self.stats["fallback"] += 1
            return self._fallback_route(origin_coords, normalized_stops, return_to_origin)

        self.stats["requests"] += 1
        cache_key = self._cache_key(origin_coords, with_coords, return_to_origin, route_objective)

        cached = self._memory_cache.get(cache_key)
        if cached:
            self.stats["cache_hit_memory"] += 1
            ordered = self._ordered_stops_from_signatures(cached.get("ordered_stop_signatures") or [], with_coords)
            if ordered:
                cached_has_geometry = bool(cached.get("geometry_latlng"))
                if (not include_geometry) or cached_has_geometry:
                    return self._result_from_cached(cached, ordered + without_coords)
                enriched = self._enrich_cached_geometry(
                    cached,
                    origin_coords,
                    ordered,
                    return_to_origin=return_to_origin,
                    objective=route_objective,
                )
                if enriched:
                    self._memory_cache[cache_key] = enriched
                    db.upsert_route_cache(
                        cache_key,
                        enriched,
                        provider=enriched.get("provider"),
                        profile=enriched.get("profile"),
                        objective=route_objective,
                        ttl_days=self.cache_ttl_days,
                    )
                    return self._result_from_cached(enriched, ordered + without_coords)
                return self._result_from_cached(cached, ordered + without_coords)

        cached_db = db.get_route_cache(cache_key)
        if cached_db:
            self.stats["cache_hit_db"] += 1
            self._memory_cache[cache_key] = cached_db
            ordered = self._ordered_stops_from_signatures(cached_db.get("ordered_stop_signatures") or [], with_coords)
            if ordered:
                cached_has_geometry = bool(cached_db.get("geometry_latlng"))
                if (not include_geometry) or cached_has_geometry:
                    return self._result_from_cached(cached_db, ordered + without_coords)
                enriched = self._enrich_cached_geometry(
                    cached_db,
                    origin_coords,
                    ordered,
                    return_to_origin=return_to_origin,
                    objective=route_objective,
                )
                if enriched:
                    self._memory_cache[cache_key] = enriched
                    db.upsert_route_cache(
                        cache_key,
                        enriched,
                        provider=enriched.get("provider"),
                        profile=enriched.get("profile"),
                        objective=route_objective,
                        ttl_days=self.cache_ttl_days,
                    )
                    return self._result_from_cached(enriched, ordered + without_coords)
                return self._result_from_cached(cached_db, ordered + without_coords)

        if not self.routing_enabled or not self.provider:
            self.stats["fallback"] += 1
            return self._fallback_route(origin_coords, normalized_stops, return_to_origin)

        started_at = perf_counter()
        try:
            calculated = self._build_provider_route(
                origin_coords,
                with_coords,
                return_to_origin=return_to_origin,
                objective=route_objective,
                include_geometry=include_geometry,
            )
            persisted = {
                "provider": calculated.get("provider") or self.provider_name,
                "profile": calculated.get("profile") or self.profile,
                "objective": route_objective,
                "ordered_stop_signatures": [self._stop_signature(stop) for stop in calculated["ordered_stops"]],
                "leg_miles": calculated.get("leg_miles") or [],
                "total_miles": float(calculated.get("total_miles") or 0.0),
                "geometry_latlng": calculated.get("geometry_latlng") or [],
                "leg_geometries_latlng": calculated.get("leg_geometries_latlng") or [],
                "used_fallback": False,
            }
            self._memory_cache[cache_key] = persisted
            db.upsert_route_cache(
                cache_key,
                persisted,
                provider=persisted.get("provider"),
                profile=persisted.get("profile"),
                objective=route_objective,
                ttl_days=self.cache_ttl_days,
            )
            self.stats["success"] += 1
            duration_ms = int((perf_counter() - started_at) * 1000)
            logger.info("Routing success provider=%s profile=%s duration_ms=%s", self.provider_name, self.profile, duration_ms)
            return self._result_from_cached(persisted, calculated["ordered_stops"] + without_coords)
        except OpenRouteServiceError as exc:
            self.stats["errors"] += 1
            self.stats["fallback"] += 1
            logger.warning("Routing provider failed, using fallback: %s", exc)
            return self._fallback_route(origin_coords, normalized_stops, return_to_origin)

    def _build_provider_route(
        self,
        origin_coords,
        stops,
        return_to_origin=False,
        objective="distance",
        include_geometry=True,
    ):
        matrix_coords = [origin_coords] + [stop.get("coords") for stop in stops]
        matrix = self.provider.distance_matrix(matrix_coords)
        if len(matrix) != len(matrix_coords):
            raise OpenRouteServiceError("Distance matrix size mismatch.")

        node_path = _solve_node_path(
            matrix,
            stop_count=len(stops),
            return_to_origin=return_to_origin,
        )
        ordered_stops = []
        for node in node_path:
            if node == 0:
                continue
            ordered_stops.append(stops[node - 1])

        route_points = [origin_coords] + [stop.get("coords") for stop in ordered_stops]
        if return_to_origin and ordered_stops:
            route_points.append(origin_coords)

        leg_miles = _miles_from_matrix(matrix, node_path)
        if any(not math.isfinite(float(value or 0.0)) for value in leg_miles):
            raise OpenRouteServiceError("Routing matrix returned unreachable leg(s).")
        total_miles = float(sum(leg_miles))
        geometry_latlng = []

        if include_geometry:
            directions = self.provider.directions(route_points, objective=objective)
            directions_legs = directions.get("leg_miles") or []
            expected_legs = len(route_points) - 1
            if len(directions_legs) == expected_legs and all(
                math.isfinite(float(value or 0.0)) for value in directions_legs
            ):
                leg_miles = [float(value or 0.0) for value in directions_legs]
            total_miles = float(directions.get("total_miles") or sum(leg_miles))
            geometry_latlng = directions.get("geometry_latlng") or []

        return {
            "ordered_stops": ordered_stops,
            "leg_miles": leg_miles,
            "total_miles": total_miles,
            "geometry_latlng": geometry_latlng,
            "leg_geometries_latlng": [],
            "provider": self.provider_name,
            "profile": self.profile,
            "used_fallback": False,
        }

    def _enrich_cached_geometry(self, cached, origin_coords, ordered_stops, return_to_origin=False, objective="distance"):
        if not self.provider:
            return None
        if not ordered_stops:
            return cached
        route_points = [origin_coords] + [stop.get("coords") for stop in ordered_stops if stop.get("coords")]
        if return_to_origin and ordered_stops:
            route_points.append(origin_coords)
        try:
            directions = self.provider.directions(route_points, objective=objective)
        except OpenRouteServiceError:
            return None
        geometry = directions.get("geometry_latlng") or []
        if not geometry:
            return None

        updated = dict(cached)
        updated["geometry_latlng"] = geometry
        directions_legs = directions.get("leg_miles") or []
        expected_legs = len(route_points) - 1
        directions_total = float(directions.get("total_miles") or 0.0)
        if directions_total > 0:
            updated["total_miles"] = directions_total
        if len(directions_legs) == expected_legs and all(
            math.isfinite(float(value or 0.0)) for value in directions_legs
        ):
            updated["leg_miles"] = [float(value or 0.0) for value in directions_legs]
            if directions_total <= 0:
                updated["total_miles"] = float(sum(updated["leg_miles"]))
        elif expected_legs == 1 and directions_total > 0:
            updated["leg_miles"] = [directions_total]
        return updated

    def _fallback_route(self, origin_coords, stops, return_to_origin=False):
        with_coords = [stop for stop in (stops or []) if stop.get("coords")]
        without_coords = [stop for stop in (stops or []) if not stop.get("coords")]
        ordered = (
            tsp_solver.solve_route(origin_coords, with_coords, return_to_origin=return_to_origin)
            if origin_coords
            else with_coords
        )

        node_coords = [origin_coords]
        node_coords.extend(stop.get("coords") for stop in ordered if stop.get("coords"))
        if return_to_origin and ordered:
            node_coords.append(origin_coords)

        leg_miles = []
        for idx in range(len(node_coords) - 1):
            leg_miles.append(
                float(
                    geo_utils.haversine_distance_coords(
                        node_coords[idx],
                        node_coords[idx + 1],
                    )
                )
            )
        geometry = [[float(coords[0]), float(coords[1])] for coords in node_coords if coords]
        return {
            "ordered_stops": ordered + without_coords,
            "leg_miles": leg_miles,
            "total_miles": float(sum(leg_miles)),
            "geometry_latlng": geometry,
            "leg_geometries_latlng": [],
            "provider": "haversine",
            "profile": "fallback",
            "used_fallback": True,
        }

    def _cache_key(self, origin_coords, stops, return_to_origin, objective):
        canonical_stops = []
        for stop in stops:
            signature = self._stop_signature(stop)
            canonical_stops.append(signature)
        payload = {
            "provider": self.provider_name,
            "profile": self.profile,
            "origin": [round(float(origin_coords[0]), 6), round(float(origin_coords[1]), 6)],
            "stops": sorted(canonical_stops),
            "return_to_origin": bool(return_to_origin),
            "objective": str(objective or "distance").strip().lower(),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        return f"route:{digest}"

    def _stop_signature(self, stop):
        coords = stop.get("coords") or (None, None)
        lat = round(float(coords[0]), 6) if coords and coords[0] is not None else None
        lng = round(float(coords[1]), 6) if coords and coords[1] is not None else None
        return "|".join(
            [
                (stop.get("state") or "").strip().upper(),
                geo_utils.normalize_zip(stop.get("zip")),
                "" if lat is None else str(lat),
                "" if lng is None else str(lng),
            ]
        )

    def _ordered_stops_from_signatures(self, signatures, stops):
        pools = {}
        for stop in stops:
            signature = self._stop_signature(stop)
            pools.setdefault(signature, []).append(stop)

        ordered = []
        for signature in signatures:
            bucket = pools.get(signature) or []
            if bucket:
                ordered.append(bucket.pop(0))

        for bucket in pools.values():
            if bucket:
                ordered.extend(bucket)
        return ordered

    def _result_from_cached(self, cached, ordered_stops):
        return {
            "ordered_stops": ordered_stops,
            "leg_miles": [float(value or 0.0) for value in (cached.get("leg_miles") or [])],
            "total_miles": float(cached.get("total_miles") or 0.0),
            "geometry_latlng": cached.get("geometry_latlng") or [],
            "leg_geometries_latlng": cached.get("leg_geometries_latlng") or [],
            "provider": cached.get("provider") or self.provider_name,
            "profile": cached.get("profile") or self.profile,
            "used_fallback": bool(cached.get("used_fallback")),
        }
