"""The 'tr' lane: a durable queue of partidas to read, polite single-connection reads, role
extraction, and expansion (entity holders by ЕИК, new partidas of known persons by name search).
State lives in tr.* so a run can stop anywhere and resume.

A partida we hold is read again when the portal's change list names it (why='change') and in a slow
rotation (why='rotate', every ROTATE_DAYS) that measures what the change list misses. A changed partida
goes to ops.change_log; a partida that stops answering keeps its data until a second read a day later
agrees."""
import datetime as dt
import gzip
import hashlib
import re
import time

from . import registry as R
from .config import RAW_TR
from .db import log_change

PAUSE = 0.5          # seconds between requests: one connection, ~1.5 req/s at most
MAX_ATTEMPTS = 8
MAX_ENTITY_DEPTH = 10  # safety net against pathological ownership chains
MAX_NAME_HITS = 100    # a name naming more partidas than this is ambiguous (SIGMA MAX_HITS)
NAME_SEARCH_EVERY = dt.timedelta(days=7)
ROTATE_DAYS = 60       # every partida we hold is read again at least this often


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
    """Every 9-digit supplier/subcontractor ЕИК from the live tables, not read yet (priority 1), then every
    bidder and consortium member of the offers (priority 2): a company that only lost still gets its partida,
    its page and its tags (before 27.09.2026 it did not)."""
    n = conn.execute("""
        INSERT INTO tr.queue(eik, reason, priority, depth)
        SELECT DISTINCT left(eik, 9), 'contract', 1, 0 FROM (
          SELECT eik FROM live.contract_supplier WHERE eik IS NOT NULL
          UNION SELECT eik FROM live.subcontract WHERE eik IS NOT NULL) s
        WHERE length(eik) IN (9, 13)
        ON CONFLICT (eik) DO UPDATE SET priority = 1, depth = 0
        WHERE tr.queue.priority > 1""").rowcount
    n += conn.execute("""
        INSERT INTO tr.queue(eik, reason, priority, depth)
        SELECT DISTINCT left(eik, 9), 'offer', 2, 0 FROM (
          SELECT bidder_eik eik FROM eopsvc.offer WHERE bidder_eik IS NOT NULL
          UNION SELECT m->>'eik' FROM eopsvc.offer o, jsonb_array_elements(coalesce(o.consortium, '[]')) m WHERE m->>'eik' IS NOT NULL) s
        WHERE eik ~ '^([0-9]{9}|[0-9]{13})$'
        ON CONFLICT (eik) DO UPDATE SET priority = 2, depth = 0
        WHERE tr.queue.priority > 2""").rowcount
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
                "UPDATE tr.queue SET status='pending', next_at=now(), attempts=0, why='change' "
                "WHERE eik = ANY(%s) AND (status <> 'pending' OR why = 'rotate')", (uics,)).rowcount
        page += 1
        conn.execute("INSERT INTO tr.change_day(day, pass, next_page, done) VALUES (%s,%s,%s,%s) "
                     "ON CONFLICT (day, pass) DO UPDATE SET next_page=EXCLUDED.next_page, done=EXCLUDED.done",
                     (day, pass_no, page, not res["has_more"]))
        if not res["has_more"]:
            return touched
        time.sleep(30)  # the portal list is paced like SIGMA's (30 s per page)


def linked(conn, roles, persons):
    """A partida found by name search belongs to the tracked set only if one of its holders is a
    person hash or a company ЕИК we already hold; otherwise it is a namesake."""
    hashes = [p["indent"] for p in persons]
    eiks = [r["holder_id"] for r in roles if r["holder_kind"] == "entity" and R.UIC9.match(r["holder_id"] or "")]
    return bool(conn.execute(
        "SELECT EXISTS (SELECT 1 FROM tr.person WHERE indent = ANY(%s)) "
        "OR EXISTS (SELECT 1 FROM tr.deed WHERE status = 'ok' AND eik = ANY(%s))", (hashes, eiks)).fetchone()[0])


def store(conn, eik, xml, require_link=False):
    """Persist one successful read: raw XML, deed facts, roles (replaced), persons.
    With require_link, an unlinked partida is recorded as 'unrelated' with no roles."""
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
    if require_link and not linked(conn, roles, persons):
        conn.execute("INSERT INTO tr.deed(eik, status, fetched_at, name) VALUES (%s,'unrelated',%s,%s) "
                     "ON CONFLICT (eik) DO UPDATE SET status='unrelated', fetched_at=EXCLUDED.fetched_at, "
                     "name=EXCLUDED.name, error=NULL", (eik, now, facts["name"]))
        conn.execute("DELETE FROM tr.role WHERE eik=%s", (eik,))
        return []
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


