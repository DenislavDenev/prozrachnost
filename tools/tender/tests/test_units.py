"""Unit tests for the pure logic behind docs/methodology.md (no database)."""
import datetime as dt
from xml.etree import ElementTree as ET

from app.queries import translit
from ingest import normalize as N
from ingest import registry as R
from ingest.networks import union_find

H1, H2 = "a" * 64, "b" * 64


def deed(fields, uic="111111111"):
    """A minimal partida: fields = [(ident, element, op, entry_no, date, inner_xml)]."""
    body = "".join(
        f'<{el} FieldIdent="{i}" FieldOperation="{op}" FieldEntryNumber="{no}" FieldEntryDate="{d}T10:00:00">{inner}</{el}>'
        for i, el, op, no, d, inner in fields)
    xml = (f'<DeedResult><Deed UIC="{uic}" CompanyName="X" DeedStatus="N" LegalForm="OOD">'
           f'<SubDeed SubUIC="0001" SubUICType="MainCircumstances" SubDeedStatus="A">{body}</SubDeed></Deed></DeedResult>')
    return R.parse_deed(xml.encode(), uic)


def person(h, name, rid="1", itype="EGN"):
    return f'<Manager RecordID="{rid}"><Person><Indent>{h}</Indent><Name>{name}</Name><IndentType>{itype}</IndentType></Person></Manager>'


def test_role_ends_when_next_entry_omits_holder():
    d = deed([("00070", "Managers", "Add", "1", "2019-01-01", person(H1, "Иван Иванов Петров")),
              ("00070", "Managers", "Add", "2", "2021-05-01", person(H2, "Мария Петрова Иванова", "2"))])
    roles, persons = R.roles_from_deed("111111111", d)
    by = {r["holder_id"]: r for r in roles}
    assert by[H1]["valid_from"] == "2019-01-01" and by[H1]["valid_to"] == "2021-05-01"
    assert by[H2]["valid_to"] is None
    assert {p["indent"] for p in persons} == {H1, H2}


def test_erase_ends_every_holder():
    d = deed([("00070", "Managers", "Add", "1", "2019-01-01", person(H1, "Иван Иванов Петров")),
              ("00070", "Managers", "Erase", "2", "2020-01-01", "")])
    (r,), _ = R.roles_from_deed("111111111", d)
    assert r["valid_to"] == "2020-01-01"


def test_same_name_different_hash_are_two_people():
    d = deed([("00070", "Managers", "Add", "1", "2019-01-01",
               person(H1, "Иван Иванов Петров") + person(H2, "Иван Иванов Петров", "2"))])
    roles, _ = R.roles_from_deed("111111111", d)
    assert {r["holder_id"] for r in roles} == {H1, H2}


def test_birthdate_identifier_is_local_only():
    d = deed([("00070", "Managers", "Add", "1", "2019-01-01", person(H1, "John Smith", itype="BirthDate"))])
    (r,), persons = R.roles_from_deed("111111111", d)
    assert r["holder_id"].startswith("local:111111111:birthdate:") and persons == []


def test_collective_holder_is_not_attributed():
    d = deed([("00070", "Managers", "Add", "1", "2019-01-01", person(H1, "Иван Петров и Мария Петрова"))])
    roles, _ = R.roles_from_deed("111111111", d)
    assert roles == []


def test_00210_is_partner_not_sole_owner():
    assert R.ROLE_FIELDS["00210"] == "partner" and R.ROLE_FIELDS["00230"] == "sole_owner"
    assert R.VIEW_OF_ROLE["partner"] == "ownership" and R.VIEW_OF_ROLE["manager"] == "management"


def test_eik_checksum():
    assert N.eik_valid("831641791") and N.eik_valid("202210490")
    assert not N.eik_valid("831641792") and not N.eik_valid("000000000") and not N.eik_valid("12345")


def test_group_members_split_and_unpairable_lists():
    assert N.split_members("А ООД; Б ЕООД", "202210490; 831641791") == [("202210490", "А ООД"), ("831641791", "Б ЕООД")]
    assert N.split_members("А; Б; В", "1; 2") == [(None, "А"), (None, "Б"), (None, "В")]


