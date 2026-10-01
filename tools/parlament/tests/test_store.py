"""The import against a scratch database: the check before writing, the hold rule, the change log, what the pages read.

Runs only when PARLAMENT_TEST_DSN points at a disposable database (it is wiped):
    PARLAMENT_TEST_DSN=dbname=parlament_test pytest tests/test_store.py
"""
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path

import pytest

DSN = os.environ.get("PARLAMENT_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="PARLAMENT_TEST_DSN not set")

FX = Path(__file__).parent / "fixtures" / "parliament"
TODAY = dt.date(2026, 9, 28)
# the sittings served, by id: their answer and their two files (real answers of parliament.bg)
SITTINGS = {11174: ("sten-240926.json", "gv240926.csv", "iv240926.csv"), 11159: ("sten-310726.json", "gv310726.csv", "iv310726.csv")}


@pytest.fixture
def conn(tmp_path, monkeypatch):
    from ingest import db, load
    monkeypatch.setattr(load, "RAW", tmp_path / "raw")
    with db.connect(autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS live, ops CASCADE; DROP TABLE IF EXISTS public.schema_migrations")
        db.migrate(c)
        yield c


class Source:
    """parliament.bg for the tests: the months list the sittings of SITTINGS (the real list, shortened to them)."""

    def __init__(self):
        self.files = {}
        for sid, (sten, gv, iv) in SITTINGS.items():
            s = json.loads((FX / sten).read_bytes())
            self.files[f"pl-sten/{sid}"] = (FX / sten).read_bytes()
            for f in s["files"]:
                name = f["Pl_StenDfile"].rsplit("/", 1)[-1]
                if name.endswith(".csv"):
                    self.files[name] = (FX / (gv if "_gv" in name else iv)).read_bytes()
        self.months = {"2026/7": [{"t_id": 11159, "t_label": 11159, "t_date": "2026-07-31"}], "2026/8": [],
                       "2026/9": [x for x in json.loads((FX / "month-2026-09.json").read_bytes()) if x["t_id"] in SITTINGS]}
        self.files["coll-list-ns/bg"] = (FX / "roster.json").read_bytes()
        # the assemblies and their people: the 51st and 52nd, cut to the MPs of roster.json and their 51st profiles
        self.files["fn-assembly/bg"] = (FX / "fn-assembly.json").read_bytes()
        self.files["archive/bg/61"] = (FX / "archive-61.json").read_bytes()
        for a in (61, 62):
            self.files[f"fn-mps/bg/{a}"] = (FX / f"fn-mps-{a}.json").read_bytes()
        for f in FX.glob("mp-profile-*.json"):
            self.files[f"mp-profile/bg/{f.stem.rsplit('-', 1)[1]}"] = f.read_bytes()
        self.files["mp-absense/bg"] = (FX / "mp-absense.json").read_bytes()
        self.files["mp-penalty"] = (FX / "mp-penalty.json").read_bytes()
        # the bills: of the month of 09.2026 only the one voted on 24.09 (first reading, sitting 11174)
        sept = [x for x in json.loads((FX / "bills-2026-09.json").read_bytes()) if x["t_id"] == 167546]
        self.files["archive-period/bg/L_Acts/2026/9/1/0"] = json.dumps(sept).encode()
        self.files["archive-period/bg/L_Acts/2026/8/1/0"] = b"[]"
        self.files["bill/167546"] = (FX / "bill-167546.json").read_bytes()
        self.calls = []

    def get(self, url, data=None):
        self.calls.append(url)
        m = re.search(r"archive-period/bg/Pl_StenV/(\d+)/(\d+)/0/0$", url)
        if m:
            return json.dumps(self.months.get(f"{m[1]}/{m[2]}", [])).encode()
        key = url.split("/api/v1/")[-1] if "/api/v1/" in url else url.rsplit("/", 1)[-1]
        got = self.files[key]
        if isinstance(got, Exception):
            raise got
        return got


def run(conn, src, first=(2026, 7), today=TODAY):
    from ingest import load
    return load.load(conn, {}, first=first, get=src.get, today=today)


def everyone(conn, src):
    """What n8n runs besides the sittings: the roster, the people, the absences."""
    from ingest import bills, load, people
    load.roster(conn, {}, get=src.get)
    people.assemblies(conn)
    people.people(conn, {}, get=src.get)
    people.absences(conn, {}, get=src.get)
    bills.load(conn, {}, get=src.get, today=TODAY)


def table_hash(conn):
    h = hashlib.sha256()
    for t in ("sitting", "item", "item_group", "mp", "vote", "line", "mp_stat"):
        for r in conn.execute(f"SELECT * FROM live.{t} ORDER BY 1, 2, 3"):
            h.update(repr(r).encode())
    return h.hexdigest()


def log(conn):
    return conn.execute("SELECT ref, field, old, new, cause FROM ops.change_log ORDER BY id").fetchall()


def iv_name(src, sid):
    s = json.loads(src.files[f"pl-sten/{sid}"])
    return next(f["Pl_StenDfile"].rsplit("/", 1)[-1] for f in s["files"] if "_iv" in f["Pl_StenDfile"] and f["Pl_StenDfile"].endswith(".csv"))


def test_two_sittings_are_written_checked_and_counted(conn):
    src = Source()
    st = run(conn, src)
    assert st["sittings"] == {"stored": 2} and st["problems"] == [] and st["rebuilt"] == [52]
    q = lambda sql, *a: conn.execute(sql, a).fetchall()   # noqa: E731
    assert q("SELECT id, assembly FROM live.sitting ORDER BY id") == [(11159, 52), (11174, 52)]
    assert q("SELECT count(*) FROM live.item") == [(17,)] and q("SELECT count(*) FROM live.vote") == [(4080,)]
    assert q("SELECT yes, no_, abstain, voted FROM live.item WHERE sitting = 11174 AND no = 2") == [(183, 10, 0, 193)]
    # Стефан Апостолов: in 24.09 П++++=000000, the line of ГЕРБ-СДС ("ГЕРБ - СДС" in the file) is + in item 2
    s = q("SELECT votes, voted, yes, abstain, regs, present FROM live.mp_stat WHERE mp = 3839")[0]
    assert s[0] == 11 + 4 and s[4] == 2
    lines = dict(q("SELECT item, line FROM live.line WHERE sitting = 11174 AND grp = 'ГЕРБ-СДС'"))
    assert lines[2] == "+" and all(v in ("+", "-", None) for v in lines.values())
    # against and abstained are one side, not for: Възраждане abstained as a whole in items 3-5 and 7 of 24.09 and in
    # item 3 of 31.07 (0 for, 0 against), its line there is "-", and Ангел Янчев, who abstained with it, is never against
    assert q("SELECT line FROM live.line WHERE sitting = 11174 AND item = 3 AND grp = 'ВЪЗРАЖДАНЕ'") == [("-",)]
    assert q("SELECT with_line, against_line FROM live.mp_stat WHERE mp = 3596")[0][1] == 0
    # every vote of a group with a line is with it or against it, never both
    assert q("""SELECT count(*) FROM live.mp_stat WHERE with_line + against_line > voted""") == [(0,)]
    assert q("SELECT sum(with_line) FROM live.line") == q("SELECT sum(with_line) FROM live.mp_stat")


def test_the_same_files_again_change_nothing(conn):
    src = Source()
    run(conn, src)
    first = table_hash(conn)
    assert run(conn, src)["sittings"] == {"unchanged": 2}
    assert table_hash(conn) == first and log(conn) == []


def test_files_that_do_not_add_up_are_not_written_and_are_reported(conn):
    src = Source()
    name = iv_name(src, 11174)
    src.files[name] = src.files[name].replace(",3839,ГЕРБ - СДС,52,2,+".encode(), ",3839,ГЕРБ - СДС,52,2,?".encode())
    st = run(conn, src)
    assert st["sittings"] == {"stored": 1, "invalid": 1}
    assert any("заседание 11174" in p and "unknown code" in p for p in st["problems"])
    assert conn.execute("SELECT count(*) FROM live.vote WHERE sitting = 11174").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM ops.source_state WHERE ref = 'sten/11174'").fetchone()[0] == "invalid"


def test_a_vote_changed_by_the_source_is_logged_and_the_small_difference_is_said(conn):
    src = Source()
    run(conn, src)
    name = iv_name(src, 11174)
    src.files[name] = src.files[name].replace(",3839,ГЕРБ - СДС,52,2,+".encode(), ",3839,ГЕРБ - СДС,52,2,-".encode())
    assert run(conn, src)["sittings"] == {"stored": 1, "unchanged": 1}
    assert log(conn) == [("sten/11174/2/3839", "code", "+", "-", "rewritten")]
    note = conn.execute("SELECT mismatch FROM live.item WHERE sitting = 11174 AND no = 2").fetchone()[0]
    assert "ГЕРБ-СДС (22, 1, 0, 23)" in note and "ГЕРБ-СДС (23, 0, 0, 23)" in note


def test_fewer_votes_wait_for_a_second_read_a_day_later(conn):
    src = Source()
    run(conn, src)
    first = table_hash(conn)
    name = iv_name(src, 11174)
    gv = next(k for k in src.files if "_gv240926" in k)
    # the source drops the last item from both files: the files still add up, but we would lose a vote
    src.files[name] = b"\n".join(l for l in src.files[name].split(b"\n") if not re.search(rb",12,.$", l.strip()))
    src.files[gv] = b"\n".join(l for l in src.files[gv].split(b"\n") if "Номер (12)".encode() not in l)
    assert run(conn, src)["sittings"] == {"held": 1, "unchanged": 1}
    assert table_hash(conn) == first and log(conn)[-1][4] == "held"
    assert run(conn, src)["sittings"] == {"held": 1, "unchanged": 1}                     # the same day: still held
    conn.execute("UPDATE ops.held SET first_at = now() - interval '2 days'")
    assert run(conn, src)["sittings"] == {"stored": 1, "unchanged": 1}
    assert log(conn)[1][4] == "confirmed" and conn.execute("SELECT count(*) FROM live.item WHERE sitting = 11174").fetchone()[0] == 11


def test_an_empty_file_is_no_roll_call_and_a_late_sitting_is_reported_for_a_week(conn):
    from ingest import checks, load
    src = Source()
    name = iv_name(src, 11174)
    src.files[name] = b""
    st = run(conn, src)
    everyone(conn, src)
    assert st["sittings"] == {"stored": 1, "no-files": 1} and st["problems"] == []
    assert checks.freshness(conn, TODAY) == []
    late = checks.freshness(conn, dt.date(2026, 10, 10))
    assert any("24.09.2026 (11174): 14 дни без поименно гласуване" in p for p in late)
    assert not any("11174" in p for p in checks.freshness(conn, dt.date(2026, 10, 20)))  # a week later: only on /sources


def test_a_gone_sitting_is_reported_and_nothing_is_deleted(conn):
    from ingest import http
    src = Source()
    run(conn, src)
    first = table_hash(conn)
    src.files["pl-sten/11174"] = http.Gone("404")
    st = run(conn, src)
    assert st["sittings"] == {"invalid": 1, "unchanged": 1} and any("11174" in p for p in st["problems"])
    src.months["2026/9"] = []                                         # and when the month forgets it
    assert run(conn, src)["sittings"] == {"unchanged": 1}
    assert table_hash(conn) == first


def test_the_roster_gives_the_constituency_only_by_a_unique_full_name(conn):
    from ingest import load, parse, stats
    src = Source()
    run(conn, src)
    st = load.roster(conn, {}, get=src.get)
    stats.rebuild(conn, [st["assembly"]])
    got = dict(conn.execute("SELECT name, district FROM live.mp_stat WHERE district IS NOT NULL").fetchall())
    names = {m["name"] for m in parse.roster(src.files["coll-list-ns/bg"])["mps"]}
    assert got and set(got) <= names and got.get("МИХАЕЛА МИЛЧЕВА ДОЦОВА") == "23-СОФИЯ"
    # a second MP of the same name in the roster: neither gets a constituency
    conn.execute("INSERT INTO live.roster VALUES (52, 1, 'МИХАЕЛА МИЛЧЕВА ДОЦОВА', NULL, '1-ВИДИН', NULL)")
    stats.rebuild(conn, [52])
    assert conn.execute("SELECT district FROM live.mp_stat WHERE name = 'МИХАЕЛА МИЛЧЕВА ДОЦОВА'").fetchone()[0] is None
    assert load.roster(conn, {}, get=src.get)["mps"] == 12 and log(conn) == []


def test_the_files_are_told_apart_by_content_the_same_file_twice_is_one(conn):
    """04.2023: the same file by group twice; 12.2022: a roll call under the name of the file by group; 12.2023: the
    roll call of another day under this sitting."""
    src = Source()
    s = json.loads(src.files["pl-sten/11174"])
    gv = next(f for f in s["files"] if "_gv" in f["Pl_StenDfile"] and f["Pl_StenDfile"].endswith(".csv"))
    iv = next(f for f in s["files"] if "_iv" in f["Pl_StenDfile"] and f["Pl_StenDfile"].endswith(".csv"))
    twice = dict(gv, Pl_StenDfile=gv["Pl_StenDfile"].replace("140052", "140059"))
    src.files[twice["Pl_StenDfile"].rsplit("/", 1)[-1]] = src.files[gv["Pl_StenDfile"].rsplit("/", 1)[-1]]
    s["files"].append(twice)
    src.files["pl-sten/11174"] = json.dumps(s).encode()
    assert run(conn, src)["sittings"] == {"stored": 2}
    # the roll call named as a file by group, and a roll call of another day: no file by group, no roll call of the day
    conn.execute("DELETE FROM live.sitting WHERE id = 11174")
    s["files"] = [dict(iv, Pl_StenDfile=iv["Pl_StenDfile"].replace("_iv", "_gv")), dict(iv, Pl_StenDfile=iv["Pl_StenDfile"].replace("240926", "230926"))]
    for f in s["files"]:
        src.files[f["Pl_StenDfile"].rsplit("/", 1)[-1]] = src.files[iv["Pl_StenDfile"].rsplit("/", 1)[-1]]
    src.files["pl-sten/11174"] = json.dumps(s).encode()
    assert run(conn, src)["sittings"] == {"no-files": 1, "unchanged": 1}
    err = conn.execute("SELECT error FROM ops.source_state WHERE ref = 'sten/11174'").fetchone()[0]
    assert err.startswith("няма четим файл по групи") and "iv230926.csv: името е за 23.09.2026" in err


def test_the_day_is_the_contents_not_the_names(conn):
    """17.09.2026: the file by group named "gv170626" is of 17.09; a file by group of another day is not the sitting's."""
    src = Source()
    s = json.loads(src.files["pl-sten/11174"])
    gv = next(f for f in s["files"] if "_gv" in f["Pl_StenDfile"] and f["Pl_StenDfile"].endswith(".csv"))
    body = src.files[gv["Pl_StenDfile"].rsplit("/", 1)[-1]]
    gv["Pl_StenDfile"] = gv["Pl_StenDfile"].replace("gv240926", "gv240626")
    src.files[gv["Pl_StenDfile"].rsplit("/", 1)[-1]] = body
    src.files["pl-sten/11174"] = json.dumps(s).encode()
    assert run(conn, src)["sittings"] == {"stored": 2}
    conn.execute("DELETE FROM live.sitting WHERE id = 11174")
    src.files[gv["Pl_StenDfile"].rsplit("/", 1)[-1]] = (FX / "gv310726.csv").read_bytes()
    assert run(conn, src)["sittings"] == {"no-files": 1, "unchanged": 1}
    err = conn.execute("SELECT error FROM ops.source_state WHERE ref = 'sten/11174'").fetchone()[0]
    assert "гласуванията в него са от 31.07.2026" in err


def test_an_mp_set_aside_is_said_on_the_sitting(conn):
    src = Source()
    name = iv_name(src, 11174)
    lines = src.files[name].split(b"\n")
    mine = [l for l in lines if ",3839,".encode() in l]
    codes = [l.rsplit(b",", 1)[1] for l in mine]
    shifted = [l.rsplit(b",", 1)[0] + b"," + (codes[i + 1] if i + 1 < len(codes) else b"") for i, l in enumerate(mine)]
    src.files[name] = b"\n".join(shifted[mine.index(l)] if l in mine else l for l in lines)
    assert run(conn, src)["sittings"] == {"stored": 2}
    note = conn.execute("SELECT note FROM live.sitting WHERE id = 11174").fetchone()[0]
    assert note.startswith("Знаците на СТЕФАН АПОСТОЛОВ АПОСТОЛОВ") and "гласовете му" in note
    assert conn.execute("SELECT count(*) FROM live.vote WHERE sitting = 11174 AND mp = 3839").fetchone()[0] == 0


def test_an_empty_row_of_a_group_is_kept_without_numbers(conn):
    """30.04.2026: the row of ПП in one item of the file by group has empty cells; the roll call adds up."""
    src = Source()
    gv = next(k for k in src.files if "_gv240926" in k)
    src.files[gv] = src.files[gv].replace(",ПП,52,1,0,6,7".encode(), ",ПП,52,,,,".encode(), 1)
    assert src.files[gv].count(",ПП,52,,,,".encode()) == 1
    assert run(conn, src)["sittings"] == {"stored": 2}
    row = conn.execute("""SELECT g.yes, i.mismatch FROM live.item_group g JOIN live.item i USING (sitting, no)
                          WHERE g.sitting = 11174 AND g.grp = 'ПП' AND i.kind = 'vote' AND g.yes IS NULL""").fetchone()
    assert row == (None, "по групи редът на ПП е празен")


def test_the_groups_get_one_code_and_a_renamed_group_stays_one(conn):
    from ingest import stats
    src = Source()
    run(conn, src)
    assert conn.execute("SELECT count(*) FROM live.vote WHERE grp = 'ГЕРБ - СДС'").fetchone()[0] == 0
    conn.execute("UPDATE live.sitting SET assembly = 51")
    conn.execute("UPDATE live.vote SET grp = 'ДПС-Ново начало' WHERE grp = 'ДПС' AND sitting = 11159")
    conn.execute("UPDATE live.item_group SET grp = 'ДПС-Ново начало' WHERE grp = 'ДПС' AND sitting = 11159")
    stats.rebuild(conn, [51])
    got = dict(conn.execute("SELECT sitting, string_agg(DISTINCT grp, ',') FROM live.vote WHERE grp LIKE '%ПС%' GROUP BY 1"))
    assert got == {11159: "ДПС-НН", 11174: "АПС"}      # in the 51st, "ДПС" alone was the group АПС (06.12.2024)


def test_freshness_knows_a_stale_list(conn):
    from ingest import checks, load
    src = Source()
    run(conn, src)
    everyone(conn, src)
    assert checks.freshness(conn, TODAY) == []
    conn.execute("UPDATE ops.source_state SET last_ok = %s::date - interval '5 days' WHERE ref LIKE 'month/%%'", (TODAY,))
    assert any("списъкът на заседанията" in p for p in checks.freshness(conn, TODAY))
    conn.execute("UPDATE ops.source_state SET last_ok = %s::date - interval '5 days' WHERE ref = 'absences'", (TODAY,))
    assert any("официалните отсъствия" in p for p in checks.freshness(conn, TODAY))


def test_the_assemblies_their_people_and_the_same_person_across_them(conn):
    from ingest import people
    src = Source()
    run(conn, src)
    everyone(conn, src)
    q = lambda sql, *a: conn.execute(sql, a).fetchall()   # noqa: E731
    assert q("SELECT count(*), min(no), max(no) FROM live.assembly") == [(60, 1, 107)]
    assert q("SELECT no, api_id, start, \"end\" FROM live.assembly WHERE no = 51") == [(51, 61, dt.date(2024, 11, 11), dt.date(2026, 4, 30))]
    assert q("SELECT count(*) FROM live.profile") == [(18,)]
    assert q("SELECT count(*) FROM live.body WHERE assembly = 51 AND kind = 'група'")[0][0] >= 11
    # Атанас Атанасов (5121 in the 52nd) was in the 51st as 4842 (the Assembly lists it; the name is there once)
    assert q("SELECT id, person FROM live.profile WHERE name = 'АТАНАС ПЕТРОВ АТАНАСОВ' ORDER BY id") == [(4842, 4842), (5121, 4842)]
    assert q("SELECT count(DISTINCT person) FROM live.profile") == [(12,)]   # 12 people, 6 of them in both
    # a namesake in the earlier assembly is not linked: the name must be there once
    conn.execute("INSERT INTO live.profile (id, assembly, name) VALUES (1, 51, 'АТАНАС ПЕТРОВ АТАНАСОВ')")
    people.link(conn)
    assert q("SELECT person FROM live.profile WHERE id = 5121") == [(5121,)]
    conn.execute("DELETE FROM live.profile WHERE id = 1")
    # the roll call's MPs get their profile and constituency; the chair's speeches get hers
    assert q("SELECT count(*) FROM live.mp WHERE assembly = 52 AND profile IS NOT NULL")[0][0] >= 12
    assert q("SELECT profile, district FROM live.mp_stat WHERE name = 'МИХАЕЛА МИЛЧЕВА ДОЦОВА'") == [(5237, q("SELECT district FROM live.profile WHERE id = 5237")[0][0])]
    # the absences and penalties are kept when the Assembly no longer shows them
    n = q("SELECT count(*) FROM live.absence")[0][0]
    assert n > 0 and q("SELECT count(*) FROM live.penalty")[0][0] > 0
    src.files["mp-absense/bg"] = b"[]"
    assert people.absences(conn, {}, get=src.get)["new"] == {"absences": 0, "penalties": 0}
    assert q("SELECT count(*) FROM live.absence") == [(n,)]


def test_a_sitting_keeps_its_stenogram_video_and_a_shorter_one_waits(conn):
    from ingest import people
    src = Source()
    src.files["pl-sten/11159"] = (FX / "sten-310726-text.json").read_bytes()
    people.assemblies(conn)
    people.people(conn, {}, get=src.get)
    assert run(conn, src)["sittings"] == {"stored": 2}
    q = lambda sql, *a: conn.execute(sql, a).fetchall()   # noqa: E731
    assert q("SELECT count(*) FROM live.speech WHERE sitting = 11159") == [(39,)]
    assert q("SELECT count(*) FROM live.speech WHERE sitting = 11174") == [(0,)]           # not published yet
    assert q("SELECT array_length(video, 1) > 0, steno_sha IS NOT NULL FROM live.sitting WHERE id = 11159") == [(True, True)]
    assert q("SELECT DISTINCT profile FROM live.speech WHERE sitting = 11159 AND name = 'МИХАЕЛА ДОЦОВА'") == [(5237,)]
    assert q("SELECT count(*) FROM live.speech WHERE tsv @@ to_tsquery('simple', 'заседание')")[0][0] > 0
    # the same again: nothing changes; a stenogram with fewer speeches waits a day
    before = q("SELECT no, text FROM live.speech WHERE sitting = 11159 ORDER BY no")
    run(conn, src)
    s = json.loads(src.files["pl-sten/11159"])
    s["Pl_Sten_body"] = s["Pl_Sten_body"][:len(s["Pl_Sten_body"]) // 2]
    src.files["pl-sten/11159"] = json.dumps(s).encode()
    run(conn, src)
    assert q("SELECT no, text FROM live.speech WHERE sitting = 11159 ORDER BY no") == before
    assert [r[4] for r in log(conn)] == ["held"]
    conn.execute("UPDATE ops.held SET first_at = now() - interval '2 days'")
    run(conn, src)
    assert q("SELECT count(*) FROM live.speech WHERE sitting = 11159")[0][0] < 39
    assert [r[4] for r in log(conn)] == ["held", "confirmed", "rewritten"]


def test_an_old_sitting_is_kept_with_its_scan_and_its_assembly_by_date(conn):
    from ingest import load, people
    src = Source()
    src.months = {"1879/3": [{"t_id": 9507, "t_label": 9507, "t_date": "1879-03-28"}]}
    src.files["pl-sten/9507"] = (FX / "sten-9507.json").read_bytes()
    people.assemblies(conn)
    st = load.load(conn, {}, first=(1879, 3), get=src.get, today=dt.date(1879, 3, 31))
    assert st["sittings"] == {"no-votes": 1} and st["problems"] == []
    assert conn.execute("SELECT assembly, pdf FROM live.sitting WHERE id = 9507").fetchone() == (100, "/pub/StenD/20191212105118XVIII_28-03-1879.pdf")
    src.files["20191212105118XVIII_28-03-1879.pdf"] = b"%PDF-1.4 scanned"
    assert load.pdfs(conn, {}, get=src.get) == {"archived": 1, "bytes": 16, "left": 0, "problems": []}
    assert load.pdfs(conn, {}, get=src.get)["archived"] == 0                             # once


def test_a_sitting_of_2009_is_read_from_its_sheets(conn):
    from ingest import people
    src = Source()
    src.months = {"2009/7": [{"t_id": 596, "t_label": 596, "t_date": "2009-07-30"}]}
    src.files["pl-sten/596"] = (FX / "sten-300709.json").read_bytes()
    for k in ("gv", "iv"):
        src.files[f"{k}300709.xls"] = (FX / f"{k}300709.xls").read_bytes()
    people.assemblies(conn)
    st = run(conn, src, first=(2009, 7), today=dt.date(2009, 7, 31))
    assert st["sittings"] == {"stored": 1} and st["rebuilt"] == [41]
    q = lambda sql, *a: conn.execute(sql, a).fetchall()   # noqa: E731
    assert q("SELECT assembly, iv FROM live.sitting WHERE id = 596") == [(41, "/pub/StenD/iv300709.xls")]
    assert q("SELECT count(*) FROM live.item WHERE sitting = 596") == [(7,)] and q("SELECT count(*) FROM live.vote WHERE sitting = 596") == [(1680,)]
    assert q("SELECT string_agg(code, '' ORDER BY item) FROM live.vote WHERE sitting = 596 AND mp = 343") == [("О0+00+0",)]
    assert q("SELECT count(*) FROM live.speech WHERE sitting = 596")[0][0] > 1


def test_a_sitting_the_source_cannot_answer_is_reported_and_the_rest_go_on(conn):
    from ingest import http
    src = Source()
    src.files["pl-sten/11174"] = http.Failed("източникът не отговаря: pl-sten/11174 след 5 опита")
    st = run(conn, src)
    assert st["sittings"] == {"stored": 1, "invalid": 1} and any("11174" in p and "не отговаря" in p for p in st["problems"])


def test_a_bill_its_steps_and_its_votes(conn):
    from ingest import bills
    src = Source()
    run(conn, src)
    everyone(conn, src)
    q = lambda sql, *a: conn.execute(sql, a).fetchall()   # noqa: E731
    assert q("SELECT sign, government, assembly FROM live.bill WHERE id = 167546") == [("52-602-01-43", True, 52)]
    assert q("SELECT count(*) FROM live.bill_step WHERE bill = 167546")[0][0] == 5
    # the first reading on 24.09.2026: "ЗИД на Наказателния кодекс – първо гласуване" is item 2 of sitting 11174
    got = q("SELECT sitting, item, reading FROM live.bill_item WHERE bill = 167546 ORDER BY item")
    assert (11174, 2, 1) in got and all(r[0] == 11174 for r in got)
    topics = [t for t, in q("SELECT i.topic FROM live.bill_item b JOIN live.item i ON i.sitting = b.sitting AND i.no = b.item WHERE b.bill = 167546")]
    assert all(bills.key(t) == "зид на наказателния кодекс" for t in topics)
    # the same answer again changes nothing; one with fewer steps waits for a second read
    assert bills.load(conn, {}, get=src.get, today=TODAY)["bills"] == {"unchanged": 1}
    b = json.loads(src.files["bill/167546"])
    b["activity"] = b["activity"][:1]
    b["steno_hall"] = []
    src.files["bill/167546"] = json.dumps(b).encode()
    assert bills.load(conn, {}, get=src.get, today=TODAY)["bills"] == {"held": 1}
    assert q("SELECT count(*) FROM live.bill_step WHERE bill = 167546")[0][0] == 5
