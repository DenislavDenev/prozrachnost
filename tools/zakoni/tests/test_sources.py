"""Parsers and the streaming reader on real answers of the archive (tests/fixtures/egov, cut from the files of
02.10.2026, personal fields replaced). No database."""
import json
import re
from pathlib import Path

import pytest

from ingest import parse, stream
from ingest.stream import ShapeError

FX = Path(__file__).parent / "fixtures" / "egov"
PRIS, ARCH, CONS, STRAT, IMPACT = ("057c5c06-a11e-4e2d-8862-189c4d06e7b7", "e01f99a7-e1e0-4b5e-be8d-d5141a41e0f2",
                                   "b600c109-9d30-4ba0-8463-9ed88a89f745", "0a1d9485-a8fd-4e59-97f5-4063b5b72001",
                                   "043ba0e4-18e1-4880-8a1f-ea739103dca4")


def recs(res):
    return stream.records(FX / f"{res}.json")


def write(tmp_path, text, name="a.json"):
    p = tmp_path / name
    p.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
    return p


def test_acts_of_the_council_of_ministers_on_a_real_answer():
    p = parse.pris(recs(PRIS), "current")
    assert p.count == 35 and len(p.tables["pris_act"]) == 35      # the legend is not counted
    acts = {r["pris_id"]: r for r in p.tables["pris_act"]}
    # checked by hand against the register's export of 02.10.2026 (resource 057c5c06, record pris_id 171000)
    a = acts[171000]
    assert (a["doc_num"], a["accepted"].isoformat(), a["legal_act_type"], a["importer"]) == ("716", "2026-09-14", "Решения", "министър-председателят")
    assert a["about"].startswith("ЗА ИЗМЕНЕНИЕ НА РЕШЕНИЕ № 475 НА МИНИСТЕРСКИЯ СЪВЕТ")
    assert "<" not in a["about"] and "&lt;" not in a["about"] and a["about_raw"].startswith("&lt;p&gt;")   # plain text, original kept
    assert acts[170993]["legal_reason"].startswith("чл. 24, ал. 4 от Наредбата за условията")
    assert acts[170203]["public_consultation_number"] == "12326-K" and acts[170203]["gazette_number"] == 54 and acts[170203]["gazette_year"] == 2026
    assert acts[170625]["confidential"] and acts[170625]["about"] == "Поверителен акт"
    assert not acts[164236]["active"]
    assert len(p.tables["pris_institution"]) == 5 and len(p.tables["pris_related"]) == 32


def test_the_archive_register_has_two_digit_gazette_years_and_no_html():
    p = parse.pris(recs(ARCH), "archive")
    acts = {r["pris_id"]: r for r in p.tables["pris_act"]}
    a = acts[129997]
    assert (a["accepted"].isoformat(), a["gazette_number"], a["gazette_year_raw"], a["gazette_year"]) == ("1988-12-30", 4, "89", 1989)
    assert a["about"].startswith("ОБЯВЯВАНЕ НА С. КАТУНИЩЕ") and "Автор: МС" in a["about"]
    assert all(r["origin"] == "archive" for r in acts.values())


def test_gazette_year_is_the_one_near_the_date_of_the_act():
    import datetime as dt
    assert parse.gazette_year("89", dt.date(1988, 12, 30)) == 1989
    assert parse.gazette_year("05", dt.date(2005, 3, 1)) == 2005
    assert parse.gazette_year("2021", dt.date(2021, 1, 1)) == 2021
    assert parse.gazette_year("55", dt.date(2005, 3, 1)) is None      # not near: unknown, not guessed
    assert parse.gazette_year(None, dt.date(2005, 3, 1)) is None


def test_consultations_on_a_real_answer():
    p = parse.consultation(recs(CONS))
    cons = {r["reg_num"]: r for r in p.tables["consultation"]}
    assert p.count == 31 and len(cons) == 30 and p.notes["folded"] == 1       # 12367-K stands twice in the source
    c = cons["12692-K"]
    # checked against https://strategy.bg/bg/public-consultations/12692 on 02.10.2026: 15.09.2026 - 15.10.2026, active
    assert (c["date_open"].isoformat(), c["date_close"].isoformat(), c["act_type"], c["institution_name"]) == (
        "2026-09-15", "2026-10-15", "Акт на министър", "Министерство на здравеопазването")
    assert c["name"].startswith("Проект на Наредба за допълнение на Наредба № 3 от 25.01.2008")
    assert cons["12691-K"]["institution_name"] == "Общинска администрация - Девин" and cons["12691-K"]["consultation_type"] == "Общинско"
    assert any(r["short_term_reason"] for r in cons.values())
    # the files of the doubled number are both kept
    assert len([f for f in p.tables["consultation_file"] if f["reg_num"] == "12367-K"]) >= 2


