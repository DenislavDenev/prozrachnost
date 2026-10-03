"""The archive into silver and gold on the test database: the hold rule, the log of changes, idempotence, the checks that
let a table out, the duplicate year, freshness. Run on the server against sigurnost_test (sg.sh)."""
import datetime as dt
import json
from decimal import Decimal

import pytest

from ingest import archive, checks, config, gold, load
from tests import helpers as h

UTC = dt.timezone.utc


@pytest.fixture
def arch(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ARCHIVE", tmp_path)

    def make(sets, **kw):
        return h.build_archive(tmp_path, sets, **kw)
    return make


def run(c, st, now=None):
    rep = load.ingest(c, st, now)
    rep["gold"] = gold.build(c)
    return rep


def one(c, q, *a):
    return c.execute(q, a).fetchone()[0]


def snapshot(c):
    out = {t: one(c, f"SELECT md5(string_agg(x::text, '|' ORDER BY x::text)) FROM {t} x") for t in
           ("silver.row", "silver.cell", "gold.observation", "gold.crime_row")}
    out["resource"] = one(c, "SELECT md5(string_agg(concat_ws('|', resource_uri, sha256, kind, family, template, n_rows, issues, status, is_current), ',' ORDER BY resource_uri, sha256)) FROM silver.resource")
    out["source_table"] = one(c, "SELECT md5(string_agg(concat_ws('|', year, family, resource_uri, sha256, template, n_rows, note), ',' ORDER BY year, family)) FROM gold.source_table")
    return out


def test_the_archive_is_read_into_silver_and_gold(c, arch):
    st = arch({h.POLICE: h.police_2024()})
    rep = run(c, st)
    assert rep["sets"] == 1 and rep["resources"] == 6 and rep["outcomes"] == {"new": 6}
    assert one(c, "SELECT count(*) FROM silver.resource WHERE is_current AND status = 'built'") == 6
    assert one(c, "SELECT count(*) FROM silver.row") == 80 + 32 + 143 + 2218 + 3 * 44 + 80 - 80 + one(c, "SELECT count(*) FROM silver.row WHERE resource_uri = %s", h.OTHER)
    assert one(c, "SELECT count(*) FROM silver.cell WHERE issue") == 1
    assert one(c, "SELECT value FROM silver.cell WHERE resource_uri = %s AND row_no = 1 AND col_no = 2", h.B) == 74709
    pub = {(p["year"], p["family"]) for p in rep["gold"]["published"]}
    assert pub == {(2024, f) for f in ("types", "structures", "main", "types_by_structure", "econ_by_structure")}
    # the oblast Монтана's domestic-violence registrations: the source prints a letter, gold keeps no number and the text
    v = c.execute("""SELECT o.value, o.value_text FROM gold.observation o JOIN gold.crime_row r USING (year, family, structure_code, row_no)
                     WHERE o.family = 'types_by_structure' AND o.structure_code = 'BG312' AND r.text LIKE 'Престъпления, извършени в условията на домашно%%'
                       AND o.indicator = 'reg'""").fetchone()
    assert v == (None, "с")
    assert one(c, "SELECT value FROM gold.observation WHERE family = 'structures' AND structure_code = 'BG331' AND indicator = 'reg'") == 6535
    assert one(c, "SELECT value FROM gold.observation WHERE family = 'types' AND indicator = 'per100k' AND row_no = 1") == Decimal("1080.15")
    assert one(c, "SELECT count(*) FROM gold.unmatched") == 0


def test_a_second_run_of_the_same_answers_changes_nothing_and_logs_nothing(c, arch):
    st = arch({h.POLICE: h.police_2024()})
    run(c, st)
    before, log = snapshot(c), one(c, "SELECT count(*) FROM ops.change_log")
    rep = run(c, st)
    assert rep["outcomes"] == {"same": 6} and rep["gold"]["published"] == [] and rep["gold"]["unchanged"] == [2024]
    assert snapshot(c) == before and one(c, "SELECT count(*) FROM ops.change_log") == log


def test_two_builds_from_the_same_answers_give_the_same_public_tables(c, arch, tmp_path):
    st = arch({h.POLICE: h.police_2024()})
    run(c, st)
    first = snapshot(c)
    h2 = tmp_path / "again"
    h2.mkdir()
    from tests.conftest import reset
    reset(c)
    run(c, st)
    assert snapshot(c) == first


def test_a_smaller_answer_is_held_until_a_second_read_a_day_later_says_the_same(c, arch):
    full = h.police_2024()
    st = arch({h.POLICE: full})
    t0 = dt.datetime(2026, 10, 2, 6, 0, tzinfo=UTC)
    run(c, st, t0)
    cut = json.loads(full[h.D])
    cut["data"] = cut["data"][:1200]
    small = dict(full, **{h.D: json.dumps(cut, ensure_ascii=False).encode()})
    sha_full = one(c, "SELECT sha256 FROM silver.resource WHERE resource_uri = %s AND is_current", h.D)
    st = arch({h.POLICE: small})
    assert load.ingest(c, st, t0 + dt.timedelta(days=1))["outcomes"]["held"] == 1
    assert one(c, "SELECT sha256 FROM silver.resource WHERE resource_uri = %s AND is_current", h.D) == sha_full
    held = c.execute("SELECT sha256, reason FROM ops.held WHERE ref = %s", (h.D,)).fetchone()
    assert held[0] != sha_full and "редовете паднаха" in held[1]
    # the second read an hour later is the same answer, but too soon
    assert load.ingest(c, st, t0 + dt.timedelta(days=1, hours=1))["outcomes"]["held"] == 1
    assert one(c, "SELECT sha256 FROM silver.resource WHERE resource_uri = %s AND is_current", h.D) == sha_full
    # a day later: confirmed, taken, the log says so
    assert load.ingest(c, st, t0 + dt.timedelta(days=2))["outcomes"]["confirmed"] == 1
    assert one(c, "SELECT sha256 FROM silver.resource WHERE resource_uri = %s AND is_current", h.D) == held[0]
    assert one(c, "SELECT count(*) FROM ops.held") == 0
    assert [r[0] for r in c.execute("SELECT cause FROM ops.change_log WHERE ref = %s ORDER BY id", (h.D,))] == ["new-record", "held", "confirmed"]
    assert one(c, "SELECT count(*) FROM silver.resource WHERE resource_uri = %s", h.D) == 2        # the history stays


def test_a_different_second_answer_does_not_confirm_the_held_one(c, arch):
    full = h.police_2024()
    t0 = dt.datetime(2026, 10, 2, 6, 0, tzinfo=UTC)
    run(c, arch({h.POLICE: full}), t0)

    def smaller(n):
        d = json.loads(full[h.D])
        d["data"] = d["data"][:n]
        return dict(full, **{h.D: json.dumps(d, ensure_ascii=False).encode()})
    load.ingest(c, arch({h.POLICE: smaller(1200)}), t0 + dt.timedelta(days=1))
    first = one(c, "SELECT sha256 FROM ops.held WHERE ref = %s", h.D)
    out = load.ingest(c, arch({h.POLICE: smaller(1100)}), t0 + dt.timedelta(days=3))["outcomes"]
    assert out == {"same": 5, "held": 1}
    assert one(c, "SELECT sha256 FROM ops.held WHERE ref = %s", h.D) != first
    assert one(c, "SELECT count(*) FROM silver.resource WHERE resource_uri = %s AND is_current AND n_rows = 2218", h.D) == 1


def test_a_table_that_turns_empty_is_held_not_taken(c, arch):
    full = h.police_2024()
    t0 = dt.datetime(2026, 10, 2, 6, 0, tzinfo=UTC)
    run(c, arch({h.POLICE: full}), t0)
    out = load.ingest(c, arch({h.POLICE: dict(full, **{h.B: b'{"success":true}'})}), t0 + dt.timedelta(days=1))["outcomes"]
    assert out["held"] == 1
    assert one(c, "SELECT reason FROM ops.held WHERE ref = %s", h.B) == "таблицата стана празна"


def test_an_html_answer_is_stored_as_invalid_and_the_good_version_stays(c, arch):
    full = h.police_2024()
    run(c, arch({h.POLICE: full}))
    st = arch({h.POLICE: dict(full, **{h.C: b"<html><body>503 Service Unavailable</body></html>"})})
    rep = load.ingest(c, st)
    assert rep["outcomes"]["invalid"] == 1
    assert one(c, "SELECT status FROM silver.resource WHERE resource_uri = %s AND is_current", h.C) == "built"
    assert one(c, "SELECT count(*) FROM silver.resource WHERE resource_uri = %s AND status = 'invalid'", h.C) == 1
    problems, _ = checks.freshness(c, st=st, now=dt.datetime(2026, 10, 2, 8, 0, tzinfo=UTC))
    assert any(h.C in p and "невалиден" in p for p in problems)
    # the same bad answer again is not read twice and not logged twice
    n = one(c, "SELECT count(*) FROM ops.change_log")
    assert load.ingest(c, st)["outcomes"]["same"] == 6 and one(c, "SELECT count(*) FROM ops.change_log") == n


def test_a_changed_answer_that_is_not_smaller_replaces_the_old_and_is_logged_field_by_field(c, arch):
    full = h.police_2024()
    run(c, arch({h.POLICE: full}))
    d = json.loads(full[h.B])
    d["data"][12][2] = "6536"
    new = dict(full, **{h.B: json.dumps(d, ensure_ascii=False).encode()})
    out = load.ingest(c, arch({h.POLICE: new}))["outcomes"]
    assert out == {"same": 5, "rewritten": 1}
    assert one(c, "SELECT count(*) FROM silver.resource WHERE resource_uri = %s", h.B) == 2
    assert one(c, "SELECT value FROM silver.cell c JOIN silver.resource r USING (resource_uri, sha256) WHERE r.resource_uri = %s AND r.is_current AND row_no = 5 AND col_no = 2", h.B) == 6536
    assert one(c, "SELECT cause FROM ops.change_log WHERE ref = %s ORDER BY id DESC LIMIT 1", h.B) == "rewritten"


def test_a_failed_check_publishes_nothing_and_leaves_the_previous_gold(c, arch):
    full = h.police_2024()
    run(c, arch({h.POLICE: full}))
    old = one(c, "SELECT count(*) FROM gold.observation")
    d = json.loads(full[h.B])
    d["data"][9 + 3][2] = str(int(d["data"][9 + 3][2]) + 5)           # Благоевград +5: the structures no longer add up
    new = dict(full, **{h.B: json.dumps(d, ensure_ascii=False).encode()})
    st = arch({h.POLICE: new})
    rep = run(c, st)
    held = {(x["family"]) for x in rep["gold"]["held"]}
    assert "structures" in held and "types" in held and "types_by_structure" in held
    assert any("structures_sum reg" in p for p in rep["gold"]["problems"])
    assert one(c, "SELECT count(*) FROM gold.observation") == old
    assert one(c, "SELECT value FROM gold.observation WHERE family = 'structures' AND structure_code = 'BG413' AND indicator = 'reg'") == 2632
    problems, _ = checks.freshness(c, st=st, now=dt.datetime(2026, 10, 2, 8, 0, tzinfo=UTC))
    assert any("structures: таблицата не е публикувана" in p for p in problems)


def test_two_versions_of_a_year_use_the_fuller_one_and_the_difference_is_logged(c, arch):
    full = h.police_2024()
    year19 = json.loads(full[h.A])
    year19["data"][0][0] = "Полицейска статистика 2019"       # the banner names the set's year
    half = dict(year19, data=year19["data"][:60])
    other = "fcb6aff7-0000-4000-8000-000000000001"
    enc = lambda d: json.dumps(d, ensure_ascii=False).encode()
    st = arch({h.POLICE_2019: {h.A: enc(year19)}, h.POLICE_2019_B: {other: enc(half)}})
    rep = run(c, st)
    assert [p for p in rep["gold"]["published"] if p["year"] == 2019] == [{"year": 2019, "family": "types", "rows": 80}]
    note = one(c, "SELECT note FROM gold.source_table WHERE year = 2019 AND family = 'types'")
    assert "2 версии" in note and "80" in note
    assert c.execute("SELECT old, new, cause FROM ops.change_log WHERE ref = '2019/types'").fetchall() == [("51", "80", "duplicate")]
    assert one(c, "SELECT resource_uri FROM gold.source_table WHERE year = 2019 AND family = 'types'") == h.A


def test_an_unknown_structure_is_unmatched_and_stops_the_tables_read_against_it(c, arch):
    full = h.police_2024()
    d = json.loads(full[h.B])
    assert d["data"][14][1] == "Видин"
    d["data"][14][1] = "Атлантида"
    st = arch({h.POLICE: dict(full, **{h.B: json.dumps(d, ensure_ascii=False).encode()})})
    rep = run(c, st)
    assert [x["family"] for x in rep["gold"]["held"]] == ["types_by_structure"]
    assert c.execute("SELECT name, reason FROM gold.unmatched").fetchall() == [("Атлантида", "структурата не е в списъка")]
    assert one(c, "SELECT count(*) FROM gold.observation WHERE family = 'structures' AND structure_code = 'BG311'") == 0
    problems, _ = checks.freshness(c, st=st, now=dt.datetime(2026, 10, 2, 8, 0, tzinfo=UTC))
    assert any("структури не са в списъка" in p for p in problems)


def test_licences_are_read_from_the_terms_of_the_set(c, arch):
    st = arch({h.POLICE: h.police_2024()})
    load.sync_datasets(c, st)
    assert c.execute("SELECT licence, shared FROM silver.dataset WHERE set_uri = %s", (h.POLICE,)).fetchone() == ("CC BY (признаване на авторските права)", True)
    assert c.execute("SELECT licence, shared FROM silver.dataset WHERE set_uri = %s", (h.OLD,)).fetchone() == ("CC0 (без защитени авторски права)", True)
    stated = sum(1 for d in config.datasets() if d["terms_of_use_id"] == "")
    assert stated and one(c, "SELECT count(*) FROM silver.dataset WHERE NOT shared") == stated
    assert one(c, "SELECT count(*) FROM silver.dataset WHERE terms_of_use_id = '' AND shared") == 0


def test_freshness_is_quiet_when_all_is_well_and_speaks_for_each_problem(c, arch):
    st = arch({h.POLICE: h.police_2024()})
    run(c, st)
    now = dt.datetime(2026, 10, 2, 8, 0, tzinfo=UTC)
    problems, info = checks.freshness(c, st=st, now=now)
    assert problems == []
    assert any("2025 още не е публикувана" in i for i in info) and any("клетки" in i for i in info)
    problems, _ = checks.freshness(c, st=st, now=now + dt.timedelta(hours=40))
    assert problems == ["Сигурност: архивът не е четен успешно 45 ч (над 30 ч)"]
    c.execute("INSERT INTO ops.held (ref, sha256, first_seen, reason) VALUES ('x', 'y', %s, 'малко редове')", (now - dt.timedelta(days=2),))
    assert any("задържан над ден" in p for p in checks.freshness(c, st=st, now=now)[0])
    problems, _ = checks.freshness(c, st=st, now=dt.datetime(2027, 3, 1, tzinfo=UTC) + dt.timedelta(days=400))
    assert any("години без нова" in p for p in problems)


def test_a_missing_archive_is_a_problem_not_a_crash(c, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ARCHIVE", tmp_path / "nothing")
    problems, _ = checks.freshness(c)
    assert problems and "Състоянието на архива не се чете" in problems[0]


def test_the_step_refuses_an_old_archive(c, arch):
    from ingest import run as runmod
    st = arch({h.POLICE: h.police_2024()}, last_ok="2020-01-01T00:00:00Z")
    out = runmod.step_build(c, type("A", (), {"force": False, "rebuild": False})())
    assert "архивът е остарял" in out["problems"][0]
    assert one(c, "SELECT count(*) FROM silver.resource") == 0


def test_the_monthly_bulletin_is_five_links_with_their_months_never_opened(c, arch):
    st = arch({h.POLICE: h.police_2024(), h.BULLETIN: None})
    rep = run(c, st)
    assert rep["links"] == 5 and rep["resources"] == 6 and "empty" not in rep["outcomes"]
    rows = c.execute("SELECT name, period, url FROM silver.link ORDER BY period").fetchall()
    assert [r[1].isoformat() for r in rows] == ["2018-01-01", "2018-02-01", "2026-06-01", "2026-07-01", "2026-08-01"]
    assert rows[0][2].startswith("https://www.mvr.bg/upload/") and rows[0][2].endswith(".pdf")
    assert one(c, "SELECT count(*) FROM silver.resource WHERE set_uri = %s", h.BULLETIN) == 0
    problems, info = checks.freshness(c, st=st, now=dt.datetime(2026, 9, 5, 8, 0, tzinfo=UTC))
    assert not any("бюлетин" in i for i in info)
    problems, info = checks.freshness(c, st=st, now=dt.datetime(2026, 12, 5, 8, 0, tzinfo=UTC))
    assert any("Последният месечен бюлетин на МВР е за 08.2026" in i for i in info)


def test_the_period_of_a_bulletin_comes_from_its_name():
    assert load.link_period("Бюлетин април 2024 г.") == dt.date(2024, 4, 1)
    assert load.link_period("Бюлетин декември 2025 г.") == dt.date(2025, 12, 1)
    assert load.link_period("Нещо друго") is None and load.link_period("Бюлетин Марсиански 2024") is None


def test_a_table_whose_banner_names_another_year_is_kept_in_silver_and_left_out_of_gold(c, arch):
    """The set of 2016 has a table titled "Полицейска статистика 2015" (real: resource 04c20b3e): whose year is it? We do not guess."""
    full = h.police_2024()
    st = arch({h.Y2016: {h.B: full[h.B]}})                     # the banner of this table says 2024
    rep = run(c, st)
    assert rep["gold"]["published"] == []
    assert c.execute("SELECT title_year, family, status, note FROM silver.resource").fetchall() == [
        (2024, "structures", "built", "заглавието е за 2024 г., наборът е за 2016 г.")]
    assert one(c, "SELECT count(*) FROM gold.observation") == 0


def test_the_other_dialects_are_read_into_their_own_templates_and_years_are_not_compared_across_them(c, arch):
    sets = {h.Y2016: {h.uri(h.Y2016, "b6369887"): h.fixture(h.Y2016, "b6369887"), h.uri(h.Y2016, "b9a2090b"): h.fixture(h.Y2016, "b9a2090b")},
            h.Y2023: {h.uri(h.Y2023, "b1b14807"): h.fixture(h.Y2023, "b1b14807"), h.uri(h.Y2023, "eb0edc37"): h.fixture(h.Y2023, "eb0edc37")},
            h.POLICE: {h.A: h.fixture(h.POLICE, h.A)}}
    rep = run(c, arch(sets))
    assert rep["outcomes"] == {"new": 5} and rep["gold"]["held"] == [] and rep["gold"]["problems"] == []
    got = c.execute("SELECT year, family, template FROM gold.source_table ORDER BY year, family").fetchall()
    assert [(g[0], g[1]) for g in got] == [(2016, "structures"), (2016, "types"), (2023, "structures"), (2023, "types"), (2024, "types")]
    types = {g[0]: g[2] for g in got if g[1] == "types"}
    assert len(set(types.values())) == 3
    from app import queries as Q
    rows24, _ = Q.type_rows(2024)
    rows23, _ = Q.type_rows(2023)
    Q.compare(rows24, rows23, "reg")
    assert all(r["other_reg"] is None for r in rows24)          # not comparable: a different template
    # the swapped columns of 2023 are in the right indicators
    assert one(c, "SELECT value FROM gold.observation WHERE year = 2023 AND family = 'types' AND row_no = 1 AND indicator = 'share_solved_unknown'") is not None
    # the stray 13th column of the object rows is not an indicator and is said in the note
    assert one(c, "SELECT note FROM silver.resource WHERE resource_uri = %s", h.uri(h.Y2023, "eb0edc37")).startswith("колона 12: 2 клетки без заглавие")


E23 = h.uri(h.Y2023, "eb0edc37")


def test_a_known_difference_and_a_duplicate_row_publish_the_table_with_the_difference_named(c, arch):
    """2023 (real resource eb0edc37): the duplicate "25. Търговище" is out of gold and the missing directorate makes the rows 397 short
    of the total: the recorded difference, so the table is published and the result is marked source."""
    rep = run(c, arch({h.Y2023: {E23: h.fixture(h.Y2023, "eb0edc37")}}))
    assert [p["family"] for p in rep["gold"]["published"]] == ["structures"] and rep["gold"]["held"] == []
    assert one(c, "SELECT count(*) FROM gold.crime_row WHERE family = 'structures'") == 31          # 32 rows less the repeat
    assert one(c, "SELECT count(*) FROM silver.row WHERE dup") == 1
    got = c.execute("SELECT scope, expected, got, status FROM gold.check_result WHERE check_id = 'structures_sum' ORDER BY scope").fetchall()
    assert got == [("reg", 73111, 72714, "source"), ("reg_unknown", 59208, 58811, "source"), ("solved", 38465, 38407, "source"),
                   ("solved_unknown", 26236, 26178, "source")]
    assert one(c, "SELECT note FROM silver.resource") == "колона 12: 2 клетки без заглавие; повторен ред: 25. Търговище (ред 32)"
    assert checks.freshness(c, now=dt.datetime(2025, 2, 3, 8, tzinfo=UTC), st=arch({h.Y2023: {E23: h.fixture(h.Y2023, "eb0edc37")}}))[0] == []


def test_a_changed_number_is_not_covered_by_the_recorded_difference(c, arch):
    d = json.loads(h.fixture(h.Y2023, "eb0edc37"))
    d["data"][12]["2"] = str(int(d["data"][12]["2"]) + 1)
    rep = run(c, arch({h.Y2023: {E23: json.dumps(d, ensure_ascii=False).encode()}}))
    assert rep["gold"]["published"] == [] and "structures_sum reg" in rep["gold"]["problems"][0]


def test_an_isolated_table_is_kept_in_silver_and_out_of_gold_without_an_alarm(c, arch, monkeypatch):
    monkeypatch.setattr(checks, "isolated", lambda: {h.B: dict(resource_uri=h.B, year="2024", family="structures", reason="проба")})
    st = arch({h.POLICE: h.police_2024()})
    rep = run(c, st)
    assert "structures" not in {p["family"] for p in rep["gold"]["published"]}
    assert one(c, "SELECT count(*) FROM silver.resource WHERE resource_uri = %s AND is_current", h.B) == 1
    assert [p for p in checks.freshness(c, now=dt.datetime(2026, 10, 3, 8, tzinfo=UTC), st=st)[0] if "structures" in p] == []
