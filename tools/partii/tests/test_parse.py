"""The parsers on real answers of ЕРИК (tests/fixtures, cut by tools/cut_fixtures.py from the archive of 09.11.2026).

The expected numbers were read from the archived answers themselves, which are what the tables of the register's pages
show: `recordsTotal` and the totals are the source's own. The names of persons in the fixtures are replaced
(see the cutter); nothing else is.
"""
import copy
import datetime as dt
import json
from decimal import Decimal

import pytest

from ingest import archive, parse, store
from tests.helpers import FIX, donors_without, dump, entries, js, raw

D = Decimal


# ---------- the home page and the pages of an election ----------

def test_the_home_page_lists_the_elections_newest_first():
    listed = parse.home(raw("home"))
    assert len(listed) == 21 and [e for e, _ in listed][:4] == [95, 96, 94, 93] and listed[-1][0] == 68
    assert dict(listed)[95] == "Избори за президент и вицепрезидент на Република България на 25 октомври 2026 г."


def test_kind_and_day_come_from_the_title_and_a_title_without_a_day_gives_none():
    t = dict(parse.home(raw("home")))
    assert parse.title_info(t[95]) == ("president", dt.date(2026, 10, 25))
    assert parse.title_info(t[93]) == ("parliament", dt.date(2026, 4, 19))
    assert parse.title_info(t[81]) == ("local_new", dt.date(2024, 6, 23))          # "на на 23.06.2024 г."
    assert parse.title_info(t[96]) == ("local_partial", dt.date(2026, 10, 18))
    assert parse.title_info(t[76]) == ("local", dt.date(2023, 10, 29))
    assert parse.title_info(t[79]) == ("european", None)                              # the title has no day: db/ref/election_day.csv
    assert store.ref_days()[79] == dt.date(2024, 6, 9)


def test_a_home_page_without_elections_is_refused():
    with pytest.raises(parse.ShapeError):
        parse.home(b"<html><body>Service unavailable</body></html>")


def test_the_page_states_the_declaration_threshold_in_euro_in_text_and_in_script():
    assert parse.page_donation(raw("page_donation", 96)) == dict(threshold=D("620.20"), threshold_shown=D("620.20"), currency="EUR")
    old = parse.page_donation(raw("page_donation", 79))     # 933 leva / 1,95583: the script has the exact value, the text its rounding
    assert old["threshold_shown"] == D("477.04") and abs(old["threshold"] - D(933) / D("1.95583")) < D("0.0001")


def test_a_threshold_in_another_currency_or_two_different_thresholds_are_refused():
    page = raw("page_donation", 96).decode()
    with pytest.raises(parse.ShapeError, match="не е в евро"):
        parse.page_donation(page.replace("620,20 €", "620,20 лв.").encode())
    with pytest.raises(parse.ShapeError, match="са различни"):
        parse.page_donation(page.replace("620,20 €", "720,20 €").encode())
    with pytest.raises(parse.ShapeError):
        parse.page_donation(b"<html><body>error</body></html>")


def test_the_page_counts_the_registered_participants():
    assert parse.page_participants(raw("page_participants", 81)) == dict(cik=22, rik=0, oik=24)
    assert parse.page_participants(raw("page_participants", 95)) == dict(cik=27, rik=0, oik=0)
    with pytest.raises(parse.ShapeError):
        parse.page_participants(b"<html>nothing</html>")


# ---------- donors ----------

def test_donors_of_a_whole_election_with_the_source_s_own_counter_and_totals():
    t = parse.donors(raw("donors", 79))
    assert t["records_total"] == 39 == len(t["rows"])
    assert t["total_money"] == D("35369.1271736296099356") and t["total_in_kind"] == 0
    first = t["rows"][0]      # row 1 of the archived answer
    assert (first["reg_in"], first["source_id"], first["recipient_kind"], first["recipient_name"]) == (1, 1215, "coalition", "КП СОЛИДАРНА БЪЛГАРИЯ")
    assert (first["day"], first["kind"], first["money"], first["decl"]) == (dt.date(2024, 4, 29), "money", D("2556.4594059810924262"), "published")
    third = t["rows"][2]
    assert (third["label"], third["money"], third["decl"]) == ("КП ПРОДЪЛЖАВАМЕ ПРОМЯНАТА - ДЕМОКРАТИЧНА БЪЛГАРИЯ - Коалиция", D("269.9621132716033602"), "none")
    assert (t["rows"][-1]["source_id"], t["rows"][-1]["money"]) == (1213, D("475.5014495124831913"))


