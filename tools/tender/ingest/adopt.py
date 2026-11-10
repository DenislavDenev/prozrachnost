"""The step `adopt`: what was read before the readers landed through the shared module gets its load.

Until 10.11.2026 the readers wrote the landing zone themselves. Every answer they read is still on disk. This step
reads each of them once more through the same parser and records it the way a read is recorded today: the file in
the register of raw files (in place, where it lies), a load in ops.load dated at the original read, and the number
of that load on the rows it brought. Nothing is asked from a source.

  eop-file   every file of every day, through build.land_file (the build reads only loaded files)
  sebra      every period, rewritten from its kept answer through sebra.land
  ecb        the history, read from the bank again (it is one small file and nothing of it was kept)
  tr         every partida we hold as read: the kept XML must be the one the table names by its sha256
  offers     every procedure read: the kept answer whose sha256 the queue names

A resource that already has a load is skipped, so the step can stop and run again, and a reader may work beside
it. An answer that is not on disk, or is not the one the table was written from, is counted and queued for a new
read; its rows stay without a load until then (the rule landed_rows_have_a_load counts them).

The rows are not rewritten for the register and the offers (their times of reading stay). `part` splits the work
between processes: (i, n) takes the resources whose key falls in share i of n.
"""
import gzip
import hashlib
import json
import datetime as dt
import zlib

from . import build, db, eop, eop_offers, fx, pipeline, registry as R, sebra, tr_worker
from .config import RAW_TR, TOOL

BATCH = 500   # resources per transaction


def _mine(ref, part):
    return part is None or zlib.crc32(str(ref).encode()) % part[1] == part[0]


def _loaded(conn, source):
    return {r[0] for r in conn.execute("SELECT ref FROM ops.load WHERE tool = %s AND source = %s AND is_current", (TOOL, source))}


def eop_files(conn, stats):
    """Every file of the open data that has no load yet, oldest day first."""
    have = {(str(d), k) for d, k in conn.execute("SELECT day, kind FROM tender_src.eop_file WHERE load_id IS NOT NULL")}
    n = 0
    for folder in sorted(p for p in eop.RAW_EOP.iterdir() if p.is_dir() and (p / "manifest.json").exists()):
        m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        for kind, f in m["files"].items():
            if (m["day"], kind) in have or not (folder / f"{kind}.json").exists():
                continue
            if hashlib.sha256((folder / f"{kind}.json").read_bytes()).hexdigest() != f["sha256"]:
                stats["eop_other_file"] = stats.get("eop_other_file", 0) + 1   # the manifest names another file: left for a refetch
                continue
            at = dt.datetime.fromisoformat(m["fetched_at"])
            status = build.land_file(conn, m["day"], kind, f, at, at=at)
            stats[f"eop_{status}"] = stats.get(f"eop_{status}", 0) + 1
            n += 1
    return n


def sebra_files(conn, stats):
    """Every period from its kept answer, rewritten with its load."""
    done = _loaded(conn, "sebra")
    st = {"files": 0, "rows": 0, "verified": 0, "held": 0}
    for uri, name, fmt, updated_at, sha, loaded_at in conn.execute(
            "SELECT uri, name, format, updated_at, sha256, loaded_at FROM tender_src.sebra_resource WHERE sha256 IS NOT NULL ORDER BY name").fetchall():
        if uri in done:
            continue
        path = sebra.RAW / f"{uri}.{fmt}.gz"
        raw = gzip.decompress(path.read_bytes()) if path.exists() else None
        if raw is None or hashlib.sha256(raw).hexdigest() != sha:
            stats["sebra_missing_raw"] = stats.get("sebra_missing_raw", 0) + 1
            conn.execute("UPDATE tender_src.sebra_resource SET updated_at = NULL WHERE uri = %s", (uri,))   # downloaded again by the next read
            continue
        res = {"uri": uri, "name": name, "format": fmt, "updated_at": updated_at}
        sebra.land(conn, res, sebra.parse(sebra.rows_of(res, raw)), raw, st, at=loaded_at)
    stats["sebra_files"], stats["sebra_rows"] = st["files"], st["rows"]


def _each(conn, items, one, stats, key):
    """one(item) for every item, BATCH of them per transaction; an item that fails is counted and the rest go on."""
    r = db.run_of(conn)
    for i in range(0, len(items), BATCH):
        with conn.transaction():
            for item in items[i:i + BATCH]:
                try:
                    with conn.transaction():
                        stats[key] = stats.get(key, 0) + (1 if one(r, item) else 0)
                except Exception as e:   # noqa: BLE001 - a reader took it meanwhile, or the kept answer cannot be read
                    stats[f"{key}_failed"] = stats.get(f"{key}_failed", 0) + 1
                    stats.setdefault(f"{key}_errors", [])
                    if len(stats[f"{key}_errors"]) < 5:
                        stats[f"{key}_errors"].append(f"{item[0]}: {e!r}"[:200])


