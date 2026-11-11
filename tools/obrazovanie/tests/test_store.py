"""The landing zone, the clean tables and the serving layer in the shared database, with real MON answers."""
import datetime as dt
import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from ingest import pipeline, run, store
from ingest.context import parse_context
from ingest.dzi import parse_dzi
from ingest.history import parse_nvo7_year
from ingest.nvo import parse_nvo
from ingest.parse import ShapeError, parse_nvo7
from ingest.sources import Resource, current_pair, parse_schools
from ingest.status import StatusRow

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "mon"
START = dt.datetime(2026, 9, 29, tzinfo=dt.timezone.utc)


def step(conn, work, name="test"):
    """One step as the real ones run: under pipeline.run, then the rules, then the publication."""
    with pipeline.run(conn, "obrazovanie", name) as r:
        out = work(r)
        problems = pipeline.run_checks(conn, r)
        published = pipeline.publish(conn, r, ROOT)
    return out, problems, published


def nvo7(conn, r, exam_sha="exam-a", results=None, schools=None, now=START):
    exam, register = current_pair((FIX / "nvo7-catalog.json").read_bytes(), (FIX / "school-catalog.json").read_bytes())
    results = results if results is not None else parse_nvo7((FIX / "nvo7-resource-08157f0e.json").read_bytes())
    schools = schools if schools is not None else parse_schools((FIX / "school-register-236b21ed.json").read_bytes())
    register_load = store.load_register(conn, r, register, schools, now=now)
    return store.load_nvo7(conn, r, exam.year, exam, register, exam_sha, "reg-a", results, register_load, now=now)


def test_nvo7_year_lands_builds_and_publishes_once(conn):
    (loaded, problems, published) = step(conn, lambda r: nvo7(conn, r))
    assert loaded.status == "loaded" and published.publication_id
    assert conn.execute("SELECT count(*) FROM obrazovanie.school").fetchone()[0] == 4
    assert conn.execute("SELECT count(*) FROM pub_obrazovanie.exam_result").fetchone()[0] == 8
    assert conn.execute("SELECT matched_count, unmatched FROM pub_obrazovanie.nvo7_publication").fetchone() == (4, [])
    log = conn.execute("SELECT count(*) FROM ops.change_log WHERE tool = 'obrazovanie'").fetchone()[0]
    (again, _, republished) = step(conn, lambda r: nvo7(conn, r))
    assert again.status == "unchanged" and republished.publication_id is None
    assert conn.execute("SELECT count(*) FROM ops.change_log WHERE tool = 'obrazovanie'").fetchone()[0] == log
    view = main.snapshot()
    assert view["school_count"] == 4 and len(view["schools"]) == 4
    assert view["exam_updated"] == "2026-07-07"
    assert view["schools"][0]["subjects"]["БЕЛ"]["scale"] == "points100"
    # the name and the place come from the register when the code is there, not from the exam file (ГР.БАНСКО)
    assert next(x for x in view["schools"] if x["code"] == "105201")["town"] == "Банско"


def test_schools_point_to_the_shared_places_and_unknown_names_get_no_key(conn):
    schools = parse_schools((FIX / "school-register-236b21ed.json").read_bytes())
    code = "2811518"
    schools[code] = replace(schools[code], municipality="Несъществуваща", town="Никъде")
    step(conn, lambda r: nvo7(conn, r, schools=schools))
    rows = dict(((n, (m, e)) for n, m, e in conn.execute(
        "SELECT neispuo, municipality_code, ekatte FROM obrazovanie.school")))
    assert rows[code] == (None, None)
    bansko = conn.execute("""SELECT s.municipality_code, m.name_bg, st.name_bg FROM obrazovanie.school s
        JOIN core.municipality m USING (municipality_code) JOIN core.settlement st USING (ekatte)
        WHERE s.neispuo = '105201'""").fetchone()
    assert bansko[1:] == ("Банско", "Банско") and re.fullmatch(r"BLG\d\d", bansko[0])
    share = conn.execute("""SELECT actual FROM ops.check_result WHERE tool = 'obrazovanie'
        AND name = 'municipality_unmatched' ORDER BY result_id DESC LIMIT 1""").fetchone()[0]
    assert share == 2500   # one of four schools, per 10 000: the rule reports it and its threshold refuses it