def test_money_and_non_monetary_value_are_separate_columns():
    t = parse.donors(raw("donors", 81))
    assert t["total_money"] == D("3067.7512871773109115") and t["total_in_kind"] == D("1227.1005148709243646")
    assert [r["kind"] for r in t["rows"]] == ["in_kind", "in_kind", "money", "money", "in_kind"]
    assert all((r["kind"] == "money") == (r["in_kind"] == 0) for r in t["rows"])
    local = t["rows"][0]      # a participant registered in a municipal commission (ОИК)
    assert (local["reg_in"], local["source_id"], local["label"]) == (3, 117586, "НИЕ ИДВАМЕ - Партия, община Хасково, кметство Любеново")


def test_no_name_of_a_person_leaves_the_parser():
    for e in entries("donors"):
        t = parse.donors(archive.read(e, FIX))
        assert all(r["donor_kind"] == "person" and r["donor_name"] is None for r in t["rows"])
        assert "Физическо лице" not in repr(t)
    assert "Кандидат " not in repr(parse.candidates(raw("candidates", 79)))
    assert "Лице " not in repr(parse.results(raw("results", 79)))


def test_a_legal_entity_is_named_and_a_sole_trader_is_not():
    d = js("donors", 96)
    d["data"][0]["fullName"] = "АЛФА КОНСУЛТ ЕООД"
    d["data"][1]["fullName"] = "ЕТ Иван Петров - Комерс"
    d["data"][2]["fullName"] = "Адриана Адамова"        # "АД" inside a name is not a legal form
    rows = parse.donors(dump(d))["rows"]
    assert [(r["donor_kind"], r["donor_name"]) for r in rows] == [("legal", "АЛФА КОНСУЛТ ЕООД"), ("sole_trader", None), ("person", None)]


def test_equal_rows_are_kept_apart_by_an_ordinal_and_the_identity_has_no_name():
    d = js("donors", 96)
    twin = copy.deepcopy(d["data"][0])
    twin["fullName"] = "Друго лице"                    # another person who gave the same on the same day to the same participant
    d["data"].append(twin)
    d["recordsTotal"] = d["recordsFiltered"] = 4
    d["additionalParameters"]["TotalDonationPrice"] += twin["donationPrice"]
    rows = parse.donors(dump(d))["rows"]
    assert rows[3]["row_key"] == rows[0]["row_key"] and (rows[0]["ordinal"], rows[3]["ordinal"]) == (1, 2)
    assert len({(r["row_key"], r["ordinal"]) for r in rows}) == 4


def test_fewer_rows_than_the_counter_or_a_sum_that_differs_is_a_mismatch():
    d = js("donors", 79)
    short = dict(d, data=d["data"][:-1])
    with pytest.raises(parse.Mismatch, match="източникът брои 39"):
        parse.donors(dump(short))
    off = copy.deepcopy(d)
    off["additionalParameters"]["TotalDonationPrice"] += 0.01
    with pytest.raises(parse.Mismatch, match="парични"):
        parse.donors(dump(off))
    off = copy.deepcopy(js("donors", 81))
    off["additionalParameters"]["TotalDonationValue"] = 0.0
    with pytest.raises(parse.Mismatch, match="непарични"):
        parse.donors(dump(off))


def test_the_non_monetary_value_is_never_added_to_the_money():
    d = js("donors", 81)
    both = copy.deepcopy(d)      # a source total that had the two added together must not pass as the money
    both["additionalParameters"]["TotalDonationPrice"] += both["additionalParameters"]["TotalDonationValue"]
    with pytest.raises(parse.Mismatch):
        parse.donors(dump(both))


def test_a_row_whose_stated_kind_disagrees_with_its_amount_keeps_both():
    d = js("donors", 81)
    d["data"][0]["donationType"] = "Парично"           # the register of 2023 has such a row: marked money, the amount under "Стойност"
    row = parse.donors(dump(d))["rows"][0]
    assert (row["kind"], row["stated_kind"], row["money"], row["in_kind"]) == ("in_kind", "money", 0, D("383.4689108971638639"))


