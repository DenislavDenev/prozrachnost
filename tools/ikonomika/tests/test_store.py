"""The hold rule, the change log, the clean tables, the steps and the move, against the test database
(STANDARD 1А.4-6, 1Б, 1В; ARCHITECTURE 6, 15).

Runs only when IKONOMIKA_TEST_DSN points at a disposable database (it is wiped):
    IKONOMIKA_TEST_DSN=dbname=ikonomika_test pytest tests/test_store.py
"""
import datetime as dt
import hashlib
import re
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

FX = Path(__file__).parent / "fixtures"
BOM = "\N{ZERO WIDTH NO-BREAK SPACE}"
T0 = dt.datetime(2026, 11, 1, 10, tzinfo=dt.timezone.utc)


def log(conn):
    return conn.execute("""SELECT ref, field, old, new, cause FROM ops.change_log WHERE tool = 'ikonomika'
                           ORDER BY change_id""").fetchall()


def rows(*vals):
    return [({"unit": "X"}, "BG", t, v, f) for t, v, f in vals]


def apply(conn, r, rows_, at=T0, **kw):
    from ingest import store
    return store.apply(conn, r, "eurostat", "gdp_a", "gdp_a", None, rows_, now=at, **kw)


def facts(conn, indicator="gdp_a"):
    """The current clean facts of an indicator, after a build."""
    return conn.execute("""SELECT o.period, o.value::float8, o.flag FROM ikonomika.observation o JOIN ikonomika.series s USING (series_id)
        WHERE s.indicator_code = %s AND o.superseded_at IS NULL ORDER BY 1""", (f"ikonomika.{indicator}",)).fetchall()


def build(conn, r):
    from ingest import store
    store.build(conn, r)


def test_first_read_is_one_line_and_the_same_answer_changes_nothing(conn, run):
    three = rows(("2023", 1.5, None), ("2024", 2.5, None), ("2025", 3.5, "p"))
    assert apply(conn, run, three) == "stored"
    assert log(conn) == [("gdp_a", "rows", None, "3", "new-record")]
    assert apply(conn, run, three, T0 + dt.timedelta(hours=1)) == "unchanged"
    assert len(log(conn)) == 1
    build(conn, run)
    assert facts(conn) == [("2023", 1.5, None), ("2024", 2.5, None), ("2025", 3.5, "p")]
    assert conn.execute("""SELECT period_kind, period_start, period_end FROM ikonomika.observation WHERE period = '2024'""").fetchone() \
        == ("year", dt.date(2024, 1, 1), dt.date(2024, 12, 31))


def test_a_revision_and_a_new_period_are_logged_and_the_old_fact_is_superseded(conn, run):
    apply(conn, run, rows(("2024", 2.5, None), ("2025", 3.5, "p")))
    build(conn, run)
    assert apply(conn, run, rows(("2024", 2.5, None), ("2025", 3.7, None), ("2026", 1.0, "p")), T0 + dt.timedelta(days=1)) == "stored"
    causes = [c for *_, c in log(conn)]
    assert causes.count("rewritten") == 2 and causes.count("new-record") == 2
    build(conn, run)
    assert facts(conn) == [("2024", 2.5, None), ("2025", 3.7, None), ("2026", 1.0, "p")]
    old = conn.execute("SELECT value::float8, flag, superseded_at IS NOT NULL FROM ikonomika.observation WHERE period = '2025' ORDER BY first_seen_at").fetchall()
    assert old == [(3.5, "p", True), (3.7, None, False)]      # nothing is deleted: the old number is superseded