def test_value_flags():
    # estimate 10 000, value 1 000 000 = 100x -> dropped decimal point, repaired to the estimate
    assert N.value_flag(1_000_000, 10_000, 10_000, 1_000_000, None, []) == ("value_suspect", "estimate")
    # annex pushed value 150x -> sum the initial value
    assert N.value_flag(150_000, None, None, 1_000, 150_000, [150]) == ("annex_suspect", "initial")
    # legitimate 20% annex stays ok
    assert N.value_flag(1_200, 1_000, 1_000, 1_000, 1_200, [1.2]) == ("ok", "effective")
    assert N.value_flag(0, None, None, 0, None, [])[0] == "value_low"
    assert N.value_flag(20_000, 1_500, 1_500, 20_000, None, [])[0] == "review"


def test_fx_bgn_fixed_and_foreign_window():
    fx = N.Fx({"USD": [(dt.date(2024, 1, 2), 1.1)]})
    assert round(fx.to_eur(1.95583, "BGN", None)[0], 6) == 1
    assert round(fx.to_eur(110, "USD", dt.date(2024, 1, 5))[0], 6) == 100
    assert fx.to_eur(110, "USD", dt.date(2024, 2, 5)) == (None, None)  # no rate within 10 days


def test_contract_id_is_stable_and_supplier_sensitive():
    a = N.contract_id("U1", "7", "1", ["eik:202210490"])
    assert a == N.contract_id("U1", "7", "1", ["eik:202210490"]) and a != N.contract_id("U1", "7", "1", ["eik:831641791"])


def test_unawarded_lot_is_not_a_contract():
    rows = [{"noAwarding": "Да", "uniqueProcurementNumber": "U", "contractValue": "0,00"},
            {"noAwarding": "Не", "uniqueProcurementNumber": "U", "contractNumber": "1", "contractValue": "100,00",
             "contractCurrency": "EUR", "supplierName": "А", "supplierRegisterNumber": "202210490", "contractDate": "01.02.2024"}]
    res = N.normalize([("2024-02-02", "contracts", rows)], N.Fx({}))
    assert len(res["contract"]) == 1 and res["contract"][0]["value_initial"] == 100


def test_annex_chain_current_value():
    c = {"noAwarding": "Не", "uniqueProcurementNumber": "U", "contractNumber": "9", "contractValue": "1000",
         "contractCurrency": "EUR", "supplierName": "А", "supplierRegisterNumber": "202210490", "contractDate": "01.02.2024",
         "publicationDate": "2024-02-02T10:00:00"}
    a1 = {"uniqueProcurementNumber": "U", "contractNumber": "9", "lastContractValue": "1000", "currentContractValue": "1100",
          "contractValueDifference": "100", "publicationDate": "2024-05-01T10:00:00"}
    a2 = dict(a1, lastContractValue="1100", currentContractValue="1300", publicationDate="2024-08-01T10:00:00")
    res = N.normalize([("2024-02-02", "contracts", [c]), ("2024-08-01", "annexes", [a2]), ("2024-05-01", "annexes", [a1])], N.Fx({}))
    k = res["contract"][0]
    assert k["value_current"] == 1300 and k["annex_count"] == 2 and k["value_flag"] == "ok" and k["amount_eur"] == 1300


def test_union_find_and_translit():
    comp = union_find([("p:1", "c:1"), ("c:1", "p:2"), ("p:3", "c:2")])
    assert comp["p:1"] == comp["p:2"] != comp["p:3"]
    assert translit("Ivanov") == "иванов" and translit("Zhivkov") == "живков"


def test_estimate_ratio_eur_estimates_and_implausible_date():
    t = {"uniqueProcurementNumber": "U", "subject": "S", "estimatedValue": "195583", "currency": "BGN",
         "publicationDate": "2024-01-10T10:00:00", "buyerRegistryNumber": "000093435"}
    c = {"noAwarding": "Не", "uniqueProcurementNumber": "U", "contractNumber": "1", "contractValue": "250000",
         "contractCurrency": "EUR", "supplierName": "А", "supplierRegisterNumber": "202210490",
         "contractDate": "14.05.2029", "publicationDate": "2024-05-23T09:00:00"}
    res = N.normalize([("2024-01-10", "tenders", [t]), ("2024-05-23", "contracts", [c])], N.Fx({}))
    k, tt = res["contract"][0], res["tender"][0]
    assert round(tt["estimated_eur"]) == 100000                       # BGN at 1.95583
    assert k["estimate_ratio"] == 2.5 and k["value_flag"] == "ok"     # 2.5x the estimate, below the 10x review
    assert str(k["effective_date"]) == "2024-05-23" and k["date_flag"] == "contract_date_implausible"
    assert {r["entity"] for r in res["source_record"]} == {"tender", "contract"}


