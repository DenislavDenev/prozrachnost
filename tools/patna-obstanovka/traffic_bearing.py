"""Orient BGToll direction markers along the assessed road geometry."""
import json
import math
import urllib.parse
import urllib.request

from pyproj import Transformer
from shapely.geometry import MultiLineString, Point
from shapely.ops import transform
from shapely.strtree import STRtree

from db import connect

MAX_MATCH_DISTANCE_M = 500


def refresh_bearings(db):
    if not db.execute("SELECT 1 FROM traffic LIMIT 1").fetchone() or not db.execute("SELECT 1 FROM risk LIMIT 1").fetchone():
        return 0
    project = Transformer.from_crs("EPSG:4326", "EPSG:32635", always_xy=True).transform
    lines, layers = [], []
    for row in db.execute("SELECT layer,geometry FROM risk"):
        try:
            line = transform(project, MultiLineString(json.loads(row["geometry"])))
            if line.length >= 10:
                lines.append(line)
                layers.append(row["layer"])
        except (ValueError, TypeError):
            continue
    tree = STRtree(lines)
    result = []
    for row in db.execute("SELECT scp,lat,lon FROM traffic"):
        point = transform(project, Point(row["lon"], row["lat"]))
        candidates = tree.query(point.buffer(MAX_MATCH_DISTANCE_M))
        if not len(candidates):
            continue
        index = min((int(i) for i in candidates), key=lambda i: point.distance(lines[i]))
        line = lines[index]
        if point.distance(line) > MAX_MATCH_DISTANCE_M:
            continue
        position = line.project(point)
        start = line.interpolate(max(0, position - 30))
        end = line.interpolate(min(line.length, position + 30))
        dx, dy = end.x - start.x, end.y - start.y
        if math.hypot(dx, dy) < 2:
            continue
        bearing = math.degrees(math.atan2(dx, dy)) % 360
        # Left lanes are digitised in the opposite direction to the road's kilometre axis.
        if layers[index] in (0, 2):
            bearing = (bearing + 180) % 360
        if str(row["scp"]).endswith("2"):
            bearing = (bearing + 180) % 360
        result.append((round(bearing, 1), row["scp"]))
    with db:
        db.executemany("UPDATE traffic SET bearing=? WHERE scp=?", result)
    return len(result)


def refresh_osm_missing(db):
    """Use nearby OSM road geometry only when the assessed network has no segment."""
    missing = db.execute("SELECT scp,lat,lon FROM traffic WHERE bearing IS NULL").fetchall()
    if not missing:
        return 0
    sites = sorted({(round(row["lat"], 6), round(row["lon"], 6)) for row in missing})
    features = []
    for offset in range(0, len(sites), 10):
        batch = sites[offset:offset + 10]
        clauses = "".join(f'way(around:300,{lat},{lon})["highway"~"^(motorway|trunk|primary|secondary|tertiary)(|_link)$"];' for lat, lon in batch)
        query = f"[out:json][timeout:45];({clauses});out geom;"
        request = urllib.request.Request("https://overpass-api.de/api/interpreter", data=urllib.parse.urlencode({"data": query}).encode(), headers={"User-Agent": "ProzrachnostRoadMap/0.2", "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(request, timeout=55) as response:
                features.extend(json.load(response).get("elements", []))
        except Exception as exc:
            print(f"OSM bearing batch {offset // 10 + 1}: {exc}")
    project = Transformer.from_crs("EPSG:4326", "EPSG:32635", always_xy=True).transform
    lines = []
    for feature in features:
        points = [(point["lon"], point["lat"]) for point in feature.get("geometry", [])]
        if len(points) > 1:
            from shapely.geometry import LineString
            lines.append(transform(project, LineString(points)))
    if not lines:
        return 0
    tree = STRtree(lines)
    updates = []
    for row in missing:
        point = transform(project, Point(row["lon"], row["lat"]))
        candidates = tree.query(point.buffer(300))
        if not len(candidates):
            continue
        line = min((lines[int(i)] for i in candidates), key=point.distance)
        if point.distance(line) > 300:
            continue
        position = line.project(point)
        start = line.interpolate(max(0, position - 30))
        end = line.interpolate(min(line.length, position + 30))
        dx, dy = end.x - start.x, end.y - start.y
        if math.hypot(dx, dy) < 2:
            continue
        bearing = math.degrees(math.atan2(dx, dy)) % 360
        # OSM defines the axis, but not the BGToll kilometre direction.
        if 90 < bearing < 270:
            bearing = (bearing + 180) % 360
        if str(row["scp"]).endswith("2"):
            bearing = (bearing + 180) % 360
        updates.append((round(bearing, 1), row["scp"]))
    with db:
        db.executemany("UPDATE traffic SET bearing=? WHERE scp=?", updates)
    return len(updates)


if __name__ == "__main__":
    with connect() as connection:
        print(f"Oriented {refresh_bearings(connection)} traffic directions from assessed roads")
        print(f"Oriented {refresh_osm_missing(connection)} more from OSM roads")
