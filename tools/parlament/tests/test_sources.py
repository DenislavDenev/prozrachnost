"""The parsers against real answers of parliament.bg (tests/fixtures/parliament), every layout seen since 12.2021.

The values below were checked by hand on 28.09.2026 against the Assembly's own PDF of the same sitting: the roll call
of 24.09.2026 (iv240926.pdf) for three MPs, and the file by group (gv240926.pdf) for item 2.
"""
from pathlib import Path

import pytest

from ingest import parse

FX = Path(__file__).parent / "fixtures" / "parliament"


def raw(name):
    return (FX / name).read_bytes()


def codes(votes, mp):
    return "".join(c for no, _, _, _, c in sorted(votes, key=lambda r: r[3]) if no == mp)


def test_the_file_by_group_of_24_09_2026_is_the_assemblys_pdf():
    items = parse.groups(raw("gv240926.csv"))
    assert len(items) == 12
    assert items[1] == {"kind": "registration", "at": items[1]["at"], "topic": None, "total": (188, 240),
                        "groups": {"ПБ": (113, 131), "ГЕРБ-СДС": (29, 39), "ДПС": (15, 21), "ДБ": (14, 21), "ПП": (7, 16),
                                   "ВЪЗРАЖДАНЕ": (10, 12)}}
    two = items[2]
    assert two["topic"] == "ЗИД на Наказателния кодекс – първо гласуване" and str(two["at"]) == "2026-09-24 10:12:00"
    assert two["total"] == (183, 10, 0, 193)                                     # the PDF: за 183, против 10, гласували 193
    assert two["groups"]["ПБ"] == (119, 0, 0, 119) and two["groups"]["ВЪЗРАЖДАНЕ"] == (0, 10, 0, 10)
    assert "ДПС" in two["groups"] and "ГЕРБ-СДС" in two["groups"]                # "ДПС " and "ГЕРБ - СДС" in the file


def test_the_roll_call_of_24_09_2026_is_the_assemblys_pdf():
    votes = parse.rollcall(raw("iv240926.csv"))
    assert len(votes) == 2880 and len({v[0] for v in votes}) == 240
    assert codes(votes, 4098) == "О+0++++00000"                                  # Айлин Пехливанова, ПП
    assert codes(votes, 3839) == "П++++=000000"                                  # Стефан Апостолов, ГЕРБ - СДС
    assert codes(votes, 4083) == "П++++==+++++"                                  # Чило Попов, ДБ
    assert next(v for v in votes if v[0] == 4098)[1:3] == ("АЙЛИН НУРИДИН ПЕХЛИВАНОВА", "ПП")


def test_the_two_files_agree_and_prove_the_meaning_of_the_codes():
    """+ for, - against, = abstain, 0 did not vote; П present at a registration: only this reading adds up."""
    items, votes = parse.groups(raw("gv240926.csv")), parse.rollcall(raw("iv240926.csv"))
    assert parse.check(items, votes) == ([], {})
    swapped = [(n, m, g, i, {"+": "-", "-": "+"}.get(c, c)) for n, m, g, i, c in votes]
    assert parse.check(items, swapped)[0]
    as_present = [(n, m, g, i, "П" if c in "ОР" else c) for n, m, g, i, c in votes]
    assert parse.check(items, as_present)[0]


@pytest.mark.parametrize("pre,items,votes,mps", [
    ("310726", 5, 1200, 240),        # the roll call with a column per item (2026)
    ("250222", 101, 24240, 240),     # the same, in windows-1251, with the separator column elsewhere in the header (2022)
    ("080322", 2, 480, 240),         # the file by group in blocks, from the online sittings (2022)
    ("260122-closed", 5, 1195, 239),  # a topic with commas and no quotes; an MP listed without a single code
    ("101224", 7, 1680, 240),        # blocks with a column НС (12.2024)
])
def test_every_layout_since_12_2021_is_read_and_adds_up(pre, items, votes, mps):
    it, v = parse.groups(raw(f"gv{pre}.csv")), parse.rollcall(raw(f"iv{pre}.csv"))
    assert (len(it), len(v), len({x[0] for x in v})) == (items, votes, mps)
    assert parse.check(it, v) == ([], {})


