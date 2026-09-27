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
if DSN:
    os.environ["TENDER_DSN"] = DSN

YESTERDAY = dt.date.today() - dt.timedelta(days=1)


@pytest.fixture
def conn():
    from ingest import db
    with db.connect(autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS live, stage, previous, ops, tr, ed, eopsvc, sebra CASCADE; "
                  "DROP TABLE IF EXISTS public.schema_migrations")
        db.migrate(c)
        yield c


def log(conn, source=None):
    return conn.execute("SELECT source, ref, field, old, new, cause FROM ops.change_log "
                        + ("WHERE source = %s " if source else "") + "ORDER BY id", (source,) if source else None).fetchall()


# ---------- offers ----------

def answer(*offers):
    rows = [{"tender_id": 7, "lot_no": lot, "round": 1, "offer_id": oid, "bidder_name": name, "bidder_eik": None, "consortium": None,
             "submitted_at": None, "price": price, "price_opened": price is not None} for lot, oid, name, price in offers]
    return {"offers": rows, "contract_lots": [], "lots": []}


def test_offers_are_kept_until_a_second_read_agrees(conn, tmp_path, monkeypatch):
    from ingest import eop_offers as E
    monkeypatch.setattr(E, "RAW", tmp_path)
    conn.execute("INSERT INTO eopsvc.queue (tender_id, reason) VALUES (7, 'backfill')")
    count = lambda: conn.execute("SELECT count(*) FROM eopsvc.offer WHERE tender_id = 7").fetchone()[0]
    three = answer((1, 1, "А", 100), (1, 2, "Б", 110), (1, 3, "В", 120))
    assert E.store(conn, 7, three, b"a") == "stored" and count() == 3
    assert log(conn) == []                                            # a first read is not a change
    two = answer((1, 1, "А", 100), (1, 2, "Б", 110))
    assert E.store(conn, 7, two, b"b") == "held"
    assert count() == 3                                               # ours stays
    q = conn.execute("SELECT status, reason, next_at > now() + interval '23 hours' FROM eopsvc.queue WHERE tender_id = 7").fetchone()
    assert q == ("pending", "confirm", True)
    assert log(conn)[-1][4:] == ("2", "held")
    other = answer((1, 1, "А", 100), (1, 3, "В", 120))                # a different smaller answer is held again
    assert E.store(conn, 7, other, b"c") == "held" and count() == 3
    assert E.store(conn, 7, other, b"c") == "stored" and count() == 2  # the second read agrees
    causes = [r[5] for r in log(conn)]
    assert causes[-2:] == ["confirmed", "rewritten"] and log(conn)[-1][1] == "7/1/1/2"   # offer 2 gone
    assert conn.execute("SELECT status, held_sha FROM eopsvc.queue WHERE tender_id = 7").fetchone() == ("done", None)
    E.store(conn, 7, answer((1, 1, "А", 100), (1, 3, "В", 125)), b"d")   # a price moved
    assert log(conn)[-1] == ("offers", "7/1/1/3", "price", "120", "125", "rewritten")
    more = answer((1, 1, "А", 100), (1, 3, "В", 125), (1, 4, "Г", 90))  # more offers need no confirmation
    assert E.store(conn, 7, more, b"e") == "stored" and count() == 3
    assert len(list(tmp_path.glob("7/*.json.gz"))) >= 1


def live_tables(conn):
    conn.execute("""CREATE SCHEMA live;
        CREATE TABLE live.tender (unp text, tender_id text, submission_deadline timestamptz, published_at timestamptz, source_day date);
        CREATE TABLE live.contract (id text, unp text, source_day date)""")


