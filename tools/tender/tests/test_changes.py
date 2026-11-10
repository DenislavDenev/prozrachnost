"""The change-catching lanes and the D-1 chain against a scratch database (docs/methodology.md, 9):
nothing we hold is lost on one answer, every difference is logged with its cause, the queues put first
what someone waits for, the chain waits, runs once a day and says when a source is late.

Runs only when TENDER_TEST_DSN points at a disposable database (it is wiped):
    TENDER_TEST_DSN=dbname=tender_test pytest tests/test_changes.py
"""
import datetime as dt
import os

import pytest

DSN = os.environ.get("TENDER_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TENDER_TEST_DSN not set")

YESTERDAY = dt.date.today() - dt.timedelta(days=1)


def log(conn, source=None):
    """The changes logged, without the one line the shared module writes for the first load of a resource."""
    return conn.execute("SELECT source, ref, field, old, new, cause FROM ops.change_log WHERE tool = 'tender' "
                        "AND NOT (field = 'rows' AND cause = 'new-record' AND old IS NULL) "
                        + ("AND source = %s " if source else "") + "ORDER BY change_id", (source,) if source else None).fetchall()


@pytest.fixture
def at_once(monkeypatch):
    """A held answer is confirmed by the next read that agrees, without the day between them."""
    from ingest import db
    monkeypatch.setattr(db, "HOLD", dt.timedelta(0))


def loads(conn, source):
    return conn.execute("SELECT status, rows, is_current FROM ops.load WHERE tool = 'tender' AND source = %s ORDER BY load_id", (source,)).fetchall()


# ---------- offers ----------

def answer(*offers):
    rows = [{"tender_id": 7, "lot_no": lot, "round": 1, "offer_id": oid, "bidder_name": name, "bidder_eik": None, "consortium": None,
             "submitted_at": None, "price": price, "price_opened": price is not None} for lot, oid, name, price in offers]
    return {"offers": rows, "contract_lots": [], "lots": []}


def test_offers_are_kept_until_a_second_read_agrees(conn, tmp_path, monkeypatch):
    from ingest import db, eop_offers as E
    monkeypatch.setattr(E, "RAW", tmp_path)
    conn.execute("INSERT INTO tender_src.eopsvc_queue (tender_id, reason) VALUES (7, 'backfill')")
    count = lambda: conn.execute("SELECT count(*) FROM tender_src.eopsvc_offer WHERE tender_id = 7").fetchone()[0]
    three = answer((1, 1, "А", 100), (1, 2, "Б", 110), (1, 3, "В", 120))
    assert E.store(conn, 7, three, b"a") == "stored" and count() == 3
    assert log(conn) == []                                            # a first read is not a change
    two = answer((1, 1, "А", 100), (1, 2, "Б", 110))
    assert E.store(conn, 7, two, b"b") == "held"
    assert count() == 3                                               # ours stays
    q = conn.execute("SELECT status, reason, next_at > now() + interval '23 hours' FROM tender_src.eopsvc_queue WHERE tender_id = 7").fetchone()
    assert q == ("pending", "confirm", True)
    assert log(conn)[-1][3:] == ("4", "3", "held")                    # rows of the answer: the offers and the page
    assert conn.execute("SELECT rows FROM ops.hold WHERE tool = 'tender' AND source = 'offers' AND ref = '7'").fetchone() == (3,)
    other = answer((1, 1, "А", 100), (1, 3, "В", 120))                # a different smaller answer is held again
    assert E.store(conn, 7, other, b"c") == "held" and count() == 3
    assert E.store(conn, 7, other, b"c") == "held" and count() == 3   # the same answer at once: a day has not passed
    monkeypatch.setattr(db, "HOLD", dt.timedelta(0))
    assert E.store(conn, 7, other, b"c") == "stored" and count() == 2  # the second read agrees
    assert [s for s, _, _ in loads(conn, "offers")] == ["loaded", "held", "held", "held", "confirmed"]
    assert conn.execute("SELECT count(DISTINCT load_id), count(*) FILTER (WHERE load_id IS NULL) FROM tender_src.eopsvc_offer").fetchone() == (1, 0)
    causes = [r[5] for r in log(conn)]
    assert causes[-2:] == ["confirmed", "rewritten"] and log(conn)[-1][1] == "7/1/1/2"   # offer 2 gone
    assert conn.execute("SELECT status, held_sha FROM tender_src.eopsvc_queue WHERE tender_id = 7").fetchone() == ("done", None)
    E.store(conn, 7, answer((1, 1, "А", 100), (1, 3, "В", 125)), b"d")   # a price moved
    assert log(conn)[-1] == ("offers", "7/1/1/3", "price", "120", "125", "rewritten")
    more = answer((1, 1, "А", 100), (1, 3, "В", 125), (1, 4, "Г", 90))  # more offers need no confirmation
    assert E.store(conn, 7, more, b"e") == "stored" and count() == 3
    # every answer is in the register of raw files, the held ones too, and the same answer is one file
    assert len(list(tmp_path.glob("store/*/*-7.json.gz"))) == 5
    assert conn.execute("SELECT count(*), count(DISTINCT raw_file_id) FROM ops.fetch WHERE tool = 'tender' AND source = 'offers'").fetchone() == (7, 5)
    E.store(conn, 7, more, b"e")                                        # read again, nothing new: no row is written
    assert loads(conn, "offers")[-1] == ("unchanged", 4, False)


def live_tables(conn):
    conn.execute("""CREATE TABLE tender.tender (unp text, tender_id text, submission_deadline timestamptz, published_at timestamptz, source_day date);
        CREATE TABLE tender.contract (id text, unp text, source_day date)""")