# what the D-1 chain waits for: ЕИК of new contracts (priority 1) and partidas the change list named,
# never the rotation (a rotated partida of priority 1 is not new)
FIRST = "((priority <= 1 AND why IS NULL) OR why = 'change')"


def waiting(conn):
    return conn.execute(f"SELECT count(*) FROM tr.queue WHERE status='pending' AND next_at <= now() AND {FIRST}").fetchone()[0]


def process(conn, budget_s, stats, first=False):
    """Read queued partidas until the queue is empty or the time budget is spent (first: only FIRST)."""
    deadline = time.monotonic() + budget_s
    stats.setdefault("read", 0)
    stats.setdefault("absent", 0)
    stats.setdefault("errors", 0)
    while time.monotonic() < deadline:
        row = conn.execute(
            "SELECT eik, depth, attempts, reason, why, held_sha FROM tr.queue WHERE status='pending' AND next_at <= now() "
            + (f"AND {FIRST} " if first else "") + "ORDER BY why IS NOT DISTINCT FROM 'rotate', priority, enqueued_at LIMIT 1").fetchone()
        if not row:
            break
        eik, depth, attempts, reason, why, held = row
        try:
            xml = R.fetch_deed(eik)
            before = conn.execute("SELECT status, sha256 FROM tr.deed WHERE eik=%s", (eik,)).fetchone()
            if xml is None and before and before[0] == "ok" and held != "absent":
                # a partida we hold does not answer: keep it, ask again in a day
                log_change(conn, "tr", eik, "deed", "ok", "absent", "held")
                conn.execute("UPDATE tr.queue SET held_sha='absent', next_at=now() + interval '1 day' WHERE eik=%s", (eik,))
                stats["held"] = stats.get("held", 0) + 1
                time.sleep(PAUSE)
                continue
            if xml is None and before and before[0] == "ok":
                log_change(conn, "tr", eik, "deed", "ok", "absent", "confirmed")
            elif xml is not None and before and before[1] and before[1] != hashlib.sha256(xml).hexdigest():
                log_change(conn, "tr", eik, "deed", before[1], hashlib.sha256(xml).hexdigest(), why or "read")
                if why == "rotate":  # the change list did not name it
                    stats["rotate_missed"] = stats.get("rotate_missed", 0) + 1
            roles = store(conn, eik, xml, require_link=reason == "name_search")
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
        conn.execute("UPDATE tr.queue SET status='done', done_at=now(), attempts=0, last_error=NULL, why=NULL, held_sha=NULL "
                     "WHERE eik=%s", (eik,))
        stats["read" if xml else "absent"] += 1
        if depth < MAX_ENTITY_DEPTH:  # companies that hold roles are followed by ЕИК
            for r in roles:
                if r["holder_kind"] == "entity" and R.UIC9.match(r["holder_id"]):
                    enqueue(conn, r["holder_id"], "holder", 2, depth + 1)
        time.sleep(PAUSE)
    stats["pending"] = conn.execute("SELECT count(*) FROM tr.queue WHERE status='pending'").fetchone()[0]


