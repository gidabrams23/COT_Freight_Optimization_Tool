import json
import math
from pathlib import Path

import sqlite3

ZIP_COORDS_PATH = Path(__file__).resolve().parent.parent / "static" / "data" / "zip_coords.json"
_ZIP_COORDS_CACHE = None
_PLANT_COORDS_CACHE = None

PLANT_COORDS = {
    "GA": (34.43611, -83.10639),
    "TX": (31.66222, -96.49722),
    "VA": (38.09389, -76.82611),
    "IA": (41.55944, -95.90250),
    "OR": (44.13944, -123.05889),
    "NV": (40.96833, -117.72667),
}


def invalidate_coordinate_caches(zip_coords=False, plant_coords=False):
    global _ZIP_COORDS_CACHE, _PLANT_COORDS_CACHE
    if zip_coords:
        _ZIP_COORDS_CACHE = None
    if plant_coords:
        _PLANT_COORDS_CACHE = None


def load_zip_coordinates(path=ZIP_COORDS_PATH):
    global _ZIP_COORDS_CACHE
    if _ZIP_COORDS_CACHE is not None:
        return _ZIP_COORDS_CACHE

    coords = _load_zip_coords_from_db()
    if coords:
        _ZIP_COORDS_CACHE = coords
        return coords

    if not path.exists():
        _ZIP_COORDS_CACHE = {}
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    coords = {}
    for zip_code, value in data.items():
        normalized = normalize_zip(zip_code)
        if not normalized:
            continue
        if isinstance(value, (list, tuple)) and len(value) == 2:
            coords[normalized] = (float(value[0]), float(value[1]))
        elif isinstance(value, dict) and "lat" in value and "lon" in value:
            coords[normalized] = (float(value["lat"]), float(value["lon"]))
    _ZIP_COORDS_CACHE = coords
    return coords


def _load_zip_coords_from_db():
    try:
        import db
    except Exception:
        return {}

    try:
        with db.get_connection() as connection:
            rows = connection.execute(
                "SELECT zip, lat, lng FROM zip_coordinates"
            ).fetchall()
    except sqlite3.Error:
        return {}

    coords = {}
    for row in rows:
        zip_code = normalize_zip(row["zip"])
        if not zip_code:
            continue
        coords[zip_code] = (float(row["lat"]), float(row["lng"]))
    return coords


def haversine_distance(zip1, zip2, zip_coords_dict):
    coords1 = zip_coords_dict.get(normalize_zip(zip1))
    coords2 = zip_coords_dict.get(normalize_zip(zip2))
    if not coords1 or not coords2:
        return float("inf")
    return haversine_distance_coords(coords1, coords2)


def haversine_distance_coords(coords1, coords2):
    lat1, lon1 = coords1
    lat2, lon2 = coords2
    r = 3959
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))
    return r * c


def nearest_neighbor_route(origin_coords, destinations, zip_coords_dict):
    if not origin_coords:
        return []

    remaining = [
        normalize_zip(zip_code)
        for zip_code in destinations
        if normalize_zip(zip_code) in zip_coords_dict
    ]
    route = []
    current_coords = origin_coords

    while remaining:
        next_zip = min(
            remaining,
            key=lambda zip_code: haversine_distance_coords(
                current_coords, zip_coords_dict[zip_code]
            ),
        )
        route.append(next_zip)
        current_coords = zip_coords_dict[next_zip]
        remaining.remove(next_zip)

    unknown = [
        normalize_zip(zip_code)
        for zip_code in destinations
        if normalize_zip(zip_code) not in zip_coords_dict
    ]
    route.extend(unknown)
    return route


def plant_coords_for_code(plant_code):
    global _PLANT_COORDS_CACHE
    if _PLANT_COORDS_CACHE is None:
        _PLANT_COORDS_CACHE = _load_plant_coords_from_db() or dict(PLANT_COORDS)
    return _PLANT_COORDS_CACHE.get(plant_code)


def _load_plant_coords_from_db():
    try:
        import db
    except Exception:
        return {}

    try:
        with db.get_connection() as connection:
            rows = connection.execute(
                "SELECT plant_code, lat, lng FROM plants"
            ).fetchall()
    except sqlite3.Error:
        return {}

    coords = {}
    for row in rows:
        code = (row["plant_code"] or "").strip().upper()
        if not code:
            continue
        coords[code] = (float(row["lat"]), float(row["lng"]))
    return coords


def normalize_zip(value):
    if value is None:
        return ""

    if isinstance(value, (int,)):
        digits = str(value)
    elif isinstance(value, float):
        if math.isnan(value):
            return ""
        digits = str(int(value))
    else:
        raw = str(value).strip()
        if not raw:
            return ""
        if "-" in raw:
            raw = raw.split("-", 1)[0].strip()
        if raw.endswith(".0") and raw.replace(".", "").isdigit():
            raw = raw.split(".", 1)[0]
        digits = "".join(ch for ch in raw if ch.isdigit())

    if not digits:
        return ""
    return digits.zfill(5)[:5]