def test_a_smaller_answer_is_held_until_a_second_read_a_day_later_agrees(conn, run):
    three = rows(("2023", 1.5, None), ("2024", 2.5, None), ("2025", 3.5, None))
    two = rows(("2024", 2.5, None), ("2025", 3.5, None))
    apply(conn, run, three)
    assert apply(conn, run, two, T0 + dt.timedelta(hours=1)) == "held"
    build(conn, run)
    assert len(facts(conn)) == 3                                                     # ours stays
    assert log(conn)[-1][1:] == ("rows", "3", "2", "held")
    assert apply(conn, run, two, T0 + dt.timedelta(hours=5)) == "held"             # the same day: still held
    other = rows(("2023", 1.5, None), ("2025", 3.5, None))
    assert apply(conn, run, other, T0 + dt.timedelta(days=2)) == "held"            # a different answer starts again
    assert conn.execute("SELECT state FROM (SELECT status AS state FROM ikonomika.source_state WHERE ref = 'gdp_a') x").fetchone()[0] == "held"
    assert apply(conn, run, other, T0 + dt.timedelta(days=3, hours=1)) == "stored"  # a day later it agrees
    assert [r[4] for r in log(conn)][-2:] == ["confirmed", "removed"]
    build(conn, run)
    assert [p for p, *_ in facts(conn)] == ["2023", "2025"]
    assert conn.execute("SELECT count(*) FROM ops.hold WHERE tool = 'ikonomika'").fetchone()[0] == 0
    # the landing zone keeps only the current version of the resource
    assert conn.execute("SELECT count(DISTINCT load_id) FROM ikonomika_src.observation").fetchone()[0] == 1


def test_when_the_source_comes_back_the_hold_is_dropped(conn, run):
    three = rows(("2023", 1.5, None), ("2024", 2.5, None), ("2025", 3.5, None))
    apply(conn, run, three)
    apply(conn, run, three[1:], T0 + dt.timedelta(hours=1))
    assert apply(conn, run, three, T0 + dt.timedelta(hours=2)) == "unchanged"
    assert conn.execute("SELECT count(*) FROM ops.hold WHERE tool = 'ikonomika'").fetchone()[0] == 0


def test_duplicate_rows_are_refused(conn, run):
    from ingest.jsonstat import ShapeError
    with pytest.raises(ShapeError, match="twice"):
        apply(conn, run, rows(("2024", 1, None), ("2024", 1, None)))
    assert conn.execute("SELECT count(*) FROM ikonomika_src.observation").fetchone()[0] == 0


def test_an_unknown_period_stops_the_build(conn, run):
    import psycopg
    apply(conn, run, rows(("2024W05", 1, None)))
    with pytest.raises(psycopg.errors.NotNullViolation):
        build(conn, run)
    assert conn.execute("SELECT count(*) FROM ikonomika.observation").fetchone()[0] == 0


def table_hash(conn):
    h = hashlib.sha256()
    for r in conn.execute("""SELECT s.indicator_code, s.dims::text, s.geo, o.period, o.value::text, o.flag
            FROM ikonomika_src.observation x JOIN ikonomika.series s ON s.indicator_code = 'ikonomika.' || x.indicator
              AND s.dims = x.dims AND s.geo = x.geo
            JOIN ikonomika.observation o ON o.series_id = s.series_id AND o.period = x.period AND o.superseded_at IS NULL
            ORDER BY 1, 2, 3, 4"""):
        h.update(repr(r).encode())
    return h.hexdigest()


def fake_eurostat(monkeypatch, answers):
    from ingest import eurostat
    monkeypatch.setattr(eurostat, "indicators", lambda: [
        {"id": "gdp_a", "dataset": "nama_10_gdp", "filters": "na_item=B1GQ&unit=CP_MEUR", "geo": "BG", "stale_days": "400",
         "label": "БВП", "unit_label": "млн. €"},
        {"id": "gdp_nuts", "dataset": "nama_10r_3gdp", "filters": "unit=MIO_EUR", "geo": "NUTS", "stale_days": "730",
         "label": "БВП по области", "unit_label": "млн. €"}])

    def get(url):
        for key, body in answers.items():
            if f"/{key}?" in url:
                if isinstance(body, Exception):
                    raise body
                return body
        raise AssertionError(url)
    return get


