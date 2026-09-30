"""Published exam results by the common Bulgarian territorial reference."""

from collections import defaultdict
from decimal import Decimal
from urllib.parse import urlencode

from .municipalities import key, reference


LEVELS = (("obshtini", "Общини", "id"), ("oblasti", "Области", "nuts3"),
          ("rayoni", "Райони", "nuts2"), ("makrorayoni", "Макрорайони", "nuts1"),
          ("darzhava", "Държава", None))
REGIONS = {"BG31": "Северозападен", "BG32": "Северен централен",
           "BG33": "Североизточен", "BG34": "Югоизточен",
           "BG41": "Югозападен", "BG42": "Южен централен",
           "BG3": "Северна и Югоизточна България",
           "BG4": "Югозападна и Южна централна България"}


def aggregate(rows, level, exam, year, subject, session="may", kind="mandatory"):
    """Group exact published place names; suppressed DZI counts make their mean unknown."""
    field = dict((slug, column) for slug, _, column in LEVELS).get(level)
    if level not in {slug for slug, _, _ in LEVELS}:
        raise ValueError("Unknown territorial level")
    refs = reference()
    by_code = defaultdict(list)
    unmapped = 0
    for row in rows:
        ref = refs.get(key(row[0], row[1]))
        if ref is None:
            unmapped += 1
            continue
        code = ref[field] if field else "BG"
        by_code[code].append((row[2], row[3]))
    places = {}
    for ref in refs.values():
        code = ref[field] if field else "BG"
        if code in places:
            continue
        name = ref["name_bg"] if level == "obshtini" else (
            ref["oblast"] if level == "oblasti" else
            "България" if level == "darzhava" else REGIONS[code])
        places[code] = dict(code=code, name=name, oblast=ref["oblast"] if level == "obshtini" else "",
                            municipality=ref["name_bg"] if level == "obshtini" else "")
    for code, place in places.items():
        values = by_code[code]
        hidden = exam == "dzi" and any(score is not None and takers is None for score, takers in values)
        valid = [(Decimal(str(score)), takers) for score, takers in values
                 if score is not None and takers is not None and takers > 0]
        takers = sum(n for _, n in valid)
        place["score"] = (sum(score * n for score, n in valid) / takers).quantize(Decimal("0.01")) if takers and not hidden else None
        place["takers"] = takers if takers and not hidden else None
        place["schools"] = len(values)
        if level == "obshtini" and values:
            params = {"oblast": place["oblast"], "municipality": place["municipality"]}
            if exam == "nvo7":
                path = "/uchilishta"
            elif exam == "dzi":
                path = f"/matura/{year.replace('/', '-')}/uchilishta"
                params.update(session=session, kind=kind, subject=subject)
            else:
                path = f"/nvo/{exam[-1] if exam == 'nvo4' else '10'}/{year.replace('/', '-')}/uchilishta"
                params["subject"] = subject
            place["href"] = path + "?" + urlencode(params)
        else:
            place["href"] = None
    return sorted(places.values(), key=lambda item: (item["name"].casefold(), item["code"])), unmapped
