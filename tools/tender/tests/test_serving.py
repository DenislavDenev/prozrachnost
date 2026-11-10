"""Ingest against serving, the rule for the names of people, and the move from the old database.

The pages read only pub_tender and pub. A person is published only in a role in an entity that received public
money, under a token that lives for one build. The serving layer follows a build only when the rules hold.
Needs TENDER_TEST_DSN (a disposable database).
"""
import asyncio
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
A, B, D = "202210490", "831641791", "130873641"      # A and B win contracts, D has neither a contract nor a payment
P1, P2, P3 = "1" * 64, "2" * 64, "3" * 64            # the register's identifiers of three people


def contract(num, eik, name, day, value):
    return {"noAwarding": "Не", "uniqueProcurementNumber": f"U{num}", "contractNumber": str(num), "contractValue": str(value),
            "contractCurrency": "EUR", "supplierName": name, "supplierRegisterNumber": eik, "awardedToGroup": "Не",
            "contractDate": day, "publicationDate": "2025-01-01T00:00:00", "offersCount": 1,
            "buyerRegistryNumber": "000695089", "buyerName": "АГЕНЦИЯ"}


ROLES = [  # eik, field, role, kind, holder, name, from
    (A, "00070", "manager", "person", P1, "ИВАН ИВАНОВ ПЕТРОВ", "2018-01-01"),
    (D, "00070", "manager", "person", P1, "ИВАН ИВАНОВ ПЕТРОВ", "2019-01-01"),      # the same person, in a company without public money
    (D, "00190", "partner", "person", P2, "ПЕТЪР ПЕТРОВ СТОЯНОВ", "2019-01-01"),     # a person only in such a company
    (B, "00070", "manager", "person", P3, "МАРИЯ ПЕТРОВА ИВАНОВА", "2015-01-01"),
    (D, "00190", "partner", "entity", A, "А ООД", "2019-01-01"),                     # a company holds a share: not a person
    (D, "00070", "manager", "person", f"local:{D}:JOHN SMITH", "JOHN SMITH", "2016-01-01"),
]


def fill(conn):
    for eik in (A, B, D):
        conn.execute("INSERT INTO tender_src.tr_deed(eik, status, fetched_at, name) VALUES (%s,'ok',now(),%s)", (eik, "X" + eik))
    for eik, f, role, kind, h, name, fr in ROLES:
        conn.execute("INSERT INTO tender_src.tr_role VALUES (%s,'0001',%s,%s,%s,%s,%s,NULL,NULL,NULL,'1',%s,NULL,NULL,now())",
                     (eik, f, role, kind, h, name, fr))
    for i, (h, name) in enumerate([(P1, "ИВАН ИВАНОВ ПЕТРОВ"), (P2, "ПЕТЪР ПЕТРОВ СТОЯНОВ"), (P3, "МАРИЯ ПЕТРОВА ИВАНОВА")]):
        conn.execute("INSERT INTO tender_src.tr_person(id, indent, name, name_key) VALUES (%s,%s,%s,%s)", (f"T{i}", h, name, name))
    conn.execute("INSERT INTO tender_src.eop_day VALUES ('2025-01-01', true, now())")
    conn.execute("INSERT INTO tender_src.eop_file VALUES ('2025-01-01', 'contracts', 'k', 'abc', 1, now())")


def build(conn, rows=None):
    from ingest import build as B_, normalize as N
    rows = rows or [contract(1, A, "А ООД", "01.06.2022", 1000), contract(2, B, "Б ЕООД", "01.01.2024", 700)]
    res = N.normalize([("2025-01-01", "contracts", rows)], N.Fx({}))
    with conn.transaction():
        B_.start_stage(conn)
        for t in ("buyer", "tender", "lot", "contract", "contract_supplier", "amendment", "subcontract", "source_record",
                  "award", "notice", "annex_orphan"):
            B_._copy(conn, t, res[t])
    B_.step_derive(conn, {})
    stats = {}
    B_.step_publish(conn, stats)
    return stats


