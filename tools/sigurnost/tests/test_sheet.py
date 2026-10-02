"""The parser of the Ministry's sheet-like tables, on real answers from the archive (tests/fixtures/egov, see make_fixtures.py)."""
import json
from decimal import Decimal

import pytest

from ingest import sheet, templates
from tests import helpers as h


def parse(set_uri, res):
    return sheet.parse(h.fixture(set_uri, res))


def row(block, text):
    return next(r for r in block.rows if r.text.startswith(text))


def values(r):
    return [c[2] for c in r.cells]


def test_by_crime_types_has_exact_rows_and_three_checked_values():
    """Resource ef7a8f29 ("Полицейска статистика 2024 г.", by crime types). Row 4.7 was checked on the portal's printout of the
    resource: 2635, 2066, 38.1, 1381, 52.41, 993, 71.9044170890659, 48.063891577928366 (the plan quotes the same line)."""
    sh = parse(h.POLICE, h.A)
    b = sh.blocks[0]
    assert len(sh.blocks) == 1 and len(b.rows) == 80 and sh.n_cols == 10
    assert b.dimension == "Видове престъпления"
    assert values(row(b, "Престъпления, извършени в условията на домашно насилие")) == [
        Decimal(x) for x in ("2635", "2066", "38.1", "1381", "52.41", "993", "71.9044170890659", "48.063891577928366")]
    total = next(r for r in b.rows if r.total)
    assert values(total)[:2] == [Decimal(74709), Decimal(60513)] and total.level == 0
    assert values(row(b, "Убийство (чл.115-127 НК)"))[:5] == [Decimal(237), Decimal(228), Decimal("3.43"), Decimal(91), Decimal("38.4")]


def test_by_structures_has_the_oblasts_and_the_general_directorates():
    """Resource 0e35ccef, row 4 (Варна): 6535 registered, 5720 with an unknown offender, 1390.06 per 100 thousand, 2574 solved."""
    b = parse(h.POLICE, h.B).blocks[0]
    assert len(b.rows) == 32 and b.dimension == "Области"
    v = row(b, "Варна")
    assert v.code == "4." and values(v)[:5] == [Decimal(6535), Decimal(5720), Decimal("1390.06"), Decimal(2574), Decimal("39.39")]
    assert [r.text for r in b.rows[-3:]] == ['ГД "Национална полиция"', 'ГД "Гранична полиция"', 'ГД "Борба с организираната престъпност"']


def test_merged_header_rows_are_joined_and_words_cut_by_a_hyphen_are_rejoined():
    b = parse(h.POLICE, h.B).blocks[0]
    labels = [lab for _, lab in b.columns]
    assert labels[0] == "Общ брой регистрирани престъпления"         # "Общ брой" / "регист-" / "рирани" / "престъп-" / "ления"
    assert labels[4] == "% на разкриваемост спрямо общия брой регистрирани престъпления"
    assert [c for c, _ in b.columns] == list(range(2, 10))


def test_numbering_gives_the_levels_and_the_markers_the_parts():
    """"1." is a point, "1.1." a sub-point, "·" and "-" are "в това число" parts (resource ef7a8f29)."""
    b = parse(h.POLICE, h.A).blocks[0]
    def lv(text):
        r = row(b, text)
        return r.code, r.level, r.marker
    assert lv("Престъпления против личността") == ("1.", 1, "")
    assert lv("Убийство (чл.115-127 НК)") == ("1.1.", 2, "")
    assert lv("Умишлено убийство (чл.115-116,118 НК) - довършено") == ("", 3, "·")
    assert lv("по поръчение на ОПГ") == ("", 4, "-")
    assert row(b, "Престъпления против брака").code == ""            # the source prints no number here


def test_the_main_table_has_its_label_in_the_first_column_and_a_footnote():
    """Resource 7d075b6c: the total row is "ОБЩ БРОЙ" in the first column; the footnote is kept as a note, not a row."""
    sh = parse(h.POLICE, h.C)
    b = sh.blocks[0]
    assert len(b.rows) == 143 and b.rows[0].total and b.rows[0].text == "ОБЩ БРОЙ"
    assert values(b.rows[0]) == [Decimal(x) for x in ("86049", "688", "1244.18", "40285", "46.82", "35178", "5553", "2813", "2596")]
    assert len(sh.notes) == 1 and sh.notes[0].startswith("*В рамките на отчетния период лицата се броят по един път")


def test_structure_after_structure_gives_one_block_each_and_the_stray_letter_is_an_issue_not_a_zero():
    """Resource 9d2cc270 prints a header and 76 rows for each structure. In Монтана, row "5. Престъпления, извършени в условията
    на домашно насилие", the registered crimes read "с" (a Cyrillic letter): no value, the text is kept, one issue."""
    sh = parse(h.POLICE, h.D)
    assert len(sh.blocks) == 29 and sum(len(b.rows) for b in sh.blocks) == 2218
    assert [b.structure for b in sh.blocks][:3] == ["Благоевград", "Бургас", "Варна"]
    assert sh.blocks[-1].structure.startswith("Общо за Р")
    assert sh.issues == 1
    bad = [(b.structure, r.text, c) for b in sh.blocks for r in b.rows for c in r.cells if c[3]]
    assert bad == [("Монтана", "Престъпления, извършени в условията на домашно насилие", (2, "с", None, True))]
    blag = sh.blocks[0]
    assert values(blag.rows[0])[:5] == [Decimal(2632), Decimal("874.02"), Decimal(1422), Decimal("54.03"), Decimal(1288)]


