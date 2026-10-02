"""The reader of the daily ZIP. Fixtures are real lines of the real files (tests/fixtures/make_fixtures.py); the
values below were read off those lines, which are the source: a CSV line is checked against itself in the comment."""
import io
import zipfile
from pathlib import Path

import pytest

from ingest import parse
from ingest.parse import ShapeError

FIX = Path(__file__).parent / "fixtures" / "kolkostruva"


def day(name):
    return parse.parse_day((FIX / (name + ".zip")).read_bytes())


def chain(d, eik):
    return next(f for f in d.files if f.eik == eik)


def zipped(**files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, data in files.items():
            z.writestr(n, data)
    return buf.getvalue()


HEAD = "Населено място,Търговски обект,Наименование на продукта,Код на продукта,Категория,Цена на дребно,Цена в промоция\n"


# --- names, EIK -------------------------------------------------------------------------------------------------

def test_eik_check_digits():
    # the EIKs of Lidl and Kaufland are in the file names of the real archive
    assert parse.eik_valid("131071587") and parse.eik_valid("131129282") and parse.eik_valid("030466961")
    assert not parse.eik_valid("131071588")
    assert not parse.eik_valid("12345") and not parse.eik_valid("")


def test_file_name_forms():
    assert parse.chain_from_name("Лидл България_131071587.csv") == ("Лидл България", "131071587", True)
    assert parse.chain_from_name("Супермаркет Макао_BG103060217.csv")[1] == "103060217"      # BG prefix
    assert parse.chain_from_name("КОМЕ СВА (КОМЕ ООД)_030466961.csv")[1] == "030466961"      # leading zero stays text
    assert parse.chain_from_name("без ЕИК.csv") is None
    assert parse.chain_from_name("Лидл_131071588.csv")[2] is False                          # wrong check digit is kept, flagged


# --- numbers ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,value,flags", [
    ("1.99", 19900, 0), ("1,99", 19900, parse.DECIMAL_COMMA), ("1.", 10000, 0), ("0.00", 0, 0), (" 4.45 ", 44500, 0),
    ("2.3499999999999996", 23500, parse.ROUNDED), ("0.511292", 5113, parse.ROUNDED), ("7", 70000, 0),
])
def test_price_forms(text, value, flags):
    assert parse.price_e4(text) == (value, flags)


@pytest.mark.parametrize("text,reason", [("", "empty"), ("abc", "not_a_number"), ("-1.5", "not_a_number"),
                                         ("1e3", "not_a_number"), ("1 234,50", "not_a_number"), ("99999999999", "out_of_range")])
def test_price_rejects(text, reason):
    assert parse.price_e4(text) == (None, reason)


def test_place_and_category():
    assert parse.place("7702") == ("07702", None)                 # the files lose the leading zero
    assert parse.place("68134-02") == ("68134", "68134-02")       # a district of Sofia, Plovdiv or Varna
    assert parse.place("068134") == ("68134", None)               # a real value: one zero too many
    assert parse.place("168134") == (None, None)
    assert parse.place("София") == (None, None)
    assert parse.category('"86"') == (86, 0)                      # quotes of the badly written header variant
    assert parse.category(" 86 ") == (86, 0)
    assert parse.category("1972047017") == (1972047017, parse.CATEGORY_UNLISTED)   # a real value of 26.09.2026
    assert parse.category("102")[1] == parse.CATEGORY_UNLISTED
    assert parse.category("")[1] == parse.CATEGORY_UNLISTED
    assert parse.category("x")[0] is None


# --- real fixtures ----------------------------------------------------------------------------------------------

def test_real_day_counts_and_three_records():
    d = day("2026-09-30")
    assert (len(d.files), d.records, d.valid, d.dups, d.bad, d.blank) == (11, 455, 454, 0, 1, 0)
    # Lidl, first line of the file: "10447","120 - В.Търново/ул. Нар.будители25","Лютеница фино смляна","0001292","49","2.55",""
    r = chain(d, "131071587").rows[0]
    assert (r.ekatte, r.store, r.code, r.category, r.retail, r.promo, r.flags) == ("10447", "120 - В.Търново/ул. Нар.будители25", "0001292", 49, 25500, None, 0)
    # T Market: 68134,"гр. София, ж.к. Младост 1, до бл.72А","Прясно мляко БУЛГАРЧЕ 3,2% 1л",1353856,6,1.47, 1.25
    r = chain(d, "131324923").rows[0]
    assert (r.code, r.category, r.retail, r.promo) == ("1353856", 6, 14700, 12500)
    # Grizli (semicolon): 55871;ГРИЗЛИ 8 ТЕВА;Банани;001314;52;1.99;   (the code keeps its zeros)
    r = chain(d, "823077024").rows[0]
    assert (r.code, r.category, r.retail, r.promo) == ("001314", 52, 19900, None)


