"""Ingest against serving, and the rule for the names of people.

The pages read only pub_tender and pub. A person is published only in a role in an entity that received public
money, under a token that lives for one build. The serving layer follows a build only when the rules hold.
Needs TENDER_TEST_DSN (a disposable database).
"""
from urllib.parse import quote
import asyncio
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
A, B, D = "202210490", "831641791", "130873641"      # A and B win contracts, D has neither a contract nor a payment
E, UNIT = "000024663", "0000246630010"                 # a company without a partida read, and a unit of it
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
    (D, "00190", "partner", "person", f"local:{D}:JOHN SMITH", "JOHN  SMYTH", "2017-01-01"),   # the same holder, written another way
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
    # E and a unit of E (a 13-digit number) win contracts too: they share the node of E, and E names that node
    rows = rows or [contract(1, A, "А ООД", "01.06.2022", 1000), contract(2, B, "Б ЕООД", "01.01.2024", 700),
                    contract(3, E, "Я ООД", "01.02.2024", 60), contract(4, UNIT, "A КЛОН", "01.02.2024", 50)]
    res = N.normalize([("2025-01-01", "contracts", rows)], N.Fx({}))
    with conn.transaction():
        B_.start_stage(conn)
        for t in ("buyer", "tender", "lot", "contract", "contract_supplier", "amendment", "subcontract", "source_record",
                  "award", "notice", "annex_orphan"):
            B_._copy(conn, t, res[t])
    B_.step_derive(conn, {})
    stats = {}
    __import__("conftest").landed(conn)
    B_.step_publish(conn, stats)
    return stats


def one(conn, sql, *args):
    return conn.execute(sql, args or None).fetchone()[0]


def get(path):
    """One request through the whole app (the middleware too), without a server: (status, headers, body)."""
    from app import main
    path, _, query = path.partition("?")
    sent, asked = [], []

    async def receive():
        if asked:                    # a streamed answer (CSV) listens for a disconnect until it ends
            await asyncio.Future()
        asked.append(1)
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
    assert one(conn, "SELECT count(*) FROM tender.edge WHERE holder_kind = 'person'") == 6
    # a holder written in two ways is one node, named the same in every build (the first spelling by the alphabet)
    assert conn.execute("SELECT label::text FROM tender.node WHERE kind = 'local_person'").fetchall() == [("JOHN SMITH",)]
    # the node of E is E, though its unit comes first by the alphabet
    assert conn.execute("SELECT ref, label::text FROM tender.node WHERE id = %s", ("c:" + E,)).fetchall() == [("eik:" + E, "Я ООД")]
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
    assert one(conn, "SELECT count(DISTINCT holder) FROM pub_tender.edge WHERE holder LIKE 'l:%'") == 1   # JOHN SMITH, under a token
    assert conn.execute("SELECT label::text FROM pub_tender.node WHERE kind = 'local_person'").fetchall() == [("JOHN SMITH",)]
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
    # the planner knows the published tables at once (-1 is a table never analysed)
    assert conn.execute("""SELECT relname, reltuples::int FROM pg_class WHERE oid IN ('pub_tender.contract'::regclass,
        'pub_tender.edge'::regclass) ORDER BY 1""").fetchall() == [("contract", 4), ("edge", 3)]
    status, headers, html = get("/contracts")
    assert status == 200 and headers["x-publication"] == str(first) and headers["etag"] == f'W/"pub-{first}"' and "last-modified" in headers
    assert "А ООД" in html
    # a page shows what the source gives: the row numbers that key some serving tables are not data
    conn.execute("""INSERT INTO pub_tender.amendment (contract_id, published_at, current_value, currency, n)
        SELECT id, '2025-03-01', 5, 'EUR', 1 FROM pub_tender.contract ORDER BY id LIMIT 1""")
    cid = conn.execute("SELECT contract_id FROM pub_tender.amendment").fetchone()[0]
    import json
    answer = json.loads(get(f"/contracts/{cid}.json")[2])
    assert len(answer["amendments"]) == 1 and "n" not in answer["amendments"][0] and "row_id" not in json.dumps(answer)
    conn.execute("DELETE FROM pub_tender.amendment")
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
    assert one(conn, "SELECT count(*) FROM pub_tender.contract") == 4 and one(conn, "SELECT max(publication_id) FROM pub.publication") == first
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


