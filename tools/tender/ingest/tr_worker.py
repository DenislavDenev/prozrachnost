"""The 'tr' lane: a durable queue of partidas to read, polite single-connection reads, role
extraction, and expansion (entity holders by ЕИК, new partidas of known persons by name search).
State lives in tr.* so a run can stop anywhere and resume."""
import datetime as dt
import gzip
import hashlib
import time

from . import registry as R
from .config import RAW_TR

PAUSE = 0.5          # seconds between requests: one connection, ~1.5 req/s at most
MAX_ATTEMPTS = 8
MAX_ENTITY_DEPTH = 10  # safety net against pathological ownership chains
MAX_NAME_HITS = 100    # a name naming more partidas than this is ambiguous (SIGMA MAX_HITS)
NAME_SEARCH_EVERY = dt.timedelta(days=7)


def _ulid():
    import os
    t = int(time.time() * 1000).to_bytes(6, "big")
    raw = int.from_bytes(t + os.urandom(10), "big")
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    return "".join(alphabet[(raw >> (5 * i)) & 31] for i in reversed(range(26)))


def enqueue(conn, eik, reason, priority, depth=0):
    conn.execute(
        "INSERT INTO tr.queue(eik, reason, priority, depth) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (eik) DO UPDATE SET priority=LEAST(tr.queue.priority, EXCLUDED.priority), "
        "depth=LEAST(tr.queue.depth, EXCLUDED.depth)", (eik, reason, priority, depth))


def seed_from_contracts(conn):
    """Every 9-digit supplier/subcontractor ЕИК from the live tables, not read yet."""
    n = conn.execute("""
        INSERT INTO tr.queue(eik, reason, priority, depth)
        SELECT DISTINCT left(eik, 9), 'contract', 1, 0 FROM (
          SELECT eik FROM live.contract_supplier WHERE eik IS NOT NULL
          UNION SELECT eik FROM live.subcontract WHERE eik IS NOT NULL) s
        WHERE length(eik) IN (9, 13)
        ON CONFLICT (eik) DO UPDATE SET priority = 1, depth = 0
        WHERE tr.queue.priority > 1""").rowcount
    return n


def enqueue_changes(conn, day, pass_no):
    """Read the portal's change list for `day`; re-read partidas we already track."""
    row = conn.execute("SELECT next_page, done FROM tr.change_day WHERE day=%s AND pass=%s",
                       (day, pass_no)).fetchone()
    if row and row[1]:
        return 0
    page, touched = (row[0] if row else 1), 0
    while True:
        res = R.changes(str(day), page)
        uics = [i["uic"] for i in res["items"]]
        if uics:
            touched += conn.execute(
                "UPDATE tr.queue SET status='pending', reason='change', next_at=now(), attempts=0 "
                "WHERE eik = ANY(%s) AND status <> 'pending'", (uics,)).rowcount
        page += 1
        conn.execute("INSERT INTO tr.change_day(day, pass, next_page, done) VALUES (%s,%s,%s,%s) "
                     "ON CONFLICT (day, pass) DO UPDATE SET next_page=EXCLUDED.next_page, done=EXCLUDED.done",
                     (day, pass_no, page, not res["has_more"]))
        if not res["has_more"]:
            return touched
        time.sleep(30)  # the portal list is paced like SIGMA's (30 s per page)


def store(conn, eik, xml):
    """Persist one successful read: raw XML, deed facts, roles (replaced), persons."""
    now = dt.datetime.now(dt.timezone.utc)
    if xml is None:
        conn.execute("INSERT INTO tr.deed(eik, status, fetched_at) VALUES (%s,'absent',%s) "
                     "ON CONFLICT (eik) DO UPDATE SET status='absent', fetched_at=EXCLUDED.fetched_at, "
                     "error=NULL", (eik, now))
        return []
    path = RAW_TR / "deeds" / eik[:3] / f"{eik}.xml.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(xml))
    deed = R.parse_deed(xml, eik)
    facts = R.deed_facts(deed)
    roles, persons = R.roles_from_deed(eik, deed)
    with conn.transaction():
        conn.execute(
            "INSERT INTO tr.deed(eik, status, fetched_at, sha256, name, legal_form, deed_status, seat, "
            "capital_eur, registered_on) VALUES (%s,'ok',%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (eik) DO UPDATE "
            "SET status='ok', fetched_at=EXCLUDED.fetched_at, sha256=EXCLUDED.sha256, name=EXCLUDED.name, "
            "legal_form=EXCLUDED.legal_form, deed_status=EXCLUDED.deed_status, seat=EXCLUDED.seat, "
            "capital_eur=EXCLUDED.capital_eur, registered_on=EXCLUDED.registered_on, error=NULL",
            (eik, now, hashlib.sha256(xml).hexdigest(), facts["name"], facts["legal_form"], deed.status,
             facts["seat"], facts["capital"], facts["registered_on"]))
        conn.execute("DELETE FROM tr.role WHERE eik=%s", (eik,))
        with conn.cursor().copy(
                "COPY tr.role(eik, sub_uic, field_ident, role, holder_kind, holder_id, holder_name, "
                "indent_type, share, country, entry_no, valid_from, valid_to, uncertain_after, observed_at) "
                "FROM STDIN") as cp:
            for r in roles:
                cp.write_row([r["eik"], r["sub_uic"], r["field_ident"], r["role"], r["holder_kind"],
                              r["holder_id"], r["holder_name"], r["indent_type"], r["share"], r["country"],
                              r["entry_no"], r["valid_from"], r["valid_to"], r["uncertain_after"], now])
        for p in persons:
            conn.execute(
                "INSERT INTO tr.person(id, indent, indent_type, name, name_key) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (indent) DO UPDATE SET name=EXCLUDED.name, name_key=EXCLUDED.name_key",
                (_ulid(), p["indent"], p["indent_type"], p["name"],
                 R.person_name_key(R.without_title(p["name"]))))
    return roles