def test_personal_data_never_reaches_the_rows():
    p = parse.consultation(recs(CONS))
    blob = json.dumps(p.tables, default=str, ensure_ascii=False)
    assert "@" not in blob and "Иван Иванов" not in blob and "Петър Петров" not in blob and "author_name" not in blob
    assert any(r["comment_count"] for r in p.tables["consultation"])           # counted, not stored


def test_strategic_documents_and_the_sentinel_date():
    p = parse.strategy(recs(STRAT))
    docs = p.tables["strategy_doc"]
    assert p.count == 21 and len(docs) == 21 and len({d["doc_key"] for d in docs}) == 21
    open_ended = [d for d in docs if d["date_expiring_raw"] == "9999-01-01"]
    assert open_ended and all(d["date_expiring"] is None for d in open_ended)        # 9999-01-01 is "no end", not a date
    assert any(d["date_accepted_raw"] == "9999-01-01" and d["date_accepted"] is None for d in docs)
    assert len(p.tables["strategy_sub"]) == 3


def test_impact_contracts_keep_no_natural_person():
    p = parse.impact(recs(IMPACT))
    rows = p.tables["impact_contract"]
    legal = [r for r in rows if r["executor_kind"] == "юридическо лице"]
    assert any(r["eik"] == "121026679" and r["executor"] == '"Агенция Стратегма" ООД' and str(r["price_bgn"]) == "31680.00" for r in legal)
    natural = [r for r in rows if r["executor_kind"] == "физическо лице"]
    assert natural and all(r["executor"] is None and r["eik"] is None for r in natural)
    assert p.notes["dropped_ids"] == 1                      # a 10-digit "ЕИК" can be an ЕГН: not kept at all
    assert all(r["eik"] is None or len(r["eik"]) in (9, 13) for r in rows)


def test_eik_check_digit():
    assert parse.eik_valid("121026679") and parse.eik_valid("175382262")
    assert not parse.eik_valid("121026670") and not parse.eik_valid("1234567890") and not parse.eik_valid(None)


def test_text_is_unescaped_once_and_twice():
    from ingest.text import plain
    assert plain("&lt;p&gt;A&amp;nbsp;B&lt;/p&gt;") == "A B"
    assert plain("&lt;p&gt;ЗА &quot;СЕЛМА&quot;&lt;/p&gt;&lt;p&gt;Втори&lt;/p&gt;") == 'ЗА "СЕЛМА"\nВтори'
    assert plain("ОБЯВЯВАНЕ  НА  &quot;X&quot;") == 'ОБЯВЯВАНЕ НА "X"'
    assert plain(None) is None and plain("&lt;p&gt;&lt;/p&gt;") is None


# ---- shape checks: nothing is written when the answer is not the one we know
def test_the_legend_is_not_data(tmp_path):
    body = json.dumps({"success": True, "data": [{"pris_id": 1, "doc_num": "1"}]})
    with pytest.raises(ShapeError, match="легендата"):
        parse.pris(stream.records(write(tmp_path, body)), "current")


def test_an_unknown_missing_or_mistyped_field_stops_the_import(tmp_path):
    rows = list(recs(PRIS))
    with pytest.raises(ShapeError, match="непознати"):
        parse.pris(iter([rows[0], {**rows[1], "novo_pole": 1}]), "current")
    bad = dict(rows[1]); del bad["importer"]
    with pytest.raises(ShapeError, match="липсващи"):
        parse.pris(iter([rows[0], bad]), "current")
    with pytest.raises(ShapeError, match="pris_id е str"):
        parse.pris(iter([rows[0], {**rows[1], "pris_id": "171000"}]), "current")
    with pytest.raises(ShapeError, match="дата"):
        parse.pris(iter([rows[0], {**rows[1], "doc_accepted_date": "14.09.2026"}]), "current")


def test_a_repeated_key_stops_the_import():
    rows = list(recs(PRIS))
    with pytest.raises(ShapeError, match="се повтаря"):
        parse.pris(iter([rows[0], rows[1], rows[1]]), "current")
    c = list(recs(CONS))
    twice = {**c[1], "name": "Друго"}
    with pytest.raises(ShapeError, match="различни данни"):
        parse.consultation(iter([c[0], c[1], twice]))


