"""End-to-end checks of the time and counting rules (methodology 1-3) on a scratch database.

Runs only when TENDER_TEST_DSN points at a disposable database (it is wiped):
    createdb -O tender tender_test
    TENDER_TEST_DSN=dbname=tender_test pytest tests/test_integration.py
"""
import os

import pytest

DSN = os.environ.get("TENDER_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TENDER_TEST_DSN not set")
if DSN:
    os.environ["TENDER_DSN"] = DSN

A, B, C = "202210490", "831641791", "130873641"
P1, P2, P3, P4 = "1" * 64, "2" * 64, "3" * 64, "4" * 64


def contract(num, eik, name, day, value, group=None):
    return {"noAwarding": "Не", "uniqueProcurementNumber": f"U{num}", "contractNumber": str(num), "contractValue": str(value),
            "contractCurrency": "EUR", "supplierName": group[1] if group else name,
            "supplierRegisterNumber": group[0] if group else eik, "awardedToGroup": "Да" if group else "Не",
            "contractDate": day, "publicationDate": "2025-01-01T00:00:00", "offersCount": 1,
            "buyerRegistryNumber": "000695089", "buyerName": "АГЕНЦИЯ"}


ROLES = [  # eik, field, role, kind, holder, name, from, to
    (A, "00230", "sole_owner", "person", P1, "ИВАН ИВАНОВ ПЕТРОВ", "2018-01-01", "2021-01-01"),
    (A, "00070", "manager", "person", P2, "МАРИЯ ПЕТРОВА ИВАНОВА", "2020-01-01", None),
    (B, "00190", "partner", "person", P2, "МАРИЯ ПЕТРОВА ИВАНОВА", "2023-06-01", None),
    (B, "00070", "manager", "person", P3, "ИВАН ИВАНОВ ПЕТРОВ", "2015-01-01", None),  # same name as P1, other person
    (A, "00190", "partner", "entity", C, "ЦЕ ООД", "2019-01-01", None),               # cycle A <- C <- A
    (C, "00190", "partner", "entity", A, "А ООД", "2019-01-01", None),
    (B, "00070", "manager", "person", f"local:{B}:birthdate:{P4}:JOHN SMITH", "JOHN SMITH", "2016-01-01", None),
]


@pytest.fixture(scope="module")
def built():
    from ingest import build, db, normalize as N
    with db.connect(autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS live, stage, previous, ops, tr, ed, eopsvc, sebra CASCADE; DROP TABLE IF EXISTS public.schema_migrations")
        db.migrate(conn)
        rows = [contract(1, A, "А ООД", "01.06.2022", 1000), contract(2, A, "А ООД", "01.06.2024", 2000),
                contract(3, None, None, "01.03.2023", 5000, group=(f"{A}; {B}", "А ООД; Б ЕООД")),
                contract(4, B, "Б ЕООД", "01.01.2024", 700)]
        res = N.normalize([("2025-01-01", "contracts", rows)], N.Fx({}))
        for eik in (A, B, C):
            conn.execute("INSERT INTO tr.deed(eik, status, fetched_at, name) VALUES (%s,'ok',now(),%s)", (eik, "X" + eik))
        for eik, f, role, kind, h, name, fr, to in ROLES:
            conn.execute("INSERT INTO tr.role VALUES (%s,'0001',%s,%s,%s,%s,%s,NULL,NULL,NULL,'1',%s,%s,NULL,now())",
                         (eik, f, role, kind, h, name, fr, to))
        for i, (h, name) in enumerate([(P1, "ИВАН ИВАНОВ ПЕТРОВ"), (P2, "МАРИЯ ПЕТРОВА ИВАНОВА"), (P3, "ИВАН ИВАНОВ ПЕТРОВ")]):
            conn.execute("INSERT INTO tr.person(id, indent, name, name_key) VALUES (%s,%s,%s,%s)", (f"T{i}", h, name, name))
        conn.execute("INSERT INTO ops.eop_day VALUES ('2025-01-01', true, now())")
        for _ in range(2):  # a second full cycle must not duplicate anything
            with conn.transaction():
                conn.execute((build.DERIVE / "schema.sql").read_text(encoding="utf-8"))
                conn.execute("SET search_path = public")
                for t in ("buyer", "tender", "lot", "contract", "contract_supplier", "amendment", "subcontract"):
                    build._copy(conn, t, res[t])
            build.step_derive(conn, {})
            build.step_publish(conn, {})
    from app import queries as Q
    return Q


def test_counts_do_not_double_on_rebuild(built):
    assert built.one("SELECT count(*) n FROM live.contract")["n"] == 4
    assert built.one("SELECT count(*) n FROM live.contract_supplier")["n"] == 5


def test_former_owner_gets_no_contracts_after_leaving(built):
    m = built.person_modes("p:T0")
    assert m["at_contract_date"]["n"] == 0          # left A in 2021, A's contracts are 2022+
    assert m["all_history"]["n"] == 3               # historic view still lists them, labelled as such


def test_contract_reached_by_two_paths_counts_once(built):
    m = built.person_modes("p:T1")                  # manager of A, partner of B from 2023-06
    assert m["at_contract_date"]["n"] == 4          # A22, A24, joint 2023 (via A), B 2024-01
    assert float(m["at_contract_date"]["eur"]) == 1000 + 2000 + 5000 + 700


def test_path_must_be_valid_at_date(built):
    at_2019 = built.network("p:T0", "all", "2019-07-01")
    assert {e["company"] for e in at_2019["edges"] if e["holder"] == "p:T0"} == {"c:" + A}
    assert not any(e["holder"] == "p:T1" for e in at_2019["edges"])   # manager only from 2020
    at_2022 = built.network("p:T0", "all", "2022-07-01")
    assert at_2022["edges"] == []                                        # ownership ended in 2021


def test_ownership_cycle_terminates(built):
    net = built.network("c:" + A, "ownership", None, depth=4)
    assert {("c:" + C, "c:" + A), ("c:" + A, "c:" + C)} <= {(e["holder"], e["company"]) for e in net["edges"]}


def test_same_name_two_people_and_birthdate_not_joined(built):
    assert built.one("SELECT count(*) n FROM live.person WHERE name = 'ИВАН ИВАНОВ ПЕТРОВ'")["n"] == 2
    assert built.one("SELECT count(*) n FROM live.edge WHERE holder LIKE 'l:%%'")["n"] == 1
    assert built.one("SELECT count(*) n FROM live.person WHERE name = 'JOHN SMITH'")["n"] == 0


def test_joint_contract_marked_and_single_bid_counted(built):
    assert built.one("SELECT bool_and(joint) j FROM live.contract_supplier s JOIN live.contract c ON c.id = s.contract_id "
                     "WHERE c.unp = 'U3'")["j"] is True
    ind = built.indicators("eik:" + A)
    assert ind["known"] == ind["single"] == 1          # 3-year window: only the 2024 contract


def test_latin_search_finds_cyrillic_person(built):
    res = built.search("Mariya Petrova", "person")
    assert res and res[0]["label"] == "МАРИЯ ПЕТРОВА ИВАНОВА"