def process(conn, budget_s, stats):
    """Read queued partidas until the queue is empty or the time budget is spent."""
    deadline = time.monotonic() + budget_s
    stats.setdefault("read", 0)
    stats.setdefault("absent", 0)
    stats.setdefault("errors", 0)
    while time.monotonic() < deadline:
        row = conn.execute(
            "SELECT eik, depth, attempts FROM tr.queue WHERE status='pending' AND next_at <= now() "
            "ORDER BY priority, enqueued_at LIMIT 1").fetchone()
        if not row:
            break
        eik, depth, attempts = row
        try:
            xml = R.fetch_deed(eik)
            roles = store(conn, eik, xml)
        except Exception as e:  # noqa: BLE001 - recorded, retried with backoff
            attempts += 1
            status = "failed" if attempts >= MAX_ATTEMPTS else "pending"
            conn.execute("UPDATE tr.queue SET attempts=%s, status=%s, last_error=%s, "
                         "next_at=now() + make_interval(mins => %s) WHERE eik=%s",
                         (attempts, status, repr(e)[:500], 2 ** attempts, eik))
            if status == "failed":
                conn.execute("INSERT INTO tr.deed(eik, status, fetched_at, error) VALUES (%s,'error',now(),%s) "
                             "ON CONFLICT (eik) DO UPDATE SET error=EXCLUDED.error", (eik, repr(e)[:500]))
            stats["errors"] += 1
            time.sleep(PAUSE * 4)
            continue
        conn.execute("UPDATE tr.queue SET status='done', done_at=now(), attempts=0, last_error=NULL "
                     "WHERE eik=%s", (eik,))
        stats["read" if xml else "absent"] += 1
        if depth < MAX_ENTITY_DEPTH:  # companies that hold roles are followed by ЕИК
            for r in roles:
                if r["holder_kind"] == "entity" and R.UIC9.match(r["holder_id"]):
                    enqueue(conn, r["holder_id"], "holder", 2, depth + 1)
        time.sleep(PAUSE)
    stats["pending"] = conn.execute("SELECT count(*) FROM tr.queue WHERE status='pending'").fetchone()[0]


def search_names(conn, budget_s, stats):
    """Find partidas of known persons outside the tracked set: search each person's name, queue the
    hits. Identity is confirmed later by the hash in the read partida, never by the name."""
    deadline = time.monotonic() + budget_s
    stats.setdefault("names", 0)
    stats.setdefault("ambiguous", 0)
    stats.setdefault("queued", 0)
    while time.monotonic() < deadline:
        row = conn.execute("""
            SELECT p.name_key, min(p.name) FROM tr.person p
            LEFT JOIN tr.name_search s ON s.name_key = p.name_key
            WHERE p.name_key <> '' AND (s.name_key IS NULL OR s.searched_at < now() - %s)
            GROUP BY p.name_key ORDER BY min(s.searched_at) NULLS FIRST LIMIT 1""",
                           (NAME_SEARCH_EVERY,)).fetchone()
        if not row:
            break
        key, name = row
        try:
            first = R.search_holders(R.without_title(name))
            total, items = first["total"], list(first["items"])
            status = "ambiguous" if total > MAX_NAME_HITS else "done"
            page = 2
            while status == "done" and len(items) < total:
                time.sleep(PAUSE)
                nxt = R.search_holders(R.without_title(name), page)
                if not nxt["items"]:
                    break
                items += nxt["items"]
                page += 1
        except Exception as e:  # noqa: BLE001
            conn.execute("INSERT INTO tr.name_search(name_key, status, searched_at) VALUES (%s,'failed',now()) "
                         "ON CONFLICT (name_key) DO UPDATE SET status='failed', searched_at=now()", (key,))
            stats.setdefault("errors", 0)
            stats["errors"] += 1
            time.sleep(PAUSE * 4)
            continue
        if status == "done":
            for it in items:
                if it["fieldIdent"] in R.ROLE_FIELDS and R.person_name_key(R.without_title(it["name"])) == key:
                    before = conn.execute("SELECT 1 FROM tr.queue WHERE eik=%s", (it["uic"],)).fetchone()
                    if not before:
                        enqueue(conn, it["uic"], "name_search", 4, 1)
                        stats["queued"] += 1
        else:
            stats["ambiguous"] += 1
        conn.execute("INSERT INTO tr.name_search(name_key, total, status, searched_at) VALUES (%s,%s,%s,now()) "
                     "ON CONFLICT (name_key) DO UPDATE SET total=EXCLUDED.total, status=EXCLUDED.status, "
                     "searched_at=now()", (key, total, status))
        stats["names"] += 1
        time.sleep(PAUSE)