def test_offer_queue_reasons_and_order(conn, tmp_path, monkeypatch):
    from ingest import eop_offers as E
    live_tables(conn)
    now = dt.datetime.now(dt.timezone.utc)
    for unp, tid, deadline in (("U1", "1", now - dt.timedelta(days=3)), ("U2", "2", now - dt.timedelta(days=100)),
                               ("U3", "3", now + dt.timedelta(days=5)), ("U4", "4", now - dt.timedelta(days=200)),
                               ("U5", "5", now - dt.timedelta(days=100)), ("U6", "6", now - dt.timedelta(days=30))):
        conn.execute("INSERT INTO live.tender VALUES (%s, %s, %s, %s, %s)", (unp, tid, deadline, deadline - dt.timedelta(days=30), dt.date(2025, 1, 1)))
    assert E.enqueue(conn) == {"new": 5, "update": 0}
    reasons = dict(conn.execute("SELECT tender_id, reason FROM eopsvc.queue").fetchall())
    assert reasons == {1: "new", 2: "backfill", 4: "backfill", 5: "backfill", 6: "backfill"}   # 3: deadline ahead
    # 4 was read, then got a contract; 5 was read with closed prices; 6 was read 40 days ago
    conn.execute("UPDATE eopsvc.queue SET status = 'done', fetched_at = now() - interval '10 days' WHERE tender_id IN (4, 5)")
    conn.execute("UPDATE eopsvc.queue SET status = 'done', fetched_at = now() - interval '40 days' WHERE tender_id = 6")
    conn.execute("INSERT INTO live.contract VALUES ('c', 'U4', current_date)")
    conn.execute("INSERT INTO eopsvc.offer (tender_id, lot_no, round, offer_id, price_opened, fetched_at) VALUES (5, 0, 1, 1, false, now())")
    assert E.enqueue(conn) == {"new": 0, "update": 1}
    assert E.enqueue(conn, periodic=True) == {"new": 0, "update": 0, "prices": 1, "refresh": 1}
    reasons = dict(conn.execute("SELECT tender_id, reason FROM eopsvc.queue").fetchall())
    assert (reasons[4], reasons[5], reasons[6]) == ("update", "prices", "refresh")
    conn.execute("UPDATE eopsvc.queue SET reason = 'visit' WHERE tender_id = 2")
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
    conn.execute("INSERT INTO tr.queue (eik, reason, priority) VALUES (%s, 'contract', 1)", (eik,))
    answers.append(partida(eik))
    T.process(conn, 30, {})
    roles = lambda: conn.execute("SELECT count(*) FROM tr.role WHERE eik = %s", (eik,)).fetchone()[0]
    status = lambda: conn.execute("SELECT status FROM tr.deed WHERE eik = %s", (eik,)).fetchone()[0]
    assert status() == "ok" and roles() == 1 and log(conn) == []
    # the change list names it; the register does not answer
    conn.execute("UPDATE tr.queue SET status = 'pending', why = 'change' WHERE eik = %s", (eik,))
    answers.append(None)
    st = {}
    T.process(conn, 30, st)
    assert status() == "ok" and roles() == 1 and st["held"] == 1
    assert conn.execute("SELECT held_sha, next_at > now() + interval '23 hours' FROM tr.queue WHERE eik = %s", (eik,)).fetchone() == ("absent", True)
    assert log(conn)[-1][5] == "held"
    conn.execute("UPDATE tr.queue SET next_at = now() WHERE eik = %s", (eik,))
    answers.append(None)
    T.process(conn, 30, {})
    assert status() == "absent" and log(conn)[-1][5] == "confirmed"
    # it answers again, changed, found by the rotation: the change list missed it
    conn.execute("UPDATE tr.queue SET status = 'pending', why = 'rotate', held_sha = NULL WHERE eik = %s", (eik,))
    answers.append(partida(eik))
    T.process(conn, 30, {})
    conn.execute("UPDATE tr.queue SET status = 'pending', why = 'rotate' WHERE eik = %s", (eik,))
    answers.append(partida(eik, "МАРИЯ ПЕТРОВА"))
    st = {}
    T.process(conn, 30, st)
    assert st["rotate_missed"] == 1 and log(conn)[-1][5] == "rotate"
    assert conn.execute("SELECT why, status FROM tr.queue WHERE eik = %s", (eik,)).fetchone() == (None, "done")


