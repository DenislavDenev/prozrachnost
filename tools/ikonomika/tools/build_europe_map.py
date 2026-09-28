"""The outlines for the map (run by hand when NUTS changes), all from Eurostat GISCO in the equal-area projection
EPSG:3035, so Bulgaria and Europe share one plane and the map can fly from one to the other:

  - the countries of 2024 at 1:20 million, the grey ground under the map (Belarus, Moldova, the United Kingdom …
    have no NUTS regions and are only here);
  - NUTS 2024 levels 0 (countries) to 3 at 1:20 million, the map of Europe;
  - NUTS 2024 of Bulgaria at 1:1 million, levels 0 to 3, the map of Bulgaria (simplified to about 100 m).

Licence (GISCO): use only for non-commercial purposes and with the credit
"© EuroGeographics for the administrative boundaries" on the map; the page shows it in Bulgarian.

Writes app/static/europe.json: {"h", "frame": {x0, y1, s}, "names": {NUTS code: Latin name}, "world": {country: path}, "0".."3": {code: path},
"bg": {"0".."3": {code: path}}}, in a frame 1000 wide. Parts outside the frame (the Canaries, the Azores, Madeira,
the French overseas regions, Svalbard, Asia) are cut off, as on the maps of Eurostat. Needs shapely (dev only).

    python tools/build_europe_map.py
"""
import json
import urllib.request
from pathlib import Path

from shapely.geometry import box, shape

ROOT = Path(__file__).resolve().parent.parent
GISCO = "https://gisco-services.ec.europa.eu/distribution/v2"
NUTS = GISCO + "/nuts/geojson/NUTS_RG_{}_2024_3035_LEVL_{}.geojson"
WORLD = GISCO + "/countries/geojson/CNTR_RG_20M_2024_3035.geojson"
X0, X1, Y0, Y1 = 2_500_000, 7_450_000, 1_380_000, 5_450_000   # EPSG:3035 metres: Iceland to Türkiye, Cyprus to North Cape
FRAME = box(X0, Y0, X1, Y1)
W = 1000
S = W / (X1 - X0)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Prozrachnost/ikonomika (+https://github.com/DenislavDenev/prozrachnost)"})
    return json.loads(urllib.request.urlopen(req, timeout=300).read())["features"]


def svg(geom, digits=1, simplify=0):
    """SVG path of a geometry cut to the frame, in map units (1000 wide); '' when nothing is left."""
    g = geom.intersection(FRAME)
    if simplify:
        g = g.simplify(simplify, preserve_topology=True)
    polys = [g] if g.geom_type == "Polygon" else [p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon"]
    out = []
    for p in polys:
        for ring in [p.exterior, *p.interiors]:
            pts, last = [], None
            for x, y in ring.coords:
                q = (round((x - X0) * S, digits), round((Y1 - y) * S, digits))
                if q != last:
                    pts.append(q)
                    last = q
            if len(pts) >= 4:
                out.append("M" + "L".join(f"{a:g},{b:g}" for a, b in pts) + "Z")
    return "".join(out)


def main():
    # the frame: map x = (X - x0) * s, map y = (y1 - Y) * s for EPSG:3035 metres; Тендер draws on the same plane
    out = {"h": round((Y1 - Y0) * S), "frame": {"x0": X0, "y1": Y1, "s": S}, "names": {}, "world": {}, "bg": {}}
    for f in get(WORLD):
        d = svg(shape(f["geometry"]))
        if d:
            out["world"][f["properties"]["CNTR_ID"]] = d
    print("countries in the frame:", len(out["world"]))
    for level in range(4):
        feats = get(NUTS.format("20M", level))
        out[str(level)] = {}
        for f in feats:
            code, d = f["properties"]["NUTS_ID"], svg(shape(f["geometry"]))
            if d:
                out[str(level)][code] = d
                out["names"][code] = f["properties"]["NAME_LATN"]
        # Bulgaria in detail: 1:1 million, simplified to 100 m, two decimals (the map zooms in about 9 times)
        out["bg"][str(level)] = {f["properties"]["NUTS_ID"]: svg(shape(f["geometry"]), 2, 100)
                                 for f in get(NUTS.format("01M", level)) if f["properties"]["CNTR_CODE"] == "BG"}
        print(f"level {level}: {len(out[str(level)])} of {len(feats)} drawn, Bulgaria {len(out['bg'][str(level)])}")
    assert {"BG", "DE", "NO", "TR"} <= out["0"].keys() and {"BY", "MD", "UK"} <= out["world"].keys()
    assert len(out["3"]) > 1000 and len(out["bg"]["3"]) == 28 and len(out["bg"]["2"]) == 6 and len(out["bg"]["1"]) == 2
    (ROOT / "app" / "static" / "europe.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
