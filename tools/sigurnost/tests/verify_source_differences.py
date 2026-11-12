"""Proves every row of db/ref/source_differences.csv from the raw answers of the archive with a second, independent calculation:
plain json and csv, none of the code of ingest/. Run on the server (the archive is there):

    SIGURNOST_ARCHIVE=/opt/tender/arhiv python tests/verify_source_differences.py

Exit 1 when a recorded difference is not what the raw file says. Without the archive the file only checks that it can be read.
"""
import csv
import glob
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCH = Path(os.environ.get("SIGURNOST_ARCHIVE", "/opt/tender/arhiv"))
COL = {"reg": 2, "reg_unknown": 3, "solved": 5, "solved_unknown": 7}          # the table by structures / by types (both dialects)
BLOCK_COL = {"reg": 2, "solved": 4}                                            # the table structure after structure


def key(s):
    return re.sub(r"\s+", " ", s.replace("„", '"').replace("“", '"').replace("”", '"').lower()).strip()


def aliases():
    out = {}
    with open(ROOT / "db/ref/structure.csv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            for a in r["aliases"].split("|"):
                out.setdefault(key(a), r["code"])
    return out


def rows(uri):
    hits = sorted(glob.glob(f"{ARCH}/egov/*/{uri}/*.json"))
    raw = json.loads(Path(hits[-1]).read_bytes())["data"]
    if raw and isinstance(raw[0], dict):
        w = 1 + max(int(k) for r in raw for k in r)
        raw = [[r.get(str(i), "") for i in range(w)] for r in raw]
    return [[c.strip() for c in r] for r in raw]


def num(s):
    return float(s) if re.fullmatch(r"-?\d+(\.\d+)?", s) else None


def total(rs, col):
    return next(num(r[col]) for r in rs if r[1] == "Общ брой" and num(r[col]) is not None)


def block_totals(rs, col, al):
    """{structure code: total of its block}: a block starts where the second cell is the name and the third reads "Регистрирани"."""
    out, cur = {}, None
    for i, r in enumerate(rs):
        if len(r) > 2 and r[2] == "Регистрирани":
            name = next((x[1] for x in rs[i:i + 3] if x[1]), "")          # the name is in this row or in the next one
            cur = al.get(key(name))
        elif cur and r[1] == "Общ брой" and num(r[col]) is not None:
            out[cur] = num(r[col])
            cur = None
    return out


def verify(r, al):
    check, scope, uri = r["check_id"], r["scope"], r["resource_uri"]
    rs = rows(uri)
    if check == "structures_sum":
        seen, s = set(), 0.0
        for x in rs:
            if x[1] and x[1] != "Общ брой" and num(x[COL[scope]]) is not None:
                k = tuple(x)
                if x[0] and k in seen:              # an exact repeat of a numbered row is not counted twice
                    continue
                seen.add(k)
                s += num(x[COL[scope]])
        return total(rs, COL[scope]), s
    other = rows(r["with_resource"])
    if check == "types_vs_structures":
        return total(other, COL[scope]), total(rs, COL[scope])
    if check == "blocks_vs_structures":
        ind, code = scope.split(":")
        blocks = block_totals(rs, BLOCK_COL[ind], al)
        by_row = {al.get(key(x[1])): num(x[COL[ind]]) for x in other if x[1] and num(x[COL[ind]]) is not None}
        if code == "BG":
            gd = sum(v for c, v in by_row.items() if c and c.startswith("GD-") and c not in blocks)
            return blocks["BG"], sum(v for c, v in blocks.items() if c != "BG") + gd
        return by_row[code], blocks[code]
    raise SystemExit("unknown check " + check)


def main():
    al = aliases()
    recorded = list(csv.DictReader(open(ROOT / "db/ref/source_differences.csv", encoding="utf-8", newline="")))
    if not (ARCH / "egov").exists():
        print(f"no archive at {ARCH}: {len(recorded)} recorded differences read, not verified")
        return 0
    bad = 0
    for r in recorded:
        want, got = verify(r, al)
        ok = want == float(r["expected"]) and got == float(r["got"])
        bad += not ok
        print("ok " if ok else "BAD", r["resource_uri"][:8], r["check_id"], r["scope"], "expected", want, "got", got)
    print(f"{len(recorded) - bad} of {len(recorded)} recorded differences proven from the raw files")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