def test_eurostat_step_writes_checks_and_keeps_what_fails(conn, run, monkeypatch):
    from ingest import eurostat, http
    gdp, nuts = (FX / "eurostat/gdp_bg_since2023.json").read_bytes(), (FX / "eurostat/gdp_nuts_mio.json").read_bytes()
    get = fake_eurostat(monkeypatch, {"nama_10_gdp": gdp, "nama_10r_3gdp": nuts})
    stats = eurostat.load(conn, run, get=get)
    assert stats["indicators"] == {"gdp_a": "stored", "gdp_nuts": "stored"} and stats["problems"] == []
    build(conn, run)
    assert len(facts(conn, "gdp_nuts")) == 925
    assert ("2025", 116018.3, "p") in facts(conn)
    # the regions of Bulgaria point to core.nuts; every one of the 28 oblasts, 6 regions, 2 macro-regions and the country
    assert conn.execute("""SELECT count(*) FROM ikonomika.series WHERE indicator_code = 'ikonomika.gdp_nuts'
                           AND nuts_code IS NOT NULL""").fetchone()[0] == 37
    first = table_hash(conn)
    assert eurostat.load(conn, run, get=get)["indicators"] == {"gdp_a": "unchanged", "gdp_nuts": "unchanged"}
    build(conn, run)
    assert table_hash(conn) == first and [c for *_, c in log(conn)] == ["new-record", "new-record"]   # idempotent
    # a region that no longer adds up: the regions are not written, the rest is
    broken = nuts.replace(b'"0":14440.05', b'"0":15440.05', 1)                # BG in 2000
    assert broken != nuts
    get = fake_eurostat(monkeypatch, {"nama_10_gdp": gdp, "nama_10r_3gdp": broken})
    stats = eurostat.load(conn, run, get=get)
    assert stats["indicators"]["gdp_nuts"] == "invalid" and "сборът на областите" in stats["problems"][0]
    build(conn, run)
    assert table_hash(conn) == first
    assert conn.execute("""SELECT status FROM ops.load WHERE tool = 'ikonomika' AND ref = 'gdp_nuts'
                           ORDER BY load_id DESC LIMIT 1""").fetchone()[0] == "rejected"
    # a renamed dataset (404): reported as gone, the old numbers stay
    get = fake_eurostat(monkeypatch, {"nama_10_gdp": http.Gone("404 nama_10_gdp"), "nama_10r_3gdp": nuts})
    stats = eurostat.load(conn, run, get=get)
    assert stats["indicators"]["gdp_a"] == "gone"
    build(conn, run)
    assert table_hash(conn) == first
    assert conn.execute("SELECT status FROM ikonomika.source_state WHERE ref = 'gdp_a'").fetchone()[0] == "gone"


def test_freshness_names_what_is_late(conn, run, monkeypatch):
    from ingest import checks, eurostat
    gdp, nuts = (FX / "eurostat/gdp_bg_since2023.json").read_bytes(), (FX / "eurostat/gdp_nuts_mio.json").read_bytes()
    get = fake_eurostat(monkeypatch, {"nama_10_gdp": gdp, "nama_10r_3gdp": nuts})
    eurostat.load(conn, run, get=get)
    build(conn, run)
    inds = eurostat.indicators()
    assert checks.freshness(conn, inds) == ["Икономика: курсовете на БНБ са към никога"]
    conn.execute("UPDATE ikonomika.source_state SET last_new_period = now() - interval '800 days' WHERE ref = 'gdp_nuts'")
    conn.execute("UPDATE ikonomika.source_state SET last_ok = now() - interval '3 days' WHERE ref = 'gdp_a'")
    probs = checks.freshness(conn, inds)
    assert any("gdp_nuts няма нов период" in p for p in probs)
    assert any("gdp_a не е четен успешно" in p for p in probs)


def bnb_get(today_csv):
    from ingest import bnb
    q3 = (FX / "bnb/q2026-3.csv").read_bytes().decode("utf-8-sig")
    search = (FX / "bnb/search.html").read_bytes()

    def archive(url):
        # the archive's answer for the queried days and codes: the fixture's USD/JPY/IDR columns, and the
        # day's CSV for 25.09 for every code
        q = parse_qs(urlsplit(url).query)
        start = dt.date(int(q["periodStartYear"][0]), int(q["periodStartMonths"][0]), int(q["periodStartDays"][0]))
        codes, day = q["valutes"], {d["code"]: v for _, d, _, v in bnb.parse(today_csv)}
        lines = q3.splitlines()
        out = lines[:2]
        for ln in lines[2:]:
            f = [x.strip() for x in ln.split(",") if x.strip()]
            if f and f[0] != "25.09.2026" and dt.datetime.strptime(f[0], "%d.%m.%Y").date() >= start:
                groups = {f[i]: f[i:i + 3] for i in range(1, len(f), 3)}
                out.append(", ".join([f[0]] + [x for c in codes if c in groups for x in groups[c]]) + ", ")
        out.append(", ".join(["25.09.2026"] + [x for c in codes for x in (c, str(day[c]), "")]) + ", ")
        return (BOM + "\n".join(out)).encode()

    def get(url):
        if url == bnb.SEARCH_URL:
            return search
        if url == bnb.TODAY_URL:
            return today_csv
        return archive(url)
    return get


