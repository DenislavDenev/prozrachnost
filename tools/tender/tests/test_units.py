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


def test_annex_in_euro_on_a_lev_contract_is_not_converted_twice():
    # from 01.01.2026 annexes restate лев contracts in euro (00233-2024-0091: 25 933 688,54 лв. = 13 259 684,40 €)
    c = {"noAwarding": "Не", "uniqueProcurementNumber": "U", "contractNumber": "9", "contractValue": "25933688,54",
         "contractCurrency": "BGN", "supplierName": "А", "supplierRegisterNumber": "202210490", "contractDate": "26.09.2025",
         "publicationDate": "2025-10-20T10:00:00"}
    a = {"uniqueProcurementNumber": "U", "contractNumber": "9", "lastContractValue": "13259684,4", "currentContractValue": "13259684,4",
         "contractValueDifference": "0", "contractCurrency": "EUR", "publicationDate": "2026-03-13T05:20:36"}
    k = N.normalize([("2025-10-20", "contracts", [c]), ("2026-03-13", "annexes", [a])], N.Fx({}))["contract"][0]
    assert k["value_current_currency"] == "EUR" and round(k["value_current_eur"], 2) == 13259684.4
    assert round(k["amount_eur"], 2) == 13259684.4 and round(k["value_initial_eur"], 2) == 13259684.4 and k["value_flag"] == "ok"
    # a later annex in лев on a euro contract is converted from лев
    c2 = dict(c, contractNumber="10", contractCurrency="EUR", contractValue="1000")
    a2 = dict(a, contractNumber="10", lastContractValue="1000", currentContractValue="3911,66", contractCurrency="BGN")
    k = N.normalize([("2025-10-20", "contracts", [c2]), ("2026-03-13", "annexes", [a2])], N.Fx({}))["contract"][0]
    assert round(k["amount_eur"], 2) == 2000.0


# ---------- completeness against ЦАИС ЕОП (audit 27.09.2026, docs/audit-2026-09-27.md) ----------

def _fx(t):
    import json
    from pathlib import Path
    return json.loads((Path(__file__).parent / "fixtures" / "eop_svc" / f"{t}.json").read_text(encoding="utf-8"))


def test_offer_participant_is_what_the_page_shows_never_a_person():
    # 00081-2026-0089 (Варна): a ДЗЗД submitted by one of its members, a foreign company's branch submitted by a person
    from ingest import eop_offers as O
    one = _fx(563386)
    rows = O.parse(563386, one["participation"], one["lots"])
    got = {(r["bidder_name"], r["bidder_eik"]) for r in rows}
    assert len(rows) == 6 and O.CANARY_PARTICIPANTS[563386] <= got
    assert ("Изи Еко Клийн ЕООД", "208242114") in got and ("БКС Чистота ЕООД", "127016841") in got
    ddzd = next(r for r in rows if "ЧИСТА ВАРНА" in r["bidder_name"])
    assert ddzd["bidder_eik"] is None and (ddzd["submitter_name"], ddzd["submitter_eik"]) == ("ЕКО РЕСУРС-Р ООД", "131306107")
    assert {m["eik"] for m in ddzd["consortium"]} == {"131306107", "202239595", "103801869"}
    branch = next(r for r in rows if r["bidder_eik"] == "208392391")
    assert branch["submitter_name"] is None and branch["submitter_eik"] is None     # the person who submitted is not kept
    assert not any("IVAN" in str(v) for r in rows for v in r.values())


