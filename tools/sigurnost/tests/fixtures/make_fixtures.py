"""Cuts the fixtures out of the real answers in the archive of Наблюдател (run once, on the PC or on the server).

    python tests/fixtures/make_fixtures.py <archive>/egov

The answers are copied byte for byte; only the long tables are cut at a block boundary (the cut is said in the name of the
file). Nothing here is written by hand and there is no personal data: the tables are counts of crimes.
"""
import glob
import json
import shutil
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "egov"
POLICE = "386ae85b-0c5c-4a5e-bd88-a8c7c123b765"
OLD = "dc074958-c8d5-4484-808a-800335ea4a23"
WHOLE = {
    POLICE: ["ef7a8f29", "0e35ccef", "7d075b6c", "9d2cc270", "2e387772", "181dd1cb"],
    OLD: ["fb97f50f", "02165265", "0e0ee6b9", "4282e687"],
}
CUT = {            # (set, resource, blocks kept)
    (POLICE, "21a6fa0a", 3),
    (OLD, "ac9e3dae", 3),
}


def find(root, ds, res):
    return Path(glob.glob(f"{root}/{ds}/{res}*/*.json")[0])


def block_starts(rows):
    """The lines where a block's header begins: the police tables print "Престъпления," above each block; the old flat tables
    print the structure's name alone on a line."""
    marks = [i for i, r in enumerate(rows) if "Престъпления," in r]
    if len(marks) > 3:
        return marks
    return [i for i, r in enumerate(rows) if i > 0 and r[1] and not r[0] and sum(1 for x in r if x) <= 2]


def main(root):
    root = Path(root)
    for ds, resources in WHOLE.items():
        for res in resources:
            src = find(root, ds, res)
            dst = OUT / ds / f"{src.parent.name}.json"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        lst = sorted(glob.glob(f"{root}/{ds}/_list/*.json"))[-1]
        shutil.copyfile(lst, OUT / ds / "_list.json")
    for ds, res, keep in sorted(CUT):
        src = find(root, ds, res)
        d = json.loads(src.read_bytes())
        rows = d["data"]
        stops = block_starts(rows)
        d["data"] = rows[:stops[keep]] if len(stops) > keep else rows
        dst = OUT / ds / f"{src.parent.name}.cut{keep}.json"
        dst.write_bytes(json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


if __name__ == "__main__":
    main(sys.argv[1])