def partidas(conn, stats, part=None):
    """Every partida held as read: the kept XML, when it is the one the table was written from."""
    done = _loaded(conn, "tr")
    todo = [row for row in conn.execute("SELECT eik, sha256, fetched_at FROM tender_src.tr_deed WHERE status = 'ok' AND load_id IS NULL ORDER BY eik").fetchall()
            if row[0] not in done and _mine(row[0], part)]
    again = []

    def one(r, item):
        eik, sha, fetched_at = item
        path = RAW_TR / "deeds" / eik[:3] / f"{eik}.xml.gz"
        xml = gzip.decompress(path.read_bytes()) if path.exists() else None
        if xml is None or hashlib.sha256(xml).hexdigest() != sha:
            again.append(eik)
            return False
        deed = R.parse_deed(xml, eik)
        roles, _ = R.roles_from_deed(eik, deed)
        file, _ = pipeline.register_raw(conn, r, "tr", eik, path, media_type="application/gzip", fetched_at=fetched_at)
        pipeline.load(conn, r, "tr", eik, tr_worker.rows_of(R.deed_facts(deed), deed.status, roles),
                      lambda c, load_id, rows: c.execute("UPDATE tender_src.tr_deed SET load_id = %s WHERE eik = %s", (load_id, eik)),
                      raw_file_id=file.raw_file_id, now=fetched_at)
        return True

    _each(conn, todo, one, stats, "tr")
    if again:   # no kept answer to stand behind the rows: read again soon, after what someone waits for
        stats["tr_missing_raw"] = len(again)
        conn.execute("UPDATE tender_src.tr_queue SET status = 'pending', next_at = now(), attempts = 0, why = 'rotate' WHERE eik = ANY(%s) AND status = 'done'", (again,))
    stats["tr_roles"] = conn.execute("""UPDATE tender_src.tr_role x SET load_id = d.load_id FROM tender_src.tr_deed d
        WHERE d.eik = x.eik AND d.load_id IS NOT NULL AND x.load_id IS NULL""").rowcount
    stats["tr_persons"] = conn.execute("""UPDATE tender_src.tr_person p SET load_id = x.load_id
        FROM (SELECT holder_id, max(load_id) load_id FROM tender_src.tr_role WHERE load_id IS NOT NULL GROUP BY 1) x
        WHERE x.holder_id = p.indent AND p.load_id IS NULL""").rowcount


OFFER_TABLES = ("offer", "lot", "contract_lot", "announcement", "detail", "publication", "appeal", "document")


def procedures(conn, stats, part=None):
    """Every procedure read: the kept answer whose sha256 the queue names."""
    done = _loaded(conn, "offers")
    todo = [row for row in conn.execute("SELECT tender_id, sha256, fetched_at FROM tender_src.eopsvc_queue WHERE status = 'done' AND sha256 IS NOT NULL ORDER BY 1").fetchall()
            if str(row[0]) not in done and _mine(row[0], part)]
    again = []

    def one(r, item):
        tid, sha, fetched_at = item
        for path in sorted((eop_offers.RAW / str(tid)).glob("*.json.gz"), reverse=True):
            blob = gzip.decompress(path.read_bytes())
            if hashlib.sha256(blob).hexdigest() == sha:
                break
        else:
            again.append(tid)
            return False
        got = eop_offers.parsed(tid, json.loads(blob))
        file, _ = pipeline.register_raw(conn, r, "offers", str(tid), path, media_type="application/gzip", fetched_at=fetched_at)
        pipeline.load(conn, r, "offers", str(tid), eop_offers.rows_of(got), lambda c, load_id, rows: None,
                      raw_file_id=file.raw_file_id, now=fetched_at)
        return True

    _each(conn, todo, one, stats, "offers")
    if again:
        stats["offers_missing_raw"] = len(again)
        conn.execute("UPDATE tender_src.eopsvc_queue SET status = 'pending', reason = 'refresh', next_at = now(), attempts = 0 WHERE tender_id = ANY(%s)", (again,))
    for t in OFFER_TABLES:
        stats[f"offers_{t}"] = conn.execute(f"""UPDATE tender_src.eopsvc_{t} x SET load_id = l.load_id FROM ops.load l
            WHERE l.tool = 'tender' AND l.source = 'offers' AND l.is_current AND l.ref = x.tender_id::text AND x.load_id IS NULL""").rowcount


def run(conn, stats, what=("eop", "sebra", "ecb", "tr", "offers"), part=None):
    conn.execute("SET synchronous_commit = off")   # the step can run again; nothing here is the only copy
    if "eop" in what:
        eop_files(conn, stats)
    if "sebra" in what:
        sebra_files(conn, stats)
    if "ecb" in what and not conn.execute("SELECT 1 FROM tender_src.fx_rate WHERE load_id IS NOT NULL LIMIT 1").fetchone():
        stats["ecb_rates"] = fx.refresh(conn)
    if "tr" in what:
        partidas(conn, stats, part)
    if "offers" in what:
        procedures(conn, stats, part)
    stats["without_load"] = dict(conn.execute(WITHOUT_LOAD).fetchall())


# the landed rows that still have no load, by table (the rule landed_rows_have_a_load sums them)
WITHOUT_LOAD = """
SELECT 'eop_file', count(*) FROM tender_src.eop_file WHERE load_id IS NULL
UNION ALL SELECT 'fx_rate', count(*) FROM tender_src.fx_rate WHERE load_id IS NULL
UNION ALL SELECT 'tr_deed', count(*) FROM tender_src.tr_deed WHERE status = 'ok' AND load_id IS NULL
UNION ALL SELECT 'tr_role', count(*) FROM tender_src.tr_role WHERE load_id IS NULL
UNION ALL SELECT 'eopsvc_offer', count(*) FROM tender_src.eopsvc_offer WHERE load_id IS NULL
UNION ALL SELECT 'eopsvc_publication', count(*) FROM tender_src.eopsvc_publication WHERE load_id IS NULL
UNION ALL SELECT 'eopsvc_document', count(*) FROM tender_src.eopsvc_document WHERE load_id IS NULL
UNION ALL SELECT 'sebra_payment', count(*) FROM tender_src.sebra_payment WHERE load_id IS NULL
"""