def test_a_name_shared_by_two_settlements_is_not_guessed(conn):
    two = conn.execute("""SELECT m.oblast_code, s.municipality_code, s.name_bg FROM core.settlement s
        JOIN core.municipality m USING (municipality_code) WHERE s.gone_at IS NULL
        GROUP BY 1, 2, 3 HAVING count(*) > 1 LIMIT 1""").fetchone()
    assert two, "the real EKATTE has names repeated inside one municipality"
    assert conn.execute("SELECT obrazovanie.ekatte_of('x', %s, %s)", two[1:]).fetchone()[0] is None
    assert conn.execute("SELECT obrazovanie.municipality_of('СОФИЯ-ГРАД', 'Столична')").fetchone()[0] == "SOF46"
    # a town whose name a village elsewhere also has: unique inside its municipality, so it has its code
    assert conn.execute("SELECT obrazovanie.ekatte_of('Габрово', 'GAB05', 'Габрово')").fetchone()[0] == "14218"
    assert conn.execute("SELECT obrazovanie.ekatte_of('Габрово', NULL, 'гр. Габрово')").fetchone()[0] == "14218"


def test_schools_with_the_same_name_are_listed_by_code(conn):
    """Two schools may have one name; the list, the CSV and the map read them in the order of their codes, not in the
    order the rows lie in the table."""
    schools = parse_schools((FIX / "school-register-236b21ed.json").read_bytes())
    for code in ("2902103", "909612"):
        schools[code] = replace(schools[code], name="Основно училище")
    step(conn, lambda r: nvo7(conn, r, schools=schools))
    named = [x["code"] for x in main.snapshot()["schools"] if x["name"] == "Основно училище"]
    assert named == ["2902103", "909612"]


def test_smaller_response_waits_for_second_read_a_day_later(conn):
    step(conn, lambda r: nvo7(conn, r))
    results = parse_nvo7((FIX / "nvo7-resource-08157f0e.json").read_bytes())
    smaller = [x for x in results if x.neispuo != "2811518"]
    held, _, _ = step(conn, lambda r: nvo7(conn, r, "exam-b", smaller))
    assert held.status == "held"
    assert conn.execute("SELECT count(*) FROM pub_obrazovanie.school").fetchone()[0] == 4
    later = START + dt.timedelta(days=1, seconds=1)
    confirmed, _, _ = step(conn, lambda r: nvo7(conn, r, "exam-b", smaller, now=later))
    assert confirmed.status == "confirmed"
    assert conn.execute("SELECT count(*) FROM pub_obrazovanie.school").fetchone()[0] == 3
    assert conn.execute("SELECT count(*) FROM ops.hold WHERE tool = 'obrazovanie'").fetchone()[0] == 0
    assert conn.execute("""SELECT count(*) FROM ops.change_log WHERE tool = 'obrazovanie'
        AND cause = 'confirmed'""").fetchone()[0] == 1


def test_too_many_unknown_codes_stop_the_year_before_anything_is_written(conn):
    schools = parse_schools((FIX / "school-register-236b21ed.json").read_bytes())
    few = dict(list(schools.items())[:2])
    with pytest.raises(ShapeError, match="unmatched"):
        step(conn, lambda r: nvo7(conn, r, schools=few))
    assert conn.execute("SELECT count(*) FROM obrazovanie_src.nvo7").fetchone()[0] == 0


