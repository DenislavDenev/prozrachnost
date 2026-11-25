"""What the map derives from the sources: the direction of each БГТОЛ arrow, and the crashes linked to the assessed
road sections. Both work in metres (UTM 35N) on the current clean rows.

The geometry libraries (shapely, numpy, pyproj: about 20 MB) are imported only when there is something to compute:
the read every 15 minutes runs beside the heavy jobs without their lock and stays under 100 MB."""
import json
import math
import urllib.parse

from . import http

MAX_BEARING_M = 500       # a БГТОЛ point takes the direction of an assessed section this close
MAX_OSM_M = 300           # or of an OSM road this close, when no section is
MAX_MATCH_M = 75          # a crash is linked to a section this close
AMBIGUITY_M = 15          # unless another section is less than this further: junctions and parallel lanes
RATINGS = {'LOW', 'MEDIUM', 'HIGH', 'VERY HIGH'}
OVERPASS = 'https://overpass-api.de/api/interpreter'


def _project():
    from pyproj import Transformer
    return Transformer.from_crs('EPSG:4326', 'EPSG:32635', always_xy=True).transform


def _sections(conn, project, min_length=0.0):
    from shapely.geometry import MultiLineString
    from shapely.ops import transform
    ids, lines, layers, ratings = [], [], [], []
    for section_id, layer, rating, geometry in conn.execute(
            'SELECT section_id, layer, risk_level, geometry FROM patna_obstanovka.risk_section WHERE superseded_at IS NULL ORDER BY position'):
        try:
            line = transform(project, MultiLineString(json.loads(geometry)))
        except (ValueError, TypeError):
            continue
        if line.is_empty or not line.length or line.length < min_length:
            continue
        ids.append(section_id)
        lines.append(line)
        layers.append(layer)
        ratings.append(rating)
    return ids, lines, layers, ratings


def _bearing(line, point):
    position = line.project(point)
    start = line.interpolate(max(0, position - 30))
    end = line.interpolate(min(line.length, position + 30))
    dx, dy = end.x - start.x, end.y - start.y
    if math.hypot(dx, dy) < 2:
        return None
    return math.degrees(math.atan2(dx, dy)) % 360


def bearings_from_sections(conn, everything=False):
    """The arrow of each БГТОЛ point from the nearest assessed section. Only the points that are new or moved, unless
    everything (after a new read of the sections). A point with no section near gets method 'none' and waits for OSM."""
    stations = conn.execute('''SELECT t.scp, t.lat, t.lon FROM patna_obstanovka.traffic t
        LEFT JOIN patna_obstanovka.station_bearing b USING (scp)
        WHERE %s OR b.scp IS NULL OR (b.lat, b.lon) <> (t.lat, t.lon)''', (everything,)).fetchall()
    if not stations:
        return 0
    from shapely.geometry import Point
    from shapely.ops import transform
    from shapely.strtree import STRtree
    project = _project()
    _, lines, layers, _ = _sections(conn, project, min_length=10)
    tree = STRtree(lines) if lines else None
    result = []
    for scp, lat, lon in stations:
        point = transform(project, Point(lon, lat))
        candidates = tree.query(point.buffer(MAX_BEARING_M)) if tree else []
        bearing = None
        if len(candidates):
            index = min((int(i) for i in candidates), key=lambda i: point.distance(lines[i]))
            if point.distance(lines[index]) <= MAX_BEARING_M:
                bearing = _bearing(lines[index], point)
                if bearing is not None:
                    # left lanes are digitised against the kilometre axis of the road
                    if layers[index] in (0, 2):
                        bearing = (bearing + 180) % 360
                    if scp.endswith('2'):
                        bearing = (bearing + 180) % 360
                    bearing = round(bearing, 1)
        result.append((scp, lat, lon, bearing, 'risk' if bearing is not None else 'none'))
    with conn.transaction():
        # a point that OSM oriented keeps that, unless a section now gives it a direction or the point moved
        conn.cursor().executemany('''INSERT INTO patna_obstanovka.station_bearing (scp, lat, lon, bearing, method)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (scp) DO UPDATE SET lat = EXCLUDED.lat, lon = EXCLUDED.lon, bearing = EXCLUDED.bearing,
              method = EXCLUDED.method, computed_at = now()
            WHERE EXCLUDED.method = 'risk' OR station_bearing.method NOT IN ('osm', 'road.db')
              OR (station_bearing.lat, station_bearing.lon) <> (EXCLUDED.lat, EXCLUDED.lon)''', result)
    return sum(1 for r in result if r[3] is not None)