def test_the_empty_dates_of_the_source_are_no_date():
    d = js("donors", 96)
    d["data"][0]["donationDate"] = "1753-01-01T00:00:00"
    assert parse.donors(dump(d))["rows"][0]["day"] is None
    assert all(r["day"] is None for r in parse.candidates(raw("candidates", 68))["rows"])      # 0001-01-01 in the whole answer
    d["data"][0]["donationDate"] = "вчера"
    with pytest.raises(parse.ShapeError, match="не е дата"):
        parse.donors(dump(d))


def test_the_declaration_has_three_states():
    assert {r["decl"] for r in parse.donors(raw("donors", 95, which=1))["rows"]} == {"processing"}
    assert {r["decl"] for r in parse.donors(raw("donors", 79))["rows"]} == {"published", "none"}


@pytest.mark.parametrize("change, message", [
    (lambda d: d["data"][0].update(newField=1), "непознати или липсващи полета"),
    (lambda d: d["data"][0].pop("donationValueTxt"), "непознати или липсващи полета"),
    (lambda d: d.pop("recordsFiltered"), "непознати или липсващи полета"),
    (lambda d: d.update(recordsTotal="3"), "не е число"),
    (lambda d: d.pop("additionalParameters"), "общите суми"),
    (lambda d: d["data"][0].update(donationPriceTxt="500,00"), "не е число"),
    (lambda d: d["data"][0].update(donationPriceTxt="-500.00", donationPrice=-500.0), "отрицателна"),
    (lambda d: d["data"][0].update(donationPriceTxt="501.00"), "са различни"),
    (lambda d: d["data"][0].update(donationType="Заем"), "непознат вид"),
    (lambda d: d["data"][0].update(hasDeclaration="да"), "декларация"),
    (lambda d: d["data"][0].update(rik=d["data"][0]["cik"]), "получателят"),
    (lambda d: d["data"][0]["cik"].update(regIn=7), "непознат получател"),
    (lambda d: d["data"][0]["cik"].pop("bankIban"), "получателят"),
])
def test_a_changed_shape_stops_the_answer(change, message):
    d = js("donors", 96)
    change(d)
    with pytest.raises(parse.ShapeError, match=message):
        parse.donors(dump(d))


@pytest.mark.parametrize("data, message", [
    (b"", "празен"), (b"   \n", "празен"),
    (b"<!DOCTYPE html><html><body>403 Forbidden</body></html>", "HTML"),
    (b'{"draw":1,"recordsTotal":3,"recordsFil', "невалиден JSON"),
    (b"[]", "не е обект"),
    ("﻿".encode() + b"\xd0", "UTF-8"),
])
def test_an_answer_that_is_not_the_table_stops(data, message):
    with pytest.raises(parse.ShapeError, match=message):
        parse.donors(data)


def test_a_byte_order_mark_does_not_matter():
    assert parse.donors("﻿".encode() + raw("donors", 96))["records_total"] == 3


# ---------- the other tables ----------

def test_candidate_funds_with_their_totals_and_without_names():
    t = parse.candidates(raw("candidates", 79))
    assert (t["records_total"], t["total_money"], t["total_in_kind"]) == (51, D("15364.3210299463654815"), D("63138.6061160734827859"))
    old = parse.candidates(raw("candidates", 68))
    assert (old["records_total"], old["total_money"], old["total_in_kind"]) == (29, D("157792.00"), D("6426.40"))
    assert {r["role"] for r in old["rows"]} == {"Кандидат", "Член на инициативен комитет"} and max(r["ordinal"] for r in old["rows"]) == 3
    assert set(old["rows"][0]) == {"n", "label", "role", "day", "money", "in_kind", "decl", "row_key", "ordinal"}
    d = js("candidates", 79)
    d["totalDonationsNoMoney"] = 0.0
    with pytest.raises(parse.Mismatch, match="непарични"):
        parse.candidates(dump(d))


