"""The countries of the world for the map's "Свят" (run by hand when the boundaries change): Eurostat GISCO, the
countries of 2024 at 1:60 million, in the equal-area Equal Earth projection (Šavrič, Patterson, Jenny 2018), which
shows every country at its true size.

Licence (GISCO): use only for non-commercial purposes and with the credit
"© EuroGeographics for the administrative boundaries" on the map; the page shows it in Bulgarian.

Writes app/static/world.json: {"h", "shapes": {country: SVG path}, "names": {country: English name},
"europe": [x, y, w, h]} in a frame 1000 wide; "europe" is the box of Europe in it, where the map hands over to the
map of Europe (another projection) and back. Needs shapely (dev only).

    python tools/build_world_map.py
"""
import json
import math
import urllib.request
from pathlib import Path

from shapely.geometry import shape

ROOT = Path(__file__).resolve().parent.parent
URL = "https://gisco-services.ec.europa.eu/distribution/v2/countries/geojson/CNTR_RG_60M_2024_4326.geojson"
A1, A2, A3, A4, M = 1.340264, -0.081106, 0.000893, 0.003796, math.sqrt(3) / 2
W = 1000
EUROPE = (-25, 34, 45, 72)   # lon0, lat0, lon1, lat1 of the parts that make "Europe" for the handover box


def equal_earth(lon, lat):
    t = math.asin(M * math.sin(math.radians(lat)))
    t2, t6 = t * t, t ** 6
    x = math.radians(lon) * math.cos(t) / (M * (A1 + 3 * A2 * t2 + t6 * (7 * A3 + 9 * A4 * t2)))
    y = t * (A1 + A2 * t2 + t6 * (A3 + A4 * t2))
    return x, y


XMAX = equal_earth(180, 0)[0]
YMAX = equal_earth(0, 90)[1]
S = W / (2 * XMAX)


def xy(lon, lat):
    x, y = equal_earth(lon, lat)
    return round((x + XMAX) * S, 1), round((YMAX - y) * S, 1)


def main():
    req = urllib.request.Request(URL, headers={"User-Agent": "Prozrachnost/tender (+https://github.com/DenislavDenev/prozrachnost)"})
    feats = json.loads(urllib.request.urlopen(req, timeout=300).read())["features"]
    out = {"h": round(2 * YMAX * S), "shapes": {}, "names": {}}
    ex0 = ey0 = math.inf
    ex1 = ey1 = -math.inf
    for f in feats:
        g = shape(f["geometry"])
        polys = [g] if g.geom_type == "Polygon" else list(g.geoms)
        d = []
        for p in polys:
            for ring in [p.exterior, *p.interiors]:
                pts, last = [], None
                for lon, lat in ring.coords:
                    q = xy(lon, lat)
                    if q != last:
                        pts.append(q)
                        last = q
                if len(pts) >= 4:
                    d.append("M" + "L".join(f"{a:g},{b:g}" for a, b in pts) + "Z")
            c = p.representative_point()
            if EUROPE[0] <= c.x <= EUROPE[2] and EUROPE[1] <= c.y <= EUROPE[3]:
                for lon, lat in p.exterior.coords:
                    x, y = xy(lon, lat)
                    ex0, ex1, ey0, ey1 = min(ex0, x), max(ex1, x), min(ey0, y), max(ey1, y)
        if d:
            cid = f["properties"]["CNTR_ID"]
            out["shapes"][cid] = "".join(d)
            out["names"][cid] = f["properties"]["NAME_ENGL"]
    out["europe"] = [round(ex0, 1), round(ey0, 1), round(ex1 - ex0, 1), round(ey1 - ey0, 1)]
    assert {"BG", "US", "CN", "IL", "AU", "UK", "EL"} <= out["shapes"].keys(), sorted(out["shapes"])[:10]
    (ROOT / "app" / "static" / "world.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(len(out["shapes"]), "countries; Europe box", out["europe"])


if __name__ == "__main__":
    main()