def test_the_topic_spread_over_cells_is_whole():
    items = parse.groups(raw("gv260122-closed.csv"))
    assert items[2]["topic"].endswith("на територията на Република България - процедура за допускане в зала")
    assert items[2]["total"] == (201, 0, 0, 201)


def test_a_mac_cyrillic_file_is_read_but_an_online_sitting_does_not_add_up():
    """26.01.2022: the file by group counts the MPs online too, the roll call only the hall: refused, not guessed."""
    items, votes = parse.groups(raw("gv260122-online.csv")), parse.rollcall(raw("iv260122-online.csv"))
    assert items[2]["topic"].startswith("Програма за работата на Народното събрание") and items[2]["total"] == (154, 4, 47, 205)
    bad, notes = parse.check(items, votes)
    assert bad[0].startswith("точка 1: поименно ВЪЗРАЖДАНЕ (11, 13)") and "по групи ВЪЗРАЖДАНЕ (13, 13)" in bad[0]


def test_a_difference_of_a_vote_is_a_note_a_larger_one_stops_the_sitting():
    items, votes = parse.groups(raw("gv240926.csv")), parse.rollcall(raw("iv240926.csv"))
    one = [(n, m, g, i, "-" if (n, i) == (3839, 2) else c) for n, m, g, i, c in votes]      # Апостолов: против
    bad, notes = parse.check(items, one)
    assert bad == [] and list(notes) == [2] and "ГЕРБ-СДС (22, 1, 0, 23)" in notes[2]
    gerb = {n for n, _, g, i, _ in votes if g == "ГЕРБ-СДС" and i == 2}
    many = [(n, m, g, i, "-" if n in gerb and i == 2 and c == "+" else c) for n, m, g, i, c in votes]
    assert parse.check(items, many)[0][0].startswith("точка 2:")


def test_the_roll_call_and_the_file_by_group_must_have_the_same_items():
    items, votes = parse.groups(raw("gv240926.csv")), parse.rollcall(raw("iv240926.csv"))
    assert "точките се различават" in parse.check(items, [v for v in votes if v[3] != 12])[0][0]
    assert "два пъти" in parse.check(items, votes + votes[:1])[0][-1]


@pytest.mark.parametrize("bad,match", [
    (b"<!doctype html><html lang=bg>", "HTML"),
    (b"", "unexpected"),
    (b"NAME,textbox7,textbox8,ITEM,textbox2\nX,1,A,1,?\n", "unknown code"),
    (b"NAME,textbox7,textbox8,ITEM,textbox2\nX,,A,1,+\n", "not a count"),
])
def test_the_roll_call_refuses_what_it_does_not_know(bad, match):
    with pytest.raises(parse.ShapeError, match=match):
        parse.rollcall(bad)


def test_the_file_by_group_refuses_a_cut_file_and_another_layout():
    good = raw("gv240926.csv")
    with pytest.raises(parse.ShapeError):
        parse.groups(good[:good.index(b"\n", 5000)] + "\nНомер (9) ГЛАСУВАНЕ проведено".encode())
    with pytest.raises(parse.ShapeError, match="unexpected start"):
        parse.groups("a;b;c\n1;2;3\n".encode())
    with pytest.raises(parse.ShapeError, match="not a count"):
        parse.groups(good.replace(b",183,10,0,193,", b",183,10,,193,", 1))


def test_a_sitting_its_assembly_and_its_csv_files():
    s = parse.sitting(raw("sten-240926.json"))
    assert (s["id"], str(s["date"]), s["assembly"]) == (11174, "2026-09-24", 52)
    assert s["files"] == ["/pub/StenD/20260924140052_gv240926.csv", "/pub/StenD/20260924140118_iv240926.csv"]
    assert parse.sitting(raw("sten-10612.json"))["assembly"] == 47
    assert parse.sitting(raw("sten-10612.json"))["files"][1].endswith("iv260122 - Извънредно закрито.csv")
    assert parse.file_date("/pub/StenD/20231212121408_iv011223.csv") == parse._date("2023-12-01")
    assert parse.file_date("x.csv") is None