def test_bnb_step_reads_whole_quarters_and_reconciles(conn, run):
    from ingest import bnb
    today_csv = (FX / "bnb/today.csv").read_bytes()
    today = dt.date(2026, 9, 25)
    stats = bnb.load(conn, run, get=bnb_get(today_csv), today=today)
    assert stats["problems"] == [] and stats["windows"] == {"stored": 5}   # 29 currencies in 5 queries of 6
    # the daily read is the whole quarter: the same resource as the full read from 1991
    assert {x[0].split("/")[0] for x in conn.execute(
        "SELECT ref FROM ops.load WHERE tool = 'ikonomika' AND source = 'bnb'")} == {"2026-07-01"}
    build(conn, run)
    assert ("2026-09-25", 1.1403, None) in [f for f in conn.execute(
        """SELECT o.period, o.value::float8, o.flag FROM ikonomika.observation o JOIN ikonomika.series s USING (series_id)
           WHERE s.indicator_code = 'ikonomika.fx_eur' AND s.dims->>'code' = 'USD'""")]
    assert bnb.load(conn, run, get=bnb_get(today_csv), today=today)["windows"] == {"unchanged": 5}
    # the archive disagrees with the day's CSV: reported, not silently kept
    bad = today_csv.replace(b"USD,1.1403,0.8770", b"USD,1.1500,0.8696")
    assert bad != today_csv
    get2 = bnb_get(today_csv)
    stats = bnb.load(conn, run, get=lambda url: bad if url == bnb.TODAY_URL else get2(url), today=today)
    assert any("USD 2026-09-25" in p for p in stats["problems"])
    assert conn.execute("SELECT status FROM ikonomika.source_state WHERE source = 'bnb' AND ref = 'fx'").fetchone()[0] == "invalid"


def test_the_bnb_rates_reach_core_in_euro(conn, run):
    """From 2026 the ECB reference rate as the BNB publishes it; before, the rate in leva turned into euro with the
    BNB's own rate of the euro on the same day. core.to_eur then converts with them."""
    from ingest import bnb, store
    store.load_ref(conn, run)       # the Bulgarian names of the currencies
    for name in ("bnb/q2025-4.csv", "bnb/today.csv"):
        got = bnb.parse((FX / name).read_bytes())
        for ind in {x[0] for x in got}:
            store.apply(conn, run, "bnb", ind, f"{name}/{ind}", None, [(d, "BG", str(t), v, None) for i, d, t, v in got if i == ind],
                        now=T0)
    build(conn, run)
    got = conn.execute("SELECT per_eur, source FROM core.fx_rate WHERE currency_code = 'USD' AND day = '2025-10-01'").fetchone()
    assert got == (round(Decimal("1.95583") / Decimal("1.66823"), 10), "bnb-bgn")      # 1.66823 leva for 1 dollar
    assert conn.execute("SELECT per_eur FROM core.fx_rate WHERE currency_code = 'JPY' AND day = '2025-10-01'").fetchone()[0]         == round(Decimal("1.95583") * 100 / Decimal("1.13401"), 10)                   # 1.13401 leva for 100 yen
    assert conn.execute("SELECT per_eur, source FROM core.fx_rate WHERE currency_code = 'USD' AND day = '2026-09-25'").fetchone() \
        == (Decimal("1.1403"), "bnb")
    assert conn.execute("SELECT name_bg FROM core.currency WHERE currency_code = 'JPY'").fetchone()[0] == "Японска йена"
    assert conn.execute("SELECT core.to_eur(1140.30, 'USD', '2026-09-25')").fetchone()[0] == Decimal("1000.00")
    assert conn.execute("SELECT count(*) FROM core.fx_rate WHERE currency_code IN ('EUR', 'BGN')").fetchone()[0] == 0
    assert conn.execute("SELECT core.take_fx_rates()").fetchone()[0] == 0     # nothing new: nothing written


def ecb_fixture(key):
    return (FX / "ecb" / f"{key}.csv").read_bytes()


