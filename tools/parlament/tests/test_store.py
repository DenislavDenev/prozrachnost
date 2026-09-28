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
    # Стефан Апостолов: in 24.09 П++++=000000, the line of ГЕРБ - СДС is + in items 2-5 and = nowhere
    s = q("SELECT votes, voted, yes, abstain, regs, present FROM live.mp_stat WHERE mp = 3839")[0]
    assert s[0] == 11 + 4 and s[4] == 2
    lines = dict(q("SELECT item, line FROM live.line WHERE sitting = 11174 AND grp = 'ГЕРБ - СДС'"))
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
    assert "ГЕРБ - СДС (22, 1, 0, 23)" in note and "ГЕРБ - СДС (23, 0, 0, 23)" in note


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


def test_freshness_knows_a_stale_list(conn):
    from ingest import checks, load
    src = Source()
    run(conn, src)
    load.roster(conn, {}, get=src.get)
    assert checks.freshness(conn, TODAY) == []
    conn.execute("UPDATE ops.source_state SET last_ok = now() - interval '5 days' WHERE ref LIKE 'month/%'")
    assert any("списъкът на заседанията" in p for p in checks.freshness(conn, TODAY))
