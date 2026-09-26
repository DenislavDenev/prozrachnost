"""The 'offers' lane: who offered what in each procedure.

The open data of ЦАИС ЕОП has only the number of offers. The public procedure page
app.eop.bg/today/{tenderId} shows every participant with ЕИК, time and price; it reads them from
service.eop.bg (GetPublicTenderParticipation, and GetPublishedLots to number the lots). This lane
reads that answer once per procedure after its deadline, again when the procedure gets a new record
(a contract, a change), weekly while prices are still closed and monthly for the last 90 days. At most
two requests per second on one connection; every raw answer is kept gzipped with its sha256.

Nothing we hold is dropped on one answer: an answer with fewer offers than we hold is kept back and read
again a day later, and replaces ours only when the second read has the same offers. Every difference
between two reads goes to ops.change_log.

check() is the daily validity check that runs before the reads (step eop-check): fixed canary
procedures must give the same offers, the answer must have the expected shape, and the offers read
must not come back empty where the contracts report offers. A failed canary or shape means the service changed: the
reads stop and an alert goes to Discord (n8n); nothing is lost, the queue waits.
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
from .db import log_change
from .normalize import eik_valid

SVC = "https://service.eop.bg/NX1Service.svc/"
RAW = DATA / "raw" / "eop_svc"
PAUSE = 0.5          # seconds between requests (two per second at most, one connection)
MAX_ATTEMPTS = 6
CULTURE_BG = 3       # RetrieveCultures: 1 en-GB, 3 bg-BG
# procedures closed long ago, whose offers must not change: (offers, sum of prices)
CANARY = {463627: (2, 390000.0), 495012: (3, 1648930.56), 56580: (2, 478373.23)}
# the order of the queue: what someone waits for first, the long backfill last
ORDER = ("CASE q.reason WHEN 'visit' THEN 0 WHEN 'update' THEN 1 WHEN 'new' THEN 1 WHEN 'confirm' THEN 2 WHEN 'recheck' THEN 2 "
         "WHEN 'prices' THEN 3 WHEN 'refresh' THEN 4 ELSE 5 END")
FIRST = ("visit", "update", "new")   # the reasons the D-1 chain waits for
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


def parse_contracts(cl):
    """GetPublishedContractListItems -> [(lot_no, contract id)]; the id is the open data's contract number."""
    if not isinstance(cl, dict):
        return []
    out = [(0, str(c["Id"])) for c in cl.get("ContractListItems") or []]
    for L in cl.get("Lots") or []:
        out += [(L.get("LotNumber") or 0, str(c["Id"])) for c in L.get("ContractListItems") or []]
    return out


def fetch(tender_id, contracts=True):
    """One procedure: offers, the lot of each contract and the lot titles, as ЦАИС ЕОП numbers them.
    The lot list is asked for only when the lot names do not carry their number ("3. ..."), the contract
    list only when the procedure has contracts. Returns ({offers, contract_lots, lots}, raw bytes)."""
    raw = call("GetPublicTenderParticipation", tenderId=tender_id, cultureId=CULTURE_BG)
    part = json.loads(raw) if raw else None
    lots = []
    if part and part.get("Lots"):
        named = [re.match(r"\s*(\d+)\s*\.\s*(.*)", L.get("TenderName") or "", re.S) for L in part["Lots"]]
        if all(named):
            lots = [{"OrderNumber": int(m.group(1)), "TenderName": L.get("TenderName")} for m, L in zip(named, part["Lots"])]
        else:
            lots = json.loads(call("GetPublishedLots", tenderId=tender_id) or b"[]") or []
    cl = json.loads(call("GetPublishedContractListItems", tenderId=tender_id) or b"null") if contracts else None
    blob = json.dumps({"participation": part, "lots": lots, "contracts": cl}, ensure_ascii=False).encode()
    titles = [(l.get("OrderNumber") or 0, re.sub(r"^\s*\d+\s*\.\s*", "", l.get("TenderName") or "").strip() or None) for l in lots]
    return {"offers": parse(tender_id, part, lots) if part else [], "contract_lots": parse_contracts(cl), "lots": titles}, blob


def offer_set(rows):
    """What identifies the offers of one answer: sorted (lot, round, offer id)."""
    return sorted((r["lot_no"], r["round"], r["offer_id"]) for r in rows)


def offer_diff(old, new):
    """[(ref, field, old, new)] between two reads, per offer (lot/round/offer id): gone, added, and a
    changed price, opening or bidder."""
    key = lambda r: (r["lot_no"], r["round"], r["offer_id"])
    a, b = {key(r): r for r in old}, {key(r): r for r in new}
    out = []
    for k in sorted(a.keys() | b.keys()):
        ref = "/".join(map(str, k))
        if k not in b:
            out.append((ref, None, f"{a[k]['bidder_name']} {a[k]['price']}", None))
        elif k not in a:
            out.append((ref, None, None, f"{b[k]['bidder_name']} {b[k]['price']}"))
        else:
            for f in ("price", "price_opened", "bidder_name", "bidder_eik"):
                o, n = a[k][f], b[k][f]
                if f == "price" and o is not None and n is not None and abs(float(o) - float(n)) < 0.005:
                    continue
                if o != n:
                    out.append((ref, f, o, n))
    return out