def test_offer_queue_reasons_and_order(conn, tmp_path, monkeypatch):
    from ingest import eop_offers as E
    live_tables(conn)
    now = dt.datetime.now(dt.timezone.utc)
    for unp, tid, deadline in (("U1", "1", now - dt.timedelta(days=3)), ("U2", "2", now - dt.timedelta(days=100)),
                               ("U3", "3", now + dt.timedelta(days=5)), ("U4", "4", now - dt.timedelta(days=200)),
                               ("U5", "5", now - dt.timedelta(days=100)), ("U6", "6", now - dt.timedelta(days=30))):
        conn.execute("INSERT INTO tender.tender VALUES (%s, %s, %s, %s, %s)", (unp, tid, deadline, deadline - dt.timedelta(days=30), dt.date(2025, 1, 1)))
    assert E.enqueue(conn) == {"new": 5, "update": 0}
    reasons = dict(conn.execute("SELECT tender_id, reason FROM tender_src.eopsvc_queue").fetchall())
    assert reasons == {1: "new", 2: "backfill", 4: "backfill", 5: "backfill", 6: "backfill"}   # 3: deadline ahead
    # 4 was read, then got a contract; 5 was read with closed prices; 6 was read 40 days ago
    conn.execute("UPDATE tender_src.eopsvc_queue SET status = 'done', fetched_at = now() - interval '10 days' WHERE tender_id IN (4, 5)")
    conn.execute("UPDATE tender_src.eopsvc_queue SET status = 'done', fetched_at = now() - interval '40 days' WHERE tender_id = 6")
    conn.execute("INSERT INTO tender.contract VALUES ('c', 'U4', current_date)")
    conn.execute("INSERT INTO tender_src.eopsvc_offer (tender_id, lot_no, round, offer_id, price_opened, fetched_at) VALUES (5, 0, 1, 1, false, now())")
    assert E.enqueue(conn) == {"new": 0, "update": 1}
    assert E.enqueue(conn, periodic=True) == {"new": 0, "update": 0, "prices": 1, "refresh": 1}
    reasons = dict(conn.execute("SELECT tender_id, reason FROM tender_src.eopsvc_queue").fetchall())
    assert (reasons[4], reasons[5], reasons[6]) == ("update", "prices", "refresh")
    conn.execute("UPDATE tender_src.eopsvc_queue SET reason = 'visit' WHERE tender_id = 2")
    assert E.waiting(conn) == 3                                         # visit 2, update 4, new 1
    read = []
    monkeypatch.setattr(E, "RAW", tmp_path)
    monkeypatch.setattr(E, "fetch", lambda tid, contracts: (read.append(tid), ({"offers": [], "contract_lots": [], "lots": []}, b"{}"))[1])
    E.work(conn, 30, {}, first=True)
    assert read == [2, 4, 1] and E.waiting(conn) == 0                    # only what the chain waits for, visitor first
    E.work(conn, 30, {})
    assert read[3:] == [5, 6]                                            # then closed prices, then refresh


# ---------- register ----------

def partida(eik, manager="ИВАН ПЕТРОВ"):
    return (f'<DeedResult><Deed UIC="{eik}" CompanyName="Х" DeedStatus="N" LegalForm="OOD">'
            f'<SubDeed SubUIC="0001" SubUICType="MainCircumstances" SubDeedStatus="A">'
            f'<Managers FieldIdent="00070" FieldOperation="Add" FieldEntryNumber="1" FieldEntryDate="2020-01-01T10:00:00">'
            f'<Manager RecordID="1"><Person><Indent>{"a" * 64}</Indent><Name>{manager}</Name><IndentType>EGN</IndentType></Person></Manager>'
            f'</Managers></SubDeed></Deed></DeedResult>').encode()


def test_a_partida_that_stops_answering_is_kept_until_confirmed(conn, tmp_path, monkeypatch):
    from ingest import registry as R, tr_worker as T
    monkeypatch.setattr(T, "RAW_TR", tmp_path)
    monkeypatch.setattr(T.time, "sleep", lambda s: None)
    answers = []
    monkeypatch.setattr(R, "fetch_deed", lambda eik: answers.pop(0))
    eik = "121212121"
    conn.execute("INSERT INTO tender_src.tr_queue (eik, reason, priority) VALUES (%s, 'contract', 1)", (eik,))
    answers.append(partida(eik))
    T.process(conn, 30, {})
    roles = lambda: conn.execute("SELECT count(*) FROM tender_src.tr_role WHERE eik = %s", (eik,)).fetchone()[0]
    status = lambda: conn.execute("SELECT status FROM tender_src.tr_deed WHERE eik = %s", (eik,)).fetchone()[0]
    assert status() == "ok" and roles() == 1 and log(conn) == []
    # the change list names it; the register does not answer
    conn.execute("UPDATE tender_src.tr_queue SET status = 'pending', why = 'change' WHERE eik = %s", (eik,))
    answers.append(None)
    st = {}
    T.process(conn, 30, st)
    assert status() == "ok" and roles() == 1 and st["held"] == 1
    assert conn.execute("SELECT held_sha, next_at > now() + interval '23 hours' FROM tender_src.tr_queue WHERE eik = %s", (eik,)).fetchone() == ("absent", True)
    assert log(conn)[-1][5] == "held"
    conn.execute("UPDATE tender_src.tr_queue SET next_at = now() WHERE eik = %s", (eik,))
    answers.append(None)
    T.process(conn, 30, {})
    assert status() == "absent" and log(conn)[-1][5] == "confirmed"
    # it answers again, changed, found by the rotation: the change list missed it
    conn.execute("UPDATE tender_src.tr_queue SET status = 'pending', why = 'rotate', held_sha = NULL WHERE eik = %s", (eik,))
    answers.append(partida(eik))
    T.process(conn, 30, {})
    conn.execute("UPDATE tender_src.tr_queue SET status = 'pending', why = 'rotate' WHERE eik = %s", (eik,))
    answers.append(partida(eik, "МАРИЯ ПЕТРОВА"))
    st = {}
    T.process(conn, 30, st)
    assert st["rotate_missed"] == 1 and log(conn)[-1][5] == "rotate"
    assert conn.execute("SELECT why, status FROM tender_src.tr_queue WHERE eik = %s", (eik,)).fetchone() == (None, "done")
    assert conn.execute("SELECT count(*) FILTER (WHERE load_id IS NULL), count(DISTINCT load_id) FROM tender_src.tr_role").fetchone() == (0, 1)
    assert len(list(tmp_path.glob("store/*/*.xml.gz"))) == 2             # the two answers, each once


def partida_of_two(eik):
    return partida(eik).replace(b"</Manager>", b"</Manager><Manager RecordID=\"2\"><Person><Indent>" + b"b" * 64
                                + b"</Indent><Name>\xd0\x9f\xd0\x95\xd0\xa2\xd0\xaa\xd0\xa0 \xd0\x98\xd0\x92\xd0\x90\xd0\x9d\xd0\x9e\xd0\x92</Name><IndentType>EGN</IndentType></Person></Manager>")


