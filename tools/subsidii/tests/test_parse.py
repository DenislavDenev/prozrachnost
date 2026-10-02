"""The reader of the report's CSV: shape, encoding, the sums of a file. No database."""
import pytest

from ingest import names, parse
from tests.helpers import csv_of, derive, fixture, rows_of


def lines(raw):
    return list(parse.read_lines(raw))


def test_the_real_files_have_the_blocks_and_the_totals_of_the_report():
    # FY2024 and FY2025 are cut from the real files of 28.09.2026 (tools/cut_fixtures.py): whole blocks, cells untouched
    p = parse.parse(fixture(2024))
    assert p.encoding == "cp1251"
    assert (p.stats["rows"], p.stats["holders"], p.stats["payments"]) == (346, 42, 304)
    first = p.lines[0]
    # checked by eye against the line in the archived file dfz/2024/2026-09-28.091727f259d1.csv (the recipient's ОБЩО row)
    assert first.total and first.f[parse.NAME] == "БГ Агро Земеделска Компания ЕООД" and first.f[parse.OBSHTINA] == "Вълчи дол"
    assert [first.f[i] for i in parse.TOTAL_AMOUNTS] == ["2604264.9", "736564.95", "469620.47", "1206185.42", "3810450.32"]
    pay = p.lines[1]
    assert not pay.total and pay.f[parse.MEASURE] == "Държавна помощ отстъпка от акциза върху газьола 2023"
    assert (pay.f[parse.START], pay.f[parse.END], pay.f[parse.NB]) == ("30.10.2023", "30.10.2023", "225692.27")
    assert pay.owner == ("БГ Агро Земеделска Компания ЕООД", "-", "Варна", "Вълчи дол") and pay.block == 1
    eco = next(l for l in p.lines if l.f[parse.CODE] == "I.4" and l.f[parse.MEASURE].startswith("I.В.3") and l.block == 1)
    assert (eco.f[parse.OBJECTIVE], eco.f[parse.EFGZ]) == ("SO4,SO5,SO6,SO9", "780949.23")
    q = parse.parse(fixture(2025))
    assert (q.stats["rows"], q.stats["holders"], q.stats["payments"]) == (355, 45, 310)


def test_the_sums_of_a_file_agree_with_themselves_except_where_the_source_does_not():
    p = parse.parse(fixture(2024))
    s = p.stats
    assert s["identity_fail"] == 0
    t = s["total"]
    assert t["efgz"] + t["ezfrs_nb"] == t["total"] and t["ezfrs"] + t["nb"] == t["ezfrs_nb"]
    # the source lists some payments twice and counts them once in the ОБЩО row: three such recipients in the cut
    assert [(d[1], str(d[2]), str(d[3])) for d in s["diffs"]] == [("efgz", "399911.64", "465806.69"), ("efgz", "30245.35", "50750.46"), ("efgz", "2777.24", "3166.94")]
    assert parse.parse(fixture(2025)).stats["diffs"][0][1:] == ("efgz", pytest.approx(parse.amount("33150.92")), pytest.approx(parse.amount("37062.52")))


def test_the_amounts_without_a_leading_zero_and_the_empty_cell():
    assert str(parse.amount(".92")) == "0.92" and parse.amount("-") is None and str(parse.amount("1955.8")) == "1955.8"
    raw = fixture(2024)
    assert b'".' in raw     # the real file has amounts like ".92"
    assert parse.parse(raw).stats["rows"] == 346


def test_the_date_is_the_financial_year_not_the_calendar_year():
    p = parse.parse(fixture(2024)).stats
    assert str(p["first_start"]) >= "2023-10-16" and str(p["last_end"]) <= "2024-10-15"
    assert parse.date("15.10.2024").isoformat() == "2024-10-15"
    from ingest import archive
    assert tuple(map(str, archive.fiscal_year_bounds(2025))) == ("2024-10-16", "2025-10-15")


