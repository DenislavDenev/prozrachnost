import datetime as dt
import os
from pathlib import Path

import psycopg
import pytest

from app import main
from ingest import db
from ingest.history import parse_nvo7_year
from ingest.parse import parse_nvo7
from ingest.sources import Resource, current_pair, parse_schools, reconcile


FIX = Path(__file__).parent / "fixtures/mon"


@pytest.fixture
def conn(monkeypatch, tmp_path):
    dsn = os.environ.get("OBRAZOVANIE_TEST_DSN")
    if not dsn:
        pytest.skip("OBRAZOVANIE_TEST_DSN is required for database tests")
    assert "test" in dsn.lower(), "Only a test database may be used"
    monkeypatch.setattr(db, "DSN", dsn)
    monkeypatch.setattr(db, "RAW", tmp_path / "raw")
    with psycopg.connect(dsn, autocommit=True) as connection:
        db.migrate(connection)
        connection.execute("TRUNCATE live.exam_result,live.school,live.publication,ops.raw_file,ops.source_state,ops.held,ops.change_log RESTART IDENTITY CASCADE")
        yield connection


def inputs():
    exam, register = current_pair((FIX / "nvo7-catalog.json").read_bytes(), (FIX / "school-catalog.json").read_bytes())
    results = parse_nvo7((FIX / "nvo7-resource-08157f0e.json").read_bytes())
    schools = parse_schools((FIX / "school-register-236b21ed.json").read_bytes())
    checks = reconcile(results, schools)
    return exam, register, results, schools, checks


def test_replaced_code_is_held_even_when_count_is_unchanged():
    assert db.needs_hold({"105201", "909612"}, {"105201", "999999"}, 2, 2)
    assert not db.needs_hold({"105201"}, {"105201", "999999"}, 1, 2)


def test_publication_is_atomic_and_idempotent(conn):
    exam, register, results, schools, checks = inputs()
    assert db.publish(conn, exam.year, exam, register, "exam-a", "reg-a", results, schools, checks) == "stored"
    assert conn.execute("SELECT count(*) FROM live.school").fetchone()[0] == 4
    assert conn.execute("SELECT count(*) FROM live.exam_result").fetchone()[0] == 8
    assert conn.execute("SELECT matched_count FROM live.publication").fetchone()[0] == 4
    first_log = conn.execute("SELECT count(*) FROM ops.change_log").fetchone()[0]
    assert db.publish(conn, exam.year, exam, register, "exam-a", "reg-a", results, schools, checks) == "unchanged"
    assert conn.execute("SELECT count(*) FROM ops.change_log").fetchone()[0] == first_log
    view = main.snapshot()
    assert view["school_count"] == 4
    assert len(view["schools"]) == 4
    assert view["schools"][0]["subjects"]["БЕЛ"]["scale"] == "points100"


def test_smaller_response_waits_for_second_read_a_day_later(conn):
    exam, register, results, schools, checks = inputs()
    start = dt.datetime(2026, 9, 29, tzinfo=dt.timezone.utc)
    db.publish(conn, exam.year, exam, register, "exam-a", "reg-a", results, schools, checks, now=start)
    smaller = [row for row in results if row.neispuo != "2811518"]
    reduced = {code: school for code, school in schools.items() if code != "2811518"}
    reduced_check = reconcile(smaller, reduced)
    assert db.publish(conn, exam.year, exam, register, "exam-b", "reg-b", smaller, reduced,
                      reduced_check, now=start) == "held"
    assert conn.execute("SELECT count(*) FROM live.school").fetchone()[0] == 4
    assert db.publish(conn, exam.year, exam, register, "exam-c", "reg-b", smaller, reduced,
                      reduced_check, now=start + dt.timedelta(days=2)) == "held"
    assert conn.execute("SELECT count(*) FROM live.school").fetchone()[0] == 4
    assert db.publish(conn, exam.year, exam, register, "exam-c", "reg-b", smaller, reduced,
                      reduced_check, now=start + dt.timedelta(days=3, seconds=1)) == "stored"
    assert conn.execute("SELECT count(*) FROM live.school").fetchone()[0] == 3
    assert conn.execute("SELECT count(*) FROM ops.held").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM ops.change_log WHERE cause='confirmed'").fetchone()[0] == 1


def test_raw_answers_are_kept_by_sha(conn):
    uri = "https://data.egov.bg/api/listResources?dataset=school-year"
    sha1 = db.save_raw(conn, uri, b"real answer")
    sha2 = db.save_raw(conn, uri, b"real answer")
    assert sha1 == sha2
    saved_uri, saved_sha, saved_path = conn.execute("SELECT url,sha,path FROM ops.raw_file").fetchone()
    assert (saved_uri, saved_sha) == (uri, sha1)
    path = Path(saved_path)
    assert path.parent.name == dt.date.today().isoformat()
    assert path.name.endswith(".json") and "/" not in path.name
    assert path.read_bytes() == b"real answer"
    assert conn.execute("SELECT count(*) FROM ops.raw_file").fetchone()[0] == 1


def test_code_free_year_keeps_explicit_verification(conn):
    results = parse_nvo7_year((FIX / "nvo7-2017-2018.json").read_bytes(), "2017/2018")
    codes = sorted({row.neispuo for row in results})
    exam = Resource("exam-2018", "2017/2018", "НВО VII 2018", "2018-07-01")
    register = Resource("register-2018", "2017/2018", "Регистър 2018 без код", "2018-02-01")
    checks = {"schools": len(codes), "matched": 0, "unmatched": codes}
    assert db.publish(conn, exam.year, exam, register, "exam-a", "reg-a", results, {}, checks,
                      verification="no-code") == "stored"
    assert conn.execute("SELECT verification,matched_count FROM live.publication").fetchone() == ("no-code", 0)
    assert conn.execute("SELECT count(*) FROM live.school WHERE matched=false").fetchone()[0] == len(codes)