def test_a_partida_with_fewer_rows_is_held_until_a_second_read_agrees(conn, tmp_path, monkeypatch):
    from ingest import db, registry as R, tr_worker as T
    monkeypatch.setattr(T, "RAW_TR", tmp_path)
    monkeypatch.setattr(T.time, "sleep", lambda s: None)
    answers = []
    monkeypatch.setattr(R, "fetch_deed", lambda eik: answers.pop(0))
    eik = "121212121"
    conn.execute("INSERT INTO tender_src.tr_queue (eik, reason, priority) VALUES (%s, 'contract', 1)", (eik,))
    roles = lambda: conn.execute("SELECT count(*) FROM tender_src.tr_role WHERE eik = %s", (eik,)).fetchone()[0]
    again = lambda: conn.execute("UPDATE tender_src.tr_queue SET status = 'pending', next_at = now() WHERE eik = %s", (eik,))
    answers.append(partida_of_two(eik))
    T.process(conn, 30, {})
    assert roles() == 2
    answers.append(partida(eik))                                         # one manager is gone from the answer
    again()
    T.process(conn, 30, st := {})
    assert roles() == 2 and st["held"] == 1                              # ours stay
    assert conn.execute("SELECT status, held_sha, next_at > now() + interval '23 hours' FROM tender_src.tr_queue WHERE eik = %s", (eik,)).fetchone() == ("pending", "rows", True)
    monkeypatch.setattr(db, "HOLD", dt.timedelta(0))
    answers.append(partida(eik))
    again()
    T.process(conn, 30, {})
    assert roles() == 1 and [s for s, _, _ in loads(conn, "tr")] == ["loaded", "held", "confirmed"]
    assert conn.execute("SELECT status, held_sha FROM tender_src.tr_queue WHERE eik = %s", (eik,)).fetchone() == ("done", None)
    answers.append(partida(eik))                                         # the rotation reads it again, unchanged
    again()
    before = conn.execute("SELECT fetched_at FROM tender_src.tr_deed WHERE eik = %s", (eik,)).fetchone()[0]
    T.process(conn, 30, {})
    assert loads(conn, "tr")[-1][0] == "unchanged"
    assert conn.execute("SELECT fetched_at FROM tender_src.tr_deed WHERE eik = %s", (eik,)).fetchone()[0] > before   # so the rotation moves on


def test_the_change_list_is_read_beside_the_hourly_read_and_catches_up(conn, monkeypatch):
    """The portal's list of a day is long. A day a run did not finish is resumed at its page by the next run, an
    older day of the window that was never read is read too, and the step does not wait for the lane of the read."""
    from ingest import db, registry as R, tr_worker as T
    monkeypatch.setattr(T.time, "sleep", lambda s: None)
    today = dt.date(2026, 11, 10)
    asked = []

    def changes(day, page):
        asked.append((day, page))
        return {"items": [{"uic": "121212121"}] if page == 1 else [], "has_more": page < 3}
    monkeypatch.setattr(R, "changes", changes)
    conn.execute("INSERT INTO tender_src.tr_queue (eik, reason, priority, status) VALUES ('121212121', 'contract', 1, 'done')")
    conn.execute("INSERT INTO tender_src.tr_change_day (day, pass, next_page, done) VALUES ('2026-11-09', 1, 2, false), ('2026-11-08', 1, 4, true)")
    with db.job("tr-read", lane="tr"):                                   # the hourly read is running
        with db.job("tr-changes", lane="tr-changes") as (c, st):
            T.read_changes(c, 600, st, today)
    assert asked[:2] == [("2026-11-09", 2), ("2026-11-09", 3)]            # yesterday, from the page it stopped on
    assert ("2026-10-27", 1) in asked and ("2026-11-07", 1) in asked      # the second pass, and a day never read
    assert not [a for a in asked if a[0] == "2026-11-08"]                 # a day read to its end is not asked again
    assert conn.execute("SELECT count(*), bool_and(done) FROM tender_src.tr_change_day").fetchone() == (14, True)
    assert conn.execute("SELECT status, why FROM tender_src.tr_queue").fetchone() == ("pending", "change")
    asked.clear()
    T.read_changes(conn, 600, {}, today)
    assert asked == []                                                    # all read: nothing is asked twice
    T.read_changes(conn, 0, st := {}, today + dt.timedelta(days=1))       # no time left: the day waits for the next run
    assert st == {"left": 14} and asked == []


def test_rotation_queues_the_oldest_share_and_reads_it_last(conn):
    from ingest import tr_worker as T
    for i in range(120):
        eik = f"{100000000 + i}"
        conn.execute("INSERT INTO tender_src.tr_deed (eik, status, fetched_at) VALUES (%s, 'ok', now() - make_interval(days => %s))", (eik, i))
        conn.execute("INSERT INTO tender_src.tr_queue (eik, reason, priority, status) VALUES (%s, 'contract', 1, 'done')", (eik,))
    assert T.rotate(conn) == 2                                           # 120 / 60 days
    assert {r[0] for r in conn.execute("SELECT eik FROM tender_src.tr_queue WHERE why = 'rotate'")} == {"100000119", "100000118"}
    assert T.waiting(conn) == 0                                          # the chain does not wait for the rotation
    conn.execute("INSERT INTO tender_src.tr_queue (eik, reason, priority) VALUES ('999999999', 'contract', 1)")
    first = conn.execute("SELECT eik FROM tender_src.tr_queue WHERE status = 'pending' ORDER BY why IS NOT DISTINCT FROM 'rotate', priority, enqueued_at LIMIT 1").fetchone()
    assert first == ("999999999",) and T.waiting(conn) == 1


# ---------- СЕБРА ----------

def test_sebra_verify_hold_and_history(conn, tmp_path, monkeypatch, at_once):
    from ingest import sebra as S
    monkeypatch.setattr(S, "RAW", tmp_path)
    res = {"uri": "r1", "name": "За периода", "format": "csv", "updated_at": "2026-01-01T00:00:00+00:00"}
    monkeypatch.setattr(S, "resources", lambda: [res])
    body = {"raw": b""}

    def rows(n, tag=""):
        body["raw"] = f"{n}{tag}".encode()
        return [{"settlement_date": dt.date(2026, 1, 1), "receiver_name": f"Ф{i}", "is_person": False, "receiver_iban": None,
                 "fin_code": None, "fin_name": None, "amount": 10, "currency": "BGN", "reason": None, "reg_date": None,
                 "reg_no": None, "pay_code": None, "organization": "О", "primary_organization": "П", "primary_org_code": "1"} for i in range(n)]
    state = {"n": 3, "tag": ""}
    monkeypatch.setattr(S, "read_rows", lambda r: (rows(state["n"], state["tag"]), body["raw"]))
    monkeypatch.setattr(S, "parse", lambda recs: recs)
    count = lambda: conn.execute("SELECT count(*) FROM tender_src.sebra_payment").fetchone()[0]
    st = {}
    S.load(conn, st)
    assert count() == 3 and st["files"] == 1
    S.load(conn, st := {})
    assert st["files"] == 0                                              # same date: not downloaded
    S.load(conn, st := {}, verify=True)
    assert st["verified"] == 1 and st["files"] == 0 and log(conn) == []  # same bytes
    state.update(n=2)                                                    # replaced, same date, fewer rows
    S.load(conn, st := {}, verify=True)
    assert st["held"] == 1 and count() == 3 and log(conn)[-1][5] == "held"
    S.load(conn, st := {}, verify=True)
    assert st["files"] == 1 and count() == 2 and log(conn)[-1][5] == "confirmed"
    assert len(list(tmp_path.glob("*/*-r1.csv.gz"))) == 2                # both answers are kept, each under its sha256
    assert [s for s, _, _ in loads(conn, "sebra")] == ["loaded", "unchanged", "held", "confirmed"]
    assert conn.execute("SELECT count(*) FILTER (WHERE load_id IS NULL), count(DISTINCT load_id) FROM tender_src.sebra_payment").fetchone() == (0, 1)
    state.update(n=4)                                                    # more rows, same date: rewritten
    S.load(conn, st := {}, verify=True)
    assert count() == 4 and log(conn)[-1][3:] == ("2", "4", "rewritten")