def test_the_kind_of_a_file_is_read_from_its_content():
    """12.2022: a roll call under the name of the file by group; 03.2026: a file damaged at the source."""
    assert parse.kind(raw("gv240926.csv")) == "gv" and parse.kind(raw("iv240926.csv")) == "iv"
    assert parse.kind(raw("gv080322.csv")) == "gv" and parse.kind(raw("iv310726.csv")) == "iv"
    assert parse.kind("x;y\n".encode()) is None
    with pytest.raises(parse.ShapeError, match="повреден"):
        parse.kind(raw("iv240926.csv")[:3000] + bytes([0xDE, 0x00, 0x15, 0xE9]) + raw("iv240926.csv")[3000:])


def test_a_block_file_with_the_assembly_column_and_a_group_written_two_ways():
    """10.12.2024: blocks with a column НС; "ДЕМОКРАЦИЯ, ПРАВА И СВОБОДИ-ДПС" in one file, without the space in the other."""
    items, votes = parse.groups(raw("gv101224.csv")), parse.rollcall(raw("iv101224.csv"))
    assert items[1]["total"] == (218, 240) and items[2]["total"] == (224, 0, 0, 224)
    assert items[1]["groups"]["ДЕМОКРАЦИЯ,ПРАВА И СВОБОДИ-ДПС"] == (17, 19)
    assert parse.check(items, votes) == ([], {})


def test_an_mp_with_codes_out_of_place_is_set_aside_and_named():
    """04.2024-02.2026: one MP's codes shifted (a vote in the registration's column, nothing after): the MP is set
    aside and the rest must add up allowing for them."""
    items, votes = parse.groups(raw("gv240926.csv")), parse.rollcall(raw("iv240926.csv"))
    mine = sorted((v for v in votes if v[0] == 3839), key=lambda v: v[3])
    codes = [c for *_, c in mine]
    shifted = [(n, m, g, i, codes[i] if i < 12 else "") for n, m, g, i, c in mine]      # item k gets the code of k + 1
    moved = [v for v in votes if v[0] != 3839] + [v for v in shifted if v[4]]
    assert parse.check(items, moved)[0]                                                  # as it is: it does not add up
    kept, aside = parse.split_shifted(items, moved)
    assert aside == {3839: ("СТЕФАН АПОСТОЛОВ АПОСТОЛОВ", "ГЕРБ-СДС")} and len(kept) == 2880 - 12
    assert parse.check(items, kept, {3839: "ГЕРБ-СДС"}) == ([], {})
    assert parse.check(items, kept)[0] == ["разминавания в 6 от 12 точки"]            # without the allowance: refused


def test_an_empty_code_is_an_mp_not_on_the_list_for_that_item():
    rows = "NAME,textbox7,textbox8,ITEM,textbox2\nX,1,A,1,П\nX,1,A,2,\n".encode()
    assert parse.rollcall(rows) == [(1, "X", "A", 1, "П")]
    with pytest.raises(parse.ShapeError, match="not JSON"):
        parse.sitting(b"<!doctype html><html>")                                  # a wrong API path answers 200 with the site


@pytest.mark.parametrize("heading,no", [
    ("ПЕТДЕСЕТ И ВТОРО НАРОДНО СЪБРАНИЕ, ПЕТДЕСЕТ И ТРЕТО ЗАСЕДАНИЕ", 52),
    ("ЧЕТИРИДЕСЕТ И СЕДМО НАРОДНО СЪБРАНИЕ\r\n\r\nЧЕТВЪРТО ИЗВЪНРЕДНО ЗАСЕДАНИЕ", 47),
    ("ПЕТДЕСЕТО НАРОДНО СЪБРАНИЕ", 50),
    ("Чeтиридесет и шесто Народно събрание", 46),                              # a Latin e, as on the site
    ("ЗАСЕДАНИЕ", None),
])
def test_the_assembly_is_read_from_the_heading(heading, no):
    assert parse.assembly_no(heading) == no


def test_the_months_list_and_the_roster():
    assert parse.sittings(raw("month-2026-09.json"))[:2] == [(11178, parse._date("2026-09-25")), (11174, parse._date("2026-09-24"))]
    with pytest.raises(parse.ShapeError):
        parse.sittings(b'{"a": 1}')
    r = parse.roster(raw("roster.json"))
    assert r["assembly"] == 52 and len(r["mps"]) == 12
    assert r["mps"][0] == {"profile": 5237, "name": "МИХАЕЛА МИЛЧЕВА ДОЦОВА", "group": 'Парламентарна група "Прогресивна България"',
                           "district": "23-СОФИЯ", "since": parse._date("2026-04-30")}