def one(conn, sql, *args):
    return conn.execute(sql, args or None).fetchone()[0]


def get(path):
    """One request through the whole app (the middleware too), without a server: (status, headers, body)."""
    from app import main
    path, _, query = path.partition("?")
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http", "path": path,
             "raw_path": path.encode(), "query_string": query.encode(), "headers": [], "server": ("test", 80), "client": ("test", 1),
             "root_path": ""}
    asyncio.run(main.app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], {k.decode().lower(): v.decode() for k, v in start["headers"]}, body.decode()


def test_people_are_shown_only_in_entities_with_public_money_and_under_a_token_of_the_build(conn):
    fill(conn)
    build(conn)
    # the ingest side holds every role and every person
    assert one(conn, "SELECT count(*) FROM tender.edge WHERE holder_kind = 'person'") == 5
    assert one(conn, "SELECT count(*) FROM tender.person") == 3
    # the pages: the two people with a role in a company with a contract, and only those roles
    shown = conn.execute("SELECT name::text, id FROM pub_tender.person ORDER BY 1").fetchall()
    assert [n for n, _ in shown] == ["ИВАН ИВАНОВ ПЕТРОВ", "МАРИЯ ПЕТРОВА ИВАНОВА"]
    roles = conn.execute("SELECT holder_name::text, company FROM pub_tender.edge WHERE holder_kind = 'person' ORDER BY 1, 2").fetchall()
    assert roles == [("ИВАН ИВАНОВ ПЕТРОВ", "c:" + A), ("МАРИЯ ПЕТРОВА ИВАНОВА", "c:" + B)]     # not his role in D
    assert one(conn, "SELECT count(*) FROM pub_tender.edge WHERE holder_kind = 'entity'") == 1    # companies are not people
    names = {r[0] for r in conn.execute("SELECT label::text FROM pub_tender.node UNION SELECT label::text FROM pub_tender.search_item")}
    assert "ПЕТЪР ПЕТРОВ СТОЯНОВ" not in names and "JOHN SMITH" not in names and "ИВАН ИВАНОВ ПЕТРОВ" in names
    assert one(conn, "SELECT companies FROM pub_tender.person_stats s JOIN pub_tender.person p USING (id) WHERE p.name = 'ИВАН ИВАНОВ ПЕТРОВ'") == 1
    # no key of a person: not our number, not the register's identifier, and nothing that survives a build
    tokens = {t for _, t in shown}
    assert not tokens & {"T0", "T1", "T2", P1, P2, P3} and all(re.fullmatch(r"[0-9a-f]{32}", t) for t in tokens)
    ivan = dict(shown)["ИВАН ИВАНОВ ПЕТРОВ"]
    status, _, html = get(f"/persons/{ivan}")
    assert status == 200 and "иван иванов петров" in html.lower()
    from app import queries
    assert [r["company"] for r in queries.person(ivan)["roles"]] == ["c:" + A]                    # his page: the role in A only
    assert get("/persons/T0")[0] == 404 and get(f"/persons/{P1}")[0] == 404
    build(conn)
    again = dict(conn.execute("SELECT name::text, id FROM pub_tender.person").fetchall())
    assert again["ИВАН ИВАНОВ ПЕТРОВ"] != ivan and get(f"/persons/{ivan}")[0] == 404              # the old token is gone
    # a payment from the budget is public money too: the people of D appear once D is paid
    conn.execute("INSERT INTO tender_src.sebra_resource (uri, name, format) VALUES ('r', 'x', 'csv')")
    conn.execute("""INSERT INTO tender_src.sebra_payment (resource_uri, row_no, settlement_date, receiver_name, is_person, amount, currency)
        VALUES ('r', 1, '2025-02-01', %s, false, 10, 'EUR'), ('r', 2, '2025-02-01', 'ФИЗИЧЕСКО ЛИЦЕ', true, 5, 'EUR')""", ("X" + D + " " + D,))
    build(conn)
    assert {r[0] for r in conn.execute("SELECT name::text FROM pub_tender.person")} == {
        "ИВАН ИВАНОВ ПЕТРОВ", "МАРИЯ ПЕТРОВА ИВАНОВА", "ПЕТЪР ПЕТРОВ СТОЯНОВ"}
    assert one(conn, "SELECT count(*) FROM pub_tender.edge WHERE holder LIKE 'l:%'") == 1          # JOHN SMITH, under a token
    assert one(conn, "SELECT count(*) FROM pub_tender.edge e JOIN tender.edge o ON o.holder = e.holder WHERE e.holder_kind = 'person'") == 0
    # nothing of the guards of the foundation is left open
    assert one(conn, "SELECT count(*) FROM core.serving_violation") == 0
    assert one(conn, "SELECT count(*) FROM core.person_column_undecided WHERE schema_name LIKE '%tender%'") == 0