# ---------- records between two builds ----------

def test_record_changes_between_builds(conn):
    from ingest import build, normalize as N
    cols = ", ".join(f"{f} text" for f in build.CONTRACT_FIELDS)
    tcols = ", ".join(f"{f} text" for f in build.TENDER_FIELDS)
    for s in ("tender", "tender_stage"):
        conn.execute(f"CREATE TABLE {s}.contract (id text, source_day date, annex_count int, {cols}); "
                     f"CREATE TABLE {s}.tender (unp text, source_day date, {tcols})")
    d1, d2 = dt.date(2026, 9, 1), dt.date(2026, 9, 20)
    conn.execute("""INSERT INTO tender.contract (id, source_day, annex_count, value_current, supplier_display) VALUES
        ('c1', %(d1)s, 0, '100', 'А'), ('c2', %(d1)s, 0, '50', 'Б'), ('c4', %(d1)s, 0, '100', 'Г')""", {"d1": d1})
    conn.execute("""INSERT INTO tender_stage.contract (id, source_day, annex_count, value_current, supplier_display) VALUES
        ('c1', %(d1)s, 0, '200', 'А'), ('c3', %(d1)s, 0, '1', 'В'), ('c4', %(d1)s, 1, '150', 'Г')""", {"d1": d1})
    conn.execute("INSERT INTO tender.tender (unp, source_day, submission_deadline) VALUES ('u1', %s, '2026-09-10'), ('u2', %s, '2026-09-10')", (d1, d1))
    conn.execute("INSERT INTO tender_stage.tender (unp, source_day, submission_deadline) VALUES ('u1', %s, '2026-09-12'), ('u2', %s, '2026-09-15')", (d1, d2))
    conn.execute("INSERT INTO tender_src.build_state (id, rules) VALUES (1, 'older rules')")
    assert "skipped" in build.record_changes(conn) and log(conn) == []    # our rules changed: not the source's change
    conn.execute("UPDATE tender_src.build_state SET rules = %s", (N.RULES_VERSION,))
    assert build.record_changes(conn) == {"contract": 2, "tender": 2, "removed": 1}
    got = {(r[1], r[2], r[5]) for r in log(conn)}
    assert got == {("contract/c1", "value_current", "rewritten"),          # same publication, other value
                   ("contract/c4", "value_current", "new-record"),         # an annex brought it
                   ("tender/u1", "submission_deadline", "rewritten"),
                   ("tender/u2", "submission_deadline", "new-record"),     # a later publication
                   ("contract/c2", None, "removed")}


# ---------- freshness and the D-1 chain ----------

def test_freshness_names_each_late_source(conn):
    from ingest import daily, db
    conn.execute("INSERT INTO tender_src.eop_day VALUES (%s, true, now())", (YESTERDAY,))
    conn.execute("INSERT INTO tender_src.build_state (id, at) VALUES (1, now())")
    stale = daily.freshness(conn)                                        # no source has been read: the shared freshness names each
    assert len(stale) == 5 and all("няма успешно четене" in b for b in stale), stale
    for source in ("eop-file", "offers", "tr", "sebra", "ecb"):
        db.land(conn, source, "x", {"a": 1}, lambda c, load_id, rows: None)
    assert daily.freshness(conn) == []
    conn.execute("UPDATE ops.load SET loaded_at = now() - interval '2 days' WHERE tool = 'tender' AND source = 'tr'")
    assert [b for b in daily.freshness(conn) if "Търговски регистър" in b]   # the register is read hourly: two days is late
    conn.execute("UPDATE ops.load SET loaded_at = now() WHERE tool = 'tender'")
    conn.execute("INSERT INTO ops.hold (tool, source, ref, content_sha256, rows, reason, first_at) VALUES ('tender', 'tr', '1', 'x', 1, 'липсват 2 от 3 реда', now() - interval '3 days')")
    assert [b for b in daily.freshness(conn) if "задържан" in b]          # an answer held past its time
    conn.execute("DELETE FROM ops.hold WHERE tool = 'tender'")
    conn.execute("UPDATE tender_src.build_state SET at = now() - interval '30 hours'")
    conn.execute("DELETE FROM tender_src.eop_day")
    conn.execute("INSERT INTO tender_src.eop_day VALUES (%s, true, now())", (YESTERDAY - dt.timedelta(days=2),))
    conn.execute("INSERT INTO tender_src.eopsvc_queue (tender_id, reason, next_at) VALUES (1, 'new', now() - interval '2 days'), (2, 'backfill', now() - interval '9 days')")
    conn.execute("INSERT INTO tender_src.tr_queue (eik, reason, priority, next_at) VALUES ('111111111', 'contract', 1, now() - interval '2 days')")
    conn.execute("INSERT INTO ops.change_log (tool, source, ref, cause) VALUES ('tender', 'eop-file', '2021-02-22/contracts', 'rewritten'), ('tender', 'offers', '1', 'held')")
    bad = daily.freshness(conn)
    assert len(bad) == 5, bad                                            # ЕОП, site, offers (not the backfill), ТР, archive
    assert any("2021" not in b and "ЕОП" in b for b in bad) and any("rewritten" in b for b in bad)


def test_daily_waits_runs_once_and_does_not_loop_on_failure(conn, monkeypatch):
    from ingest import daily, eop, eop_offers, tr_worker
    monkeypatch.setattr(eop, "list_day", lambda day: None)
    assert daily.run() == {"day": str(YESTERDAY), "waiting": True}
    with pytest.raises(RuntimeError, match="has not published"):
        daily.run(final=True)
    monkeypatch.setattr(eop, "list_day", lambda day: {"contracts": ("k", 1, "m")})
    calls = []
    monkeypatch.setattr(daily, "build_all", lambda out, steps=("eop", "fx", "normalize", "derive", "publish"): calls.append(steps))
    monkeypatch.setattr(tr_worker, "seed_from_contracts", lambda c: 0)
    monkeypatch.setattr(eop_offers, "enqueue", lambda c: {"new": 0, "update": 0})
    monkeypatch.setattr(eop_offers, "check", lambda c: {"ok": True})
    monkeypatch.setattr(daily, "drain", lambda *a: {"read_here": False, "left": 0})
    monkeypatch.setattr(daily, "freshness", lambda c: [])
    out = daily.run()
    assert out["offers_check"] and out["problems"] == [] and len(calls) == 2 and calls[1] == ("normalize", "derive", "publish")
    assert daily.run() == {"day": str(YESTERDAY), "skipped": "done"} and len(calls) == 2   # once a day
    # a late source: the chain finishes, the day counts as done, the problem is raised (the alert)
    conn.execute("DELETE FROM ops.run WHERE tool = 'tender'")
    monkeypatch.setattr(daily, "freshness", lambda c: ["ТР: 3 партиди чакат над ден"])
    with pytest.raises(daily.Problems):
        daily.run()
    assert daily.run()["skipped"] == "done"
    # a chain that breaks twice waits for the last call of the morning instead of rebuilding every 15 minutes
    conn.execute("DELETE FROM ops.run WHERE tool = 'tender'")
    monkeypatch.setattr(daily, "build_all", lambda out, steps=None: (_ for _ in ()).throw(RuntimeError("normalize broke")))
    for _ in range(2):
        with pytest.raises(RuntimeError, match="normalize broke"):
            daily.run()
    assert "failed 2 times" in daily.run()["skipped"]
    with pytest.raises(RuntimeError, match="normalize broke"):
        daily.run(final=True)


