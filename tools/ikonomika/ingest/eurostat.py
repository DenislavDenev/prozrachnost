"""The Eurostat step: one request per row of db/ref/indicators.csv, all read first, checked together
(checks.eurostat), then each indicator written under the hold rule. An indicator that fails its check
keeps what it had and is reported."""
import csv
from urllib.parse import parse_qsl, urlencode

from . import checks, http, jsonstat, store
from .config import EUROSTAT, ROOT

SOURCE = "eurostat"
MEMBERS = "BE BG CZ DK DE EE IE EL ES FR HR IT CY LV LT LU HU MT NL AT PL PT RO SI SK FI SE".split()
# the euro area: EA (changing composition) where a dataset has it, else EA21 (with BG, from 2026) or EA20
EURO_AREA = ["EA", "EA21", "EA20"]
GEO = {"BG": ["BG"], "BGEU": ["BG", "EU27_2020", *EURO_AREA], "EU": ["EU27_2020", *EURO_AREA, *MEMBERS],
       "NUTS": ["BG", *checks.NUTS1, *checks.NUTS2, *checks.NUTS3],
       "ALL": []}   # no geo filter: every country and region the dataset has (the map of Europe)


def indicators(path=ROOT / "db" / "ref" / "indicators.csv"):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def url(ind):
    q = parse_qsl(ind["filters"]) + [("geo", g) for g in GEO[ind["geo"]]]
    return f"{EUROSTAT}/{ind['dataset']}?{urlencode(q)}"


def pinned(ind):
    return {k for k, _ in parse_qsl(ind["filters"])}


def check(parsed, refs):
    """{ref: problem} of the cross checks (checks.eurostat) for the answers `refs` of `parsed`."""
    return {k: v for k, v in checks.eurostat(parsed).items() if k in refs}


def load(conn, r, only=None, get=None):
    get = get or http.get
    stats = r.stats
    inds = [i for i in indicators() if not only or i["id"] in only]
    got, bad = {}, {}
    for ind in inds:
        ref = ind["id"]
        file = None
        try:
            raw = get(url(ind))
            file = store.save_raw(conn, r, SOURCE, ref, f"{ind['dataset']}.json", raw)
            got[ref] = (file, jsonstat.parse(raw, pinned(ind)))
        except http.Gone as e:
            bad[ref] = ("gone", str(e), file)
        except jsonstat.ShapeError as e:
            bad[ref] = ("invalid", str(e), file)
        except Exception as e:  # noqa: BLE001 - one indicator's network trouble does not stop the others
            bad[ref] = ("error", repr(e), file)
    for ref, problem in check({k: v[1] for k, v in got.items()}, got).items():
        bad[ref] = ("invalid", problem, got.pop(ref)[0])
    out = {}
    for ref, (file, parsed) in got.items():
        out[ref] = store.apply(conn, r, SOURCE, ref, ref, file, parsed["rows"], parsed["labels"])
        store.state(conn, SOURCE, ref, updated=parsed["updated"], label=parsed["label"])
    for ref, (cause, msg, file) in bad.items():
        store.reject(conn, r, SOURCE, ref, cause, msg, file)
        out[ref] = cause
    stats["indicators"] = out
    stats["problems"] = [f"Икономика: {ref} {cause}: {msg}" for ref, (cause, msg, _) in bad.items()]
    return stats
