"""The 'offers' lane: who offered what in each procedure.

The open data of ЦАИС ЕОП has only the number of offers. The public procedure page
app.eop.bg/today/{tenderId} shows every participant with ЕИК, time and price; it reads them from
service.eop.bg (GetPublicTenderParticipation, and GetPublishedLots to number the lots). This lane
reads that answer once per procedure after its deadline, and again when the procedure gets a new
record (a contract, a change). One request per second; the raw answer is kept gzipped with its sha256.

check() is the daily validity check that runs before the reads (step eop-check): fixed canary
procedures must give the same offers, the answer must have the expected shape, and the offers read
must match offersCount of the contracts. A failed canary or shape means the service changed: the
reads stop and the repair agent is called (n8n); nothing is lost, the queue waits.
"""
import datetime as dt
import gzip
import hashlib
import json
import re
import time
import urllib.error
import urllib.request

from .config import DATA, USER_AGENT
from .normalize import eik_valid

SVC = "https://service.eop.bg/NX1Service.svc/"
RAW = DATA / "raw" / "eop_svc"
PAUSE = 1.0          # seconds between requests
MAX_ATTEMPTS = 6
CULTURE_BG = 3       # RetrieveCultures: 1 en-GB, 3 bg-BG
# procedures closed long ago, whose offers must not change: (offers, sum of prices)
CANARY = {463627: (2, 390000.0), 495012: (3, 1648930.56), 56580: (2, 478373.23)}
OFFER_KEYS = {"OfferId", "OrganizationName", "RegistryNumber", "Price", "IsPriceOpened", "SubmissionDate", "ConsortiumMembers"}


class ShapeError(ValueError):
    """The answer does not look like the one this reader was written for."""


def call(method, **body):
    body.setdefault("ianaTimeZone", "Europe/Sofia")
    req = urllib.request.Request(SVC + method, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json; charset=utf-8", "User-Agent": USER_AGENT,
        "Origin": "https://app.eop.bg", "Referer": "https://app.eop.bg/"})
    for k in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            time.sleep(PAUSE)
            return raw
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or k == 3:
                raise
            time.sleep(int(e.headers.get("Retry-After") or 0) or 10 * (k + 1))
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if k == 3:
                raise
            time.sleep(10 * (k + 1))


def when(v):
    """'/Date(1749455167797)/' -> aware datetime (UTC)."""
    m = re.fullmatch(r"/Date\((-?\d+)([+-]\d{4})?\)/", v or "")
    return dt.datetime.fromtimestamp(int(m.group(1)) / 1000, dt.timezone.utc) if m else None


def eik(v):
    s = re.sub(r"\D", "", str(v or ""))
    return s if eik_valid(s) else None


def lot_number(name, numbered):
    """'3. Доставка ...' -> 3; the service's own numbering (GetPublishedLots) wins when present."""
    if name in numbered:
        return numbered[name]
    m = re.match(r"\s*(\d+)\s*\.", name or "")
    return int(m.group(1)) if m else 0


def parse(tender_id, part, lots=()):
    """Offers of one answer of GetPublicTenderParticipation as eopsvc.offer rows (without fetched_at)."""
    if not isinstance(part, dict) or not ("Rounds" in part or "Lots" in part):
        raise ShapeError("no Rounds/Lots")
    numbered = {l.get("TenderName"): l.get("OrderNumber") for l in lots or ()}
    units = [(0, part)] if not part.get("Lots") else [(lot_number(L.get("TenderName"), numbered), L) for L in part["Lots"]]
    out = []
    for lot, u in units:
        for rnd in u.get("Rounds") or []:
            for o in rnd.get("Offers") or []:
                missing = OFFER_KEYS - set(o)
                if missing:
                    raise ShapeError(f"offer without {sorted(missing)}")
                opened = bool(o["IsPriceOpened"])
                out.append({
                    "tender_id": tender_id, "lot_no": lot, "round": rnd.get("OrderNumber") or 1, "offer_id": o["OfferId"],
                    "bidder_name": (o.get("OrganizationName") or o.get("OfferName") or "").strip() or None,
                    "bidder_eik": eik(o.get("RegistryNumber")),
                    "consortium": [{"name": (m.get("OrganizationName") or m.get("Name") or "").strip() or None,
                                    "eik": eik(m.get("RegistryNumber"))} for m in o.get("ConsortiumMembers") or []] or None,
                    "submitted_at": when(o.get("SubmissionDate")),
                    "price": o["Price"] if opened and o["Price"] is not None else None, "price_opened": opened})
    return out


def fetch(tender_id):
    """(rows, raw bytes) for one procedure: participation, plus the lot numbering when it has lots."""
    raw = call("GetPublicTenderParticipation", tenderId=tender_id, cultureId=CULTURE_BG)
    part = json.loads(raw) if raw else None
    lots, raw_lots = [], b""
    if part and part.get("Lots"):
        raw_lots = call("GetPublishedLots", tenderId=tender_id)
        lots = json.loads(raw_lots) or []
    blob = json.dumps({"participation": part, "lots": lots}, ensure_ascii=False).encode()
    return (parse(tender_id, part, lots) if part else []), blob