# ---------- completeness against ЦАИС ЕОП (audit 27.09.2026) ----------

def details(pubs, appeals=(), docs=()):
    return {"detail": {"offers_from": None, "offers_until": None, "opening_at": None, "prices_opening_at": None, "parent_tender_id": None,
                       "linked": None},
            "publications": [{"id": i, "form_type": 54, "ted_number": None, "sent_at": None, "published_at": None} for i in pubs],
            "appeals": [{"register_id": r, "proceedings_number": None, "kind": None, "subjects": [], "initiators": ["А ООД"], "defendants": [],
                         "interim_measures": True, "imposed_measures": 0, "imposed_penalties": 0, "status": s, "filed_on": None,
                         "started_on": None, "last_decision_on": None, "closed_on": None, "link": None} for r, s in appeals],
            "documents": [{"id": i, "name": f"d{i}.pdf", "size": 1, "created_at": None} for i in docs]}


def test_notices_appeals_documents_and_messages_are_never_dropped_on_one_answer(conn, tmp_path, monkeypatch):
    from ingest import eop_offers as E
    monkeypatch.setattr(E, "RAW", tmp_path)
    conn.execute("INSERT INTO tender_src.eopsvc_queue (tender_id, reason) VALUES (7, 'backfill')")
    got = lambda pubs, appeals, ann: dict(answer((1, 1, "А", 100)), details=details(pubs, appeals, [5]), announcements=ann)
    E.store(conn, 7, got([1, 2], [("ВХР-1", "в производство")], [(10, None, "Разяснение №1"), (11, None, "Решение за прекратяване")]), b"a")
    assert log(conn) == []                                                           # a first read is not a change
    gone = lambda t: conn.execute(f"SELECT count(*) FILTER (WHERE gone_at IS NOT NULL), count(*) FROM tender_src.eopsvc_{t} WHERE tender_id = 7").fetchone()
    # the page stops listing a notice and a message, the appeal moves on
    E.store(conn, 7, got([1], [("ВХР-1", "приключено производство")], [(10, None, "Разяснение №1")]), b"b")
    assert gone("publication") == (1, 2) and gone("announcement") == (1, 2) and gone("appeal") == (0, 1)
    causes = {(r[0], r[1], r[5]) for r in log(conn)}
    assert {("eop-publication", "7/2", "gone"), ("eop-announcement", "7/11", "gone"), ("eop-appeal", "7/ВХР-1", "rewritten")} <= causes
    # it comes back: kept, marked back, logged once
    E.store(conn, 7, got([1, 2], [("ВХР-1", "приключено производство")], [(10, None, "Разяснение №1"), (11, None, "Решение за прекратяване")]), b"c")
    assert gone("publication") == (0, 2) and gone("announcement") == (0, 2)
    assert sum(r[5] == "back" for r in log(conn)) == 2 and sum(r[5] == "gone" for r in log(conn)) == 2


def test_reparse_takes_the_participant_from_the_raw_answer_we_hold(conn, tmp_path, monkeypatch):
    import hashlib, json
    from pathlib import Path
    from ingest import eop_offers as E
    monkeypatch.setattr(E, "RAW", tmp_path)
    fx = json.loads((Path(__file__).parent / "fixtures" / "eop_svc" / "563386.json").read_text(encoding="utf-8"))
    from ingest import db
    blob = json.dumps({"participation": fx["participation"], "lots": fx["lots"]}, ensure_ascii=False).encode()
    conn.execute("INSERT INTO tender_src.eopsvc_queue (tender_id, reason, status, sha256) VALUES (563386, 'backfill', 'done', %s)",
                 (hashlib.sha256(blob).hexdigest(),))
    old = E.parse(563386, fx["participation"], fx["lots"])
    # the answer behind the current load; a later answer that is held is in the register too, and is not ours yet
    db.land(conn, "offers", 563386, {str(i): 1 for i in range(len(old) + 1)}, lambda c, load_id, rows: None,
            db.keep_raw(conn, "offers", 563386, blob, tmp_path / "store", "563386.json"))
    db.land(conn, "offers", 563386, {"page": 1}, lambda c, load_id, rows: None,
            db.keep_raw(conn, "offers", 563386, b'{"participation": null}', tmp_path / "store", "563386.json"))
    assert [s for s, _, _ in loads(conn, "offers")] == ["loaded", "held"]
    for r in old:   # as the reader before 27.09.2026 stored them: the submitting account as the participant
        o = next(x for rd in fx["participation"]["Rounds"] for x in rd["Offers"] if x["OfferId"] == r["offer_id"])
        r.update(bidder_name=o["OrganizationName"], bidder_eik=E.eik(o["RegistryNumber"]))
    E.write_offers(conn, old, dt.datetime(2026, 9, 27, tzinfo=dt.timezone.utc))
    E.reparse(conn, st := {})
    assert st == {"procedures": 1, "changed": 4, "missing_raw": 0}         # the ДЗЗД and the branch: name and ЕИК each
    names = {r[0] for r in conn.execute("SELECT bidder_name FROM tender_src.eopsvc_offer WHERE tender_id = 563386")}
    assert '"ЗА ЧИСТА ВАРНА" ДЗЗД' in names and not any("IVAN" in n for n in names)
    assert {r[5] for r in log(conn)} == {"held", "reparsed"}
    assert conn.execute("SELECT min(fetched_at) FROM tender_src.eopsvc_offer").fetchone()[0] == dt.datetime(2026, 9, 27, tzinfo=dt.timezone.utc)


def build_small(conn, res):
    from ingest import build
    with conn.transaction():
        build.start_stage(conn)
        for t in ("buyer", "tender", "lot", "contract", "contract_supplier", "amendment", "subcontract", "source_record",
                  "award", "notice", "annex_orphan"):
            build._copy(conn, t, res[t])
    build.step_derive(conn, {})
    __import__("conftest").landed(conn)
    build.step_publish(conn, {})