def bearings_from_osm(conn, get=http.get):
    """The points without a section near them, from OpenStreetMap roads (Overpass, ten points a request). OSM gives
    the axis of the road, not the kilometre direction of БГТОЛ. A failed request is counted, not fatal."""
    missing = conn.execute('''SELECT b.scp, b.lat, b.lon FROM patna_obstanovka.station_bearing b
        JOIN patna_obstanovka.traffic t USING (scp) WHERE b.bearing IS NULL''').fetchall()
    if not missing:
        return 0, 0
    sites = sorted({(round(lat, 6), round(lon, 6)) for _, lat, lon in missing})
    features, failed = [], 0
    for offset in range(0, len(sites), 10):
        clauses = ''.join(f'way(around:300,{lat},{lon})["highway"~"^(motorway|trunk|primary|secondary|tertiary)(|_link)$"];'
                          for lat, lon in sites[offset:offset + 10])
        body = urllib.parse.urlencode({'data': f'[out:json][timeout:45];({clauses});out geom;'}).encode()
        try:
            features.extend(json.loads(get(OVERPASS, data=body, timeout=55, retries=2,
                                           headers={'Content-Type': 'application/x-www-form-urlencoded'})).get('elements', []))
        except Exception:   # one batch less: those points stay without an arrow until the next read of the sections
            failed += 1
    from shapely.geometry import LineString, Point
    from shapely.ops import transform
    from shapely.strtree import STRtree
    project = _project()
    lines = [transform(project, LineString(pts)) for f in features
             if len(pts := [(p['lon'], p['lat']) for p in f.get('geometry', [])]) > 1]
    if not lines:
        return 0, failed
    tree = STRtree(lines)
    updates = []
    for scp, lat, lon in missing:
        point = transform(project, Point(lon, lat))
        candidates = tree.query(point.buffer(MAX_OSM_M))
        if not len(candidates):
            continue
        line = min((lines[int(i)] for i in candidates), key=point.distance)
        if point.distance(line) > MAX_OSM_M:
            continue
        bearing = _bearing(line, point)
        if bearing is None:
            continue
        if 90 < bearing < 270:
            bearing = (bearing + 180) % 360
        if scp.endswith('2'):
            bearing = (bearing + 180) % 360
        updates.append((round(bearing, 1), scp))
    with conn.transaction():
        conn.cursor().executemany("UPDATE patna_obstanovka.station_bearing SET bearing = %s, method = 'osm', computed_at = now() WHERE scp = %s",
                                  updates)
    return len(updates), failed


def crash_matches(conn):
    """(crash key, section, distance in m) for every current crash that is near exactly one rated section."""
    from shapely.geometry import Point
    from shapely.ops import transform
    from shapely.strtree import STRtree
    project = _project()
    ids, lines, _, ratings = _sections(conn, project)
    crashes = conn.execute('SELECT crash_key, lon, lat FROM patna_obstanovka.crash WHERE superseded_at IS NULL ORDER BY row_no').fetchall()
    if not lines or not crashes:
        return []
    tree = STRtree(lines)
    matches = []
    for key, lon, lat in crashes:
        point = transform(project, Point(lon, lat))
        candidates = tree.query(point.buffer(MAX_MATCH_M))
        if not len(candidates):
            continue
        nearest = sorted((point.distance(lines[int(i)]), ids[int(i)], ratings[int(i)]) for i in candidates)
        distance, section, rating = nearest[0]
        if distance > MAX_MATCH_M or rating not in RATINGS:
            continue
        if len(nearest) > 1 and nearest[1][0] - distance < AMBIGUITY_M and nearest[1][1] != section:
            continue
        matches.append((key, section, round(distance, 1)))
    return matches