def test_participant_rules_for_every_shape_the_service_sends():
    from ingest import eop_offers as O
    o = lambda offer, org, rn=None, members=(): {"OfferName": offer, "OrganizationName": org, "RegistryNumber": rn,
                                                  "ConsortiumMembers": [{"OrganizationName": n, "RegistryNumber": e} for n, e in members]}
    # one company, its name spelled with another legal form by its account (renamed ООД -> ЕООД)
    assert O.participant(o("ВЕРИГА ДОМИНО ЕООД", "ВЕРИГА ДОМИНО ООД", "103836699")) == ("ВЕРИГА ДОМИНО ЕООД", "103836699", None, None)
    # a consortium without listed members, submitted by a member: the ДЗЗД has no ЕИК, the submitter is kept
    assert O.participant(o("ДЗЗД „ЕЙ ЕН 2026“", "ЕН АР КОНСУЛТ ЕООД", "126720807")) == ("ДЗЗД „ЕЙ ЕН 2026“", None, "ЕН АР КОНСУЛТ ЕООД", "126720807")
    # a consortium with members, one of them submitted
    assert O.participant(o('ДЗЗД "БГ ТИЙМ"', "ЕКОТИЙМ ООД", "205058454", [("ЕКОТИЙМ ООД", "205058454"), ("БГБИОМАС ЕООД", "148052463")])) \
        == ('ДЗЗД "БГ ТИЙМ"', None, "ЕКОТИЙМ ООД", "205058454")
    # a ДЗЗД with its own number, listed as its only member (quotes and spacing differ)
    assert O.participant(o('" ЗА ЧИСТА ВАРНА " ДЗЗД', "ЕКО РЕСУРС-Р ООД", "131306107", [("ЗА ЧИСТА ВАРНА", "202239595")]))[:2] \
        == ('" ЗА ЧИСТА ВАРНА " ДЗЗД', "202239595")
    # a company submitted from a person's account: the company without ЕИК, the person nowhere
    assert O.participant(o("Пътни настилки ЕООД", "Иван Петров Тестов")) == ("Пътни настилки ЕООД", None, None, None)
    # a participant that is a person: only „Физическо лице“
    assert O.participant(o("Физическо лице - IVAN PETROV TESTOV", "IVAN PETROV TESTOV", None, [("АВАНДИ ГРУП ЕООД", "208242114")]))[0] == "Физическо лице"
    assert O.member_name("Физическо лице Иван Иванов") == "Физическо лице"
    # the old answer shape without OfferName is refused, not read with the submitter as participant
    import pytest
    with pytest.raises(O.ShapeError):
        O.parse(1, {"Rounds": [{"Offers": [{k: None for k in O.OFFER_KEYS - {"OfferName"}}]}]})


def test_details_notices_appeals_documents_and_no_contact_person():
    from ingest import eop_offers as O
    raw = _fx(563386)["details"]
    assert not {"ContactPersonDisplayName", "ContactPersonEmail", "ContactPersonPhone"} & set(O.sanitize_details(dict(raw, ContactPersonEmail="x@y")))
    d = O.parse_details(O.sanitize_details(raw))
    assert {p["id"] for p in d["publications"]} == {848280, 849607, 895881}
    award = next(p for p in d["publications"] if p["id"] == 895881)
    assert award["ted_number"] == "593377-2026" and award["published_at"] == dt.datetime(2026, 8, 27, 2, 5, 27, tzinfo=dt.timezone.utc)
    assert award["sent_at"] < award["published_at"]
    (a,) = d["appeals"]
    assert (a["register_id"], a["proceedings_number"], a["interim_measures"]) == ("ВХР-1614-19.05.2026", "КЗК/522/2026", True)
    assert a["initiators"] == ['"ЗМБГ" ЕАД'] and a["defendants"] == ["ОБЩИНА ВАРНА"] and a["link"].startswith("http://reg.cpc.bg/")
    assert "Методика за оценка.pdf" in {x["name"] for x in d["documents"]} and all(set(x) == {"id", "name", "size", "created_at"} for x in d["documents"])
    assert d["detail"]["opening_at"] == dt.datetime(2026, 6, 1, 11, 0, tzinfo=dt.timezone.utc)
    assert O.parse_details(None) is None
    import pytest
    with pytest.raises(O.ShapeError):
        O.parse_details({"unexpected": True})


def test_call_keeps_one_connection_and_reconnects_fast_after_a_reset(monkeypatch):
    from ingest import eop_offers as O
    made, slept = [], []

    class Resp:
        status, will_close = 200, False
        def read(self): return b"[]"
        def getheader(self, k): return None

    class Conn:
        def __init__(self, host, timeout): made.append(self); self.n = 0
        def request(self, *a, **k):
            self.n += 1
            if len(made) == 1:  # the first connection is reset by the service
                raise ConnectionResetError(104, "Connection reset by peer")
        def getresponse(self): return Resp()
        def close(self): pass
    monkeypatch.setattr(O.http.client, "HTTPSConnection", Conn)
    monkeypatch.setattr(O.time, "sleep", slept.append)
    monkeypatch.setattr(O, "_conn", None)
    assert O.call("X") == b"[]" and O.call("Y") == b"[]"
    assert len(made) == 2 and made[1].n == 2          # one new connection after the reset, then kept for the next call
    assert slept == [O.RECONNECT, O.PAUSE, O.PAUSE]   # 2 s, not 10 s


