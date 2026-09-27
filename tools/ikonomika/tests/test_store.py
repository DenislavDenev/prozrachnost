"""The hold rule, the change log and the two steps against a scratch database (STANDARD 1А.4-6, 1Б, 1В).

Runs only when IKONOMIKA_TEST_DSN points at a disposable database (it is wiped):
    IKONOMIKA_TEST_DSN=dbname=ikonomika_test pytest tests/test_store.py
"""
import datetime as dt
import hashlib
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

DSN = os.environ.get("IKONOMIKA_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="IKONOMIKA_TEST_DSN not set")

FX = Path(__file__).parent / "fixtures"
BOM = "\N{ZERO WIDTH NO-BREAK SPACE}"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """The scratch database (tests/conftest.py points every connection at it), wiped."""
    from ingest import db, store
    monkeypatch.setattr(store, "RAW", tmp_path / "raw")
    with db.connect(autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS live, ops CASCADE; DROP TABLE IF EXISTS public.schema_migrations")
        db.migrate(c)
        yield c


def log(conn):
    return conn.execute("SELECT ref, field, old, new, cause FROM ops.change_log ORDER BY id").fetchall()


def rows(*vals):
    return [({"unit": "X"}, "BG", t, v, f) for t, v, f in vals]


def age_hold(conn, days=2):
    conn.execute("UPDATE ops.held SET first_at = now() - make_interval(days => %s)", (days,))


def test_first_read_is_not_a_change_and_the_same_answer_changes_nothing(conn):
    from ingest import store
    three = rows(("2023", 1.5, None), ("2024", 2.5, None), ("2025", 3.5, "p"))
    assert store.apply(conn, "eurostat", "gdp", "gdp", "a", three) == "stored"
    assert log(conn) == []
    assert store.apply(conn, "eurostat", "gdp", "gdp", "a", three) == "unchanged"
    assert log(conn) == []
    assert conn.execute("SELECT count(*) FROM live.series").fetchone()[0] == 3


def test_a_revision_and_a_new_period_are_logged_field_by_field(conn):
    from ingest import store
    store.apply(conn, "eurostat", "gdp", "gdp", "a", rows(("2024", 2.5, None), ("2025", 3.5, "p")))
    assert store.apply(conn, "eurostat", "gdp", "gdp", "b",
                       rows(("2024", 2.5, None), ("2025", 3.7, None), ("2026", 1.0, "p"))) == "stored"
    assert log(conn) == [
        ('gdp/{"unit": "X"}/BG/2025', "value", "3.5", "3.7", "rewritten"),
        ('gdp/{"unit": "X"}/BG/2025', "flag", "p", None, "rewritten"),
        ('gdp/{"unit": "X"}/BG/2026', None, None, "1.0", "new-record"),
    ]


def test_a_smaller_answer_is_held_until_a_second_read_a_day_later_agrees(conn):
    from ingest import store
    three = rows(("2023", 1.5, None), ("2024", 2.5, None), ("2025", 3.5, None))
    two = rows(("2024", 2.5, None), ("2025", 3.5, None))
    store.apply(conn, "eurostat", "gdp", "gdp", "a", three)
    assert store.apply(conn, "eurostat", "gdp", "gdp", "b", two) == "held"
    assert conn.execute("SELECT count(*) FROM live.series").fetchone()[0] == 3          # ours stays
    assert log(conn)[-1] == ("gdp", "rows", "3", "2", "held")
    assert store.apply(conn, "eurostat", "gdp", "gdp", "b", two) == "held"              # the same day: still held
    age_hold(conn)
    other = rows(("2023", 1.5, None), ("2025", 3.5, None))
    assert store.apply(conn, "eurostat", "gdp", "gdp", "c", other) == "held"            # a different answer starts again
    assert conn.execute("SELECT sha256, first_at > now() - interval '1 hour' FROM ops.held").fetchone() == ("c", True)
    age_hold(conn)
    assert store.apply(conn, "eurostat", "gdp", "gdp", "c", other) == "stored"          # a day later it agrees
    assert [r[4] for r in log(conn)[-2:]] == ["confirmed", "removed"]
    assert conn.execute("SELECT count(*) FROM live.series").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM ops.held").fetchone()[0] == 0


def test_when_the_source_comes_back_the_hold_is_dropped(conn):
    from ingest import store
    three = rows(("2023", 1.5, None), ("2024", 2.5, None), ("2025", 3.5, None))
    store.apply(conn, "eurostat", "gdp", "gdp", "a", three)
    store.apply(conn, "eurostat", "gdp", "gdp", "b", three[1:])
    assert store.apply(conn, "eurostat", "gdp", "gdp", "a", three) == "unchanged"
    assert conn.execute("SELECT count(*) FROM ops.held").fetchone()[0] == 0


def test_duplicate_rows_are_refused(conn):
    from ingest import store
    from ingest.jsonstat import ShapeError
    with pytest.raises(ShapeError, match="twice"):
        store.apply(conn, "eurostat", "gdp", "gdp", "a", rows(("2024", 1, None), ("2024", 1, None)))
    assert conn.execute("SELECT count(*) FROM live.series").fetchone()[0] == 0


def table_hash(conn):
    h = hashlib.sha256()
    for r in conn.execute("SELECT indicator, dims::text, geo, time, value::text, flag FROM live.series ORDER BY 1, 2, 3, 4"):
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


def test_eurostat_step_writes_checks_and_keeps_what_fails(conn, monkeypatch):
    from ingest import eurostat, http
    gdp, nuts = (FX / "eurostat/gdp_bg_since2023.json").read_bytes(), (FX / "eurostat/gdp_nuts_mio.json").read_bytes()
    get = fake_eurostat(monkeypatch, {"nama_10_gdp": gdp, "nama_10r_3gdp": nuts})
    stats = eurostat.load(conn, {}, get=get)
    assert stats["indicators"] == {"gdp_a": "stored", "gdp_nuts": "stored"} and stats["problems"] == []
    assert conn.execute("SELECT count(*) FROM live.series WHERE indicator = 'gdp_nuts'").fetchone()[0] == 925
    assert conn.execute("SELECT value::float8, flag FROM live.series WHERE indicator = 'gdp_a' AND time = '2025'").fetchone() == (116018.3, "p")
    first = table_hash(conn)
    assert eurostat.load(conn, {}, get=get)["indicators"] == {"gdp_a": "unchanged", "gdp_nuts": "unchanged"}
    assert table_hash(conn) == first and log(conn) == []                    # idempotent, deterministic
    # a region that no longer adds up: the regions are not written, the rest is
    broken = nuts.replace(b'"0":14440.05', b'"0":15440.05', 1)                # BG in 2000
    assert broken != nuts
    get = fake_eurostat(monkeypatch, {"nama_10_gdp": gdp, "nama_10r_3gdp": broken})
    stats = eurostat.load(conn, {}, get=get)
    assert stats["indicators"]["gdp_nuts"] == "invalid" and "сборът на областите" in stats["problems"][0]
    assert table_hash(conn) == first
    # a renamed dataset (404): reported as gone, the old numbers stay
    get = fake_eurostat(monkeypatch, {"nama_10_gdp": http.Gone("404 nama_10_gdp"), "nama_10r_3gdp": nuts})
    stats = eurostat.load(conn, {}, get=get)
    assert stats["indicators"]["gdp_a"] == "gone" and table_hash(conn) == first
    assert conn.execute("SELECT status FROM ops.source_state WHERE ref = 'gdp_a'").fetchone()[0] == "gone"


def test_freshness_names_what_is_late(conn, monkeypatch):
    from ingest import checks, eurostat
    gdp, nuts = (FX / "eurostat/gdp_bg_since2023.json").read_bytes(), (FX / "eurostat/gdp_nuts_mio.json").read_bytes()
    get = fake_eurostat(monkeypatch, {"nama_10_gdp": gdp, "nama_10r_3gdp": nuts})
    eurostat.load(conn, {}, get=get)
    inds = eurostat.indicators()
    probs = checks.freshness(conn, inds)
    assert probs == ["Икономика: курсовете на БНБ са към никога"]
    conn.execute("UPDATE ops.source_state SET last_new_period = now() - interval '800 days' WHERE ref = 'gdp_nuts'")
    conn.execute("UPDATE ops.source_state SET last_ok = now() - interval '3 days' WHERE ref = 'gdp_a'")
    probs = checks.freshness(conn, inds)
    assert any("gdp_nuts няма нов период" in p for p in probs)
    assert any("gdp_a не е четен успешно" in p for p in probs)


def test_bnb_step_reads_windows_holds_and_reconciles(conn, monkeypatch):
    from ingest import bnb
    today_csv = (FX / "bnb/today.csv").read_bytes()
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

    calls = []

    def get(url):
        calls.append(url)
        if url == bnb.SEARCH_URL:
            return search
        if url == bnb.TODAY_URL:
            return today_csv
        return archive(url)

    today = dt.date(2026, 9, 25)
    stats = bnb.load(conn, {}, get=get, today=today)
    assert stats["problems"] == [] and stats["windows"] == {"stored": 5}   # 29 currencies in 5 queries of 6
    usd = conn.execute("SELECT value::float8 FROM live.series WHERE indicator = 'fx_eur' AND dims->>'code' = 'USD' AND time = '2026-09-25'").fetchone()
    assert usd == (1.1403,)
    assert bnb.load(conn, {}, get=get, today=today)["windows"] == {"unchanged": 5}
    # the archive disagrees with the day's CSV: reported, not silently kept
    bad = today_csv.replace(b"USD,1.1403,0.8770", b"USD,1.1500,0.8696")
    assert bad != today_csv
    get2 = lambda url: bad if url == bnb.TODAY_URL else get(url)
    stats = bnb.load(conn, {}, get=get2, today=today)
    assert any("USD 2026-09-25" in p for p in stats["problems"])
    assert conn.execute("SELECT status FROM ops.source_state WHERE source = 'bnb' AND ref = 'fx'").fetchone()[0] == "invalid"
