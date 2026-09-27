"""Build the municipality reference used by the map (run by hand when the boundaries change).

Input: geoBoundaries BGR ADM2 (265 municipalities, public domain, commit 9469f09 of
github.com/wmgeolab/geoBoundaries) and the municipality buyers ("ОБЩИНА X") from live.buyer.
Output:
  db/ref/municipality.csv          id (the municipality's ЕИК), name_bg, name_en
  app/static/bg-municipalities.json {id: {n: name_bg, d: SVG path}}   (pre-projected, 1000 wide)

Source quirks handled here: two municipalities are called Бяла (told apart by position), Златица
comes as two polygons, and Сърница (split from Велинград in 2015) has no polygon of its own, so it
is drawn inside Велинград and its contracts are added there.

  python tools/build_municipalities.py <geojson> <buyers.txt: eik|name per line>

Areas above the municipality (the map's level selector) come from geoBoundaries BGR ADM1 (28 oblasts,
same commit and licence): each municipality goes to the oblast holding most of its boundary points, and
the oblast outlines are added to the JSON (key "oblasts"), with the planning regions and macro-regions
dissolved from them (keys "regions", "macros"; needs shapely). NUTS codes of the oblasts are in OBLAST
below (NUTS 2024; checked on 2026-09-26 by placing each Eurostat GISCO NUTS 3 label point in the ADM1
polygons). Adds the columns oblast, nuts3, nuts2, nuts1 to municipality.csv:

  python tools/build_municipalities.py --areas <ADM1 geojson> <ADM2 geojson>
"""
import csv
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAT = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
       "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
       "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sht", "ъ": "a", "ь": "y", "ю": "yu", "я": "ya"}
# geoBoundaries (Wikimedia) spellings that the official transliteration does not produce
ALIAS = {"Stolichna": "Столична", "Ruzhinsi": "Ружинци", "Georgi Bamyanovo": "Георги Дамяново",
         "Strumyarni": "Струмяни"}


def translit(s):
    return "".join(LAT.get(ch, ch) for ch in s.lower())


def key(s):
    return re.sub(r"[^a-z]", "", s.lower())


def muni_name(buyer_name):
    """'ОБЩИНА - СТАРА ЗАГОРА' -> 'Стара Загора'; None for joint buyers or non-municipalities."""
    if ";" in buyer_name:
        return None
    m = re.match(r'^"?ОБЩИНА\s*[-–]?\s*(.+?)"?$', buyer_name.strip(), re.I)
    if m:
        return re.sub(r"^Град\s+", "", m.group(1).strip().title())
    return "Столична" if buyer_name.strip().upper() == "СТОЛИЧНА ОБЩИНА" else None


# ISO 3166-2:BG -> oblast, NUTS 3, NUTS 2 (район за планиране), NUTS 1 (макрорайон)
OBLAST = {
    "BG-05": ("Видин", "BG311"), "BG-12": ("Монтана", "BG312"), "BG-06": ("Враца", "BG313"), "BG-15": ("Плевен", "BG314"),
    "BG-11": ("Ловеч", "BG315"), "BG-04": ("Велико Търново", "BG321"), "BG-07": ("Габрово", "BG322"), "BG-18": ("Русе", "BG323"),
    "BG-17": ("Разград", "BG324"), "BG-19": ("Силистра", "BG325"), "BG-03": ("Варна", "BG331"), "BG-08": ("Добрич", "BG332"),
    "BG-27": ("Шумен", "BG333"), "BG-25": ("Търговище", "BG334"), "BG-02": ("Бургас", "BG341"), "BG-20": ("Сливен", "BG342"),
    "BG-28": ("Ямбол", "BG343"), "BG-24": ("Стара Загора", "BG344"), "BG-22": ("София (столица)", "BG411"),
    "BG-23": ("София област", "BG412"), "BG-01": ("Благоевград", "BG413"), "BG-14": ("Перник", "BG414"),
    "BG-10": ("Кюстендил", "BG415"), "BG-16": ("Пловдив", "BG421"), "BG-26": ("Хасково", "BG422"),
    "BG-13": ("Пазарджик", "BG423"), "BG-21": ("Смолян", "BG424"), "BG-09": ("Кърджали", "BG425"),
}
NUTS_NAME = {"BG31": "Северозападен", "BG32": "Северен централен", "BG33": "Североизточен", "BG34": "Югоизточен",
             "BG41": "Югозападен", "BG42": "Южен централен",
             "BG3": "Северна и Югоизточна България", "BG4": "Югозападна и Южна централна България"}

