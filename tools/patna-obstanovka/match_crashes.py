"""Link geocoded MVR crashes to nearby assessed roads, without guessing at junctions."""
import json

from pyproj import Transformer
from shapely.geometry import MultiLineString, Point
from shapely.ops import transform
from shapely.strtree import STRtree

from db import connect

MAX_DISTANCE_M = 75
AMBIGUITY_M = 15


def rebuild_matches(db):
    risk_count = db.execute("SELECT COUNT(*) FROM risk").fetchone()[0]
    crash_count = db.execute("SELECT COUNT(*) FROM crashes").fetchone()[0]
    if not risk_count or not crash_count:
        return 0
    project = Transformer.from_crs("EPSG:4326", "EPSG:32635", always_xy=True).transform
    ids, lines, ratings = [], [], []
    for row in db.execute("SELECT id,geometry,risk_level FROM risk"):
        try:
            line = transform(project, MultiLineString(json.loads(row["geometry"])))
            if not line.is_empty and line.length:
                ids.append(row["id"])
                lines.append(line)
                ratings.append(row["risk_level"])
        except (ValueError, TypeError):
            continue
    tree = STRtree(lines)
    matches = []
    for row in db.execute("SELECT id,lon,lat FROM crashes"):
        point = transform(project, Point(row["lon"], row["lat"]))
        candidates = tree.query(point.buffer(MAX_DISTANCE_M))
        if not len(candidates):
            continue
        nearest = sorted((point.distance(lines[int(i)]), ids[int(i)], ratings[int(i)]) for i in candidates)
        distance, road_id, rating = nearest[0]
        if distance > MAX_DISTANCE_M:
            continue
        if rating not in {"LOW", "MEDIUM", "HIGH", "VERY HIGH"}:
            continue
        # Parallel lanes and intersections cannot be assigned to one road reliably.
        if len(nearest) > 1 and nearest[1][0] - distance < AMBIGUITY_M and nearest[1][1] != road_id:
            continue
        matches.append((row["id"], road_id, round(distance, 1)))
    with db:
        db.execute("DELETE FROM crash_matches")
        db.executemany("INSERT INTO crash_matches VALUES(?,?,?)", matches)
    return len(matches)


if __name__ == "__main__":
    with connect() as connection:
        print(f"Matched {rebuild_matches(connection)} crashes")
