"""The Eurostat step: one request per row of db/ref/indicators.csv, all read first, checked together
(checks.eurostat), then each indicator written under the hold rule. An indicator that fails its check
keeps what it had and is reported."""
import csv
from urllib.parse import parse_qsl, urlencode

from . import checks, http, jsonstat, store
from .config import EUROSTAT, ROOT

SOURCE = "eurostat"
MEMBERS = "BE BG CZ DK DE EE IE EL ES FR HR IT CY LV LT LU HU MT NL AT PL PT RO SI SK FI SE".split()
GEO = {"BG": ["BG"], "BGEU": ["BG", "EU27_2020", "EA"], "EU": ["EU27_2020", "EA", *MEMBERS],
       "NUTS": ["BG", "BG3", "BG4", "BG31", "BG32", "BG33", "BG34", "BG41", "BG42", *checks.NUTS3]}


def indicators(path=ROOT / "db" / "ref" / "indicators.csv"):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def url(ind):
    q = parse_qsl(ind["filters"]) + [("geo", g) for g in GEO[ind["geo"]]]
    return f"{EUROSTAT}/{ind['dataset']}?{urlencode(q)}"


def pinned(ind):
    return {k for k, _ in parse_qsl(ind["filters"])}


def load(conn, stats, only=None, get=None):
    get = get or http.get
    inds = [i for i in indicators() if not only or i["id"] in only]
    got, bad = {}, {}
    for ind in inds:
        ref = ind["id"]
        try:
            raw = get(url(ind))
            sha = store.save_raw(conn, SOURCE, ref, f"{ind['dataset']}.json", raw)
            got[ref] = (sha, jsonstat.parse(raw, pinned(ind)))
        except http.Gone as e:
            bad[ref] = ("gone", str(e))
        except jsonstat.ShapeError as e:
            bad[ref] = ("invalid", str(e))
        except Exception as e:  # noqa: BLE001 - one indicator's network trouble does not stop the others
            bad[ref] = ("error", repr(e))
    for ref, problem in checks.eurostat({k: v[1] for k, v in got.items()}).items():
        bad[ref] = ("invalid", problem)
        got.pop(ref, None)
    out = {}
    for ref, (sha, parsed) in got.items():
        out[ref] = store.apply(conn, SOURCE, ref, ref, sha, parsed["rows"], parsed["labels"])
        store.state(conn, SOURCE, ref, updated=parsed["updated"], label=parsed["label"])
    for ref, (cause, msg) in bad.items():
        conn.execute("""INSERT INTO ops.change_log (source, ref, field, new, cause)
                        SELECT %s, %s, 'answer', %s, %s""", (SOURCE, ref, msg[:2000], cause))
        store.state(conn, SOURCE, ref, status=cause, error=msg[:2000])
        out[ref] = cause
    stats["indicators"] = out
    stats["problems"] = [f"Икономика: {ref} {cause}: {msg}" for ref, (cause, msg) in bad.items()]
    return stats