def store(conn, tender_id, rows, blob):
    now = dt.datetime.now(dt.timezone.utc)
    sha = hashlib.sha256(blob).hexdigest()
    d = RAW / str(tender_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{now:%Y%m%d}.json.gz").write_bytes(gzip.compress(blob))
    with conn.transaction():
        conn.execute("DELETE FROM eopsvc.offer WHERE tender_id = %s", (tender_id,))
        with conn.cursor().copy("COPY eopsvc.offer (tender_id, lot_no, round, offer_id, bidder_name, bidder_eik, consortium, "
                                "submitted_at, price, price_opened, fetched_at) FROM STDIN") as cp:
            for r in rows:
                cp.write_row([r["tender_id"], r["lot_no"], r["round"], r["offer_id"], r["bidder_name"], r["bidder_eik"],
                              json.dumps(r["consortium"], ensure_ascii=False) if r["consortium"] else None,
                              r["submitted_at"], r["price"], r["price_opened"], now])
        conn.execute("UPDATE eopsvc.queue SET status = 'done', fetched_at = %s, sha256 = %s, last_error = NULL, attempts = 0 "
                     "WHERE tender_id = %s", (now, sha, tender_id))


def enqueue(conn):
    """Procedures past their deadline that were never read; procedures with a newer record than the read."""
    new = conn.execute("""INSERT INTO eopsvc.queue (tender_id, reason)
        SELECT t.tender_id::bigint, 'backfill' FROM live.tender t
        WHERE t.tender_id ~ '^[0-9]+$' AND coalesce(t.submission_deadline, t.published_at + interval '30 days') < now()
        ON CONFLICT DO NOTHING""").rowcount
    upd = conn.execute("""UPDATE eopsvc.queue q SET status = 'pending', reason = 'update', next_at = now(), attempts = 0
        FROM live.tender t
        WHERE t.tender_id ~ '^[0-9]+$' AND q.tender_id = t.tender_id::bigint AND q.status = 'done'
          AND greatest(t.source_day, (SELECT max(c.source_day) FROM live.contract c WHERE c.unp = t.unp)) >= q.fetched_at::date""").rowcount
    return {"new": new, "update": upd}


def work(conn, budget_s, stats):
    """Read the queue until the budget runs out: updates first, then the newest procedures."""
    deadline = time.monotonic() + budget_s
    stats.update(done=0, errors=0, offers=0)
    while time.monotonic() < deadline - 5:
        row = conn.execute("""SELECT tender_id FROM eopsvc.queue WHERE status = 'pending' AND next_at <= now()
            ORDER BY (reason = 'backfill'), tender_id DESC LIMIT 1""").fetchone()
        if not row:
            break
        tid = row[0]
        try:
            rows, blob = fetch(tid)
            store(conn, tid, rows, blob)
            stats["done"] += 1
            stats["offers"] += len(rows)
        except ShapeError:
            raise  # the service changed: stop, the check and the repair agent take over
        except Exception as e:  # network trouble: back off this one procedure
            conn.execute("""UPDATE eopsvc.queue SET attempts = attempts + 1, last_error = %s,
                status = CASE WHEN attempts + 1 >= %s THEN 'error' ELSE 'pending' END,
                next_at = now() + make_interval(mins => 10 * power(2, attempts)::int) WHERE tender_id = %s""",
                         (f"{type(e).__name__}: {e}"[:500], MAX_ATTEMPTS, tid))
            stats["errors"] += 1
    stats["pending"] = conn.execute("SELECT count(*) FROM eopsvc.queue WHERE status = 'pending'").fetchone()[0]


def check(conn):
    """The daily validity check. Returns the report; report['ok'] is False when the reader must be repaired."""
    rep = {"canary": {}, "shape": "ok", "completeness": None}
    ok = True
    for tid, (n, total) in CANARY.items():
        try:
            rows, _ = fetch(tid)
            got = (len(rows), round(sum(r["price"] or 0 for r in rows), 2))
            rep["canary"][tid] = {"expected": [n, total], "got": list(got)}
            ok &= got == (n, total)
        except ShapeError as e:
            rep["shape"], ok = str(e), False
        except Exception as e:
            rep["canary"][tid] = {"error": f"{type(e).__name__}: {e}"[:300]}
            ok = False
    # completeness: offers read vs offersCount of the contracts of the same lot, for procedures read in the
    # last week; mismatches are read again (once), and a majority of mismatches means the reader is wrong
    rows = conn.execute("""WITH c AS (
          SELECT t.tender_id::bigint tid, coalesce(c.lot_no, 0) lot, max(c.offers_count) n FROM live.contract c
          JOIN live.tender t ON t.unp = c.unp WHERE c.offers_count IS NOT NULL AND t.tender_id ~ '^[0-9]+$' GROUP BY 1, 2),
        o AS (SELECT tender_id tid, lot_no lot, count(*) n FROM eopsvc.offer GROUP BY 1, 2)
        SELECT c.tid, c.lot, c.n, coalesce(o.n, 0) FROM c JOIN eopsvc.queue q ON q.tender_id = c.tid
        LEFT JOIN o ON o.tid = c.tid AND o.lot = c.lot
        WHERE q.status = 'done' AND q.fetched_at > now() - interval '7 days'""").fetchall()
    bad = [r for r in rows if r[2] != r[3]]
    rep["completeness"] = {"compared": len(rows), "mismatch": len(bad), "examples": [list(r) for r in bad[:10]]}
    if bad:
        conn.execute("""UPDATE eopsvc.queue SET status = 'pending', reason = 'recheck', next_at = now() + interval '1 day'
            WHERE tender_id = ANY(%s) AND reason <> 'recheck'""", ([r[0] for r in bad],))
    if len(rows) >= 20 and len(bad) > len(rows) / 2:
        ok = False
    rep["ok"] = ok
    conn.execute("INSERT INTO eopsvc.check_run (ok, report) VALUES (%s, %s)", (ok, json.dumps(rep, default=str)))
    return rep