def store(conn, tender_id, got, blob):
    """Keep one answer. Returns 'stored', or 'held' when it has fewer offers than we hold and no earlier
    read has confirmed it yet (it is read again in a day; ours stays until then)."""
    rows = got["offers"]
    now = dt.datetime.now(dt.timezone.utc)
    sha = hashlib.sha256(blob).hexdigest()
    d = RAW / str(tender_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{now:%Y%m%d%H%M}.json.gz").write_bytes(gzip.compress(blob))
    old = [dict(zip(("lot_no", "round", "offer_id", "bidder_name", "bidder_eik", "price", "price_opened"), r)) for r in conn.execute(
        "SELECT lot_no, round, offer_id, bidder_name, bidder_eik, price, price_opened FROM eopsvc.offer WHERE tender_id = %s",
        (tender_id,)).fetchall()]
    held = conn.execute("SELECT held_sha FROM eopsvc.queue WHERE tender_id = %s", (tender_id,)).fetchone()
    mark = hashlib.sha256(json.dumps(offer_set(rows)).encode()).hexdigest()
    with conn.transaction():
        if old and len(rows) < len(old):
            if not held or held[0] != mark:
                log_change(conn, "offers", tender_id, "offers", len(old), len(rows), "held")
                conn.execute("""UPDATE eopsvc.queue SET status = 'pending', reason = 'confirm', next_at = now() + interval '1 day',
                    held_sha = %s, attempts = 0, last_error = NULL WHERE tender_id = %s""", (mark, tender_id))
                return "held"
            log_change(conn, "offers", tender_id, "offers", len(old), len(rows), "confirmed")
        if old:
            for ref, field, o, n in offer_diff(old, rows):
                log_change(conn, "offers", f"{tender_id}/{ref}", field, o, n, "rewritten")
        conn.execute("DELETE FROM eopsvc.offer WHERE tender_id = %s", (tender_id,))
        with conn.cursor().copy("COPY eopsvc.offer (tender_id, lot_no, round, offer_id, bidder_name, bidder_eik, consortium, "
                                "submitted_at, price, price_opened, fetched_at) FROM STDIN") as cp:
            for r in rows:
                cp.write_row([r["tender_id"], r["lot_no"], r["round"], r["offer_id"], r["bidder_name"], r["bidder_eik"],
                              json.dumps(r["consortium"], ensure_ascii=False) if r["consortium"] else None,
                              r["submitted_at"], r["price"], r["price_opened"], now])
        conn.execute("DELETE FROM eopsvc.contract_lot WHERE tender_id = %s", (tender_id,))
        conn.execute("DELETE FROM eopsvc.lot WHERE tender_id = %s", (tender_id,))
        for cid, lot in {c: l for l, c in got["contract_lots"]}.items():  # one lot per contract
            conn.execute("INSERT INTO eopsvc.contract_lot VALUES (%s, %s, %s)", (tender_id, lot, cid))
        for lot, title in dict(got["lots"]).items():
            conn.execute("INSERT INTO eopsvc.lot VALUES (%s, %s, %s)", (tender_id, lot, title))
        conn.execute("UPDATE eopsvc.queue SET status = 'done', fetched_at = %s, sha256 = %s, last_error = NULL, attempts = 0, "
                     "held_sha = NULL WHERE tender_id = %s", (now, sha, tender_id))
    return "stored"


def enqueue(conn, periodic=False):
    """Procedures past their deadline that were never read ('new' when the deadline passed in the last
    14 days, else 'backfill'); procedures with a newer record than the read ('update'). periodic (weekly):
    procedures whose prices were still closed at the last read ('prices', deadline within a year) and
    procedures of the last 90 days not read for 30 days ('refresh')."""
    new = conn.execute("""INSERT INTO eopsvc.queue (tender_id, reason)
        SELECT t.tender_id::bigint, CASE WHEN coalesce(t.submission_deadline, t.published_at + interval '30 days') > now() - interval '14 days'
                                         THEN 'new' ELSE 'backfill' END
        FROM live.tender t
        WHERE t.tender_id ~ '^[0-9]+$' AND coalesce(t.submission_deadline, t.published_at + interval '30 days') < now()
        ON CONFLICT DO NOTHING""").rowcount
    upd = conn.execute("""UPDATE eopsvc.queue q SET status = 'pending', reason = 'update', next_at = now(), attempts = 0
        FROM live.tender t
        WHERE t.tender_id ~ '^[0-9]+$' AND q.tender_id = t.tender_id::bigint AND q.status = 'done'
          AND greatest(t.source_day, (SELECT max(c.source_day) FROM live.contract c WHERE c.unp = t.unp)) >= q.fetched_at::date""").rowcount
    out = {"new": new, "update": upd}
    if periodic:
        out["prices"] = conn.execute("""UPDATE eopsvc.queue q SET status = 'pending', reason = 'prices', next_at = now(), attempts = 0
            FROM live.tender t
            WHERE q.status = 'done' AND q.fetched_at < now() - interval '7 days' AND t.tender_id ~ '^[0-9]+$' AND q.tender_id = t.tender_id::bigint
              AND t.submission_deadline > now() - interval '1 year'
              AND EXISTS (SELECT 1 FROM eopsvc.offer o WHERE o.tender_id = q.tender_id AND NOT o.price_opened)""").rowcount
        out["refresh"] = conn.execute("""UPDATE eopsvc.queue q SET status = 'pending', reason = 'refresh', next_at = now(), attempts = 0
            FROM live.tender t
            WHERE q.status = 'done' AND q.fetched_at < now() - interval '30 days' AND t.tender_id ~ '^[0-9]+$' AND q.tender_id = t.tender_id::bigint
              AND t.submission_deadline > now() - interval '90 days'""").rowcount
    return out


def waiting(conn):
    """Procedures the D-1 chain waits for: opened by a visitor, changed, or just closed."""
    return conn.execute("SELECT count(*) FROM eopsvc.queue WHERE status = 'pending' AND next_at <= now() AND reason = ANY(%s)",
                        (list(FIRST),)).fetchone()[0]


def work(conn, budget_s, stats, first=False):
    """Read the queue until the budget runs out: updates first, then the newest procedures."""
    deadline = time.monotonic() + budget_s
    stats.update(done=0, held=0, errors=0, offers=0)
    while time.monotonic() < deadline - 5:
        row = conn.execute("""SELECT q.tender_id, EXISTS (SELECT 1 FROM live.tender t JOIN live.contract c ON c.unp = t.unp
                                   WHERE t.tender_id = q.tender_id::text)
            FROM eopsvc.queue q WHERE q.status = 'pending' AND q.next_at <= now() """ + ("AND q.reason = ANY(%(first)s) " if first else "") + """
            ORDER BY """ + ORDER + """, q.tender_id DESC LIMIT 1""", {"first": list(FIRST)} if first else None).fetchone()
        if not row:
            break
        tid = row[0]
        try:
            got, blob = fetch(tid, contracts=row[1])
            if store(conn, tid, got, blob) == "held":
                stats["held"] += 1
            else:
                stats["done"] += 1
                stats["offers"] += len(got["offers"])
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
            rows = fetch(tid)[0]["offers"]
            got = (len(rows), round(sum(r["price"] or 0 for r in rows), 2))
            rep["canary"][tid] = {"expected": [n, total], "got": list(got)}
            ok &= got == (n, total)
        except ShapeError as e:
            rep["shape"], ok = str(e), False
        except Exception as e:
            rep["canary"][tid] = {"error": f"{type(e).__name__}: {e}"[:300]}
            ok = False
    # completeness, per procedure read in the last week: offersCount of its contracts vs the offers read.
    # The two sources differ often by one offer (withdrawn, paper, counted by the buyer), so that is only
    # reported; the symptom of a broken reader is no offers at all where the contracts say there were some.
    rows = conn.execute("""WITH c AS (
          SELECT t.tender_id::bigint tid, sum(c.offers_count) n FROM live.contract c JOIN live.tender t ON t.unp = c.unp
          WHERE c.offers_count > 0 AND t.tender_id ~ '^[0-9]+$' GROUP BY 1),
        o AS (SELECT tender_id tid, count(*) n FROM eopsvc.offer GROUP BY 1)
        SELECT c.tid, c.n, coalesce(o.n, 0) FROM c JOIN eopsvc.queue q ON q.tender_id = c.tid LEFT JOIN o ON o.tid = c.tid
        WHERE q.status = 'done' AND q.fetched_at > now() - interval '7 days'""").fetchall()
    empty = [r for r in rows if r[2] == 0]
    rep["completeness"] = {"compared": len(rows), "equal": sum(r[1] == r[2] for r in rows), "none_read": len(empty),
                           "examples": [list(r) for r in empty[:10]]}
    if empty:
        conn.execute("""UPDATE eopsvc.queue SET status = 'pending', reason = 'recheck', next_at = now() + interval '1 day'
            WHERE tender_id = ANY(%s) AND reason <> 'recheck'""", ([r[0] for r in empty],))
    if len(rows) >= 20 and len(empty) > len(rows) / 2:
        ok = False
    rep["ok"] = ok
    conn.execute("INSERT INTO eopsvc.check_run (ok, report) VALUES (%s, %s)", (ok, json.dumps(rep, default=str)))
    return rep
