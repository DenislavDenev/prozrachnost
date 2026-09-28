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
        self.calls = []

    def get(self, url):
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
    assert lines[2] == "+" and all(v in ("+", "-", "=", None) for v in lines.values())
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
    load.roster(conn, {}, get=src.get)
    st = run(conn, src)
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
    assert err.startswith("няма файл по групи в CSV") and "iv230926.csv е за друг ден" in err


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
    load.roster(conn, {}, get=src.get)
    assert checks.freshness(conn, TODAY) == []
    conn.execute("UPDATE ops.source_state SET last_ok = now() - interval '5 days' WHERE ref LIKE 'month/%'")
    assert any("списъкът на заседанията" in p for p in checks.freshness(conn, TODAY))