def test_a_consultation_that_closes_before_it_opens_stops_the_import():
    c = list(recs(CONS))
    with pytest.raises(ShapeError, match="приключва преди"):
        parse.consultation(iter([c[0], {**c[1], "date_close": "2020-01-01", "date_open": "2021-01-01"}]))


@pytest.mark.parametrize("text,why", [
    ("", "празен"), ("   \n", "празен"), ("<html><body>Грешка 502</body></html>", "не е очакваният"),
    ('{"success":false,"data":[]}', "success:false"), ('{"success":true,"data":[{"a":1},{"a":2', "отрязан"),
    ('{"success":true,"data":[{"a":1},', "отрязан"), ('{"success":true,"data":[{"a":1}]', "отрязан"),
    ('{"success":true,"data":[{"a":1}]} {"x":1}', "непознато"), ('{"success":true,"data":[1,2]}', "не е обект"),
    ('{"success":true,"data":{"a":1}}', "не е очакваният"),
])
def test_bad_answers_are_refused(tmp_path, text, why):
    with pytest.raises(ShapeError, match=why):
        list(stream.records(write(tmp_path, text)))


def test_bom_and_whitespace_are_accepted(tmp_path):
    p = write(tmp_path, b'\xef\xbb\xbf {"success": true, "data": [ {"a": 1} , {"a": 2} ] }\n')
    assert list(stream.records(p)) == [{"a": 1}, {"a": 2}]


def test_a_big_file_is_read_in_chunks_with_a_small_memory(tmp_path):
    """A generated file of about 60 MB with records that straddle the 4 MB chunks: all read, the peak memory stays small."""
    resource = pytest.importorskip("resource")      # Linux (CI and the server)
    one = json.dumps({"reg_num": "1-K", "text": "ж" * 3000}, ensure_ascii=True)
    n = 12_000
    p = tmp_path / "big.json"
    with open(p, "w") as f:
        f.write('{"success":true,"data":[')
        f.write(",".join(one.replace("1-K", f"{i}-K") for i in range(n)))
        f.write("]}")
    assert p.stat().st_size > 60_000_000
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss if hasattr(resource, "getrusage") else 0
    last = None
    count = 0
    for r in stream.records(p):
        count += 1
        last = r
    assert count == n and last["reg_num"] == f"{n - 1}-K"
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    assert after - before < 150_000          # KB on Linux: a 60 MB file did not grow the process by more than 150 MB


def test_chunk_boundaries_do_not_change_the_records():
    small = "210747d1-9840-4345-939a-2338fda61031"
    for res, chunk in ((small, 1), (small, 7), (small, 1000), (CONS, 65536)):
        assert list(stream.records(FX / f"{res}.json", chunk=chunk)) == list(recs(res))


def test_every_fixture_is_the_shape_the_parsers_know():
    for res, fn in ((PRIS, lambda it: parse.pris(it, "current")), (ARCH, lambda it: parse.pris(it, "archive")), (CONS, parse.consultation),
                    (STRAT, parse.strategy), (IMPACT, parse.impact), ("e935536f-db99-45a2-a024-d55631d873e1", parse.standard),
                    ("210747d1-9840-4345-939a-2338fda61031", parse.by_institution), ("403043ed-7b82-477d-bb0f-ec7e0db996e9", parse.by_area)):
        assert fn(recs(res)) is not None
    assert parse.count_only(recs("31a54d26-3d66-425f-a242-e70bcee4b387"), "title", "Заглавие", "x") == 5
    with pytest.raises(ShapeError):
        parse.count_only(recs(CONS), "title", "Заглавие", "x")


def test_the_silver_tables_of_the_migration_are_the_columns_of_the_parsers():
    from ingest.schema import TABLES
    sql = (Path(__file__).parent.parent / "db" / "migrations" / "0001_init.sql").read_text(encoding="utf-8")
    for table, (key, data) in TABLES.items():
        body = re.search(rf"CREATE TABLE silver\.{table} \((.*?)\n\);", sql, re.S).group(1)
        cols = set(re.findall(r"\b(\w+) (?:bigserial|integer|smallint|text|date|boolean|numeric|timestamptz)\b", body))
        assert set(key + data) <= cols, (table, set(key + data) - cols)
        # every column of the table except the versioning ones is either a key or a data column
        assert cols - {"id", "valid_from", "valid_to", "row_sha", "raw_sha256"} == set(key + data), table