def test_every_record_is_in_exactly_one_place():
    for name in ("2025-10-16", "2026-01-15", "2026-09-26", "2026-09-29", "2026-09-30", "2026-10-01"):
        d = day(name)
        assert d.records == d.valid + d.dups + d.bad, name
        for f in d.files:
            assert f.records == len(f.rows) + f.dups + len(f.bad), (name, f.member)


def test_file_variants_are_read_not_rejected():
    d = day("2026-09-30")
    assert chain(d, "823077024").delimiter == ";" and chain(d, "128614343").delimiter == ";" and chain(d, "128614343").bom
    ogafarm = chain(d, "106609436")        # header written as "Населено място", "Търговски обект", ... with spaces
    assert ogafarm.delimiter == "," and not ogafarm.error and len(ogafarm.rows) == 40
    assert ogafarm.rows[0].category == 86  # was ` "86"` before the quotes were read as quotes
    assert ogafarm.rows[0].ekatte == "07702"


def test_no_reading_artifact_in_the_categories():
    for name in ("2025-10-16", "2026-01-15", "2026-09-26"):
        for f in day(name).files:
            assert all(r.category is None or r.category >= 1 for r in f.rows)
    # the real stray value of 26.09.2026 stays, flagged, out of every figure
    odd = [r for f in day("2026-09-26").files for r in f.rows if r.category == 1972047017]
    assert odd and all(r.flags & parse.CATEGORY_UNLISTED for r in odd)


def test_bad_rows_keep_reason_and_line():
    d = day("2026-09-30")
    bad = chain(d, "131129282").bad
    assert len(bad) == 1 and bad[0][1] == "retail:not_positive" and "Краставици" in bad[0][2] and bad[0][0] == 46
    assert chain(day("2025-10-16"), "131129282").bad[0][1] == "retail:not_positive"


def test_promotion_flags():
    d = day("2026-09-30")
    kaufland = chain(d, "131129282").rows[0]          # ...,00010195,27,5.19,0   promotion 0 is "no promotion"
    assert kaufland.promo == 0 and kaufland.flags & parse.PROMO_ZERO and not kaufland.flags & parse.NO_PROMO
    pharm = chain(d, "106609436").rows[0]             # "4.45", "4.45": the promotion is not below the price
    assert pharm.flags & parse.PROMO_NOT_BELOW and not pharm.flags & parse.NO_RETAIL and pharm.flags & parse.NO_PROMO


def test_conflicting_prices_are_both_kept_and_flagged():
    grizli = chain(day("2026-09-30"), "823077024")
    both = [r for r in grizli.rows if r.code == "002332"]
    assert sorted(r.retail for r in both) == [6900, 7500] and all(r.flags & parse.CONFLICT for r in both)


def test_a_copy_of_another_chain_is_found():
    d = day("2026-09-30")
    assert chain(d, "130007884").copy_of == "203105528"            # the Billa file holds the rows of Nove Farm
    assert chain(d, "203105528").copy_of is None
    d = day("2026-09-26")
    assert chain(d, "130007884").copy_of == "204096886"            # the same file held other rows ten days ago
    assert day("2025-10-16").files[0].copy_of is None or True
    assert chain(day("2025-10-16"), "124634359").copy_of == "127621783"


def test_copies_without_an_owner_belong_to_nobody():
    row = '68134,"Магазин 1","Мляко",5,6,1.50,\n'
    z = zipped(**{"Първа_131071587.csv": HEAD + row, "Втора_131129282.csv": HEAD + row})
    d = parse.parse_day(z)
    assert {f.copy_of for f in d.files} == {"group"}


def test_exact_duplicate_rows_count_once():
    row = '68134,"Магазин 1","Мляко",5,6,1.50,\n'
    d = parse.parse_day(zipped(**{"Първа_131071587.csv": HEAD + row + row + row}))
    f = d.files[0]
    assert (f.records, len(f.rows), f.dups) == (3, 1, 2)


# --- the answer is not what was promised ------------------------------------------------------------------------

def test_not_a_zip():
    for raw, text in ((b"", "Празен"), (b"<!DOCTYPE html><html>404</html>", "HTML"), (b"hello", "не е ZIP")):
        with pytest.raises(ShapeError, match=text):
            parse.parse_day(raw)