def test_the_report_says_utf8_but_is_cp1251_and_a_real_utf8_file_is_read_too():
    raw = fixture(2024)
    text = raw.decode("cp1251")
    assert parse.decode(raw)[1] == "cp1251"
    assert parse.decode(text.encode("utf-8"))[1] == "utf-8"
    assert parse.decode(b"\xef\xbb\xbf" + text.encode("utf-8"))[1] == "utf-8-sig"
    got = [l.f[parse.NAME] for l in parse.read_lines(text.encode("utf-8")) if l.total][:2]
    assert got == ["БГ Агро Земеделска Компания ЕООД", "АГРОСЕРВИЗ КОМТРАК - ТРЪСТЕНИК EООД"]
    assert [l.f[parse.NAME] for l in parse.read_lines(raw) if l.total][:2] == got      # the names are the same in both


def test_a_changed_header_or_another_page_stops_the_import():
    rows = rows_of(fixture(2024))
    rows[0][10], rows[0][12] = rows[0][12], rows[0][10]
    with pytest.raises(parse.ShapeError, match="заглавният ред"):
        lines(csv_of(rows))
    with pytest.raises(parse.ShapeError, match="страница"):
        lines(b"<!DOCTYPE html><html><body>Session expired</body></html>\n")
    with pytest.raises(parse.ShapeError, match="празен"):
        lines(b"")
    one = csv_of(rows_of(fixture(2024))[:1])
    with pytest.raises(parse.ShapeError, match="нито един ред"):
        lines(one)


def test_a_truncated_file_is_not_a_file():
    raw = fixture(2024)
    with pytest.raises(parse.ShapeError, match="отрязан"):
        lines(raw[:-7])
    with pytest.raises(parse.ShapeError):      # cut inside a row at a line feed would still miss cells only if the row is short
        rows = rows_of(raw)
        rows[5] = rows[5][:10]
        lines(csv_of(rows))


@pytest.mark.parametrize("cell,msg", [("1,5", "не е сума"), ("12.345", "не е сума"), ("1 955.8", "не е сума"), ("abc", "не е сума")])
def test_an_amount_with_a_decimal_comma_or_a_space_is_a_shape_error_not_a_number(cell, msg):
    rows = rows_of(fixture(2024))
    i = next(i for i, r in enumerate(rows) if i and r[parse.MEASURE] != parse.TOTAL_WORD and r[parse.NB] != "-")
    rows[i][parse.NB] = cell
    with pytest.raises(parse.ShapeError, match=msg):
        lines(csv_of(rows))


def test_an_impossible_date_is_a_shape_error():
    rows = rows_of(fixture(2024))
    i = next(i for i, r in enumerate(rows) if i and r[parse.START] != "-")
    rows[i][parse.START] = "31.02.2024"
    with pytest.raises(parse.ShapeError, match="не е дата"):
        lines(csv_of(rows))
    rows[i][parse.START] = "2024-02-03"
    with pytest.raises(parse.ShapeError, match="не е дата"):
        lines(csv_of(rows))


def test_the_structure_of_the_blocks_is_checked():
    rows = rows_of(fixture(2024))
    # a payment before the first ОБЩО row
    with pytest.raises(parse.ShapeError, match="преди първия"):
        lines(csv_of([rows[0], rows[2], *rows[1:]]))
    # a payment carrying the totals of a recipient
    bad = [list(r) for r in rows]
    bad[2][parse.TOTAL_T] = "5.00"
    with pytest.raises(parse.ShapeError, match="обща сума"):
        lines(csv_of(bad))
    # a ОБЩО row carrying a payment amount
    bad = [list(r) for r in rows]
    bad[1][parse.EFGZ] = "5.00"
    with pytest.raises(parse.ShapeError, match="ОБЩО"):
        lines(csv_of(bad))
    # a payment in another municipality than its block
    bad = [list(r) for r in rows]
    bad[2][parse.OBSHTINA] = "Друга"
    with pytest.raises(parse.ShapeError, match="друга област или община"):
        lines(csv_of(bad))
    # a payment row without any amount
    bad = [list(r) for r in rows]
    for i in parse.PAYMENT_AMOUNTS:
        bad[2][i] = "-"
    with pytest.raises(parse.ShapeError, match="без сума"):
        lines(csv_of(bad))


def test_a_recipient_whose_funds_do_not_add_up_is_counted():
    rows = rows_of(fixture(2024))
    rows[1][parse.TOTAL_T] = "3810450.33"      # one cent more than ЕФГЗ + ЕЗФРС и НБ
    assert parse.parse(csv_of(rows)).stats["identity_fail"] == 1


