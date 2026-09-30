"""Map the latest school results to the reviewed GISCO municipality reference."""

import csv
from collections import defaultdict
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode


REFERENCE = Path(__file__).resolve().parents[1] / "db/ref/municipality.csv"
OBLAST_ALIASES = {"софия-област": "софия област", "софия-град": "софия (столица)"}


def key(oblast, municipality):
    oblast = oblast.strip().casefold()
    return OBLAST_ALIASES.get(oblast, oblast), municipality.strip().casefold()


@lru_cache(maxsize=1)
def reference():
    with REFERENCE.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 265 or len({r["id"] for r in rows}) != 265:
        raise ValueError("Municipality reference is incomplete")
    index = {}
    for row in rows:
        place = key(row["oblast"], row["name_bg"])
        if place in index:
            raise ValueError("Ambiguous municipality reference")
        index[place] = row
    return index


def weighted(pairs):
    pairs = [(score, takers) for score, takers in pairs if score is not None and takers is not None and takers > 0]
    total = sum(takers for _, takers in pairs)
    return ((sum(score * takers for score, takers in pairs) / total).quantize(Decimal("0.01")) if total else None,
            total)


def scores(snapshot):
    """Return all GISCO municipalities, with no value where MON has no takers."""
    if not snapshot:
        return [], []
    refs = reference()
    groups = defaultdict(list)
    unmapped = []
    for school in snapshot["schools"]:
        if school["matched"] is not True:
            continue  # The two foreign schools are outside the Bulgarian boundary set.
        place = key(school["oblast"], school["municipality"])
        if place not in refs:
            unmapped.append(school["code"])
            continue
        groups[place].append(school)
    rows = []
    for place, ref in refs.items():
        schools = groups[place]
        bel, bel_takers = weighted((s["subjects"].get("БЕЛ", {}).get("score"),
                                     s["subjects"].get("БЕЛ", {}).get("takers")) for s in schools)
        math, math_takers = weighted((s["subjects"].get("МАТ", {}).get("score"),
                                       s["subjects"].get("МАТ", {}).get("takers")) for s in schools)
        rows.append(dict(code=ref["id"], name=ref["name_bg"], oblast=ref["oblast"],
                         schools=len(schools), bel=bel, math=math,
                         bel_takers=bel_takers, math_takers=math_takers,
                         href="/uchilishta?" + urlencode({"oblast": schools[0]["oblast"],
                                                           "municipality": schools[0]["municipality"]}) if schools else None))
    rows.sort(key=lambda r: (r["name"].casefold(), r["oblast"].casefold()))
    return rows, unmapped