BYALA_EAST = "000093435"   # Бяла, област Варна; the western Бяла (област Русе) is 000530493
BYALA = {BYALA_EAST: "BG-03", "000530493": "BG-18"}  # two municipalities share the name: oblast by ЕИК
EXTRA = {"176806228": "Сърница"}  # ЕИК -> name, municipalities without a polygon (see docstring)


def main(geojson, buyers_txt):
    feats = json.loads(Path(geojson).read_text(encoding="utf-8"))["features"]
    buyers = {}
    for line in Path(buyers_txt).read_text(encoding="utf-8").splitlines():
        eik, _, name = line.partition("|")
        n = muni_name(name)
        if n and ";" not in eik:
            buyers.setdefault(key(translit(n)), (n, eik))
    rows, missing = [], []
    for f in feats:
        en = f["properties"]["shapeName"]
        hit = buyers.get(key(translit(ALIAS[en])) if en in ALIAS else key(en))
        if not hit:
            missing.append(en)
        if hit and hit[0] == "Бяла":
            ring = f["geometry"]["coordinates"][0]
            if sum(p[0] for p in ring) / len(ring) > 26.5:
                hit = ("Бяла", BYALA_EAST)
        rows.append((hit[1] if hit else "", hit[0] if hit else "", en))
    # equirectangular with cos(mean latitude); Bulgaria is small enough for this to look right
    pts = [c for f in feats for ring in f["geometry"]["coordinates"] for c in ring]
    lon0, lon1 = min(p[0] for p in pts), max(p[0] for p in pts)
    lat0, lat1 = min(p[1] for p in pts), max(p[1] for p in pts)
    k = math.cos(math.radians((lat0 + lat1) / 2))
    W = 1000
    s = W / ((lon1 - lon0) * k)
    shapes = {}
    for f, r in zip(feats, rows):
        d = shapes.get(r[0], {}).get("d", "")
        for ring in f["geometry"]["coordinates"]:
            xy = [(round((x - lon0) * k * s, 1), round((lat1 - y) * s, 1)) for x, y in ring]
            d += "M" + "L".join(f"{x:g},{y:g}" for x, y in xy) + "Z"
        shapes[r[0]] = {"n": r[1] + (" и Сърница" if r[1] == "Велинград" else ""), "d": d}
    (ROOT / "db" / "ref").mkdir(parents=True, exist_ok=True)
    with open(ROOT / "db" / "ref" / "municipality.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "name_bg", "name_en", "drawn_as"])
        seen = set()
        for r in rows:
            if r[0] not in seen:
                seen.add(r[0])
                w.writerow([*r, r[0]])
        velingrad = next(r[0] for r in rows if r[1] == "Велинград")
        for eik, name in EXTRA.items():
            w.writerow([eik, name, "", velingrad])
    out = {"h": round((lat1 - lat0) * s), "shapes": shapes}
    (ROOT / "app" / "static" / "bg-municipalities.json").write_text(
        json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(len(shapes), "drawn municipalities;", len(missing), "unmatched:", missing)


def rings(g):
    return g["coordinates"] if g["type"] == "Polygon" else [r for p in g["coordinates"] for r in p]


def inside(pt, ring):
    x, y = pt
    c = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def areas(adm1, adm2):
    """Oblast and NUTS codes per municipality, and the oblast outlines, from ADM1 (see the docstring)."""
    obl = json.loads(Path(adm1).read_text(encoding="utf-8"))["features"]
    mun = json.loads(Path(adm2).read_text(encoding="utf-8"))["features"]
    polys = [(f["properties"]["shapeISO"], rings(f["geometry"])) for f in obl]
    of = {}
    for f in mun:
        # points of a grid that fall inside the municipality (boundary points sit on shared, slightly
        # misaligned borders and vote wrong for small municipalities)
        own = rings(f["geometry"])
        xs, ys = [p[0] for r in own for p in r], [p[1] for r in own for p in r]
        grid = [(min(xs) + (max(xs) - min(xs)) * (i + .5) / 16, min(ys) + (max(ys) - min(ys)) * (j + .5) / 16) for i in range(16) for j in range(16)]
        pts = [p for p in grid if sum(inside(p, r) for r in own) % 2]
        votes = {iso: sum(any(inside(p, r) for r in rs) for p in pts) for iso, rs in polys}
        of[f["properties"]["shapeName"]] = max(votes, key=votes.get)
    path = ROOT / "db" / "ref" / "municipality.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    by_id = {r["id"]: r for r in rows}
    for r in rows:
        en = r["name_en"] or by_id[r["drawn_as"]]["name_en"]  # Сърница: drawn inside Велинград
        name, n3 = OBLAST[BYALA.get(r["id"]) or of[en]]
        r.update(oblast=name, nuts3=n3, nuts2=n3[:4], nuts1=n3[:3])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, ["id", "name_bg", "name_en", "drawn_as", "oblast", "nuts3", "nuts2", "nuts1"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    # oblast outlines in the same projection as the municipalities
    pts = [c for f in mun for ring in rings(f["geometry"]) for c in ring]
    lon0, lon1 = min(p[0] for p in pts), max(p[0] for p in pts)
    lat0, lat1 = min(p[1] for p in pts), max(p[1] for p in pts)
    k = math.cos(math.radians((lat0 + lat1) / 2))
    s = 1000 / ((lon1 - lon0) * k)
    js = ROOT / "app" / "static" / "bg-municipalities.json"
    out = json.loads(js.read_text(encoding="utf-8"))
    for r in rows:  # each drawn municipality knows its oblast, so the map can colour it by any level
        if r["id"] == r["drawn_as"] and r["id"] in out["shapes"]:
            out["shapes"][r["id"]]["o"] = r["nuts3"]
    svg = lambda rs: "".join("M" + "L".join(f"{round((x - lon0) * k * s, 1):g},{round((lat1 - y) * s, 1):g}" for x, y in r[::2] + r[-1:]) + "Z" for r in rs)
    out["oblasts"] = {}
    for iso, rs in polys:
        name, n3 = OBLAST[iso]
        out["oblasts"][n3] = {"n": name, "d": svg(rs)}
    # planning regions (NUTS 2) and macro-regions (NUTS 1) as one outline each: the oblasts dissolved
    # (shapely, a dev-only dependency of this script); the small buffer closes slivers between oblasts
    from shapely.geometry import shape
    from shapely.ops import unary_union
    geo1 = {OBLAST[f["properties"]["shapeISO"]][1]: shape(f["geometry"]) for f in obl}
    for key, width in (("regions", 4), ("macros", 3)):
        out[key] = {}
        for code in sorted({n3[:width] for n3 in geo1}):
            u = unary_union([g.buffer(0.002) for n3, g in geo1.items() if n3.startswith(code)]).buffer(-0.002)
            parts = u.geoms if u.geom_type == "MultiPolygon" else [u]
            out[key][code] = {"d": svg([list(p.exterior.coords) for p in parts if p.area > 1e-4])}
    out["names"] = NUTS_NAME
    js.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    # self-check: municipalities per oblast as in the administrative division (265 in total)
    per = {"Видин": 11, "Монтана": 11, "Враца": 10, "Плевен": 11, "Ловеч": 8, "Велико Търново": 10, "Габрово": 4, "Русе": 8,
           "Разград": 7, "Силистра": 7, "Варна": 12, "Добрич": 8, "Шумен": 10, "Търговище": 5, "Бургас": 13, "Сливен": 4,
           "Ямбол": 5, "Стара Загора": 11, "Благоевград": 14, "Перник": 6, "Кюстендил": 9, "Пловдив": 18, "Хасково": 11,
           "Пазарджик": 12, "Смолян": 10, "Кърджали": 7, "София област": 22, "София (столица)": 1}
    got = {o: sum(r["oblast"] == o for r in rows) for o in per}
    assert got == per, {o: (got[o], n) for o, n in per.items() if got[o] != n}
    print(len(rows), "municipalities placed in", len({r["nuts3"] for r in rows}), "oblasts")


if __name__ == "__main__":
    if sys.argv[1] == "--areas":
        areas(*sys.argv[2:4])
    else:
        main(*sys.argv[1:3])
