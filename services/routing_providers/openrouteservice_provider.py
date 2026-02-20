import json
import urllib.error
import urllib.request


MILES_PER_METER = 0.000621371


class OpenRouteServiceError(RuntimeError):
    """Raised for OpenRouteService transport or response issues."""


class OpenRouteServiceProvider:
    BASE_URL = "https://api.openrouteservice.org"

    def __init__(self, api_key, profile="driving-hgv", timeout_ms=5000, retries=1, snap_radius_m=5000):
        self.api_key = (api_key or "").strip()
        self.profile = (profile or "driving-hgv").strip()
        self.timeout_seconds = max(float(timeout_ms or 5000) / 1000.0, 0.5)
        self.retries = max(int(retries or 0), 0)
        self.snap_radius_m = max(int(snap_radius_m or 0), 350)

    def distance_matrix(self, coords_latlng):
        if not coords_latlng:
            return []
        payload = {
            "locations": [self._to_lonlat(value) for value in coords_latlng],
            "metrics": ["distance"],
            "units": "m",
            "resolve_locations": False,
        }
        data = self._post_json(f"/v2/matrix/{self.profile}", payload)
        distances = data.get("distances") or []
        miles_matrix = []
        for row in distances:
            miles_row = []
            for value in row or []:
                if value is None:
                    miles_row.append(float("inf"))
                else:
                    miles_row.append(float(value) * MILES_PER_METER)
            miles_matrix.append(miles_row)
        return miles_matrix

    def directions(self, coords_latlng, objective="distance"):
        if len(coords_latlng) < 2:
            return {
                "leg_miles": [],
                "total_miles": 0.0,
                "geometry_latlng": [],
            }

        preference = "shortest" if str(objective or "").lower() == "distance" else "fastest"
        payload = {
            "coordinates": [self._to_lonlat(value) for value in coords_latlng],
            "instructions": True,
            "units": "m",
            "preference": preference,
            "elevation": False,
            # ZIP-centroid coordinates are often not exactly on a routable edge.
            "radiuses": [self.snap_radius_m for _ in coords_latlng],
        }
        # `driving-hgv` does not support the `/geojson` suffix; use `/json` and decode geometry.
        data = self._post_json(f"/v2/directions/{self.profile}/json", payload)
        routes = data.get("routes") or []
        if not routes:
            raise OpenRouteServiceError("No routes returned from directions API.")
        route = routes[0] or {}
        segments = route.get("segments") or []

        leg_miles = []
        for segment in segments:
            leg_miles.append(float((segment or {}).get("distance") or 0.0) * MILES_PER_METER)

        summary = route.get("summary") or {}
        if summary.get("distance") is not None:
            total_miles = float(summary.get("distance") or 0.0) * MILES_PER_METER
        else:
            total_miles = sum(leg_miles)

        geometry_latlng = self._decode_route_geometry(route.get("geometry"))

        return {
            "leg_miles": leg_miles,
            "total_miles": total_miles,
            "geometry_latlng": geometry_latlng,
        }

    def _decode_route_geometry(self, geometry):
        geometry_latlng = []
        if isinstance(geometry, str) and geometry:
            return self._decode_polyline(geometry)
        if isinstance(geometry, (list, tuple)):
            for point in geometry:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                lon, lat = point[0], point[1]
                geometry_latlng.append([float(lat), float(lon)])
            return geometry_latlng
        return []

    def _decode_polyline(self, encoded, precision=5):
        # Standard Google/ORS polyline decoder -> [[lat, lng], ...]
        if not encoded:
            return []
        index = 0
        lat = 0
        lng = 0
        coordinates = []
        factor = 10 ** precision

        while index < len(encoded):
            result = 0
            shift = 0
            while True:
                if index >= len(encoded):
                    return coordinates
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta_lat = ~(result >> 1) if (result & 1) else (result >> 1)
            lat += delta_lat

            result = 0
            shift = 0
            while True:
                if index >= len(encoded):
                    return coordinates
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta_lng = ~(result >> 1) if (result & 1) else (result >> 1)
            lng += delta_lng

            coordinates.append([lat / factor, lng / factor])

        return coordinates

    def _to_lonlat(self, coords):
        lat, lng = coords
        return [float(lng), float(lat)]

    def _post_json(self, path, payload):
        if not self.api_key:
            raise OpenRouteServiceError("Missing OpenRouteService API key.")

        url = f"{self.BASE_URL}{path}"
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        last_error = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(
                url=url,
                data=body,
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                message = raw.strip() or str(exc)
                last_error = OpenRouteServiceError(
                    f"OpenRouteService HTTP {exc.code} for {path}: {message}"
                )
                if exc.code in {429, 500, 502, 503, 504} and attempt < self.retries:
                    continue
                break
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                last_error = OpenRouteServiceError(
                    f"OpenRouteService request failed for {path}: {exc}"
                )
                if attempt < self.retries:
                    continue
                break

        raise last_error or OpenRouteServiceError(f"OpenRouteService request failed for {path}.")