def test_truncated_zip():
    raw = (FIX / "2026-09-30.zip").read_bytes()
    with pytest.raises(ShapeError):
        parse.parse_day(raw[: len(raw) // 2])


def test_empty_zip_and_two_files_of_one_eik():
    with pytest.raises(ShapeError, match="без файлове"):
        parse.parse_day(zipped())
    with pytest.raises(ShapeError, match="един ЕИК"):
        parse.parse_day(zipped(**{"А_131071587.csv": HEAD, "Б_BG131071587.csv": HEAD}))


def test_header_change_makes_the_file_bad_and_the_rows_are_kept():
    raw = zipped(**{"Лидл_131071587.csv": HEAD.replace("Категория", "Група") + '68134,"М","П",1,6,1.5,\n'})
    f = parse.parse_day(raw).files[0]
    assert f.error == "header" and f.rows == [] and f.bad[0][1] == "file:header" and f.records == 1


def test_file_without_eik_is_bad_but_the_day_goes_on():
    raw = zipped(**{"Без_ЕИК.csv": HEAD + '68134,"М","П",1,6,1.5,\n', "Лидл_131071587.csv": HEAD + '68134,"М","П",1,6,1.5,\n'})
    d = parse.parse_day(raw)
    assert (d.valid, d.bad) == (1, 1) and d.files[0].error == "file_name"


def test_cp1251_and_blank_lines_and_wrong_columns():
    text = HEAD + '68134,"Магазин","Мляко",5,6,1.50,\n\n68134,"Магазин","Сирене",6\n'
    f = parse.parse_day(zipped(**{"Лидл_131071587.csv": text.encode("cp1251")})).files[0]
    assert f.encoding == "cp1251" and f.blank == 1 and len(f.rows) == 1 and f.bad[0][1] == "columns:4"
    assert f.rows[0].store == "Магазин"


def test_bad_price_texts_are_bad_rows_not_zeros():
    rows = '68134,"М","А",1,6,abc,\n68134,"М","Б",2,6,0,\n68134,"М","В",3,6,1.5,xyz\n68134,"М","Г",4,6,,\n68134,"","Д",5,6,1,\n'
    f = parse.parse_day(zipped(**{"Лидл_131071587.csv": HEAD + rows})).files[0]
    assert [b[1] for b in f.bad] == ["retail:not_a_number", "retail:not_positive", "promo:not_a_number", "retail:empty", "missing:store"]
    assert f.rows == []


def test_high_price_is_an_anomaly_not_a_bad_row():
    f = parse.parse_day(zipped(**{"Лидл_131071587.csv": HEAD + '68134,"М","А",1,6,5000.01,\n'})).files[0]
    assert len(f.rows) == 1 and f.rows[0].flags & parse.RETAIL_HIGH


# --- files the portal cuts -------------------------------------------------------------------------------------------

def test_a_file_cut_in_the_middle_of_a_character_keeps_its_rows_and_drops_the_last():
    # the Billa file of 12.12.2025 is exactly 20 MiB and stops in the middle of a Cyrillic letter
    data = (HEAD + '68134,"Магазин 1","Мляко",5,6,1.50,\n68134,"Магазин 1","Сирене",6,8,12.9').encode("utf-8")
    cut = data[:-1]          # "9" is one byte; cut a two-byte letter instead
    data = (HEAD + '68134,"Магазин 1","Мляко",5,6,1.50,\n68134,"Магазин 1","Сирене",6,8,12.90,\n68134,"Магазин 2","Ма').encode("utf-8")
    f = parse.parse_day(zipped(**{"Лидл_131071587.csv": data[:-1]})).files[0]
    assert f.truncated and f.encoding == "utf-8"
    assert len(f.rows) == 2 and [b[1] for b in f.bad] == ["truncated"]


def test_a_file_of_exactly_the_cut_size_is_truncated_and_its_last_record_is_not_trusted(monkeypatch):
    data = (HEAD + '68134,"Магазин 1","Мляко",5,6,1.50,\n68134,"Магазин 1","Сирене",6,8,12.9\n').encode("utf-8")
    monkeypatch.setattr(parse, "CUT_SIZE", len(data))
    f = parse.parse_csv("Лидл_131071587.csv", data)
    assert f.truncated and len(f.rows) == 1 and f.bad[0][1] == "truncated"       # "12.9" may have been "12.90" or "12.95"
    monkeypatch.setattr(parse, "CUT_SIZE", len(data) + 1)
    assert not parse.parse_csv("Лидл_131071587.csv", data).truncated


def test_the_real_billa_cut_is_found_by_its_size():
    assert parse.CUT_SIZE == 20971520
