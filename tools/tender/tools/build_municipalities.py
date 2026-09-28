"""The outlines for the map (run by hand when the boundaries change), from Eurostat GISCO in the equal-area projection
EPSG:3035, drawn in the frame of Икономика's europe.json (FRAME below), so the map can fly from Bulgaria to Europe
(app/static/europe.json is a copy of Икономика's; AGENTS.md 7):

  - the 265 municipalities: LAU 2024, 1:1 million (Сърница has its own outline);
  - the oblasts (NUTS 3), planning regions (NUTS 2) and macro-regions (NUTS 1): NUTS 2024, 1:1 million.

Simplified to about 80 m. Licence (GISCO): use only for non-commercial purposes and with the credit
"© EuroGeographics for the administrative boundaries" on the map; the page shows it in Bulgarian.

Reads db/ref/municipality.csv (id = the municipality's ЕИК, name_bg, oblast, nuts3 …) and writes
  app/static/bg-municipalities.json  {frame, view: [x, y, w, h] of Bulgaria, shapes: {id: {n, d, o: nuts3}},
                                      oblasts: {nuts3: {n, d}}, regions: {nuts2: {d}}, macros: {nuts1: {d}}, names}
  db/ref/municipality.csv            drawn_as = the id whose outline holds the municipality (now always its own)
A municipality is matched by its name (case aside) within its oblast: the oblast of a LAU outline is the NUTS 3
region that holds its inner point (two municipalities are called Бяла). Needs shapely (dev only).

    python tools/build_municipalities.py
"""
import csv
import json
import urllib.request
from pathlib import Path

from shapely.geometry import shape

ROOT = Path(__file__).resolve().parent.parent
GISCO = "https://gisco-services.ec.europa.eu/distribution/v2"
LAU = GISCO + "/lau/geojson/LAU_RG_01M_2024_3035.geojson"
NUTS = GISCO + "/nuts/geojson/NUTS_RG_01M_2024_3035_LEVL_{}.geojson"
NUTS_NAME = {"BG31": "Северозападен", "BG32": "Северен централен", "BG33": "Североизточен", "BG34": "Югоизточен",
             "BG41": "Югозападен", "BG42": "Южен централен",
             "BG3": "Северна и Югоизточна България", "BG4": "Югозападна и Южна централна България"}
# the official name in LAU -> the name the municipality uses as a buyer (municipality.csv)
ALIAS = {"Добрич-селска": "Добричка"}
FRAME = {"x0": 2_500_000, "y1": 5_450_000, "s": 1000 / 4_950_000}   # = the "frame" of europe.json (Икономика)
SIMPLIFY = 80   # metres


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Prozrachnost/tender (+https://github.com/DenislavDenev/prozrachnost)"})
    return json.loads(urllib.request.urlopen(req, timeout=600).read())["features"]


def main():
    nuts = {lvl: {f["properties"]["NUTS_ID"]: shape(f["geometry"]) for f in get(NUTS.format(lvl))
                  if f["properties"]["CNTR_CODE"] == "BG"} for lvl in (0, 1, 2, 3)}
    x0, y1, s = FRAME["x0"], FRAME["y1"], FRAME["s"]
    bx0, by0, bx1, by1 = nuts[0]["BG"].bounds
    mx, my = (bx1 - bx0) * 0.02, (by1 - by0) * 0.03
    view = [round((bx0 - mx - x0) * s, 2), round((y1 - by1 - my) * s, 2), round((bx1 - bx0 + 2 * mx) * s, 2), round((by1 - by0 + 2 * my) * s, 2)]

    def svg(g):
        g = g.simplify(SIMPLIFY, preserve_topology=True)
        out = []
        for p in ([g] if g.geom_type == "Polygon" else list(g.geoms)):
            for ring in [p.exterior, *p.interiors]:
                pts, last = [], None
                for x, y in ring.coords:
                    q = (round((x - x0) * s, 2), round((y1 - y) * s, 2))   # Bulgaria is ~110 units wide
                    if q != last:
                        pts.append(q)
                        last = q
                if len(pts) >= 4:
                    out.append("M" + "L".join(f"{a:g},{b:g}" for a, b in pts) + "Z")
        return "".join(out)

    path = ROOT / "db" / "ref" / "municipality.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    by_name = {(r["name_bg"].lower(), r["nuts3"]): r for r in rows}
    shapes, missing = {}, []
    for f in get(LAU):
        if f["properties"]["CNTR_CODE"] != "BG":
            continue
        g = shape(f["geometry"])
        n3 = next(c for c, poly in nuts[3].items() if poly.contains(g.representative_point()))
        name = f["properties"]["LAU_NAME"]
        r = by_name.get((ALIAS.get(name, name).lower(), n3))
        if not r:
            missing.append(f["properties"]["LAU_NAME"])
            continue
        shapes[r["id"]] = {"n": r["name_bg"], "d": svg(g), "o": n3}
    assert not missing and len(shapes) == len(rows) == 265, (missing, len(shapes), len(rows))
    for r in rows:
        r["drawn_as"] = r["id"]   # every municipality has its own outline in LAU 2024, Сърница included
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    oblast = {r["nuts3"]: r["oblast"] for r in rows}
    out = {"frame": FRAME, "view": view, "shapes": shapes,
           "oblasts": {c: {"n": oblast[c], "d": svg(g)} for c, g in sorted(nuts[3].items())},
           "regions": {c: {"d": svg(g)} for c, g in sorted(nuts[2].items())},
           "macros": {c: {"d": svg(g)} for c, g in sorted(nuts[1].items())}, "names": NUTS_NAME}
    (ROOT / "app" / "static" / "bg-municipalities.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")),
                                                                    encoding="utf-8")
    print(len(shapes), "municipalities,", len(out["oblasts"]), "oblasts,", len(out["regions"]), "regions,", len(out["macros"]), "macro-regions")


if __name__ == "__main__":
    main()