def test_a_supplier_by_name_only_stays_on_the_contract_and_has_no_profile(conn):
    fill(conn)
    who, firm = "ГЕОРГИ СТОЯНОВ ДИМОВ", "БЕТА ООД"
    build(conn, [contract(1, A, "А ООД", "01.06.2022", 1000), contract(2, "", who, "01.01.2024", 700), contract(3, "", firm, "01.02.2024", 60)])
    assert one(conn, "SELECT party_key FROM pub_tender.contract_supplier WHERE name = %s", who) == "unnamed"
    assert one(conn, "SELECT count(*) FROM pub_tender.company WHERE name = %s", who) == 0
    assert one(conn, "SELECT count(*) FROM pub_tender.company_stats WHERE key LIKE %s", "%ГЕОРГИ%") == 0
    assert one(conn, "SELECT count(*) FROM pub_tender.search_item WHERE key LIKE %s", "%ГЕОРГИ%") == 0
    assert one(conn, "SELECT count(*) FROM pub_tender.company WHERE name = %s", firm) == 1            # a legal form is a company
    cid = one(conn, "SELECT contract_id FROM pub_tender.contract_supplier WHERE name = %s", who)
    status, _, body = get(f"/contracts/{cid}")
    assert status == 200 and "георги" in body.lower() and "/companies/unnamed" not in body             # on the contract, without a link
    status, _, body = get("/buyers/000695089")
    assert status == 200 and "георги" not in body.lower() and "Без ЕИК и правна форма" in body         # not in the ranking
    assert get("/companies/unnamed")[0] == 307


def test_a_name_without_an_eik_is_on_its_contract_and_in_no_list(conn):
    fill(conn)
    who = "ГЕОРГИ СТОЯНОВ ДИМОВ"
    build(conn, [contract(1, A, "А ООД", "01.06.2022", 1000), contract(2, "", who, "01.01.2024", 700)])
    cid = one(conn, "SELECT contract_id FROM pub_tender.contract_supplier WHERE name = %s", who)
    assert one(conn, "SELECT supplier_display FROM pub_tender.contract WHERE id = %s", cid) == "Без ЕИК и правна форма"
    assert "георги" in get(f"/contracts/{cid}")[2].lower()
    for url in ("/contracts", "/contracts?format=csv", "/buyers/000695089.csv", "/"):
        status, _, body = get(url)
        assert status == 200 and "георги" not in body.lower(), url
    status, _, body = get("/contracts?who=" + quote(who))                       # the box repeats what was typed; no contract is found by it
    assert status == 200 and f"/contracts/{cid}" not in body


def test_the_release_of_a_procedure_names_a_person_only_on_the_contract_of_that_person():
    from app.queries import public_ocds
    who = "ГЕОРГИ СТОЯНОВ ДИМОВ"
    rec = {"parties": [{"id": "p1", "name": who, "roles": ["supplier"], "identifier": {"id": "X1", "legalName": who},
                        "address": {"streetAddress": "ул. Първа 1"}, "contactPoint": {"email": "a@b.bg"}},
                       {"id": "p2", "name": "А ООД", "roles": ["supplier"], "identifier": {"id": "123456789"},
                        "contactPoint": {"email": "c@d.bg"}}],
           "awards": [{"suppliers": [{"id": "p1", "name": who}, {"id": "p2", "name": "А ООД"}]}]}
    before = repr(rec)
    other = repr(public_ocds(rec, {"А ООД"}))
    assert who not in other and "Без ЕИК и правна форма" in other and "А ООД" in other
    own = repr(public_ocds(rec, {who}))
    assert who in own
    for private in ("X1", "ул. Първа", "a@b.bg", "c@d.bg"):
        assert private not in own and private not in other
    assert repr(rec) == before                                                  # the cached raw is not changed


def test_a_buyer_whose_number_has_a_slash_has_a_page(conn):
    fill(conn)
    odd = "000695089/1234567890123"
    build(conn, [{**contract(1, A, "А ООД", "01.06.2022", 1000), "buyerRegistryNumber": odd}])
    assert one(conn, "SELECT count(*) FROM pub_tender.buyer WHERE eik = %s", odd) == 1
    assert get(f"/buyers/{odd}")[0] == 200
    assert get("/buyers/000000000/1")[0] == 404


def test_the_list_of_payments_reads_an_index_in_its_order(conn):
    fill(conn)
    with conn.transaction():
        conn.execute("SET LOCAL enable_seqscan = off")
        plan = "\n".join(r[0] for r in conn.execute("""EXPLAIN SELECT settlement_date FROM pub_tender.payment p
            ORDER BY p.settlement_date DESC NULLS LAST, p.resource_uri, p.row_no LIMIT 50"""))
    assert "payment_list_idx" in plan and "Sort" not in plan        # the order of the page, without sorting the table