def test_agencies_and_the_check_digit_of_their_eik():
    t = parse.agencies(raw("agencies", 81))
    assert t["records_total"] == 32 and sum(1 for r in t["rows"] if r["eik_valid"]) == 31
    bad = [r for r in t["rows"] if not r["eik_valid"]]
    assert [(r["name"], r["eik"]) for r in bad] == [("ГАРС ООД", "BG1080620844")]
    assert (t["rows"][0]["label"], t["rows"][0]["eik"], t["rows"][0]["kind"]) == ("ИМА ТАКЪВ НАРОД - Партия", "116050625", "Рекламна")
    assert parse.eik_ok("116050625") and not parse.eik_ok("116050626") and not parse.eik_ok("11605062") and not parse.eik_ok(None)
    assert parse.eik_ok("000695317") and parse.eik_ok("121100421")          # leading zeros stay; text, not a number
    d = js("agencies", 81)
    d["recordsTotal"] = d["recordsFiltered"] = 33
    with pytest.raises(parse.Mismatch):
        parse.agencies(dump(d))


def test_the_income_check_is_kept_as_counts_and_sums_only():
    t = parse.results(raw("results", 79))
    assert t == dict(records_total=47, checked=47, compliant=42, total_money=D("96769.1466027210931"), total_in_kind=D("63138.6061160734828"),
                     total_sum=D("159907.7527187945759"))
    d = js("results", 79)
    d["data"][0]["sum"] += 5
    with pytest.raises(parse.Mismatch):
        parse.results(dump(d))


def test_participants_by_the_commission_they_registered_in():
    t = parse.participants(raw("participants", 96, "3", which=0), 3)
    assert t["records_total"] == 31
    r = next(x for x in t["rows"] if x["source_id"] == 118110)
    assert (r["kind"], r["name"], r["municipality"], r["settlement"], r["sub_election"]) == ("party", "ДВИЖЕНИЕ НАПРЕД БЪЛГАРИЯ", "Своге", "Искрец", "Избори за кмет на кметство")
    assert parse.participants(raw("participants", 96, "1"), 1)["records_total"] == 15
    d = js("participants", 96, "1")
    d["data"][0]["participantName"] = "Сдружение"
    with pytest.raises(parse.ShapeError, match="непознат вид участник"):
        parse.participants(dump(d), 1)
    d = js("participants", 96, "1")
    d["data"][1]["id"] = d["data"][0]["id"]
    with pytest.raises(parse.ShapeError, match="повторен"):
        parse.participants(dump(d), 1)


# ---------- the reports after the election ----------

def test_the_report_sheet_of_the_parties_as_filed():
    r = parse.report(raw("report", 93, "0"), "0", store.layouts())
    assert (r["kind"], r["records_total"], len(r["columns"])) == ("party", 14, 56)
    assert r["title"] == "Избори за Народно събрание на 19 април 2026 г."
    codes = {c["col"]: c["code"] for c in r["columns"]}
    row = next(x for x in r["rows"] if x["label"] == "ДВИЖЕНИЕ ЗА ПРАВА И СВОБОДИ")
    got = {codes[col]: amount for col, _, amount in row["cells"]}
    assert (got["income.1"], got["income.2.1"], got["income.total"], got["expense.2"], got["expense.total"], got["total"]) == \
        (D("129396"), D("200000"), D("429746"), D("404436.02"), D("426164.86"), D("426164.86"))


def test_the_lines_of_the_form_keep_their_section_and_their_parent():
    cols = {c["code"]: c for c in parse.report(raw("report", 93, "0"), "0")["columns"]}
    assert cols["income.1.1"]["parent"] == "income.1" and cols["expense.1.1"]["parent"] == "expense.1"       # 1.1 exists twice: the section is in the code
    assert cols["expense.2.1.2.1"]["parent"] == "expense.2.1.2" and cols["expense.2.1.2.1"]["depth"] == 4
    assert cols["income.total"]["depth"] == 0 and cols["expense.unpaid"]["parent"] == "expense.total" and "in_kind.total" in cols and "total" in cols
    local = parse.report(raw("report", 81, "3"), "3")["columns"]
    assert [c["code"] for c in local][:4] == ["income.1", "income.1.1", "income.1.2", "income.2"] and len(local) == 42


def test_minus_one_is_nothing_filed_and_never_zero():
    r = parse.report(raw("report", 81, "3"), "3")
    cells = r["rows"][0]["cells"]
    assert cells[0] == (1, "0", D("0")) and cells[2] == (3, "-1", None)
    assert sum(1 for x in r["rows"] for c in x["cells"] if c[2] is None) == 30


