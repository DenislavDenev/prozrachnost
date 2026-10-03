"""The number of inhabitants of each municipality, read from the tool Население (its public JSON, one request per
municipality, at most one per second). The tool does not call Население while a page is served: this step keeps a copy
with its dates (silver.population), and "per inhabitant" on a page uses the copy and shows its date. Without a copy
nothing per inhabitant is shown.
"""
import datetime as dt
import json
import os
import time
import urllib.request

from . import config

BASE = os.environ.get("NASELENIE_URL", "http://localhost:8017").rstrip("/")
ADDRESS = {"Постоянен адрес": "permanent", "Настоящ адрес": "current"}


class PopulationError(Exception):
    pass


def fetch(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": config.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        raise PopulationError(f"{path}: {e}") from e


def parse_series(doc):
    """[(address, day, persons)] from the answer of /api/obshtini/<id>.json; anything else raises PopulationError."""
    try:
        out = []
        for s in doc["series"]:
            addr = ADDRESS.get(s["name"])
            if addr is None:
                raise PopulationError("непознато име на серия: " + repr(s["name"]))
            for day, n in s["points"]:
                if n is None:
                    continue
                out.append((addr, dt.date.fromisoformat(day), int(n)))
        return out
    except (KeyError, TypeError, ValueError) as e:
        raise PopulationError("отговорът на Население няма очакваната форма: " + repr(e)) from e


def store(c, municipality_id, doc, url):
    pts = parse_series(doc)
    if not pts:
        raise PopulationError(f"{municipality_id}: няма нито една стойност")
    with c.cursor() as cur:
        cur.executemany("""INSERT INTO silver.population (municipality_id, address, day, persons, source_url) VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (municipality_id, address, day) DO NOTHING""", [(municipality_id, a, d, n, url) for a, d, n in pts])
    return len(pts)


def run(c, get=fetch, pause=1.0, limit=None):
    ids = [r[0] for r in c.execute("SELECT id FROM gold.municipality ORDER BY id")]
    ok, problems = 0, []
    for i, mid in enumerate(ids[:limit]):
        path = f"/api/obshtini/{mid}.json"
        try:
            store(c, mid, get(path), path)
            ok += 1
        except PopulationError as e:
            problems.append(str(e))
        if pause and i + 1 < len(ids):
            time.sleep(pause)
    return {"municipalities": ok, "of": len(ids[:limit]), "problems": problems[:10] + ([f"и още {len(problems) - 10}"] if len(problems) > 10 else [])}