def test_pages_read_only_the_serving_layer():
    """The web role has no right outside pub and pub_tender; here the source of the pages is read for it."""
    sql = (ROOT / "app" / "queries.py").read_text(encoding="utf-8") + (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    outside = re.findall(r"(?<![\w.])(?:tender|tender_src|tender_stage|ops|core|danni_src|live|tr|ed|eopsvc|sebra)\.[a-z_]+(?=[\s,;)\"'])", sql)
    assert [x for x in outside if x not in ("ops.run.status", "tender.html")] == []   # a comment and a template, not tables
    assert "pub_tender.contract" in sql and "pub.publication" in sql
    assert not re.search(r"\b(INSERT INTO|UPDATE [a-z_.]+ SET|DELETE FROM)\b", sql)      # and they only read


def test_every_answer_says_its_publication_and_the_number_moves_only_with_the_content(conn):
    fill(conn)
    first = build(conn)["publication"]
    assert first and one(conn, "SELECT max(publication_id) FROM pub.publication") == first
    status, headers, html = get("/contracts")
    assert status == 200 and headers["x-publication"] == str(first) and headers["etag"] == f'W/"pub-{first}"' and "last-modified" in headers
    assert "А ООД" in html
    status, headers, _ = get("/healthz")
    assert status == 200 and headers["x-publication"] == str(first)
    # the rules and the serving layer again, nothing new to say: no new number
    from ingest import build as B_
    stats = {}
    B_.serve(conn, stats)
    assert stats["publication"] is None and one(conn, "SELECT max(publication_id) FROM pub.publication") == first
    # what is read between two builds is not on the pages until a publication
    conn.execute("INSERT INTO tender_src.eopsvc_queue (tender_id, reason, status, fetched_at) VALUES (5, 'new', 'done', now())")
    assert one(conn, "SELECT count(*) FROM pub_tender.eopsvc_queue") == 0
    B_.serve(conn, stats := {})
    assert stats["publication"] > first and one(conn, "SELECT count(*) FROM pub_tender.eopsvc_queue") == 1
    assert get("/contracts")[1]["x-publication"] == str(stats["publication"])


def test_a_failed_rule_keeps_the_pages_on_the_version_they_had(conn):
    fill(conn)
    first = build(conn)["publication"]
    from ingest import build as B_
    conn.execute("DELETE FROM tender.contract_supplier; DELETE FROM tender.contract")      # a build that lost every contract
    with pytest.raises(RuntimeError, match="Договорите не са намалели"):
        B_.serve(conn, {})
    assert one(conn, "SELECT count(*) FROM pub_tender.contract") == 2 and one(conn, "SELECT max(publication_id) FROM pub.publication") == first
    assert one(conn, "SELECT ok FROM ops.check_latest WHERE tool = 'tender' AND name = 'contracts_did_not_drop'") is False
    from ingest import daily
    assert any("Договорите не са намалели" in p for p in daily.freshness(conn))            # and the freshness check says so


def test_the_build_links_to_the_shared_dimensions(conn):
    """An organisation is linked to core.org when it is there (never invented), a placed buyer to core.municipality."""
    fill(conn)
    conn.execute("INSERT INTO core.org (eik, name, source) VALUES (%s, 'А ООД', 'tr') ON CONFLICT DO NOTHING", (A,))
    try:
        row = contract(1, A, "А ООД", "01.06.2022", 1000)
        row.update(buyerRegistryNumber="000024663", buyerName="ОБЩИНА БАНСКО")
        build(conn, [row, contract(2, B, "Б ЕООД", "01.01.2024", 700)])
        assert dict(conn.execute("SELECT key, org_eik FROM tender.company WHERE key IN (%s, %s)", ("eik:" + A, "eik:" + B)).fetchall()) == {
            "eik:" + A: A, "eik:" + B: None}
        assert conn.execute("SELECT municipality, municipality_code, basis FROM tender.buyer_place").fetchall() == [("000024663", "BLG01", "municipality")]
        assert conn.execute("SELECT municipality_code FROM pub_tender.buyer_place").fetchall() == [("BLG01",)]
        assert one(conn, "SELECT name_bg FROM pub_tender.municipality WHERE id = '000024663'") == "Банско"
        keys = {r[0] for r in conn.execute("""SELECT confrelid::regclass::text FROM pg_constraint
            WHERE contype = 'f' AND connamespace = 'tender'::regnamespace AND confrelid::regclass::text LIKE 'core.%'""")}
        assert keys == {"core.org", "core.municipality"}
        assert one(conn, "SELECT count(*) FROM pg_class WHERE relnamespace = 'tender_stage'::regnamespace") == 0   # nothing is kept twice
    finally:
        conn.execute("DROP SCHEMA tender CASCADE; CREATE SCHEMA tender")
        conn.execute("DELETE FROM core.org WHERE eik = %s", (A,))


def old_database(conn):
    """The old database of Тендер, made in the test database from its own migrations (db/legacy), its ledger under the
    name old_ops, and filled with what the new one holds now."""
    from ingest import move
    conn.execute("CREATE SCHEMA old_ops; CREATE SCHEMA live")
    for f in sorted((ROOT / "db" / "legacy").glob("*.sql")):
        text = re.sub(r"\bops\.", "old_ops.", f.read_text(encoding="utf-8")).replace("CREATE SCHEMA IF NOT EXISTS ops;", "")
        conn.execute(re.sub(r"CREATE EXTENSION IF NOT EXISTS \w+;", "", text))
    for source, new in move.STATE:
        schema, table = source.split(".")
        schema = "old_ops" if schema == "ops" else schema
        cols = ", ".join(move._columns(conn, schema, table))
        conn.execute(f"INSERT INTO {schema}.{table} ({cols}) SELECT {cols} FROM tender_src.{new}")
    for t in move.BUILT:
        cols = [c for c in move._columns(conn, "tender", t) if c not in ("org_eik", "municipality_code")]
        conn.execute(f"CREATE TABLE live.{t} AS SELECT {', '.join(cols)} FROM tender.{t}")
    conn.execute("""CREATE TABLE live.municipality AS
        SELECT m.eik AS id, m.name_bg, m.name_en, m.drawn_as_eik AS drawn_as, o.label_bg AS oblast, o.nuts3_code AS nuts3,
               n3.parent_code AS nuts2, n2.parent_code AS nuts1
        FROM core.municipality m JOIN core.oblast o USING (oblast_code) JOIN core.nuts n3 ON n3.nuts_code = o.nuts3_code
        JOIN core.nuts n2 ON n2.nuts_code = n3.parent_code""")
    conn.execute("""INSERT INTO old_ops.job_run (step, status, inputs, finished_at) VALUES ('daily', 'ok', '{"day": "2026-11-09"}', now()),
        ('tr-read', 'running', '{}', NULL)""")
    conn.execute("INSERT INTO old_ops.change_log (source, ref, cause) VALUES ('tr', '1', 'rotate'), ('eop-file', '2021-02-22/contracts', 'late')")


def test_replay_copies_the_old_database_and_parity_sees_a_difference(conn, test_database):
    from ingest import db, move, pipeline
    fill(conn)
    conn.execute("INSERT INTO tender_src.fx_rate VALUES ('2025-01-01', 'USD', 1.1)")
    conn.execute("INSERT INTO tender_src.ed_tag (entity_type, entity_id, code, note, author) VALUES ('company', 'eik:1', 'x', 'основание', 'admin')")
    build(conn)
    old_database(conn)
    kept = {new: one(conn, f"SELECT count(*) FROM tender_src.{new}") for _, new in move.STATE}
    # an empty landing zone again, then the move: replay, build, parity
    conn.execute("TRUNCATE " + ", ".join(f"tender_src.{new}" for _, new in move.STATE))
    conn.execute("DELETE FROM ops.check_result WHERE tool = 'tender'; DELETE FROM ops.run WHERE tool = 'tender'; DELETE FROM ops.change_log WHERE tool = 'tender'")

    def step(name, fn):
        with pipeline.run(conn, "tender", name) as r:
            fn(r)
            return r.stats
    stats = step("replay", lambda r: move.replay(conn, r, old_dsn=test_database, old_ops="old_ops"))
    assert {new: one(conn, f"SELECT count(*) FROM tender_src.{new}") for _, new in move.STATE} == kept and stats["runs"] == 2
    assert stats["tr_role"] == len(ROLES) and stats["changes"] == 2
    # the D-1 chain finds the day the old database had done; a run it left as running is not left so
    assert one(conn, "SELECT count(*) FROM ops.run WHERE tool = 'tender' AND step = 'daily' AND status = 'ok' AND params->>'day' = '2026-11-09'") == 1
    assert one(conn, "SELECT status FROM ops.run WHERE tool = 'tender' AND step = 'tr-read'") == "failed"
    assert one(conn, "INSERT INTO tender_src.ed_tag (entity_type, entity_id, code, note, author) VALUES ('company', 'eik:2', 'y', 'z', 'admin') RETURNING id") == 2
    conn.execute("DELETE FROM tender_src.ed_tag WHERE id = 2")
    with pytest.raises(RuntimeError, match="not empty"):       # a second copy would undo what was read since
        step("replay", lambda r: move.replay(conn, r, old_dsn=test_database, old_ops="old_ops"))
    build(conn)
    stats = step("parity", lambda r: move.parity(conn, r, old_dsn=test_database, old_ops="old_ops"))
    bad = conn.execute("SELECT name FROM ops.check_latest WHERE tool = 'tender' AND kind = 'parity' AND ok IS NOT true ORDER BY 1").fetchall()
    assert bad == [] and stats["differ"] == [] and stats["tables"] > 50
    # every rule the step records is in db/checks.toml, and the other way round
    listed = {r[0] for r in conn.execute("SELECT name FROM ops.check_rule WHERE tool = 'tender' AND kind = 'parity'")}
    assert listed == set(move.parity_rules())
    # the number of a network component is arbitrary: the same components under other numbers are the same
    conn.execute("UPDATE live.network_component SET component = component + 1000; UPDATE live.network_component_stats SET component = component + 1000")
    assert step("parity", lambda r: move.parity(conn, r, old_dsn=test_database, old_ops="old_ops"))["differ"] == []
    # one cent in one contract of the old database, one letter in one role: parity names exactly those tables
    conn.execute("UPDATE live.contract SET amount_eur = amount_eur + 0.01 WHERE id = (SELECT min(id) FROM live.contract)")
    conn.execute("UPDATE tr.role SET holder_name = holder_name || 'А' WHERE holder_id = %s AND eik = %s", (P1, A))
    stats = step("parity", lambda r: move.parity(conn, r, old_dsn=test_database, old_ops="old_ops"))
    assert stats["differ"] == ["contract", "tr_role"]
    bad = [r[0] for r in conn.execute("SELECT name FROM ops.check_latest WHERE tool = 'tender' AND kind = 'parity' AND ok = false ORDER BY 1")]
    assert bad == ["parity_contract_amount_eur_sum", "parity_contract_content", "parity_tr_role_content"]
    assert db.RUNS == {}