def test_an_empty_sheet_is_no_reports_and_a_changed_sheet_stops():
    assert parse.report(raw("report", 96, "0"), "0", store.layouts())["rows"] == []
    lay = store.layouts()
    changed = {k: [h + " (ново)" if i == 5 else h for i, h in enumerate(v)] for k, v in lay.items()}
    with pytest.raises(parse.ShapeError, match="не са от познатите"):
        parse.report(raw("report", 93, "0"), "0", changed)
    with pytest.raises(parse.ShapeError, match="Excel"):
        parse.report(raw("report", 93, "0")[:2000], "0")
    with pytest.raises(parse.ShapeError, match="непознато заглавие"):
        parse.columns(["1. Приходи", "Нещо без номер", "Общ размер на набраните средства"])
    with pytest.raises(parse.ShapeError, match="липсва обща колона"):
        parse.columns(["1. Приходи", "1.1 Членски внос"])


def test_the_same_cells_in_a_new_file_have_the_same_content_hash():
    a = entries("report", 93, "0")[0]
    r = parse.report(archive.read(a, FIX), "0")
    assert len(r["content_sha256"]) == 64 and r["content_sha256"] != a["sha256"]
    assert parse.report(raw("report", 93, "1"), "1")["content_sha256"] != r["content_sha256"]


# ---------- the archive ----------

def test_the_archive_keys_the_tool_reads_and_the_ones_it_leaves():
    e = "https://erik.bulnao.government.bg"
    assert archive.classify("home") == ("home", None, "")
    assert archive.classify(f"POST {e}/Reports/GetDonorsByElectionId?draw=1&start=0&length=-1&electionId=95&isOldSystemElection=true") == ("donors", 95, "")
    assert archive.classify(f"POST {e}/Reports/GetParticipantsByElectionId?draw=1&start=0&length=-1&electionId=96&electionCommissionType=3") == ("participants", 96, "3")
    assert archive.classify(f"GET {e}/Reports/AfterElectionExport/?electionId=93&participantTypeId=1") == ("report", 93, "1")
    assert archive.classify(f"GET {e}/Reports/Donation?electionId=79") == ("page_donation", 79, "")
    for personal in (f"GET {e}/Participant/Index/?ikRegisteredId=1448&electionId=93&ikTypeId=1", f"GET {e}/Home/Download/?documentId=10&idE=95",
                     f"GET {e}/Reports/GetDonorsData?financialReportId=104643", f"GET {e}/Reports/AfterElectionExportList?ElectionId=93"):
        assert archive.classify(personal) is None
    assert len(archive.entries(FIX)) == 54


def test_a_file_that_differs_from_the_index_is_refused(tmp_path):
    from tests.helpers import copy as copy_archive
    root = copy_archive(tmp_path, keep=lambda e: e["table"] == "home" or e["erik_id"] == 96)
    e = [x for x in archive.entries(root) if x["table"] == "donors"][-1]
    p = root / e["file"]
    p.write_bytes(p.read_bytes().replace(b"500.0", b"900.0"))
    with pytest.raises(archive.ArchiveError, match="не съвпада"):
        archive.read(e, root)
    page = [x for x in archive.entries(root) if x["table"] == "page_donation"][-1]      # a page is hashed without its tokens, as the archive does
    assert archive.read(page, root)


def test_fixtures_hold_no_personal_values():
    """The cutter replaced them: a contact, a bank account or a representative left in a fixture is a leak."""
    for e in entries("donors") + entries("candidates") + entries("results"):
        for r in json.loads(archive.read(e, FIX))["data"]:
            who = r.get("fullName", r.get("name"))
            assert who.startswith(("Физическо лице ", "Кандидат ", "Лице ")), e["file"]
            for k in ("cik", "rik", "oik"):
                o = r.get(k) or {}
                assert not any(o.get(f) for f in ("contactName", "contactPhone", "contactEmail", "contactAddress", "personResponsible", "personEgn", "bankIban", "bankName", "phone")), e["file"]
            assert "id=0" in (r.get("declarationLink") or r.get("fileDownLoadLink") or "id=0")