def test_rotation_queues_the_oldest_share_and_reads_it_last(conn):
    from ingest import tr_worker as T
    for i in range(120):
        eik = f"{100000000 + i}"
        conn.execute("INSERT INTO tr.deed (eik, status, fetched_at) VALUES (%s, 'ok', now() - make_interval(days => %s))", (eik, i))
        conn.execute("INSERT INTO tr.queue (eik, reason, priority, status) VALUES (%s, 'contract', 1, 'done')", (eik,))
    assert T.rotate(conn) == 2                                           # 120 / 60 days
    assert {r[0] for r in conn.execute("SELECT eik FROM tr.queue WHERE why = 'rotate'")} == {"100000119", "100000118"}
    assert T.waiting(conn) == 0                                          # the chain does not wait for the rotation
    conn.execute("INSERT INTO tr.queue (eik, reason, priority) VALUES ('999999999', 'contract', 1)")
    first = conn.execute("SELECT eik FROM tr.queue WHERE status = 'pending' ORDER BY why IS NOT DISTINCT FROM 'rotate', priority, enqueued_at LIMIT 1").fetchone()
    assert first == ("999999999",) and T.waiting(conn) == 1


# ---------- СЕБРА ----------

def test_sebra_verify_hold_and_history(conn, tmp_path, monkeypatch):
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
    count = lambda: conn.execute("SELECT count(*) FROM sebra.payment").fetchone()[0]
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
    assert len(list(tmp_path.glob("r1.*.csv.gz"))) == 1                  # the old copy is kept
    state.update(n=4)                                                    # more rows, same date: rewritten
    S.load(conn, st := {}, verify=True)
    assert count() == 4 and log(conn)[-1][3:] == ("2", "4", "rewritten")


# ---------- records between two builds ----------

def test_record_changes_between_builds(conn):
    from ingest import build, normalize as N
    cols = ", ".join(f"{f} text" for f in build.CONTRACT_FIELDS)
    tcols = ", ".join(f"{f} text" for f in build.TENDER_FIELDS)
    for s in ("live", "stage"):
        conn.execute(f"CREATE SCHEMA {s}; CREATE TABLE {s}.contract (id text, source_day date, annex_count int, {cols}); "
                     f"CREATE TABLE {s}.tender (unp text, source_day date, {tcols})")
    d1, d2 = dt.date(2026, 9, 1), dt.date(2026, 9, 20)
    conn.execute("""INSERT INTO live.contract (id, source_day, annex_count, value_current, supplier_display) VALUES
        ('c1', %(d1)s, 0, '100', 'А'), ('c2', %(d1)s, 0, '50', 'Б'), ('c4', %(d1)s, 0, '100', 'Г')""", {"d1": d1})
    conn.execute("""INSERT INTO stage.contract (id, source_day, annex_count, value_current, supplier_display) VALUES
        ('c1', %(d1)s, 0, '200', 'А'), ('c3', %(d1)s, 0, '1', 'В'), ('c4', %(d1)s, 1, '150', 'Г')""", {"d1": d1})
    conn.execute("INSERT INTO live.tender (unp, source_day, submission_deadline) VALUES ('u1', %s, '2026-09-10'), ('u2', %s, '2026-09-10')", (d1, d1))
    conn.execute("INSERT INTO stage.tender (unp, source_day, submission_deadline) VALUES ('u1', %s, '2026-09-12'), ('u2', %s, '2026-09-15')", (d1, d2))
    conn.execute("INSERT INTO ops.published (id, rules) VALUES (1, 'older rules')")
    assert "skipped" in build.record_changes(conn) and log(conn) == []    # our rules changed: not the source's change
    conn.execute("UPDATE ops.published SET rules = %s", (N.RULES_VERSION,))
    assert build.record_changes(conn) == {"contract": 2, "tender": 2, "removed": 1}
    got = {(r[1], r[2], r[5]) for r in log(conn)}
    assert got == {("contract/c1", "value_current", "rewritten"),          # same publication, other value
                   ("contract/c4", "value_current", "new-record"),         # an annex brought it
                   ("tender/u1", "submission_deadline", "rewritten"),
                   ("tender/u2", "submission_deadline", "new-record"),     # a later publication
                   ("contract/c2", None, "removed")}


# ---------- freshness and the D-1 chain ----------