def rotate(conn):
    """Queue the partidas read longest ago, 1/ROTATE_DAYS of all we hold per day, for a re-read after
    everything else (why='rotate'). Returns how many."""
    n = conn.execute("SELECT count(*) FROM tr.deed WHERE status = 'ok'").fetchone()[0]
    return conn.execute("""UPDATE tr.queue q SET status = 'pending', next_at = now(), attempts = 0, why = 'rotate'
        WHERE q.eik IN (SELECT d.eik FROM tr.deed d JOIN tr.queue q2 ON q2.eik = d.eik
                        WHERE d.status = 'ok' AND q2.status = 'done' AND d.fetched_at < now() - make_interval(days => %s)
                        ORDER BY d.fetched_at LIMIT %s)""", (ROTATE_DAYS // 2, -(-n // ROTATE_DAYS))).rowcount


def search_names(conn, budget_s, stats):
    """Find partidas outside the tracked set: search each person's name (their other companies) and
    each company's name (companies it holds), queue the hits. The read partida is kept only if a
    holder's hash or ЕИК links it (see store), never on the name alone."""
    deadline = time.monotonic() + budget_s
    stats.setdefault("names", 0)
    stats.setdefault("ambiguous", 0)
    stats.setdefault("queued", 0)
    start_n = stats["names"]
    while time.monotonic() < deadline:
        row = conn.execute("""
            WITH t AS (SELECT name_key k, min(name) name FROM tr.person WHERE name_key <> '' GROUP BY 1
                       UNION ALL SELECT 'eik:' || eik, name FROM tr.deed WHERE status = 'ok' AND name <> '')
            SELECT t.k, t.name FROM t LEFT JOIN tr.name_search s ON s.name_key = t.k
            WHERE s.name_key IS NULL OR s.searched_at < now() - %s
            ORDER BY s.searched_at NULLS FIRST LIMIT 1""", (NAME_SEARCH_EVERY,)).fetchone()
        if not row:
            break
        key, name = row
        is_company = key.startswith("eik:")
        target = name if is_company else R.without_title(name)
        want = company_key(name) if is_company else key
        match = company_key if is_company else (lambda v: R.person_name_key(R.without_title(v)))
        try:
            first = R.search_holders(target)
            total, items = first["total"], list(first["items"])
            status = "ambiguous" if total > MAX_NAME_HITS else "done"
            page = 2
            while status == "done" and len(items) < total:
                time.sleep(PAUSE)
                nxt = R.search_holders(target, page)
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
                if it["fieldIdent"] in R.ROLE_FIELDS and match(it["name"]) == want and it["uic"] != key[4:]:
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
    return stats["names"] - start_n


FORMS = re.compile(r"\b(ЕООД|ООД|ЕАД|АД|ЕТ|КДА|КД|СД|ДЗЗД|АДСИЦ|LTD|GMBH|LLC|AG)\b")


def company_key(value):
    """Company name without quotes and legal form, letters and digits only: '"ДИВА - 90" ООД' -> 'ДИВА 90'."""
    s = FORMS.sub(" ", str(value or "").upper())
    return " ".join(re.findall(r"[^\W_]+", s))


def work(conn, budget_s, stats):
    """The whole 'tr' lane in one budget: read what is queued and, while the queue is idle, run name
    searches (which queue more partidas), so the crawl does not wait for the night window."""
    deadline = time.monotonic() + budget_s
    while deadline - time.monotonic() > 30:
        process(conn, deadline - time.monotonic(), stats)
        left = deadline - time.monotonic()
        if left < 30:
            break
        if not search_names(conn, min(left, 300), stats):
            break  # nothing ready to read and nothing left to search


def prune(conn):
    """One-off: partidas read before the link rule stay only if they connect to a contract company
    through person hashes or company ЕИКs; the rest become 'unrelated', with roles and orphan persons dropped."""
    conn.execute("""CREATE TEMP TABLE conf AS SELECT DISTINCT left(eik, 9) eik FROM (
        SELECT eik FROM live.contract_supplier UNION SELECT eik FROM live.subcontract) s
        WHERE eik ~ '^[0-9]{9}([0-9]{4})?$'""")
    conn.execute("CREATE UNIQUE INDEX ON conf (eik)")
    conn.execute("""CREATE TEMP TABLE link AS SELECT eik, holder_id FROM tr.role
        WHERE holder_id ~ '^[0-9a-f]{64}$' OR (holder_kind = 'entity' AND holder_id ~ '^[0-9]{9}$')""")
    conn.execute("CREATE INDEX ON link (eik)")
    conn.execute("CREATE INDEX ON link (holder_id)")
    while conn.execute("""INSERT INTO conf SELECT x FROM (
              SELECT b.eik x FROM conf c JOIN link a ON a.eik = c.eik JOIN link b ON b.holder_id = a.holder_id
              UNION SELECT a.holder_id FROM conf c JOIN link a ON a.eik = c.eik WHERE a.holder_id ~ '^[0-9]{9}$'
              UNION SELECT a.eik FROM conf c JOIN link a ON a.holder_id = c.eik) y
            ON CONFLICT DO NOTHING""").rowcount:
        pass
    out = {"unrelated": conn.execute("""UPDATE tr.deed SET status = 'unrelated' WHERE status = 'ok'
        AND NOT EXISTS (SELECT 1 FROM conf WHERE conf.eik = tr.deed.eik)""").rowcount}
    out["roles_dropped"] = conn.execute(
        "DELETE FROM tr.role r USING tr.deed d WHERE d.eik = r.eik AND d.status = 'unrelated'").rowcount
    out["persons_dropped"] = conn.execute(
        "DELETE FROM tr.person p WHERE NOT EXISTS (SELECT 1 FROM tr.role r WHERE r.holder_id = p.indent)").rowcount
    return out
