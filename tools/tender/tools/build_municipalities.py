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


BYALA_EAST = "000093435"   # Бяла, област Варна; the western Бяла (област Русе) is 000530493
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


if __name__ == "__main__":
    main(*sys.argv[1:3])