def test_a_blank_total_is_zero_in_the_identity_but_a_missing_amount_stays_missing():
    p = parse.parse(fixture(2024))
    nb_only = next(l for l in p.lines if l.total and l.f[parse.EFGZ_T] == "-" and l.f[parse.EZFRS_T] == "-")
    assert parse.amount(nb_only.f[parse.EFGZ_T]) is None and p.stats["identity_fail"] == 0


def test_the_count_of_the_archive_is_the_newlines_minus_one():
    raw = fixture(2025)
    assert parse.newline_rows(raw) == parse.parse(raw).stats["rows"] == 355


def test_no_real_person_is_in_the_fixtures():
    for fy in (2024, 2025):
        for l in parse.read_lines(fixture(fy)):
            kind = names.kind_of(l.f[parse.NAME], l.f[parse.SURNAME])
            if kind == "natural":
                assert l.f[parse.NAME].startswith("физическо лице №") and l.f[parse.SURNAME] == "ФЛ"
            if kind == "sole_trader":
                assert l.f[parse.NAME].startswith("ЕТ Образец №")


# ---------- who is a person, who is a firm, where is the municipality ----------

@pytest.mark.parametrize("name,surname,kind", [
    ("Никола", "Косев", "natural"),
    ("БГ Агро Земеделска Компания ЕООД", "-", "legal"),
    ("ЕТ Красимир Георгиев 2004", "-", "sole_trader"),
    ("ЕТВАСИЛЕНАЗДРАВКО БУЗОЛОВ", "-", "sole_trader"),
    ("ET   Камен Шишков", "-", "sole_trader"),             # the Latin letters
    ("ЕТВИН ЕООД", "-", "legal"),                          # a company that happens to begin with ЕТ
    ("ЕТРОПАРК ЕООД", "-", "legal"),
    ("ЕТКА - 95  ЕООД", "-", "legal"),
    ("Община Белоградчик", "-", "legal"),
    ("Магура АД", "-", "legal"),
    ("ЧПТК ПРОГРЕС", "-", "legal"),
])
def test_the_kind_of_a_recipient(name, surname, kind):
    assert names.kind_of(name, surname) == kind


def test_the_search_key_ignores_case_quotes_spaces_and_latin_look_alikes():
    a = names.norm('АГРОФАКТОР EООД')            # the E is Latin in the source
    b = names.norm('„Агрофактор“  ЕООД ')
    assert a == b == "АГРОФАКТОР ЕООД"
    assert names.org_id(a) == names.org_id(b) and len(names.org_id(a)) == 12
    assert names.norm("Ф.И.П. - 2000") == "Ф И П 2000"


def test_the_municipality_is_found_by_name_together_with_the_oblast():
    places = names.load_places()
    ruse, varna = names.match_place("Русе", "Бяла", places), names.match_place("Варна", "Бяла", places)
    assert ruse[0] != varna[0] and ruse[1] == varna[1] == "name"
    assert names.match_place("София (област)", "Пирдоп", places)[0] == names.match_place("София област", "Пирдоп", places)[0]
    assert names.match_place("Варна", "Вълчи дол", places)[0] == names.match_place("Варна", "Вълчи Дол", places)[0]
    alias = names.match_place("Добрич", "Добрич-селска", places)
    assert alias[1] == "alias" and alias[0] == names.match_place("Добрич", "Добричка", places)[0] and "Добричка" in alias[2]
    assert names.match_place("Русе", "Варна", places) is None             # the name of a municipality of another oblast
    assert names.match_place("Видин", "Няма такава", places) is None


def test_every_municipality_of_the_fixtures_is_found():
    places = names.load_places()
    for fy in (2024, 2025):
        for l in parse.read_lines(fixture(fy)):
            if l.total:
                assert names.match_place(l.f[parse.OBLAST], l.f[parse.OBSHTINA], places), (l.f[parse.OBLAST], l.f[parse.OBSHTINA])


def test_a_derived_file_keeps_the_shape():
    raw = derive(2024, drop={0, 1})
    assert parse.parse(raw).stats["holders"] == 40
    changed = derive(2024, edit=lambda i, r: r.__setitem__(parse.NB, "1.00") if i == 0 and r[parse.NB] != "-" else None)
    assert changed != fixture(2024) and parse.parse(changed).stats["rows"] == 346
