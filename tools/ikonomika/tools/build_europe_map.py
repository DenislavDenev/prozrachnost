"""The outlines of Europe for the map (run by hand when NUTS changes): Eurostat GISCO, NUTS 2024, scale 1:20 million,
already in the equal-area projection EPSG:3035, levels 0 (countries) to 3.

Licence (GISCO, statistical units): use only for non-commercial purposes and with the credit
"© EuroGeographics for the administrative boundaries" on the map; the page shows it in Bulgarian.

Writes app/static/europe.json: {"h": height, "names": {code: Latin name}, "0": {code: SVG path}, ... "3": {...}},
1000 wide. The overseas parts (the Canaries, the Azores, Madeira, French overseas regions, Svalbard) fall outside
the frame and are left out, as on the maps of Eurostat.

    python tools/build_europe_map.py
"""
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/NUTS_RG_20M_2024_3035_LEVL_{}.geojson"
X0, X1, Y0, Y1 = 2_500_000, 7_450_000, 1_380_000, 5_450_000   # EPSG:3035 metres: Iceland to Türkiye, Cyprus to North Cape
W = 1000
S = W / (X1 - X0)


def rings(g):
    return g["coordinates"] if g["type"] == "Polygon" else [r for p in g["coordinates"] for r in p]


def inside(ring):
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    return X0 <= (min(xs) + max(xs)) / 2 <= X1 and Y0 <= (min(ys) + max(ys)) / 2 <= Y1


def path(ring):
    pts, last = [], None
    for x, y in ring:
        p = (round((x - X0) * S, 1), round((Y1 - y) * S, 1))
        if p != last:
            pts.append(p)
            last = p
    if len(pts) < 4:
        return ""   # smaller than a pixel on the map
    return "M" + "L".join(f"{x:g},{y:g}" for x, y in pts) + "Z"


def main():
    out = {"h": round((Y1 - Y0) * S), "names": {}}
    for level in range(4):
        req = urllib.request.Request(URL.format(level), headers={"User-Agent": "Prozrachnost/ikonomika (+https://github.com/DenislavDenev/prozrachnost)"})
        feats = json.loads(urllib.request.urlopen(req, timeout=120).read())["features"]
        shapes = {}
        for f in feats:
            code = f["properties"]["NUTS_ID"]
            d = "".join(path(r) for r in rings(f["geometry"]) if inside(r))
            if d:
                shapes[code] = d
                out["names"][code] = f["properties"]["NAME_LATN"]
        out[str(level)] = shapes
        print(f"level {level}: {len(shapes)} of {len(feats)} drawn")
    assert {"BG", "DE", "NO", "TR"} <= out["0"].keys() and len(out["3"]) > 1000
    (ROOT / "app" / "static" / "europe.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
