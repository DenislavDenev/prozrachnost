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
Y2016, Y2017, Y2019, Y2020, Y2023 = ("28dd128d-667c-44e6-bb5a-c9be1334941d", "88669f04-0bb3-4d6a-be84-4da97d77b084", "9c037c22-aeaa-4e4a-8be2-4eda7fa4b4f6",
                                     "230a6a9c-f7b7-456f-b8db-ba67d66a6b91", "b03e8542-0576-4770-8b44-977c0015589a")
WHOLE = {
    Y2016: ["b6369887", "b9a2090b", "d241ebbc"],      # long prefixed headings (types, structures, the main table "НАКАЗУЕМИ ДЕЯНИЯ")
    Y2017: ["3402d59a"],                                # cells "1680.**********" (Excel's overflow)
    Y2020: ["847ff57f"],                                # the main table with 11 columns
    Y2023: ["b1b14807", "eb0edc37"],                    # two columns swapped; rows as objects with a stray 13th column
    POLICE: ["ef7a8f29", "0e35ccef", "7d075b6c", "9d2cc270", "2e387772", "181dd1cb"],
    OLD: ["fb97f50f", "02165265", "0e0ee6b9", "4282e687"],
}
CUT = {            # (set, resource, blocks kept)
    (POLICE, "21a6fa0a", 3),
    (OLD, "ac9e3dae", 3),
    (Y2019, "3f2e7395", 17),                            # a header printed twice in the block of Пловдив
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
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


if __name__ == "__main__":
    main(sys.argv[1])