def test_a_dash_is_no_value_and_no_issue_zero_is_a_value():
    """The old flat table (resource fb97f50f of the 2014-2015 set) prints a dash where the share is undefined."""
    sh = parse(h.OLD, "fb97f50f")
    assert sh.issues == 0
    dashes = [c for r in sh.blocks[0].rows for c in r.cells if c[1] == "-"]
    assert dashes and all(c[2] is None and not c[3] for c in dashes)
    zeros = [c for r in parse(h.POLICE, h.B).blocks[0].rows for c in r.cells if c[1] == "0"]
    assert zeros and all(c[2] == Decimal(0) for c in zeros)


def test_decimal_comma_in_the_number_of_an_old_table_still_gives_the_level():
    """The ensemble 2014-2015 prints "1,1," for "1.1."."""
    sh = parse(h.OLD, "ac9e3dae")
    r = next(r for r in sh.blocks[0].rows if r.code.startswith("1,1"))
    assert r.level == 2


def test_a_later_block_of_a_flat_table_has_only_the_structures_name_above_it():
    sh = parse(h.OLD, "ac9e3dae")
    assert [b.structure for b in sh.blocks] == ["Благоевград", "Бургас", "Варна"]
    assert sh.blocks[1].columns == sh.blocks[0].columns


def test_a_resource_without_a_table_is_empty_not_an_error():
    assert sheet.parse(h.fixture(h.OLD, "02165265")).kind == "empty"          # {"success":true}
    sh = sheet.parse(h.fixture(h.OLD, "0e0ee6b9"))                            # one column of separators
    assert sh.kind == "empty" and sh.note == "само разделители"


def test_html_instead_of_data_truncated_json_and_a_wrong_shape_are_shape_errors():
    raw = h.fixture(h.POLICE, h.B)
    for bad in (b"<!DOCTYPE html><html><body>503</body></html>", raw[:2000], b"", b'{"success": false, "data": []}',
                b'{"data": [["a"]]}', b'[["a", "b", "c"]]', json.dumps({"success": True, "data": [["Общ", 1, 2]]}).encode()):
        with pytest.raises(sheet.ShapeError):
            sheet.parse(bad)


def test_ragged_rows_and_a_table_without_a_header_are_shape_errors():
    d = json.loads(h.fixture(h.POLICE, h.B))
    d["data"][12] = d["data"][12][:5]
    with pytest.raises(sheet.ShapeError, match="различна дължина"):
        sheet.parse(json.dumps(d).encode())
    d = json.loads(h.fixture(h.POLICE, h.B))
    d["data"] = d["data"][9:]                     # starts with data, no header above
    with pytest.raises(sheet.ShapeError, match="без заглавие"):
        sheet.parse(json.dumps(d).encode())


def test_a_bom_is_accepted():
    raw = h.fixture(h.POLICE, h.B)
    assert len(sheet.parse(b"\xef\xbb\xbf" + raw).blocks[0].rows) == 32


def test_texts_in_many_cells_are_not_a_table_of_numbers():
    d = json.loads(h.fixture(h.POLICE, h.B))
    for r in d["data"][9:]:
        for i in range(3, 8):
            r[i] = "n/a"
    with pytest.raises(sheet.ShapeError, match="не е таблица с числа"):
        sheet.parse(json.dumps(d).encode())


def test_a_row_without_a_label_is_kept_and_marked():
    """Resource ebac8ebb of the old set has a data row whose label was lost; the numbers are kept."""
    d = json.loads(h.fixture(h.POLICE, h.B))
    d["data"][12][1] = ""
    sh = sheet.parse(json.dumps(d).encode())
    assert sh.blocks[0].rows[4].text == "(без етикет)"


def test_the_kind_is_found_from_the_header_not_from_the_name():
    """All 25 resources of the set carry one name; here the fixtures are told apart by their header lines alone."""
    fam = {u: templates.family_of(parse(h.POLICE, u)) for u in (h.A, h.B, h.C, h.D, h.E, h.OTHER, h.NOLABELS)}
    assert fam == {h.A: "types", h.B: "structures", h.C: "main", h.D: "types_by_structure", h.E: "econ_by_structure",
                   h.OTHER: None, h.NOLABELS: None}


def test_a_table_of_a_known_title_with_other_columns_is_a_template_error():
    d = json.loads(h.fixture(h.POLICE, h.B))
    d["data"][5][6] = "Разкрити"                    # a heading that is not the known one
    with pytest.raises(templates.TemplateError):
        templates.family_of(sheet.parse(json.dumps(d).encode()))


def test_template_ids_differ_between_families_and_are_stable():
    ids = {templates.template_id(parse(h.POLICE, u), f) for u, f in ((h.A, "types"), (h.B, "structures"), (h.C, "main"), (h.D, "types_by_structure"))}
    assert len(ids) == 4
    assert templates.template_id(parse(h.POLICE, h.B), "structures") == "structures-c059b805"