def test_termination_form_names_and_bidders_without_eik_in_the_build(conn):
    from ingest import normalize as N
    T = lambda unp, tid, lot=None, **kw: {"uniqueProcurementNumber": unp, "tenderId": tid, "lotIdentifier": lot, "isLot": "Да" if lot else "Не",
                                           "publicationDate": "2026-05-01T10:00:00", "subject": unp, "noticeId": f"{tid}0{lot or ''}",
                                           "noticeType": "Обявление за поръчка – Общата директива, стандартен режим",
                                           "buyerRegistryNumber": "000093442", "buyerName": "ОБЩИНА", **kw}
    tenders = [T("W", "1001"), T("P", "1002"), T("P", "1002", "LOT-0001"), T("P", "1002", "LOT-0002"), T("K", "1003")]
    won = {"noAwarding": "Не", "uniqueProcurementNumber": "K", "tenderId": "1003", "contractNumber": "1", "contractValue": "10",
           "contractCurrency": "EUR", "supplierName": "Б ЕООД", "supplierRegisterNumber": "831641791", "contractDate": "01.07.2026",
           "noticeId": "5", "publicationDate": "2026-07-02T10:00:00"}
    no_award = {"noAwarding": "Да", "uniqueProcurementNumber": "P", "tenderId": "1002", "lotIdentifier": "LOT-0001", "noticeId": "6",
                "publicationDate": "2026-07-03T10:00:00", "offersCount": 3}
    res = N.normalize([("2026-05-01", "tenders", tenders), ("2026-07-02", "contracts", [won, no_award])], N.Fx({}))
    for tid, aid, title in ((1001, 1, "РЕШЕНИЕ ЗА ПРЕКРАТЯВАНЕ на процедурата"), (1002, 2, "Решение за прекратяване на обособена позиция № 2"),
                            (1003, 3, "Решение за прекратяване"), (1001, 4, "Решение за отмяна на решение за прекратяване")):
        conn.execute("INSERT INTO tender_src.eopsvc_announcement (tender_id, id, title) VALUES (%s, %s, %s)", (tid, aid, title))
    conn.execute("""INSERT INTO tender_src.eopsvc_publication (tender_id, id, form_type) VALUES (1001, 10010, 54), (1001, 900, 32), (1001, 901, 77)""")
    conn.execute("""INSERT INTO tender_src.eopsvc_offer (tender_id, lot_no, round, offer_id, bidder_name, bidder_eik, price_opened, fetched_at) VALUES
        (1001, 0, 1, 1, 'Б ЕООД', NULL, false, now()), (1001, 0, 1, 2, 'Физическо лице', NULL, false, now())""")
    build_small(conn, res)
    q = lambda sql: conn.execute(sql).fetchall()
    assert dict(q("SELECT unp, state FROM tender.tender")) == {"W": "cancelled", "P": "unawarded", "K": "contracted"}  # a contract wins
    assert dict(q("SELECT lot_no, status FROM tender.lot WHERE unp = 'P'")) == {1: "unawarded", 2: "cancelled"}  # only the lot it names
    assert dict(q("SELECT form_type, name FROM tender.form_type")) == {54: "Обявление за поръчка – Общата директива, стандартен режим",
                                                                     32: "Решение по чл. 22, ал. 1 от ЗОП"}  # 77 unnamed: shown as „Публикация“
    assert dict(q("SELECT offer_id, company_key FROM tender.offer")) == {1: "eik:831641791", 2: None}          # by the only company of the name
    # the pages render with all of it (a template error is a 500 for every procedure)
    for unp, word in (("W", "прекратена"), ("P", "3 оферти по обявлението"), ("K", "с договор")):
        assert word in page(f"/tenders/{unp}")
    # Обратна връзка is the last thing in the header, Подкрепи проекта the last in the footer (every tool)
    html = page("/tenders/K")
    assert html.split("</header>")[0].rstrip().endswith("</script>\n</div>") and 'action="/feedback"' in html
    assert html.split("</footer>")[0].rstrip().endswith('href="http://192.168.1.68:8001/podkrepi">Подкрепи проекта</a>\n</div>')


def page(path):
    """Render a page through the app's route, without a server (httpx is not installed for TestClient)."""
    from starlette.requests import Request
    from app import main
    route = next(r for r in main.app.routes if getattr(r, "path", None) == path.rsplit("/", 1)[0] + "/{unp}")
    req = Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b"", "app": main.app})
    resp = route.endpoint(req, path.rsplit("/", 1)[1])
    assert resp.status_code == 200
    return resp.body.decode()


def test_completeness_names_what_one_source_has_and_the_other_not(conn):
    from ingest import eop_offers as E
    from ingest import normalize as N
    t = {"uniqueProcurementNumber": "W", "tenderId": "1001", "publicationDate": "2026-05-01T10:00:00", "subject": "x", "noticeId": "10010",
         "noticeType": "Обявление", "buyerRegistryNumber": "000093442", "buyerName": "ОБЩИНА"}
    award = dict(t, noAwarding="Да", noticeId="10011")
    build_small(conn, N.normalize([("2026-05-01", "tenders", [t]), ("2026-08-01", "contracts", [award])], N.Fx({})))
    conn.execute("INSERT INTO tender_src.eopsvc_queue (tender_id, reason, status, fetched_at) VALUES (1001, 'backfill', 'done', now())")
    conn.execute("INSERT INTO tender_src.eopsvc_detail (tender_id, fetched_at) VALUES (1001, now())")
    conn.execute("INSERT INTO tender_src.eopsvc_publication (tender_id, id) VALUES (1001, 10010)")            # the award notice is missing on the page
    conn.execute("INSERT INTO tender_src.eopsvc_contract_lot VALUES (1001, 0, '77')")                          # a contract only on the page
    m = E.mismatches(conn)
    assert (m["notice_not_on_page"], m["contracts_only_on_page"]) == (1, 1) and m["notice_examples"] == [[1001, "10011"]]
    E.mismatches(conn)
    assert [r[1] for r in log(conn, "completeness")] == ["1001/notice/10011", "1001/contracts"]   # logged once


def test_a_procedure_known_only_from_its_contracts_is_queued(conn):
    from ingest import eop_offers as E
    live_tables(conn)
    conn.execute("INSERT INTO tender.tender VALUES ('S', '777', NULL, NULL, '2024-03-01')")   # no deadline, no notice date
    assert E.enqueue(conn)["new"] == 1
    assert conn.execute("SELECT reason FROM tender_src.eopsvc_queue WHERE tender_id = 777").fetchone() == ("backfill",)


