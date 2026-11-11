"""Map the latest school results to the municipalities of the shared serving layer."""

from collections import defaultdict
from decimal import Decimal
from urllib.parse import urlencode

from .queries import connect


OBLAST_ALIASES = {"софия-област": "софия област", "софия-град": "софия (столица)"}


def key(oblast, municipality):
    oblast = oblast.strip().casefold()
    return OBLAST_ALIASES.get(oblast, oblast), municipality.strip().casefold()


def reference():
    """The 265 municipalities of the shared serving layer (pub.municipality), by the names MON writes: the ЕИК of the
    municipality (the code of its contour), its name, the oblast and the codes of the oblast, region and macro-region."""
    with connect() as conn:
        rows = [dict(id=r[0], name_bg=r[1], oblast=r[2], nuts3=r[3], nuts2=r[4], nuts1=r[5]) for r in conn.execute(
            """SELECT m.eik, m.name_bg, o.label_bg, o.nuts3_code, n3.parent_code, n2.parent_code
               FROM pub.municipality m JOIN pub.oblast o USING (oblast_code)
               JOIN pub.nuts n3 ON n3.nuts_code = o.nuts3_code JOIN pub.nuts n2 ON n2.nuts_code = n3.parent_code
               WHERE NOT m.is_gone ORDER BY m.municipality_code""")]
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