def test_code_free_year_keeps_explicit_verification(conn):
    results = parse_nvo7_year((FIX / "nvo7-2017-2018.json").read_bytes(), "2017/2018")
    codes = sorted({x.neispuo for x in results})
    exam = Resource("exam-2018", "2017/2018", "НВО VII 2018", "2018-07-01 10:00:00")
    register = Resource("register-2018", "2017/2018", "Регистър 2018 без код", "2018-02-01 10:00:00")
    step(conn, lambda r: store.load_nvo7(conn, r, "2017/2018", exam, register, "exam-a", "reg-a", results, None,
                                         register_rows=1800))
    assert conn.execute("SELECT verification, matched_count, unmatched FROM pub_obrazovanie.nvo7_publication").fetchone() == (
        "no-code", 0, codes)
    assert conn.execute("SELECT count(*) FROM pub_obrazovanie.school WHERE NOT matched").fetchone()[0] == len(codes)


def test_status_list_holds_a_shrink(conn):
    source = Resource("status-test", "2025/2026", "Official list", "2025-12-16 09:00:00")
    rows = [StatusRow(1, "102003", "Основно училище", "Места", "учениците от I до VII клас", True),
            StatusRow(2, "100102", "Детска градина", "Места", "децата", False)]
    assert step(conn, lambda r: store.load_status(conn, r, "protected", source, "sha-a", rows, now=START))[0].status == "loaded"
    assert conn.execute("SELECT school_rows FROM pub_obrazovanie.status_publication").fetchone()[0] == 1
    assert step(conn, lambda r: store.load_status(conn, r, "protected", source, "sha-b", rows[:1], now=START))[0].status == "held"
    assert conn.execute("SELECT count(*) FROM pub_obrazovanie.status_row").fetchone()[0] == 2
    later = START + dt.timedelta(days=1, seconds=1)
    assert step(conn, lambda r: store.load_status(conn, r, "protected", source, "sha-b", rows[:1], now=later))[0].status == "confirmed"
    assert conn.execute("SELECT count(*) FROM pub_obrazovanie.status_row").fetchone()[0] == 1


def test_national_context_holds_a_falling_count(conn):
    pupils = Resource("pupils-test", "2025/2026", "Pupils", "2026-02-25 10:00:00")
    classes = Resource("classes-test", "2025/2026", "Classes", "2026-02-25 10:00:00")
    rows = parse_context((FIX / "pupils-context-2025.json").read_bytes(),
                         (FIX / "classes-context-2025.json").read_bytes(), "2025/2026")
    load = lambda p, rs, now=START: lambda r: store.load_context(conn, r, pupils, classes, p, "c-a", rs, now=now)  # noqa: E731
    assert step(conn, load("p-a", rows))[0].status == "loaded"
    assert main.context_snapshot()["students"] == 709413
    smaller = [replace(x, grades={**x.grades, 1: x.grades[1] - 1}) if x.kind == "основно" else x for x in rows]
    assert step(conn, load("p-b", smaller))[0].status == "held"
    assert main.context_snapshot()["students"] == 709413
    assert step(conn, load("p-b", smaller, START + dt.timedelta(days=1, seconds=1)))[0].status == "confirmed"
    assert main.context_snapshot()["students"] == 709412
    assert main.context_snapshot()["kinds"][0]["grades"]["1"] > 0


def test_dzi_resource_keeps_suppressed_takers_and_pages_read_it(conn):
    uri = "e98e4650-d3fe-4bac-b3e4-941091190a40"
    table = parse_dzi((FIX / "dzi-e98e4650.json").read_bytes(), uri)
    resource = Resource(uri, table.year, "ДЗИ по желание", "2024-07-01 10:00:00")
    step(conn, lambda r: store.load_dzi(conn, r, resource, "dzi-a", table))
    assert conn.execute("SELECT source_rows, result_count, verification FROM pub_obrazovanie.dzi_publication").fetchone() == (
        4, len(table.results), "no-code")
    assert step(conn, lambda r: store.load_dzi(conn, r, resource, "dzi-a", table))[0].status == "unchanged"
    view = main.matura_snapshot(table.year, table.session, table.kind)
    assert view["results"] == len(table.results) and set(view["anomalies"]) == {
        "missing_grade", "suppressed_takers", "source_placeholder"}
    count, rows = main.matura_schools(uri)
    assert count == sum(1 for x in table.results if x.is_school) and len(rows) == count