def test_clean_name_drops_personal_numbers_and_representatives():
    assert N.clean_name('"ГЕОТЕХМИН" ООД, представлявано от Цоло Вутов ЕГН 1234567890') == '"ГЕОТЕХМИН" ООД'
    assert N.clean_name('ВЕРА ИВАНОВНА ДИМИТРОВА, ЕГН 1234567890') == 'ВЕРА ИВАНОВНА ДИМИТРОВА'
    assert N.clean_name('ЛУДВИК БАЛЕКА - 1234567890') == 'ЛУДВИК БАЛЕКА'
    assert N.clean_name('"АСЕТС ГРУП"АД, с представляващ Ю.Л. Бисерка Асенова, ЕГН 1234567890') == '"АСЕТС ГРУП"АД'
    assert N.clean_name('Ефармогес АД, ЕИК 123456789') == 'Ефармогес АД'
    assert N.clean_name('"ДИВА - 90" ООД') == '"ДИВА - 90" ООД'  # a number in the registered name stays
    assert N.clean_name('Аспарух Михайлов Минчев, ЕГН:') == 'Аспарух Михайлов Минчев'
    assert N.clean_name('МЕТКА ЕГН СОЛАР ООД') == 'МЕТКА ЕГН СОЛАР ООД' and N.clean_name('АНГЕЛ АНЕГНОСТИЕВ') == 'АНГЕЛ АНЕГНОСТИЕВ'


def test_prefix_query_ignores_order_and_middle_name():
    from app.queries import prefix_query
    assert prefix_query("Иван  ПЕТРОВ") == "иван:* & петров:*"  # matches "ИВАН ГЕОРГИЕВ ПЕТРОВ" in any order
    assert prefix_query("a, b") is None and prefix_query("x") == "x:*"


class FakeConn:
    """live.edge as a list of (holder, company) for the path search."""
    def __init__(self, edges):
        self.edges = edges

    def execute(self, sql, params):
        nodes = set(params[0])
        rows = [(h, c) for h, c in self.edges if h in nodes or c in nodes]
        return type("R", (), {"fetchall": lambda _: rows})()


def test_every_shortest_path_is_counted_and_paged():
    from app.queries import shortest_paths
    # a and b are linked through c1..c3 (three 2-step paths) and through a long chain (not shortest)
    edges = [("p:a", f"c:{i}") for i in (1, 2, 3)] + [("p:b", f"c:{i}") for i in (1, 2, 3)] + [("p:a", "c:9"), ("p:x", "c:9"), ("p:x", "c:8"), ("p:b", "c:8")]
    r = shortest_paths(FakeConn(edges), "p:a", "p:b", limit=2)
    assert r["total"] == 3 and len(r["paths"]) == 2 and all(len(p) == 3 for p in r["paths"])
    rest = shortest_paths(FakeConn(edges), "p:a", "p:b", offset=2, limit=2)["paths"]
    assert len(rest) == 1 and rest[0] not in r["paths"]
    assert shortest_paths(FakeConn(edges), "p:a", "p:zz")["total"] == 0  # not linked: searched to the end, no cap


def test_lot_and_tender_status():
    T = lambda unp, lot, **kw: {"uniqueProcurementNumber": unp, "lotIdentifier": lot, "isLot": "Да" if lot else "Не",
                                "publicationDate": "2021-01-01T10:00:00", "subject": "x", **kw}
    tenders = [T("A", None), T("A", "LOT-0001"), T("A", "LOT-0002"), T("B", None, isCancelled="Да"), T("C", None), T("D", None)]
    contracts = [{"noAwarding": "Не", "uniqueProcurementNumber": "A", "lotIdentifier": "LOT-0001", "contractNumber": "1",
                  "contractValue": "100,00", "contractCurrency": "EUR", "supplierName": "А", "supplierRegisterNumber": "202210490",
                  "contractDate": "01.02.2021"},
                 {"noAwarding": "Да", "uniqueProcurementNumber": "A", "lotIdentifier": "LOT-0002"},
                 {"noAwarding": "Да", "uniqueProcurementNumber": "C"}]
    res = N.normalize([("2021-01-02", "tenders", tenders), ("2021-03-01", "contracts", contracts), ("2023-01-01", "annexes", [])], N.Fx({}))
    state = {t["unp"]: t["state"] for t in res["tender"]}
    lots = {(l["unp"], l["lot_no"]): l["status"] for l in res["lot"]}
    assert state == {"A": "contracted", "B": "cancelled", "C": "unawarded", "D": "no_contract"}
    assert lots == {("A", 1): "contracted", ("A", 2): "unawarded"}


