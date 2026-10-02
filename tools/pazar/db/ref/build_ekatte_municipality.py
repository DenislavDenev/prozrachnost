"""Builds ekatte_municipality.csv: every place of the NSI register (ЕКАТТЕ) with its municipality `id` (the shared key,
db/ref/municipality.csv) and its oblast.

    python db/ref/build_ekatte_municipality.py <ekatte.zip of NSI, as kept by Наблюдател>

The register gives each place its municipality by code (VAR06); municipality.csv has the id by name and NUTS 3 code,
so the two are joined on (NUTS 3, name). A municipality of the register with no match, or one of municipality.csv
with none, stops the script: the join is checked to be one to one on all 265 municipalities.
"""
import csv
import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALIASES = {"Добрич-селска": "Добричка"}     # the register and the municipality list write this name differently


def main(path):
    z = zipfile.ZipFile(path)
    places = [p for p in json.loads(z.read("ek_atte.json")) if "ekatte" in p]
    municipalities = [m for m in json.loads(z.read("ek_obst.json")) if "obshtina" in m]
    ref = list(csv.DictReader((HERE / "municipality.csv").open(encoding="utf-8-sig")))
    by_name = {}
    for r in ref:
        by_name.setdefault((r["nuts3"], r["name_bg"].strip().lower()), []).append(r)
    code_to_id = {}
    for m in municipalities:
        name = ALIASES.get(m["name"], m["name"])
        hit = by_name.get((m["nuts3"], name.strip().lower()), [])
        if len(hit) != 1:
            raise SystemExit(f"municipality {m['obshtina']} {m['name']} {m['nuts3']}: {len(hit)} matches")
        code_to_id[m["obshtina"]] = hit[0]["id"]
    if len(set(code_to_id.values())) != len(ref) or len(code_to_id) != len(ref):
        raise SystemExit(f"{len(code_to_id)} register municipalities, {len(set(code_to_id.values()))} ids, {len(ref)} in municipality.csv")
    out = HERE / "ekatte_municipality.csv"
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["ekatte", "name", "kind", "obshtina", "municipality_id", "oblast", "nuts3"])
        for p in sorted(places, key=lambda p: p["ekatte"]):
            w.writerow([p["ekatte"], p["name"], p["t_v_m"], p["obshtina"], code_to_id[p["obshtina"]],
                        p["oblast_name"].replace("обл. ", ""), p["nuts3"]])
    print(len(places), "places,", len(code_to_id), "municipalities ->", out)


if __name__ == "__main__":
    main(sys.argv[1])