def test_ecb_step_writes_both_places_checks_and_keeps_what_fails(conn, run, monkeypatch):
    from ingest import ecb, http
    monkeypatch.setattr(ecb, "SERIES", [s for s in ecb.SERIES if s[3] == "housing" and s[0] == "rates"]
                        + [s for s in ecb.SERIES if s[0] == "bank" and s[3] in ("dep_hh", "dep_hh_on")])
    files = {k: ecb_fixture(k) for k in ("MIR.M.BG.B.A2C.AM.R.A.2250.EUR.N", "MIR.M.U2.B.A2C.AM.R.A.2250.EUR.N",
                                         "MIR.M.BG.B.A2C.A.R.A.2250.BGN.N", "MIR.M.BG.B.A2C.F+I+O+P.R+B.A.2250.BGN.N",
                                         "BSI.M.BG.N.A.L20.A.1.U2.2250.Z01.E", "BSI.M.BG.N.A.L21.A.1.U2.2250.Z01.E")}
    # a month before the euro, as the ECB has it for some series: euro loans only, so it must not be kept
    head, first, *rest = files["MIR.M.BG.B.A2C.AM.R.A.2250.EUR.N"].decode("utf-8").splitlines(keepends=True)
    files["MIR.M.BG.B.A2C.AM.R.A.2250.EUR.N"] = (head + first.replace("2026-01", "2025-12").replace(",2.46,", ",4.1,") + first
                                                 + "".join(rest)).encode()

    def get(url):
        flow, key = url.split("/data/")[1].split("?")[0].split("/")
        got = files[f"{flow}.{key}"]
        if isinstance(got, Exception):
            raise got
        return got
    stats = ecb.load(conn, run, get=get)
    assert stats["indicators"] == {"rates": "stored", "bank": "stored"} and stats["problems"] == []
    build(conn, run)
    assert conn.execute("SELECT count(DISTINCT geo) FROM ikonomika.series WHERE indicator_code = 'ikonomika.rates'").fetchone()[0] == 2
    # Bulgaria: in euro from 2026-01; in leva until 2025-12, the published total from 2017-08 and before it the average
    # of its parts weighted by the amounts (from 2007); the euro series' 2025-12 (euro loans only) is dropped
    bg = {p: v for p, v in conn.execute("""SELECT o.period, o.value::float8 FROM ikonomika.observation o JOIN ikonomika.series s
        USING (series_id) WHERE s.indicator_code = 'ikonomika.rates' AND s.geo = 'BG' AND o.superseded_at IS NULL""")}
    assert min(bg) == "2007-01" and bg["2007-01"] == 8.5 and bg["2017-07"] == 3.82
    assert bg["2017-08"] == 3.85                       # published, the average is 3.84
    assert bg["2025-12"] == 2.48 and bg["2026-01"] == 2.46
    first = table_hash(conn)
    assert ecb.load(conn, run, get=get)["indicators"] == {"rates": "unchanged", "bank": "unchanged"}
    parts = files["MIR.M.BG.B.A2C.F+I+O+P.R+B.A.2250.BGN.N"]    # the average far from the published total: rates stay
    files["MIR.M.BG.B.A2C.F+I+O+P.R+B.A.2250.BGN.N"] = re.sub(rb"(A2C,F,R,A,2250,BGN,N,2020-01,)", rb"\g<1>9", parts)
    stats = ecb.load(conn, run, get=get)
    assert stats["indicators"]["rates"] == "invalid" and any("средното по срочност" in p for p in stats["problems"])
    files["MIR.M.BG.B.A2C.F+I+O+P.R+B.A.2250.BGN.N"] = parts
    files["BSI.M.BG.N.A.L21.A.1.U2.2250.Z01.E"] = http.Gone("404")   # a series is gone: bank stays as it was
    stats = ecb.load(conn, run, get=get)
    assert stats["indicators"]["bank"] == "invalid" and stats["indicators"]["rates"] == "unchanged"
    build(conn, run)
    assert table_hash(conn) == first and any("ЕЦБ BSI" in p for p in stats["problems"])


def mf_call():
    import json
    lst = (FX / "mf" / "list.json").read_bytes()
    by_uri = {r["uri"]: (FX / "mf" / f"{r['created_at'][:10]}.json").read_bytes() for r in json.loads(lst)["resources"]}
    calls = []

    def call(method, body):
        calls.append(method)
        return lst if method == "listResources" else by_uri[body["resource_uri"]]
    return call, calls