def test_the_map_by_supplier_places_each_member_and_says_what_it_cannot(conn):
    # the seat codes of the award notice: one per member in their order; ZZZ is a country only; 00 is no country
    from starlette.requests import Request
    from app import main
    conn.execute("""DROP TABLE pub_tender.contract CASCADE; DROP TABLE pub_tender.contract_supplier; DROP TABLE pub_tender.buyer;
        CREATE TABLE pub_tender.contract (id text, buyer_eik text, amount_eur numeric, is_framework bool, offers_count int, supplier_nuts text);
        CREATE TABLE pub_tender.contract_supplier (contract_id text, position int, party_key text, name text);
        CREATE TABLE pub_tender.buyer (eik text, name text)""")
    conn.execute("""INSERT INTO pub_tender.contract VALUES
        ('a', 'B1', 100, false, 1, 'CZ; BG411'),   -- a consortium: Czechia and Sofia
        ('b', 'B1', 50, false, 2, 'BG411'),
        ('c', 'B2', 30, false, 1, 'DE111'),
        ('d', 'B2', 20, false, 1, 'ATZZZ'),         -- Austria, region not given
        ('e', 'B2', 7, false, 1, '00'),             -- no country
        ('f', 'B2', 5, false, 1, 'US'),             -- abroad, off the map of Europe
        ('g', 'B2', 1000, true, 1, 'BG412')         -- a framework agreement: counted, not summed""")
    conn.execute("""INSERT INTO pub_tender.contract_supplier VALUES ('a', 0, 'name:CZ', 'ES Group'), ('a', 1, 'eik:1', 'СОФИЯ ООД'),
        ('b', 0, 'eik:1', 'СОФИЯ ООД'), ('c', 0, 'name:DE', 'Stuttgart GmbH'), ('d', 0, 'name:AT', 'Netz')""")
    conn.execute("INSERT INTO pub_tender.buyer VALUES ('B1', 'ОБЩИНА'), ('B2', 'МИНИСТЕРСТВО')")
    by = lambda out: {a["id"]: (a["n"], float(a["eur"] or 0)) for a in out["areas"]}
    eu = main.supplier_map("true", [], "country", "eu")
    assert by(eu) == {"BG": (3, 150.0), "CZ": (1, 100.0), "DE": (1, 30.0), "AT": (1, 20.0), "US": (1, 5.0)}
    assert eu["names"]["US"] == "САЩ" and float(eu["cover"]["unknown"]) == 7        # "00" is not on any map
    assert by(main.supplier_map("true", [], "oblast", "eu")) == {"BG411": (2, 150.0), "BG412": (1, 0.0), "DE111": (1, 30.0)}
    bg = main.supplier_map("true", [], "oblast", "bg")
    assert by(bg) == {"BG411": (2, 150.0), "BG412": (1, 0.0)}
    assert (float(bg["cover"]["placed"]), float(bg["cover"]["eur"])) == (150.0, 212.0)     # the rest is abroad or unknown
    req = Request({"type": "http", "method": "GET", "path": "/map/CZ.json", "headers": [], "query_string": b"by=supplier", "app": main.app})
    import json
    d = json.loads(main.map_detail(req, "CZ", by="supplier").body)
    assert [s["name"] for s in d["suppliers"]] == ["ES Group"] and [b["eik"] for b in d["buyers"]] == ["B1"]   # only the Czech member
    world = main.supplier_map("true", [], "country", "world")                    # the world: countries only
    assert by(world) == by(eu) and world["names"]["CN"] == "Китай"
    for bad in (("country", "bg"), ("muni", "eu"), ("oblast", "xx"), ("oblast", "world")):
        with pytest.raises(main.HTTPException):
            main.supplier_map("true", [], *bad)


# ---------- the files of the open data, and what was read before the loads ----------

def day_folder(raw, day, rows, kind="contracts"):
    import hashlib, json
    body = json.dumps(rows).encode()
    (raw / day).mkdir(parents=True, exist_ok=True)
    tmp = raw / day / f"{kind}.tmp"                                      # as the reader writes: a new file in place of the old
    tmp.write_bytes(body)
    tmp.replace(raw / day / f"{kind}.json")
    f = {"key": "k", "size": len(body), "sha256": hashlib.sha256(body).hexdigest(), "modified": None}
    (raw / day / "manifest.json").write_text(json.dumps({"day": day, "published": True, "files": {kind: f}, "changed": [], "invalid": [],
                                                         "fetched_at": "2026-09-01T06:00:00+00:00"}), encoding="utf-8")
    return f


def test_the_build_reads_the_loaded_file_not_the_newest_answer(conn, tmp_path, monkeypatch):
    """A file of a day that comes back shorter is on disk as the newest answer, and is not ours until a second read
    agrees: the build and the pages go on reading the file of the current load."""
    from ingest import build, db, eop
    monkeypatch.setattr(eop, "RAW_EOP", tmp_path)
    now = dt.datetime.now(dt.timezone.utc)
    three = [{"contractNumber": str(i)} for i in range(3)]
    read = lambda: [(d, k, len(rows)) for d, k, rows in build._days_rows(conn) if rows]
    assert build.land_file(conn, "2024-05-01", "contracts", day_folder(tmp_path, "2024-05-01", three), now) == "loaded"
    assert read() == [("2024-05-01", "contracts", 3)]
    first = conn.execute("SELECT sha256, rows, load_id FROM tender_src.eop_file").fetchone()
    shorter = day_folder(tmp_path, "2024-05-01", three[:2])              # the source rewrote the day with fewer records
    assert build.land_file(conn, "2024-05-01", "contracts", shorter, now) == "held"
    assert read() == [("2024-05-01", "contracts", 3)] and conn.execute("SELECT sha256, rows, load_id FROM tender_src.eop_file").fetchone() == first
    assert len(eop.read_rows("2024-05-01", "contracts")) == 2 and len(eop.read_rows("2024-05-01", "contracts", first[0])) == 3
    monkeypatch.setattr(db, "HOLD", dt.timedelta(0))
    assert build.land_file(conn, "2024-05-01", "contracts", shorter, now) == "confirmed"
    assert read() == [("2024-05-01", "contracts", 2)]
    assert build.land_file(conn, "2024-05-01", "contracts", shorter, now) == "unchanged"
    # a file that is JSON of another shape is recorded and replaces nothing; an empty file is a load
    assert build.land_file(conn, "2024-05-01", "contracts", day_folder(tmp_path, "2024-05-01", ["not a record"]), now) == "rejected"
    assert read() == [("2024-05-01", "contracts", 2)]
    assert build.land_file(conn, "2024-05-02", "annexes", day_folder(tmp_path, "2024-05-02", [], "annexes"), now) == "loaded"
    # a file without a load stops the build instead of being left out of it
    conn.execute("INSERT INTO tender_src.eop_file (day, kind, key, sha256, size, fetched_at) VALUES ('2024-05-03', 'contracts', 'k', 'x', 1, now())")
    with pytest.raises(RuntimeError, match="adopt"):
        read()