def test_award_notice_without_award_is_kept_with_its_offers():
    t = {"uniqueProcurementNumber": "U", "tenderId": "563386", "publicationDate": "2026-04-30T05:06:41", "subject": "x",
         "noticeId": "848280", "noticeType": "Обявление за поръчка"}
    a = {"noAwarding": "Да", "uniqueProcurementNumber": "U", "tenderId": 563386, "noticeId": 895881, "lotIdentifier": "LOT-0001",
         "publicationDate": "2026-08-27T05:05:27", "offersCount": 6, "noticeType": "Обявление за възложена поръчка",
         "linkToOjEu": "https://ted.europa.eu/udl?uri=TED:NOTICE:593377-2026:TEXT:BG:HTML"}
    res = N.normalize([("2026-04-30", "tenders", [t]), ("2026-08-27", "contracts", [a])], N.Fx({}))
    (aw,) = res["award"]
    assert (aw["notice_id"], aw["offers_count"], aw["lot_no"], aw["tender_id"]) == ("895881", 6, 1, "563386")
    assert {n["notice_id"] for n in res["notice"]} == {"848280", "895881"} and res["contract"] == []
    assert [s["entity"] for s in res["source_record"]] == ["tender", "award"] and res["tender"][0]["state"] == "unawarded"


def _contract(num, lot, value, supplier="А ООД", eik="202210490", day="01.02.2024", notice="1", pub="2024-02-02T10:00:00", **kw):
    return {"noAwarding": "Не", "uniqueProcurementNumber": "U", "contractNumber": num, "lotIdentifier": lot, "contractValue": str(value),
            "contractCurrency": "EUR", "supplierName": supplier, "supplierRegisterNumber": eik, "contractDate": day, "noticeId": notice,
            "publicationDate": pub, **kw}


def _annex(num, lot, last, cur, day="2024-06-01T10:00:00"):
    return {"uniqueProcurementNumber": "U", "contractNumber": num, "lotIdentifier": lot, "lastContractValue": str(last),
            "currentContractValue": str(cur), "contractCurrency": "EUR", "publicationDate": day}


def test_every_annex_finds_its_one_contract_or_is_kept_as_an_orphan():
    contracts = [_contract("115278", "LOT-0002", 1000),                        # annex says lot 1: the only contract of that number
                 _contract("ТО-105", None, 2000, supplier="Б ООД", eik="831641791"),   # annex spells it in Latin letters
                 _contract("Д-720", None, 3000, supplier="В ООД", eik="130873641"),    # annex carries ЦАИС ЕОП's own id
                 _contract("7", "LOT-0003", 500, supplier="Г ООД", eik="131306107"),
                 _contract("8", "LOT-0004", 500, supplier="Д ООД", eik="202239595")]
    annexes = [_annex("115278", "LOT-0001", 1000, 1100), _annex("TO-105", None, 2000, 2200), _annex("39240", "LOT-0001", 3000, 3300),
               _annex("99999", None, 500, 900)]                                  # two contracts start at 500: cannot tell
    res = N.normalize([("2024-02-02", "contracts", contracts), ("2024-06-01", "annexes", annexes)], N.Fx({}))
    cur = {c["contract_number"]: c["value_current"] for c in res["contract"]}
    assert cur == {"115278": 1100, "ТО-105": 2200, "Д-720": 3300, "7": None, "8": None}
    (orphan,) = res["annex_orphan"]
    assert orphan["contract_number"] == "99999" and orphan["current_value"] == 900
    st = res["_stats"]
    assert (st["annexes_number"], st["annexes_spelling"], st["annexes_start_value"], st["annexes_orphan"]) == (1, 1, 1, 1)
    assert N.number_key("Договор № 49-209") == N.number_key("49-209/23.11.20") == "49209" and N.number_key("0000021180") == "21180"


def test_an_annex_without_lot_is_not_applied_to_two_contracts():
    contracts = [_contract("5", "LOT-0001", 100), _contract("5", "LOT-0002", 200, supplier="Б ООД", eik="831641791")]
    res = N.normalize([("2024-02-02", "contracts", contracts), ("2024-06-01", "annexes", [_annex("5", None, 150, 300)])], N.Fx({}))
    assert all(c["value_current"] is None for c in res["contract"]) and len(res["annex_orphan"]) == 1
    res = N.normalize([("2024-02-02", "contracts", contracts), ("2024-06-01", "annexes", [_annex("5", "LOT-0002", 200, 300)])], N.Fx({}))
    assert {c["lot_no"]: c["value_current"] for c in res["contract"]} == {1: None, 2: 300}