def test_mf_step_reads_every_forecast_once_and_flags_the_forecast_years(conn, run):
    from ingest import mf
    call, calls = mf_call()
    stats = mf.load(conn, run, call=call, pause=0)
    assert stats["forecasts"] == 13 and stats["problems"] == [] and stats["indicators"] == {"mf_forecast": "stored"}
    assert calls.count("getResourceData") == 13
    build(conn, run)
    q = """SELECT o.value::float8, o.flag FROM ikonomika.observation o JOIN ikonomika.series s USING (series_id)
           WHERE s.indicator_code = 'ikonomika.mf_forecast' AND s.dims->>'vintage' = '2026-04-03'
             AND s.dims->>'item' = 'gdp_growth' AND o.period = %s"""
    assert conn.execute(q, ("2025",)).fetchone() == (3.1, None) and conn.execute(q, ("2026",)).fetchone() == (2.6, "f")
    first = table_hash(conn)
    assert mf.load(conn, run, call=call, pause=0)["indicators"] == {"mf_forecast": "unchanged"}
    build(conn, run)
    assert table_hash(conn) == first


def test_freshness_knows_the_other_sources(conn):
    from ingest import checks, ecb, mf
    probs = checks.freshness(conn, ecb.INDICATORS + mf.INDICATORS)
    assert any("rates още не е четен" in p for p in probs) and any("mf_forecast още не е четен" in p for p in probs)


def test_a_step_publishes_only_when_its_rules_hold(conn, monkeypatch):
    """The step: landing, clean tables, rules, then one publication. A rule that fails keeps the version the pages
    have; the clean tables still move."""
    from ingest import eurostat, run
    gdp, nuts = (FX / "eurostat/gdp_bg_since2023.json").read_bytes(), (FX / "eurostat/gdp_nuts_mio.json").read_bytes()
    get = fake_eurostat(monkeypatch, {"nama_10_gdp": gdp, "nama_10r_3gdp": nuts})
    monkeypatch.setattr(eurostat.http, "get", get)
    assert run.main(["--step", "eurostat"]) == 0
    pub = conn.execute("SELECT max(publication_id) FROM pub.publication WHERE tool = 'ikonomika'").fetchone()[0]
    assert pub and conn.execute("SELECT count(*) FROM pub_ikonomika.series").fetchone()[0] == \
        conn.execute("SELECT count(*) FROM ikonomika.observation WHERE superseded_at IS NULL").fetchone()[0] > 900
    assert conn.execute("SELECT count(*) FROM pub_ikonomika.source_state").fetchone()[0] == 2
    assert run.main(["--step", "eurostat"]) == 0                     # nothing changed but the time of the read
    # a rule that fails: a place of Bulgaria that core.nuts does not know (the build does not mend it)
    conn.execute("""INSERT INTO ikonomika.series (indicator_code, dims, geo) VALUES ('ikonomika.gdp_a', '{}', 'BG999')""")
    conn.execute("UPDATE ikonomika.source_state SET error = 'не бива да стигне до страниците' WHERE ref = 'gdp_a'")
    assert run.main(["--step", "check"]) == 1
    assert conn.execute("SELECT error FROM pub_ikonomika.source_state WHERE ref = 'gdp_a'").fetchone()[0] is None
    assert conn.execute("SELECT count(*) FROM pub.publication WHERE tool = 'ikonomika' AND publication_id > %s", (pub + 1,)).fetchone()[0] == 0
    assert conn.execute("""SELECT ok FROM ops.check_latest WHERE tool = 'ikonomika' AND name = 'bulgarian_places_in_core'""").fetchone()[0] is False


def old_database(conn, raw_dir, files):
    """The old database of its own, as schemas live and old_ops of the test database: its tables and its raw files."""
    from ingest import pipeline
    pipeline.require_test_db(conn)
    conn.execute((Path(__file__).parent.parent / "db" / "legacy" / "0001_init.sql").read_text(encoding="utf-8")
                 .replace("ops.", "old_ops.").replace("SCHEMA IF NOT EXISTS ops", "SCHEMA IF NOT EXISTS old_ops"))
    for i, (source, ref, name, at) in enumerate(files):
        raw = (FX / name).read_bytes()
        path = raw_dir / f"{i}-{Path(name).name}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        conn.execute("INSERT INTO old_ops.raw_file (source, ref, path, sha256, bytes, fetched_at) VALUES (%s,%s,%s,%s,%s,%s)",
                     (source, ref, str(path), hashlib.sha256(raw).hexdigest(), len(raw), at))