def test_freshness_names_each_late_source(conn):
    from ingest import daily
    conn.execute("INSERT INTO ops.eop_day VALUES (%s, true, now())", (YESTERDAY,))
    conn.execute("INSERT INTO ops.published (id, at) VALUES (1, now())")
    assert daily.freshness(conn) == []
    conn.execute("UPDATE ops.published SET at = now() - interval '30 hours'")
    conn.execute("DELETE FROM ops.eop_day")
    conn.execute("INSERT INTO ops.eop_day VALUES (%s, true, now())", (YESTERDAY - dt.timedelta(days=2),))
    conn.execute("INSERT INTO eopsvc.queue (tender_id, reason, next_at) VALUES (1, 'new', now() - interval '2 days'), (2, 'backfill', now() - interval '9 days')")
    conn.execute("INSERT INTO tr.queue (eik, reason, priority, next_at) VALUES ('111111111', 'contract', 1, now() - interval '2 days')")
    conn.execute("INSERT INTO ops.change_log (source, ref, cause) VALUES ('eop-file', '2021-02-22/contracts', 'rewritten'), ('offers', '1', 'held')")
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
    conn.execute("DELETE FROM ops.job_run")
    monkeypatch.setattr(daily, "freshness", lambda c: ["ТР: 3 партиди чакат над ден"])
    with pytest.raises(daily.Problems):
        daily.run()
    assert daily.run()["skipped"] == "done"
    # a chain that breaks twice waits for the last call of the morning instead of rebuilding every 15 minutes
    conn.execute("DELETE FROM ops.job_run")
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
    conn.execute("INSERT INTO eopsvc.queue (tender_id, reason) VALUES (7, 'backfill')")
    got = lambda pubs, appeals, ann: dict(answer((1, 1, "А", 100)), details=details(pubs, appeals, [5]), announcements=ann)
    E.store(conn, 7, got([1, 2], [("ВХР-1", "в производство")], [(10, None, "Разяснение №1"), (11, None, "Решение за прекратяване")]), b"a")
    assert log(conn) == []                                                           # a first read is not a change
    gone = lambda t: conn.execute(f"SELECT count(*) FILTER (WHERE gone_at IS NOT NULL), count(*) FROM eopsvc.{t} WHERE tender_id = 7").fetchone()
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
    import gzip, hashlib, json
    from pathlib import Path
    from ingest import eop_offers as E
    monkeypatch.setattr(E, "RAW", tmp_path)
    fx = json.loads((Path(__file__).parent / "fixtures" / "eop_svc" / "563386.json").read_text(encoding="utf-8"))
    blob = json.dumps({"participation": fx["participation"], "lots": fx["lots"]}, ensure_ascii=False).encode()
    (tmp_path / "563386").mkdir()
    (tmp_path / "563386" / "202609271123.json.gz").write_bytes(gzip.compress(blob))
    (tmp_path / "563386" / "202609271200.json.gz").write_bytes(gzip.compress(b'{"participation": null}'))   # a later, held answer
    conn.execute("INSERT INTO eopsvc.queue (tender_id, reason, status, sha256) VALUES (563386, 'backfill', 'done', %s)",
                 (hashlib.sha256(blob).hexdigest(),))
    old = E.parse(563386, fx["participation"], fx["lots"])
    for r in old:   # as the reader before 27.09.2026 stored them: the submitting account as the participant
        o = next(x for rd in fx["participation"]["Rounds"] for x in rd["Offers"] if x["OfferId"] == r["offer_id"])
        r.update(bidder_name=o["OrganizationName"], bidder_eik=E.eik(o["RegistryNumber"]))
    E.write_offers(conn, old, dt.datetime(2026, 9, 27, tzinfo=dt.timezone.utc))
    E.reparse(conn, st := {})
    assert st == {"procedures": 1, "changed": 4, "missing_raw": 0}         # the ДЗЗД and the branch: name and ЕИК each
    names = {r[0] for r in conn.execute("SELECT bidder_name FROM eopsvc.offer WHERE tender_id = 563386")}
    assert '"ЗА ЧИСТА ВАРНА" ДЗЗД' in names and not any("IVAN" in n for n in names)
    assert {r[5] for r in log(conn)} == {"reparsed"}
    assert conn.execute("SELECT min(fetched_at) FROM eopsvc.offer").fetchone()[0] == dt.datetime(2026, 9, 27, tzinfo=dt.timezone.utc)