def test_a_school_of_the_file_without_any_result_still_counts_as_unmatched(conn, monkeypatch):
    """The optional DZI lists schools abroad with no taker at all; the codes of the whole file are compared with
    the register, as before the move, not only the codes that have a result."""
    uri = "036f7fed-dfdc-480b-9400-41bfa64022d6"
    table = parse_dzi((FIX / "dzi-036f7fed.json").read_bytes(), uri)
    with_result = {x.neispuo for x in table.results}
    missing = sorted(table.codes - with_result)[0]
    schools = {c: Resource(c, "", "", "") for c in table.codes if c != missing}
    schools = {c: replace(parse_schools((FIX / "school-register-236b21ed.json").read_bytes()).popitem()[1], neispuo=c)
               for c in schools}
    monkeypatch.setattr(store, "check_unmatched", lambda *a: None)   # one of four is over the threshold of 2%
    register = Resource("register-2026", "2025/2026", "Регистър", "2026-02-10 10:00:00")
    resource = Resource(uri, table.year, "ДЗИ по желание", "2026-06-12 10:00:00")

    def work(r):
        load = store.load_register(conn, r, register, schools)
        return store.load_dzi(conn, r, resource, "dzi-a", table, register, "reg-a", load)
    step(conn, work)
    assert conn.execute("SELECT matched_count, unmatched FROM obrazovanie.dzi_publication").fetchone() == (
        len(table.codes) - 1, [missing])


def test_nvo_year_keeps_four_subjects(conn):
    uri = "af2a12fc-fc44-4eb3-a8d9-1798b987cf03"
    table = parse_nvo((FIX / "nvo4-af2a12fc.json").read_bytes(), uri)
    resource = Resource(uri, table.year, "НВО IV 2018", "2018-07-01 10:00:00")
    step(conn, lambda r: store.load_nvo(conn, r, resource, "nvo-a", table))
    assert conn.execute("SELECT exam, subject_count, result_count, verification FROM pub_obrazovanie.nvo_publication").fetchone() == (
        "nvo4", 4, len(table.results), "no-code")
    view = main.nvo_snapshot(table.exam, table.year)
    assert view["results"] == len(table.results)
    code = table.results[0].neispuo
    assert main.school_identity(code)["code"] == code
    assert len(main.school_nvo(code)[0]["subjects"]) == 4


def test_serving_layer_moves_only_with_a_publication_and_every_answer_names_it(conn):
    step(conn, lambda r: nvo7(conn, r))
    client = TestClient(main.app)
    first = client.get("/uchilishta")
    number = first.headers["X-Publication"]
    assert first.status_code == 200 and first.headers["ETag"] == f'W/"pub-{number}"'
    # the clean tables change, the serving layer does not until the next publication
    conn.execute("DELETE FROM obrazovanie.exam_result WHERE neispuo = '105201'")
    conn.execute("DELETE FROM obrazovanie.school WHERE neispuo = '105201'")
    assert "105201" in client.get("/uchilishta").text
    with pipeline.run(conn, "obrazovanie", "test") as r:
        assert pipeline.publish(conn, r, ROOT).publication_id
    after = client.get("/uchilishta")
    assert "105201" not in after.text and int(after.headers["X-Publication"]) > int(number)


def test_pages_read_only_the_serving_layer():
    for f in (ROOT / "app").glob("*.py"):
        text = f.read_text(encoding="utf-8")
        for schema in re.findall(r"\b(live|ops|core|obrazovanie|obrazovanie_src)\.[a-z0-9_]+\b", text):
            pytest.fail(f"{f.name} reads {schema}")