def test_offers_parse_lots_prices_and_identifiers():
    import json
    from pathlib import Path
    from ingest import eop_offers as O
    fx = lambda t: json.loads((Path(__file__).parent / "fixtures" / "eop_svc" / f"{t}.json").read_text(encoding="utf-8"))
    one = fx(495012)
    rows = O.parse(495012, one["participation"], one["lots"])
    assert len(rows) == 3 and {r["lot_no"] for r in rows} == {0} and round(sum(r["price"] for r in rows), 2) == 1648930.56
    assert all(r["bidder_eik"] and len(r["bidder_eik"]) == 9 for r in rows) and rows[0]["submitted_at"].year == 2025
    lots = fx(56601)
    rows = O.parse(56601, lots["participation"], lots["lots"])
    assert {r["lot_no"] for r in rows} <= set(range(0, 7)) and all(r["price"] is None for r in rows if not r["price_opened"])
    assert O.eik("8001011234") is None  # a 10-digit personal number is never kept
    try:
        O.parse(1, {"Rounds": [{"Offers": [{"OfferId": 1}]}]})
        assert False, "a changed answer must raise"
    except O.ShapeError:
        pass


def test_sebra_rows_drop_personal_details():
    from ingest import sebra as S
    head = S.COLS + ["CLIENT_NAME_HASH"]
    row = lambda name, acc: ["02.04.2026", name, acc, "BIC", "0010000008", "Народно събрание", "3249.8", "EUR", "ДОГОВОР", "№ 5",
                             "02.04.2026", "E1", "10", "Народно събрание", "Народно събрание", "001", "h"]
    out = S.parse([head, row("ХЕМУСХОТЕЛС АД", "BG19BUIN95611000648772"), row("ФИЗИЧЕСКО ЛИЦЕ", "812313634")])
    assert out[0]["receiver_iban"].startswith("BG19") and out[0]["amount"] == 3249.8 and out[0]["settlement_date"].month == 4
    assert out[1]["is_person"] and out[1]["receiver_iban"] is None and out[1]["reason"] is None and out[1]["reg_no"] is None
    assert S.amount("1 234,56") == 1234.56 and S.amount("17265.6") == 17265.6


def test_contract_lots_follow_the_service_numbering():
    from ingest import eop_offers as O
    cl = {"ContractListItems": [], "Lots": [{"LotNumber": 6, "ContractListItems": [{"Id": 264875}]},
                                           {"LotNumber": 1, "ContractListItems": [{"Id": 263941}]}]}
    assert O.parse_contracts(cl) == [(6, "264875"), (1, "263941")]
    assert O.parse_contracts({"ContractListItems": [{"Id": 201257}], "Lots": []}) == [(0, "201257")] and O.parse_contracts(None) == []


def test_tender_events_fold_same_day_offers_and_keep_order():
    from app.queries import tender_events
    tz = dt.timezone(dt.timedelta(hours=3))
    t = {"published_at": dt.date(2026, 3, 1), "submission_deadline": dt.datetime(2026, 3, 31, 17, 0), "contracts": []}
    offer = lambda name, day, lot=1, won=False: {"bidder_eik": None, "bidder_name": name, "company_key": None, "won": won,
                                                 "submitted_at": dt.datetime(2026, 3, day, 10, 0, tzinfo=tz)}
    # a bidder's offer for two lots is one submission; different days are separate events
    lots = [{"lot_no": 1, "offers": [offer("А", 10), offer("Б", 30, won=True)]}, {"lot_no": 2, "offers": [offer("А", 10)]}]
    ev = tender_events(t, lots)
    assert [e["kind"] for e in ev] == ["pub", "offer", "offer", "deadline"]
    assert ev[1]["offers"][0]["lots"] == [1, 2] and ev[2]["offers"][0]["won"]
    # all on one day: one event, the time of each kept
    lots = [{"lot_no": 1, "offers": [offer("А", 10), offer("Б", 10)]}]
    ev = tender_events(t, lots)
    assert [e["kind"] for e in ev] == ["pub", "offers", "deadline"] and len(ev[1]["offers"]) == 2