def build_small(conn, res):
    from ingest import build
    with conn.transaction():
        conn.execute((build.DERIVE / "schema.sql").read_text(encoding="utf-8"))
        conn.execute("SET search_path = public")
        for t in ("buyer", "tender", "lot", "contract", "contract_supplier", "amendment", "subcontract", "source_record",
                  "award", "notice", "annex_orphan"):
            build._copy(conn, t, res[t])
    build.step_derive(conn, {})
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
    res = N.normalize([("2026-05-01", "tenders", tenders), ("2026-07-02", "contracts", [won])], N.Fx({}))
    for tid, aid, title in ((1001, 1, "РЕШЕНИЕ ЗА ПРЕКРАТЯВАНЕ на процедурата"), (1002, 2, "Решение за прекратяване на обособена позиция № 2"),
                            (1003, 3, "Решение за прекратяване"), (1001, 4, "Решение за отмяна на решение за прекратяване")):
        conn.execute("INSERT INTO eopsvc.announcement (tender_id, id, title) VALUES (%s, %s, %s)", (tid, aid, title))
    conn.execute("""INSERT INTO eopsvc.publication (tender_id, id, form_type) VALUES (1001, 10010, 54), (1001, 900, 32), (1001, 901, 77)""")
    conn.execute("""INSERT INTO eopsvc.offer (tender_id, lot_no, round, offer_id, bidder_name, bidder_eik, price_opened, fetched_at) VALUES
        (1001, 0, 1, 1, 'Б ЕООД', NULL, false, now()), (1001, 0, 1, 2, 'Физическо лице', NULL, false, now())""")
    build_small(conn, res)
    q = lambda sql: conn.execute(sql).fetchall()
    assert dict(q("SELECT unp, state FROM live.tender")) == {"W": "cancelled", "P": "open", "K": "contracted"}  # a contract wins
    assert dict(q("SELECT lot_no, status FROM live.lot WHERE unp = 'P'")) == {1: "open", 2: "cancelled"}     # only the lot it names
    assert dict(q("SELECT form_type, name FROM live.form_type")) == {54: "Обявление за поръчка – Общата директива, стандартен режим",
                                                                     32: "Решение по чл. 22, ал. 1 от ЗОП"}  # 77 unnamed: shown as „Публикация“
    assert dict(q("SELECT offer_id, company_key FROM live.offer")) == {1: "eik:831641791", 2: None}          # by the only company of the name


def test_completeness_names_what_one_source_has_and_the_other_not(conn):
    from ingest import eop_offers as E
    from ingest import normalize as N
    t = {"uniqueProcurementNumber": "W", "tenderId": "1001", "publicationDate": "2026-05-01T10:00:00", "subject": "x", "noticeId": "10010",
         "noticeType": "Обявление", "buyerRegistryNumber": "000093442", "buyerName": "ОБЩИНА"}
    award = dict(t, noAwarding="Да", noticeId="10011")
    build_small(conn, N.normalize([("2026-05-01", "tenders", [t]), ("2026-08-01", "contracts", [award])], N.Fx({})))
    conn.execute("INSERT INTO eopsvc.queue (tender_id, reason, status, fetched_at) VALUES (1001, 'backfill', 'done', now())")
    conn.execute("INSERT INTO eopsvc.detail (tender_id, fetched_at) VALUES (1001, now())")
    conn.execute("INSERT INTO eopsvc.publication (tender_id, id) VALUES (1001, 10010)")            # the award notice is missing on the page
    conn.execute("INSERT INTO eopsvc.contract_lot VALUES (1001, 0, '77')")                          # a contract only on the page
    m = E.mismatches(conn)
    assert (m["notice_not_on_page"], m["contracts_only_on_page"]) == (1, 1) and m["notice_examples"] == [[1001, "10011"]]
    E.mismatches(conn)
    assert [r[1] for r in log(conn, "completeness")] == ["1001/notice/10011", "1001/contracts"]   # logged once
