"""Build a light, factual SVG preview from geoBoundaries and the road database."""
import json
import math
import sqlite3
import sys
from pathlib import Path

from shapely.geometry import LineString, shape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ROAD_DB = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "patna-obstanovka" / "road.db"
DEST = ROOT / "static" / "bulgaria-roads.svg"

country_data = json.loads((ROOT / "data" / "bg-adm0.geojson").read_text(encoding="utf-8"))
country = shape(country_data["features"][0]["geometry"]).simplify(0.004, preserve_topology=True)
min_lon, min_lat, max_lon, max_lat = country.bounds
scale = 720 / ((max_lon - min_lon) * math.cos(math.radians(42.7)))
height = round((max_lat - min_lat) * scale)


def point(lon, lat):
    return ((lon - min_lon) * math.cos(math.radians(42.7)) * scale,
            (max_lat - lat) * scale)


def svg_path(geom):
    if geom.geom_type == "Polygon":
        rings = [geom.exterior, *geom.interiors]
    elif geom.geom_type == "MultiPolygon":
        rings = [ring for poly in geom.geoms for ring in [poly.exterior, *poly.interiors]]
    elif geom.geom_type == "MultiLineString":
        rings = geom.geoms
    else:
        rings = [geom]
    out = []
    for ring in rings:
        coords = list(ring.coords)
        if len(coords) < 2:
            continue
        xy = [point(lon, lat) for lon, lat in coords]
        out.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in xy)
                   + ("Z" if "Polygon" in geom.geom_type else ""))
    return "".join(out)


conn = sqlite3.connect(ROAD_DB)
rows = conn.execute("SELECT road, geometry FROM risk WHERE road LIKE 'A-%' OR road LIKE 'I-%'").fetchall()
roads = []
highlights = []
for road, raw in rows:
    try:
        lines = json.loads(raw)
    except (ValueError, TypeError):
        continue
    for coords in lines:
        if len(coords) > 1:
            line = LineString(coords).simplify(0.003, preserve_topology=False)
            if line.length > 0.008:
                (highlights if road in {"A-1", "A-2", "I-4"} else roads).append(line)

road_paths = "".join(f'<path d="{svg_path(line)}"/>' for line in roads)
highlight_paths = "".join(f'<path d="{svg_path(line)}"/>' for line in highlights)
towns = [("София", 23.3219, 42.6977), ("Пловдив", 24.7453, 42.1354),
         ("Варна", 27.9147, 43.2141), ("Бургас", 27.4678, 42.5048)]
town_svg = "".join(
    f'<circle cx="{point(lon, lat)[0]:.1f}" cy="{point(lon, lat)[1]:.1f}" r="2.7"/>'
    f'<text x="{point(lon, lat)[0] + 7:.1f}" y="{point(lon, lat)[1] - 6:.1f}">{name}</text>'
    for name, lon, lat in towns
)
svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="-20 -25 760 {height + 50}" role="img" aria-labelledby="title desc">
<title id="title">Карта на България с оценени пътни участъци</title>
<desc id="desc">Реален контур на България от geoBoundaries и пътища от публикуваната оценка на ДАБДП за 2023 г. Светлите линии са основни пътища, тъмните подчертават магистралите Тракия и Хемус и път I-4. Не изобразява трафика в момента.</desc>
<path fill="#edf3ef" stroke="#9bb7a5" stroke-width="1.6" d="{svg_path(country)}"/>
<g fill="none" stroke="#8bb59b" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" opacity=".92">{road_paths}</g>
<g fill="none" stroke="#08785c" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round">{highlight_paths}</g>
<g fill="#12372c" font-size="15" font-family="Sofia Sans, Arial, sans-serif">{town_svg}</g>
</svg>'''
DEST.write_text(svg, encoding="utf-8")
print(f"{DEST}: {DEST.stat().st_size:,} bytes; {len(rows)} road records")