def test_the_serving_layer_has_no_violation(conn):
    step(conn, lambda r: nvo7(conn, r))
    assert conn.execute("SELECT count(*) FROM core.serving_violation").fetchone()[0] == 0
    assert conn.execute("""SELECT count(*) FROM core.person_column_undecided
        WHERE schema_name LIKE '%obrazovanie%'""").fetchone()[0] == 0


LEGACY = sorted((ROOT / "db" / "legacy").glob("*.sql"))


def old_database(conn, tmp_path):
    """The old database of its own, in schemas live and ops_old of the test database, filled as the old code did."""
    for f in LEGACY:
        conn.execute(f.read_text(encoding="utf-8").replace("ops.", "ops_old.").replace("SCHEMA IF NOT EXISTS ops;",
                                                                                      "SCHEMA IF NOT EXISTS ops_old;"))
    uri = "af2a12fc-fc44-4eb3-a8d9-1798b987cf03"
    raw = (FIX / "nvo4-af2a12fc.json").read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    path = tmp_path / "old" / "nvo4.json"
    path.parent.mkdir()
    path.write_bytes(raw)
    table = parse_nvo(raw, uri)
    conn.execute("INSERT INTO ops_old.raw_file (url, sha, path) VALUES (%s, %s, %s)", (uri, sha, str(path)))
    conn.execute("""INSERT INTO live.nvo_publication (resource, exam, school_year, sha, updated_at, verification,
          school_count, subject_count, result_count, matched_count, unmatched)
        VALUES (%s, %s, %s, %s, '2018-07-01 10:00:00', 'no-code', %s, %s, %s, 0, '[]')""",
                 (uri, table.exam, table.year, sha, table.rows, len(table.subjects), len(table.results)))
    for x in table.results:
        conn.execute("""INSERT INTO live.nvo_result (resource, neispuo, school, oblast, municipality, town, subject, takers, score)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (uri, x.neispuo, x.school, x.oblast, x.municipality, x.town, x.subject,
                                                     x.takers, x.score))
    return len(table.results)


def test_replay_from_raw_equals_the_old_database_and_parity_sees_a_difference(conn, serving, tmp_path):
    n = old_database(conn, tmp_path)
    dsn = serving
    _, problems, _ = step(conn, lambda r: run.replay(conn, r, old_dsn=dsn, old_ops="ops_old"), "replay")
    assert conn.execute("SELECT count(*) FROM pub_obrazovanie.nvo_result").fetchone()[0] == n
    with pipeline.run(conn, "obrazovanie", "parity") as r:
        run.parity(conn, r, old_dsn=dsn)
    assert r.stats["rows_differ"] == 0
    conn.execute("UPDATE live.nvo_result SET town = 'Другаде' WHERE (neispuo, subject) = (SELECT neispuo, subject FROM live.nvo_result LIMIT 1)")
    with pipeline.run(conn, "obrazovanie", "parity") as r:
        run.parity(conn, r, old_dsn=dsn)
    assert r.stats["rows_differ"] == 2
    bad = conn.execute("""SELECT name FROM ops.check_latest WHERE tool = 'obrazovanie' AND kind = 'parity'
        AND ok = false""").fetchall()
    assert bad == [("parity_rows_differ",)]


def test_a_rejected_last_read_is_a_problem_until_a_good_one(conn):
    with pipeline.run(conn, "obrazovanie", "status") as r:
        pipeline.reject(conn, r, "status", "broken", "Changed columns")
        assert any("отказано" in p for p in pipeline.run_checks(conn, r))
    assert any("отказано" in p for p in pipeline.freshness(conn, "obrazovanie"))
    assert json.dumps(pipeline.freshness(conn, "obrazovanie"), ensure_ascii=False)