def test_replay_builds_from_the_raw_files_and_parity_finds_no_difference(conn, run, tmp_path, dsn, monkeypatch):
    from ingest import eurostat, jsonstat, move, store
    monkeypatch.setattr(eurostat, "indicators", lambda: [
        {"id": "gdp_a", "dataset": "nama_10_gdp", "filters": "na_item=B1GQ&unit=CP_MEUR", "geo": "BG", "stale_days": "400",
         "label": "БВП", "unit_label": "млн. €"},
        {"id": "gdp_nuts", "dataset": "nama_10r_3gdp", "filters": "unit=MIO_EUR", "geo": "NUTS", "stale_days": "730",
         "label": "БВП по области", "unit_label": "млн. €"}])
    old_database(conn, tmp_path / "old", [
        ("eurostat", "gdp_a", "eurostat/gdp_bg_since2023.json", T0),
        ("eurostat", "gdp_nuts", "eurostat/unit_typo.json", T0),                    # refused in the old database too
        ("eurostat", "gdp_nuts", "eurostat/gdp_nuts_mio.json", T0 + dt.timedelta(minutes=1)),
        ("bnb", "today", "bnb/today.csv", T0)])
    # the old database's own tables, as its steps wrote them: what parity compares with
    for ind, name in (("gdp_a", "eurostat/gdp_bg_since2023.json"), ("gdp_nuts", "eurostat/gdp_nuts_mio.json")):
        p = jsonstat.parse((FX / name).read_bytes(), {"unit", "na_item"})
        for dims, geo, t, v, f in p["rows"]:
            conn.execute("INSERT INTO live.series VALUES (%s,%s,%s,%s,%s,%s)",
                         (ind, store.dkey(dims), geo, t, None if v is None else Decimal(str(v)), f))
        for dim, codes in p["labels"].items():
            for c, lab in codes.items():
                conn.execute("INSERT INTO live.dim_label VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (ind, dim, c, lab))
        conn.execute("""INSERT INTO old_ops.source_state (source, ref, last_read, last_ok, status, rows, updated)
                        VALUES ('eurostat', %s, %s, %s, 'ok', %s, %s)""", (ind, T0, T0, len(p["rows"]), p["updated"]))
    conn.execute("""INSERT INTO old_ops.change_log (detected_at, source, ref, field, old, new, cause)
                    VALUES (%s, 'eurostat', 'gdp_nuts', 'rows', '925', '900', 'held')""", (T0,))
    problems = move.replay(conn, run, old_dsn=dsn, old_ops="old_ops")
    assert len(problems) == 1 and "gdp_nuts" in problems[0]                        # the refused answer, as before
    assert run.stats["answers"] == {"eurostat stored": 2, "eurostat invalid": 1}
    # every raw file is registered with the time it was read, and loaded with it
    assert conn.execute("SELECT count(*) FROM ops.fetch WHERE tool = 'ikonomika'").fetchone()[0] == 4
    assert conn.execute("""SELECT min(loaded_at) FROM ops.load WHERE tool = 'ikonomika' AND source = 'eurostat'""").fetchone()[0] == T0
    assert ("gdp_nuts", "rows", "925", "900", "held") in log(conn)
    build(conn, run)
    from ingest import pipeline
    assert pipeline.publish(conn, run, Path(__file__).parent.parent).problems == []
    move.parity(conn, run, old_dsn=dsn, old_ops="old_ops")
    res = {n: (e, a, ok) for n, e, a, ok in conn.execute(
        "SELECT name, expected, actual, ok FROM ops.check_latest WHERE tool = 'ikonomika' AND kind = 'parity'")}
    assert all(ok for _, _, ok in res.values()), res
    assert res["parity_rows"][0] == res["parity_rows"][1] > 925
    # one number changed in the shared database is a difference that parity names
    conn.execute("UPDATE pub_ikonomika.series SET value = value + 1 WHERE indicator = 'gdp_a' AND time = '2024'")
    move.parity(conn, run, old_dsn=dsn, old_ops="old_ops")
    got = conn.execute("""SELECT actual, detail FROM ops.check_latest WHERE tool = 'ikonomika' AND name = 'parity_cells_differ'""").fetchone()
    assert got[0] == 1 and "gdp_a 2024" in got[1]
    # the proof of the move is kept, but it is not a daily alarm of the freshness check
    assert not any("Сверка със старата база" in p for p in pipeline.freshness(conn, "ikonomika"))