def test_adopt_gives_a_load_to_what_was_read_before(conn, tmp_path, monkeypatch):
    """What the readers wrote before they landed through the shared module: each kept answer is read once more and
    becomes the load of its rows, dated when it was read. An answer that is not the one the table was written from
    is not taken; its resource is queued for a new read."""
    import gzip, hashlib, json
    from pathlib import Path
    from ingest import adopt, eop, eop_offers as E, fx, sebra as S
    monkeypatch.setattr(eop, "RAW_EOP", tmp_path / "eop")
    monkeypatch.setattr(adopt, "RAW_TR", tmp_path / "tr")
    monkeypatch.setattr(E, "RAW", tmp_path / "svc")
    monkeypatch.setattr(S, "RAW", tmp_path / "sebra")
    monkeypatch.setattr(fx, "refresh", lambda c: 0)
    read_at = dt.datetime(2026, 9, 1, 6, tzinfo=dt.timezone.utc)
    # a day of the open data, as step_eop recorded it
    f = day_folder(tmp_path / "eop", "2024-05-01", [{"contractNumber": "1"}])
    conn.execute("INSERT INTO tender_src.eop_file (day, kind, key, sha256, size, fetched_at) VALUES ('2024-05-01', 'contracts', 'k', %s, %s, %s)",
                 (f["sha256"], f["size"], read_at))
    # two partidas: one whose kept XML is the one the table names, one whose kept XML is another
    for eik, kept in (("121212121", partida("121212121")), ("131313131", partida("131313131", "ДРУГ"))):
        xml = partida(eik)
        path = tmp_path / "tr" / "deeds" / eik[:3] / f"{eik}.xml.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(kept))
        conn.execute("INSERT INTO tender_src.tr_deed (eik, status, fetched_at, sha256) VALUES (%s, 'ok', %s, %s)", (eik, read_at, hashlib.sha256(xml).hexdigest()))
        conn.execute("INSERT INTO tender_src.tr_queue (eik, reason, priority, status) VALUES (%s, 'contract', 1, 'done')", (eik,))
        conn.execute("""INSERT INTO tender_src.tr_role (eik, sub_uic, field_ident, role, holder_kind, holder_id, holder_name, entry_no, valid_from, observed_at)
            VALUES (%s, '0001', '00070', 'manager', 'person', %s, 'ИВАН ПЕТРОВ', '1', '2020-01-01', %s)""", (eik, "a" * 64, read_at))
    conn.execute("INSERT INTO tender_src.tr_person (id, indent, name, name_key) VALUES ('T1', %s, 'ИВАН ПЕТРОВ', 'ИВАН ПЕТРОВ')", ("a" * 64,))
    # a procedure: the answer the queue names, and a later one that was held
    fxt = json.loads((Path(__file__).parent / "fixtures" / "eop_svc" / "563386.json").read_text(encoding="utf-8"))
    blob = json.dumps({"participation": fxt["participation"], "lots": fxt["lots"]}, ensure_ascii=False).encode()
    (tmp_path / "svc" / "563386").mkdir(parents=True)
    (tmp_path / "svc" / "563386" / "202609010600.json.gz").write_bytes(gzip.compress(blob))
    (tmp_path / "svc" / "563386" / "202609020600.json.gz").write_bytes(gzip.compress(b'{"participation": null}'))
    conn.execute("INSERT INTO tender_src.eopsvc_queue (tender_id, reason, status, sha256, fetched_at) VALUES (563386, 'backfill', 'done', %s, %s)",
                 (hashlib.sha256(blob).hexdigest(), read_at))
    offers = E.parse(563386, fxt["participation"], fxt["lots"])
    E.write_offers(conn, offers, read_at)
    # a period of the payments
    res = {"success": True, "data": [S.COLS, ["01.02.2026", "ФИРМА ООД", "BG00", "BIC", "1", "Банка", "10,50", "BGN", "о1", "о2", "01.02.2026", "7", "10", "О", "П", "1"]]}
    raw = json.dumps(res, ensure_ascii=False).encode()
    (tmp_path / "sebra").mkdir()
    (tmp_path / "sebra" / "r1.csv.gz").write_bytes(gzip.compress(raw))
    conn.execute("INSERT INTO tender_src.sebra_resource (uri, name, format, sha256, rows, loaded_at) VALUES ('r1', 'За периода', 'csv', %s, 1, %s)",
                 (hashlib.sha256(raw).hexdigest(), read_at))
    conn.execute("INSERT INTO tender_src.sebra_payment (resource_uri, row_no, is_person, amount) VALUES ('r1', 0, false, 10.5)")

    adopt.run(conn, st := {})
    assert (st["tr"], st["tr_missing_raw"], st["offers"], st["sebra_files"], st["eop_loaded"]) == (1, 1, 1, 1, 1), st
    assert st["without_load"] == {"eop_file": 0, "fx_rate": 0, "tr_deed": 1, "tr_role": 1, "eopsvc_offer": 0, "eopsvc_publication": 0,
                                  "eopsvc_document": 0, "sebra_payment": 0}
    # each load is dated when the answer was read, points at the kept file, and the rows carry it
    got = conn.execute("""SELECT l.source, l.ref, l.loaded_at, f.path FROM ops.load l JOIN ops.raw_file f USING (raw_file_id)
        WHERE l.tool = 'tender' AND l.is_current AND l.source IN ('tr', 'offers') ORDER BY 1""").fetchall()
    assert [(s, r, at) for s, r, at, _ in got] == [("offers", "563386", read_at), ("tr", "121212121", read_at)]
    assert got[0][3].endswith("202609010600.json.gz") and got[1][3].endswith("121212121.xml.gz")
    assert conn.execute("SELECT count(*) FILTER (WHERE load_id IS NULL), count(*) FROM tender_src.eopsvc_offer").fetchone() == (0, len(offers))
    assert conn.execute("SELECT eik FROM tender_src.tr_role WHERE load_id IS NULL").fetchall() == [("131313131",)]
    assert conn.execute("SELECT load_id IS NOT NULL FROM tender_src.tr_person").fetchone() == (True,)
    assert conn.execute("SELECT amount, load_id IS NOT NULL, receiver_name FROM tender_src.sebra_payment").fetchall() == [(10.5, True, "ФИРМА ООД")]
    # the partida without its answer is queued for a new read, after what someone waits for
    assert conn.execute("SELECT eik, status, why FROM tender_src.tr_queue ORDER BY 1").fetchall() == [
        ("121212121", "done", None), ("131313131", "pending", "rotate")]
    # the rows of a load are those a read today would make: the same answer again is unchanged, and reparse finds it
    assert E.store(conn, 563386, E.parsed(563386, json.loads(blob)), blob) == "stored"
    assert loads(conn, "offers")[-1][0] == "unchanged"
    E.reparse(conn, rp := {})
    assert rp == {"procedures": 1, "changed": 0, "missing_raw": 0}
    # again: nothing to do
    adopt.run(conn, st2 := {})
    assert st2["tr"] == 0 and "offers" not in st2 and st2["sebra_files"] == 0 and "eop_loaded" not in st2
