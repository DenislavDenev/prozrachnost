"""The parsers against real answers of parliament.bg (tests/fixtures/parliament), every layout seen since 12.2021.

The values below were checked by hand on 28.09.2026 against the Assembly's own PDF of the same sitting: the roll call
of 24.09.2026 (iv240926.pdf) for three MPs, and the file by group (gv240926.pdf) for item 2; on 29.09.2026 the roll
call of 30.07.2009 (iv300709.pdf, an XLS sitting) for three MPs.

The answers of mp-profile are cut to what the parser reads: the date and place of birth, contacts, photo, CV and the
lists not read (bills, questions, staff) are taken out before they enter the repository.
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
    ("Тридесет и девето Народно събрание", 39),        # fn-assembly/bg names the 39th and 40th so
    ("Четиридесето Народно събрание", 40),
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


def test_a_sheet_of_2009_reads_as_the_csv_and_adds_up():
    """30.07.2009, the XLS of the 41st assembly: the same layouts as the CSV (a block per item, a row per MP)."""
    gv, iv = parse.sheet(raw("gv300709.xls")), parse.sheet(raw("iv300709.xls"))
    assert parse.kind(gv) == "gv" and parse.kind(iv) == "iv"
    items, votes = parse.groups(gv), parse.rollcall(iv)
    assert len(items) == 7 and len(votes) == 1680
    assert parse.check(items, votes) == ([], {})
    assert codes(votes, 334) == "П++++++"                                        # Александър Ненков, ГЕРБ
    assert codes(votes, 335) == "П++0+++"                                        # Александър Стойков, ГЕРБ
    assert codes(votes, 343) == "О0+00+0"                                        # Антон Кутев, КБ
    assert parse.sheet(raw("gv240926.csv")) == raw("gv240926.csv")               # a CSV passes as it is
    with pytest.raises(parse.ShapeError, match="повреден"):
        parse.sheet(raw("gv300709.xls")[:3000])


def test_a_sitting_says_its_vote_files_scan_and_video():
    s = parse.sitting(raw("sten-300709.json"))
    assert s["assembly"] is None                                                 # the heading of 2009 does not name it
    assert s["files"] == ["/pub/StenD/gv300709.xls", "/pub/StenD/iv300709.xls"] and s["pdf"] is None
    old = parse.sitting(raw("sten-9507.json"))
    assert old["files"] == [] and old["pdf"].endswith("28-03-1879.pdf") and old["body"] == ""
    new = parse.sitting(raw("sten-310726-text.json"))
    assert new["video"] and all(v.startswith("https://parliament.bg/Gallery/video/archive-2026_07_31_") for v in new["video"])


def test_the_stenogram_is_split_by_speaker():
    sp = parse.speeches(parse.sitting(raw("sten-310726-text.json"))["body"])
    assert len(sp) == 39 and sp[0]["no"] == 0 and sp[0]["name"] is None           # who presided, before the first speech
    assert [x["no"] for x in sp] == list(range(39))
    chair = sp[1]
    assert chair["role"] == "председател" and chair["name"] == "МИХАЕЛА ДОЦОВА" and chair["grp"] is None
    grouped = [x for x in sp if x["grp"]]
    assert grouped and all(x["role"] is None or x["role"] == "докладчик" for x in grouped)
    assert all(x["text"] for x in sp[1:])


@pytest.mark.parametrize("line, got", [
    ("АТАНАС СЛАВОВ (ДБ): Уважаеми колеги!", ("АТАНАС СЛАВОВ", None, "ДБ")),
    ("ХРИСТО БИСЕРОВ (от място): Да, да!", ("ХРИСТО БИСЕРОВ", None, None)),
    ("ЦВЕТЕЛИНА СИМЕОНОВА-ЗАРКИН (Продължаваме Промяната, чрез интернет платформа): Тук.",
     ("ЦВЕТЕЛИНА СИМЕОНОВА-ЗАРКИН", None, "Продължаваме Промяната")),
    ("ПРЕДСЕДАТЕЛ КАДИР КАДИР (Звъни): Моля!", ("КАДИР КАДИР", "председател", None)),
    ("ЗАМЕСТНИК МИНИСТЪР-ПРЕДСЕДАТЕЛ ВЕСЕЛИН МЕТОДИЕВ: Благодаря.", ("ВЕСЕЛИН МЕТОДИЕВ", "заместник министър-председател", None)),
    ("РЕПЛИКА ОТ ДБ: Срам!", (None, "реплика", "ДБ")),
    ("ПРЕХОДНИ И ЗАКЛЮЧИТЕЛНИ РАЗПОРЕДБИ:", None),                              # a heading of a bill is no speaker
    ("§ 1. В чл. 2: текст", None),
    ("Гласували 117 народни представители: за 100", None),
])
def test_a_speaker_line(line, got):
    sp = parse.speaker(line)
    assert (sp and (sp["name"], sp["role"], sp["grp"])) == got if got else sp is None


def test_an_unpublished_stenogram_is_no_speech():
    assert parse.speeches(parse.sitting(raw("sten-240926.json"))["body"]) == []   # the notice of art. 67 until then
    with pytest.raises(parse.ShapeError):
        parse.speeches("Текст без нито едно изказване.<br />Още текст.")


def test_a_profile_keeps_the_public_role_only():
    p = parse.profile(raw("mp-profile-5121.json"))
    assert p["id"] == 5121 and p["name"] == "АТАНАС ПЕТРОВ АТАНАСОВ" and p["api_assembly"] == 62
    assert set(p) == {"id", "api_assembly", "name", "district", "list", "profession", "languages", "past", "memberships"}
    assert set(p["past"]) == {2, 51, 55, 56, 57, 58, 59, 60, 61}
    groups = [m for m in p["memberships"] if m["body_kind"] == 2]
    assert groups and all(m["since"] for m in groups)
    with pytest.raises(parse.ShapeError):
        parse.profile(b'{"A_ns_MP_id": 1}')


def test_the_assemblies_archive_absences_and_penalties():
    assert parse.assemblies(raw("fn-assembly.json")) == [(62, 52), (61, 51)]
    a = parse.archive(raw("archive-61.json"))
    assert str(a["start"]) == "2024-11-11" and str(a["end"]) == "2026-04-30"
    groups = [b for b in a["bodies"] if b["kind"] == "група"]
    assert len(groups) == 11 and all(b["since"] for b in groups)
    ab = parse.absences(raw("mp-absense.json"))
    assert ab and {x["kind"] for x in ab} <= {1, 2} and all(x["name"] for x in ab)
    pen = parse.penalties(raw("mp-penalty.json"))
    assert pen and all(x["kind"] and x["by"] for x in pen)
    with pytest.raises(parse.ShapeError):
        parse.absences(b'{"a": 1}')


def test_online_registration_is_read_and_counted_the_way_the_file_by_group_does():
    """"онлайн" in the roll calls of 01.2022 is a registration from afar (Д). 27.01.2022: the file by group counts it
    as present (188 П + 7 онлайн = 195); 21.01.2022: it does not (181 П = 181). Both add up."""
    for day, remote, present in (("270122", 7, 195), ("210122", 8, 181)):
        items, votes = parse.groups(parse.sheet(raw(f"gv{day}.xlsx"))), parse.rollcall(parse.sheet(raw(f"iv{day}.xlsx")))
        assert sum(1 for v in votes if v[3] == 1 and v[4] == "Д") == remote and items[1]["total"][0] == present
        assert parse.check(items, votes)[0] == []


def test_the_online_sittings_of_2021_count_the_hall_plus_online():
    """29.04.2021 (45th assembly): the roll call starts with "Регистрации и гласувания + онлайн от:" and the file by
    group writes "40+1" for 40 votes in the hall and 1 online; the roll call has all 41. Checked against the sheet:
    item 2, БСП 36 for of 36 voted; item 10, БСП abstained "40+1"."""
    gv, iv = parse.sheet(raw("gv290421.xlsx")), parse.sheet(raw("iv290421.xlsx"))
    assert (parse.kind(gv), parse.kind(iv)) == ("gv", "iv")
    items, votes = parse.groups(gv), parse.rollcall(iv)
    assert len(items) == 118 and items[2]["groups"]["БСП"] == (36, 0, 0, 36) and items[2]["total"] == (173, 2, 29, 204)
    assert items[10]["groups"]["БСП"] == (0, 0, 41, 41)
    assert parse.check(items, votes)[0] == []


def test_two_more_sheets_of_the_wide_roll_call():
    """28.07.2010: the sheet has no empty cell after the MP's name (name, number, group, codes). 03.12.2009: the
    header with the item numbers is repeated on every printed page. Both add up with their file by group."""
    for day, n_items, mps in (("280710", 33, 240), ("031209", 75, 240)):
        items, votes = parse.groups(parse.sheet(raw(f"gv{day}.xls"))), parse.rollcall(parse.sheet(raw(f"iv{day}.xls")))
        assert len(items) == n_items and len({v[0] for v in votes}) == mps
        assert parse.check(items, votes)[0] == []
    votes = parse.rollcall(parse.sheet(raw("iv280710.xls")))
    assert votes[0][:4] == (334, "АЛЕКСАНДЪР РУМЕНОВ НЕНКОВ", "ГЕРБ", 1) and votes[0][4] == "П"


def test_a_bill_and_the_short_title_of_its_votes():
    from ingest import bills
    b = parse.bill(raw("bill-166636.json"))
    assert (b["sign"], b["assembly"], str(b["adopted"]), b["dv_issue"], b["dv_year"], b["government"]) == ("51-554-01-169", 51, "2026-03-04", "27", 2026, False)
    assert b["sponsors"][0] == (4927, "ИСКРА ДИМИТРОВА МИХАЙЛОВА-КОПАРОВА") and len(b["sponsors"]) == 4
    assert b["committees"][0] == (3596, "Комисия по конституционни и правни въпроси", "водеща")
    hall = [s for s in b["steps"] if s["sitting"]]
    assert {(s["sitting"], s["stage"]) for s in hall} == {(11111, "зала второ гласуване")}
    assert parse.bill(raw("bill-167546.json"))["government"] is True
    assert len(parse.acts(raw("bills-2026-09.json"))) == 48
    for title, topic in (("Законопроект за изменение и допълнение на Закона за държавния дълг", "ЗИД на Закона за държавния дълг - второ гласуване - параграф 3"),
                         ("Законопроект за бюджета на държавното обществено осигуряване за 2026 г.", "Закон за бюджета на държавното обществено осигуряване за 2026 г. – първо гласуване"),
                         ("Законопроект за изменение на Наказателния кодекс", "ЗИ на Наказателния кодекс – второ гласуване")):
        assert bills.key(title) == bills.key(topic), (title, topic)
    assert bills.key("ЗИД на Закона за здравето – първо гласуване") != bills.key("Законопроект за изменение и допълнение на Закона за храните")
    assert bills.reading("ЗИД на Закона за държавния дълг - второ гласуване - параграф 3") == 2 and bills.reading("Решение за избиране") is None
    with pytest.raises(parse.ShapeError):
        parse.bill(b'{"L_Act_id": 1}')


def test_an_empty_answer_and_a_nul_in_the_text():
    with pytest.raises(parse.Empty):
        parse.profile(b"{}")
    with pytest.raises(parse.Empty):
        parse.bill(b"{}")
    import json
    s = json.loads(raw("sten-310726-text.json"))
    s["Pl_Sten_body"] = s["Pl_Sten_body"][:500] + chr(0) + s["Pl_Sten_body"][500:]
    got = parse.sitting(json.dumps(s).encode())
    assert chr(0) not in got["body"] and len(parse.speeches(got["body"])) == 39