def test_a_corrected_republication_replaces_the_contract_instead_of_adding_one():
    first = _contract("20ДГ890", "LOT-0001", 19938.24, supplier="ЕТ МОНИ-8", eik="130700899", day="19.11.2020", notice="74407",
                      pub="2020-11-23T10:00:00")
    fixed = _contract("20ДГ890", "LOT-0001", 19938.24, supplier="Д & Д ООД", eik="831260776", day="19.11.2020", notice="117976",
                      pub="2021-05-19T10:00:00")
    res = N.normalize([("2020-11-23", "contracts", [first]), ("2021-05-19", "contracts", [fixed])], N.Fx({}))
    assert [c["supplier_display"] for c in res["contract"]] == ["Д & Д ООД"] and res["_stats"]["contracts_corrected"] == 1
    assert {s["ref"] for s in res["source_record"]} == {res["contract"][0]["id"]}     # both versions point to the survivor
    # the other way round (the older one read last) keeps the newer
    res = N.normalize([("2021-05-19", "contracts", [fixed]), ("2021-05-20", "contracts", [first])], N.Fx({}))
    assert [c["supplier_display"] for c in res["contract"]] == ["Д & Д ООД"]
    # two suppliers in one notice (a framework, several contracts) are separate contracts
    other = dict(fixed, noticeId="74407")
    res = N.normalize([("2020-11-23", "contracts", [first, other])], N.Fx({}))
    assert len(res["contract"]) == 2


def test_a_row_that_goes_nowhere_stops_the_build(monkeypatch):
    import pytest
    seen = []
    monkeypatch.setattr(N, "check_accounting", seen.append)    # normalize itself runs the check on its counts
    N.normalize([("2024-02-02", "contracts", [_contract("1", None, 5), dict(_contract("2", None, 5), noAwarding="Да")])], N.Fx({}))
    assert seen and (seen[0]["in_contracts"], seen[0]["contracts_new"], seen[0]["contracts_no_award"]) == (2, 1, 1)
    monkeypatch.undo()
    N.check_accounting({"in_contracts": 2, "contracts_new": 1, "contracts_no_award": 1})
    with pytest.raises(AssertionError):
        N.check_accounting({"in_contracts": 2, "contracts_new": 1})


def test_dates_with_a_zone_are_shown_on_the_sofia_day():
    from app.main import fdate
    assert fdate(dt.datetime(2026, 5, 29, 21, 30, tzinfo=dt.timezone.utc)) == "30.05.2026"
    assert fdate(dt.date(2026, 5, 29)) == "29.05.2026" and fdate(None) == "няма данни"


def test_tender_events_show_no_award_opening_decisions_and_appeals():
    from app.queries import tender_events
    utc = dt.timezone.utc
    t = {"published_at": dt.datetime(2026, 4, 30, 5, 6), "submission_deadline": dt.datetime(2026, 5, 29, 23, 59, 59), "contracts": [],
         "awards": [{"notice_id": "895881", "published_at": dt.datetime(2026, 8, 27, 5, 5), "lot_no": 1, "offers_count": 6, "link_oj": None}],
         "eop": {"detail": {"opening_at": dt.datetime(2026, 6, 1, 11, 0, tzinfo=utc), "prices_opening_at": None,
                            "offers_until": dt.datetime(2026, 5, 29, 20, 59, 59, tzinfo=utc)},
                 "publications": [{"id": 849607, "in_open_data": False, "published_at": dt.datetime(2026, 4, 30, 2, 2, tzinfo=utc), "sent_at": None,
                                   "name": "Решение по чл. 22, ал. 1 от ЗОП", "ted_number": None},
                                  {"id": 848280, "in_open_data": True, "published_at": dt.datetime(2026, 4, 30, 2, 2, tzinfo=utc), "sent_at": None,
                                   "name": "x", "ted_number": None}],
                 "appeals": [{"filed_on": dt.date(2026, 5, 19), "started_on": None, "register_id": "ВХР-1614-19.05.2026"}]}}
    ev = tender_events(t, [])
    kinds = [e["kind"] for e in ev]
    assert kinds == ["notice", "pub", "appeal", "deadline", "opening", "noaward"]   # the deadline in ЦАИС ЕОП is the notice's: once
    assert ev[-1]["offers"] == 6 and ev[0]["title"].startswith("Решение по чл. 22")


def test_a_procedure_known_only_from_its_contracts_keeps_its_eop_id():
    # 12 618 such procedures had no ЦАИС ЕОП id before 27.09.2026: their page (offers, messages, appeals) was never read
    c = {"noAwarding": "Не", "uniqueProcurementNumber": "U", "tenderId": 123456, "contractNumber": "1", "contractValue": "10",
         "contractCurrency": "EUR", "supplierName": "А", "supplierRegisterNumber": "202210490", "contractDate": "01.02.2024"}
    (t,) = N.normalize([("2024-02-02", "contracts", [dict(c, tenderId=None, contractNumber="0"), c])], N.Fx({}))["tender"]
    assert t["synthetic"] and t["tender_id"] == "123456"
