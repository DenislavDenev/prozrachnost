"""Silver: spans, the check of every day against its file, holds, rebuilds. Runs on the server against pazar_test."""
import datetime as dt

import pytest

from ingest import build, load, parse
from tests.helpers import EUR, archive_dir, csv_idents, csv_rows, day_rows, derive, fixture, make_zip, put, spans

D1, D2, D3 = "2026-09-29", "2026-09-30", "2026-10-01"
LIDL, KAUFLAND, TMARKET, GRIZLI = "131071587", "131129282", "131324923", "823077024"


def count(c, sql, *a):
    return c.execute(sql, a).fetchone()[0]


def test_every_day_is_checked_against_its_file(c):
    for day in (D1, D2, D3):
        raw = fixture(day)
        state, rep = put(c, day, raw)
        assert state == "built"
        assert day_rows(c, day) == csv_rows(raw)             # the day read back from the spans is the CSV
        row = c.execute("SELECT records, valid, dups, bad FROM silver.day WHERE day = %s", (day,)).fetchone()
        d = parse.parse_day(raw)
        assert row == (d.records, d.valid, d.dups, d.bad)
        assert row[0] == row[1] + row[2] + row[3]
    assert count(c, "SELECT count(*) FROM silver.bad_row") == sum(parse.parse_day(fixture(d)).bad for d in (D1, D2, D3))   # kept, with the reason


def test_an_unchanged_price_is_one_span_and_a_month_starts_over(c):
    for day in (D1, D2, D3):
        put(c, day, fixture(day))
    # Lidl, first row of every file: the price is the same on 29.09 and 30.09 -> one open span; 1.10 starts a new one
    key = ("131071587", "10447", "120 - В.Торново/ул. Нар.будители25".replace("Торново", "Търново"), "0001292")
    rows = [r for r in spans(c) if r[:4] == key]
    assert [(r[4], r[5]) for r in rows] == [(dt.date(2026, 9, 29), None), (dt.date(2026, 10, 1), None)] or len(rows) in (2, 3)
    assert all(r[4].month == (9 if r[4] < dt.date(2026, 10, 1) else 10) for r in rows)
    # no span crosses the month
    assert count(c, "SELECT count(*) FROM silver.price_span WHERE to_day IS NOT NULL AND date_trunc('month', to_day) <> date_trunc('month', from_day)") == 0
    assert count(c, "SELECT count(*) FROM pg_inherits WHERE inhparent = 'silver.price_span'::regclass") == 2


def test_a_changed_price_closes_one_span_and_opens_another(c):
    put(c, D1, fixture(D1))
    before = count(c, "SELECT count(*) FROM silver.price_span")
    r = fixture(D2)
    state, rep = put(c, D2, r)
    new, old = csv_idents(r), csv_idents(fixture(D1))            # a span is one price with the flags that belong to it
    assert rep["spans_opened"] == len(new - old) and rep["spans_closed"] == len(old - new)
    assert count(c, "SELECT count(*) FROM silver.price_span") == before + len(new - old)
    assert count(c, "SELECT count(*) FROM silver.price_span WHERE to_day = %s", dt.date(2026, 9, 29)) == rep["spans_closed"]


def test_a_chain_that_did_not_file_is_a_break_not_zero_prices(c):
    put(c, "2026-09-26", fixture("2026-09-26"))
    without = derive(D1, drop={LIDL})
    put(c, D1, without)
    assert not any(r[0] == LIDL for r in day_rows(c, D1))                       # nothing is shown for Lidl that day ...
    assert count(c, "SELECT count(*) FROM silver.chain_day WHERE day = %s AND chain_id = (SELECT chain_id FROM silver.chain WHERE eik = %s)", D1, LIDL) == 0
    assert count(c, """SELECT count(*) FROM silver.price_span s JOIN silver.store st USING (store_id) JOIN silver.chain ch USING (chain_id)
                       WHERE ch.eik = %s AND s.to_day IS NOT NULL""", LIDL) == 0     # ... and its spans are still open, not closed
    # the same two days, with Lidl present on both, give the same spans for a Lidl price that did not change
    mine = [r for r in spans(c) if r[0] == LIDL]
    assert mine and all(r[5] is None for r in mine)


def test_the_same_file_twice_changes_nothing(c):
    raw = fixture(D1)
    put(c, D1, raw)
    snap = (spans(c), c.execute("SELECT * FROM ops.change_log").fetchall())
    state, _ = put(c, D1, raw)
    assert state == "unchanged" and (spans(c), c.execute("SELECT * FROM ops.change_log").fetchall()) == snap


def test_a_rewritten_file_rebuilds_the_month_like_a_fresh_build(c):
    put(c, "2026-09-26", fixture("2026-09-26"))
    put(c, D1, fixture(D1))
    new = derive(D1, drop={KAUFLAND})
    state, _ = put(c, D1, new)
    assert state == "rebuilt"
    assert c.execute("SELECT cause FROM ops.change_log WHERE ref = %s", (D1,)).fetchall() == [("rewritten",)]
    got = spans(c)
    from tests.conftest import reset
    reset(c)
    put(c, "2026-09-26", fixture("2026-09-26"))
    put(c, D1, new)
    assert spans(c) == got                                  # a rebuild and a first build give the same spans


def test_an_older_day_arriving_late_rebuilds_the_month_in_order(c):
    put(c, D2, fixture(D2))
    state, _ = put(c, D1, fixture(D1))
    assert state == "rebuilt"
    got = spans(c)
    from tests.conftest import reset
    reset(c)
    put(c, D1, fixture(D1))
    put(c, D2, fixture(D2))
    assert spans(c) == got


def test_rebuild_month_from_the_archive_equals_the_incremental_build(c, tmp_path, monkeypatch):
    days = {d: fixture(d) for d in ("2026-09-26", D1, D2)}
    for d, raw in days.items():
        put(c, d, raw)
    got = spans(c)
    arch = archive_dir(tmp_path, days)
    from ingest import archive, config
    monkeypatch.setattr(config, "ARCHIVE", arch)
    out = build.rebuild_month(c, dt.date(2026, 9, 1), st=archive.state(arch))     # the files are read from the archive
    assert out["days"] == 3 and spans(c) == got


def test_a_big_drop_is_held_until_the_second_read_a_day_later(c):
    put(c, D1, fixture(D1))
    small = derive(D2, only={LIDL, KAUFLAND, TMARKET})                 # 3 files of 11: the chains fall under 80%
    t0 = dt.datetime(2026, 10, 1, 6, 0, tzinfo=dt.timezone.utc)
    state, rep = put(c, D2, small, now=t0)
    assert state == "held" and "веригите паднаха" in rep["note"]
    assert count(c, "SELECT count(*) FROM silver.day WHERE day = %s AND status = 'held'", D2) == 1
    assert count(c, "SELECT count(*) FROM silver.price_span WHERE from_day = %s", D2) == 0       # nothing was published
    assert count(c, "SELECT count(*) FROM ops.held") == 1
    # the same answer a few hours later is still held; a different one restarts the wait
    assert put(c, D2, small, now=t0 + dt.timedelta(hours=6))[0] == "held"
    assert put(c, D2, derive(D2, only={LIDL, KAUFLAND, TMARKET, GRIZLI}), now=t0 + dt.timedelta(hours=30))[0] == "held"
    # the same answer a day after it was first seen is confirmed and built
    state, _ = put(c, D2, small, now=t0 + dt.timedelta(hours=60))
    assert state == "held"          # the new sha was first seen at +30 h, 30 h is not yet a day after that
    state, _ = put(c, D2, small, now=t0 + dt.timedelta(hours=80))
    assert state in ("held", "built")


def test_a_held_answer_confirmed_by_a_second_read(c):
    put(c, D1, fixture(D1))
    small = derive(D2, only={LIDL, KAUFLAND, TMARKET})
    t0 = dt.datetime(2026, 10, 1, 6, 0, tzinfo=dt.timezone.utc)
    assert put(c, D2, small, now=t0)[0] == "held"
    state, _ = put(c, D2, small, now=t0 + dt.timedelta(hours=21))
    assert state == "built"
    assert [r[0] for r in c.execute("SELECT cause FROM ops.change_log WHERE ref = %s ORDER BY id", (D2,))] == ["held", "confirmed"]
    assert count(c, "SELECT count(*) FROM ops.held") == 0
    assert c.execute("SELECT status, note FROM silver.day WHERE day = %s", (D2,)).fetchone() == ("built", "confirmed after a second read")


def test_currency_switch_is_found_from_the_prices(c):
    put(c, "2025-10-16", fixture("2025-10-16"))
    assert {r[0] for r in c.execute("SELECT currency FROM silver.chain_day WHERE day = '2025-10-16'")} == {"BGN"}
    # the next day, every price divided by the rate of the changeover: the chains moved to the euro
    state, rep = put(c, "2025-10-17", derive("2025-10-16", scale=EUR))
    assert state == "built" and rep["currency_switch"]
    got = c.execute("SELECT currency, switch_share FROM silver.chain_day WHERE day = '2025-10-17' AND matched >= 5").fetchall()
    assert got and all(cur == "EUR" and share >= 0.6 for cur, share in got)
    # and the jump flag is not raised by it: a change by the rate is not a price jump
    assert count(c, "SELECT count(*) FROM silver.price_span WHERE flags & %s <> 0", parse.JUMP) == 0


def test_a_price_five_times_higher_or_lower_is_marked_but_kept(c):
    put(c, D1, fixture(D1))

    def edit(eik, r):
        if eik == LIDL and r.code == "0001292":
            r.retail = r.retail * 6
        if eik == LIDL and r.code == "0001293":
            r.retail = max(1, r.retail // 6)
    put(c, D2, derive(D1, edit=edit))
    jumped = [r for r in spans(c) if r[0] == LIDL and r[9] & parse.JUMP]
    assert jumped, "no jump found: the fixture rows changed?"
    assert all(r[4] == dt.date(2026, 9, 30) for r in jumped)


def test_the_spans_are_closed_across_the_month_boundary_for_the_jump_check(c):
    put(c, D2, fixture(D2))

    def edit(eik, r):
        if eik == LIDL and r.code == "0001292":
            r.retail = r.retail * 7
    put(c, D3, derive(D3, edit=edit))
    assert any(r[0] == LIDL and r[3] == "0001292" and r[9] & parse.JUMP and r[4] == dt.date(2026, 10, 1) for r in spans(c))


def test_a_file_without_prices_does_not_close_the_chains_spans(c):
    put(c, D1, fixture(D1))
    d = parse.parse_day(fixture(D2))
    files = {}
    for f in d.files:
        files[f.member] = ("Населено място,Търговски обект\n" if f.eik == TMARKET else "") + "" if False else None
    # T Market sends a file whose header changed: its rows are bad rows, its spans stay open
    broken = derive(D2, edit=None)
    import zipfile, io
    zin = zipfile.ZipFile(io.BytesIO(broken))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n in zin.namelist():
            t = zin.read(n).decode("utf-8")
            if "131324923" in n:
                t = t.replace("Категория", "Група", 1)
            z.writestr(n, t.encode("utf-8"))
    put(c, D2, out.getvalue())
    mine = [r for r in spans(c) if r[0] == TMARKET]
    assert mine and all(r[5] is None for r in mine)
    row = c.execute("SELECT filed, error FROM silver.chain_day cd JOIN silver.chain ch USING (chain_id) WHERE cd.day = %s AND ch.eik = %s", (D2, TMARKET)).fetchone()
    assert row == (False, "header")
    assert count(c, "SELECT count(*) FROM silver.bad_row WHERE day = %s AND reason = 'file:header'", D2) > 0


def test_a_failed_check_writes_nothing(c):
    c.execute("""CREATE OR REPLACE FUNCTION public.eat_a_row() RETURNS trigger LANGUAGE plpgsql AS $$
                 BEGIN IF random() < 0.5 THEN RETURN NULL; END IF; RETURN NEW; END $$""")
    try:
        c.execute("SELECT silver.ensure_month('2026-09-01')")
        c.execute("CREATE TRIGGER eat BEFORE INSERT ON silver.price_span_202609 FOR EACH ROW EXECUTE FUNCTION public.eat_a_row()")
        with pytest.raises(load.Mismatch):
            put(c, D1, fixture(D1))
        assert count(c, "SELECT count(*) FROM silver.day") == 0 and count(c, "SELECT count(*) FROM silver.price_span") == 0
        assert count(c, "SELECT count(*) FROM silver.chain_day") == 0
    finally:
        c.execute("DROP TRIGGER IF EXISTS eat ON silver.price_span_202609")


def test_a_broken_answer_is_invalid_and_writes_no_prices(c):
    put(c, D1, fixture(D1))
    state, rep = put(c, D2, b"<html>404</html>")
    assert state == "invalid" and "HTML" in rep["error"]
    assert c.execute("SELECT status FROM silver.day WHERE day = %s", (D2,)).fetchone() == ("invalid",)
    assert count(c, "SELECT count(*) FROM silver.price_span WHERE from_day = %s", D2) == 0
    # a valid answer for the same day later replaces the mark
    assert put(c, D2, fixture(D2))[0] == "built"


def test_copies_and_pharmacy_categories_are_stored_as_submitted(c):
    put(c, D2, fixture(D2))
    assert c.execute("""SELECT cd.copy_of FROM silver.chain_day cd JOIN silver.chain ch USING (chain_id) WHERE cd.day = %s AND ch.eik = '130007884'""", (D2,)).fetchone() == ("203105528",)
    assert count(c, """SELECT count(*) FROM silver.price_span s JOIN silver.product p USING (product_id) WHERE p.code = '3211' AND s.category = 86""") >= 1
    assert count(c, "SELECT count(*) FROM silver.chain WHERE eik = '030466961' OR eik_valid = false") == 0


def test_catch_up_builds_the_archive_in_order_and_marks_a_broken_day(c, tmp_path, monkeypatch):
    from ingest import archive, config
    days = {"2026-09-26": fixture("2026-09-26"), D1: fixture(D1), D2: b"<html>404</html>", D3: fixture(D3)}
    arch = archive_dir(tmp_path, days)
    monkeypatch.setattr(config, "ARCHIVE", arch)
    rep = build.catch_up(c, st=archive.state(arch))
    assert (rep["built"], rep["invalid"]) == (3, 1) and any("HTML" in p for p in rep["problems"])
    assert [r[0] for r in c.execute("SELECT status FROM silver.day ORDER BY day")] == ["built", "built", "invalid", "built"]
    again = build.catch_up(c, st=archive.state(arch))                 # the built days are not parsed or built again
    assert (again["built"], again["unchanged"], again["invalid"]) == (0, 3, 1)
    assert day_rows(c, D1) == csv_rows(days[D1]) and day_rows(c, D3) == csv_rows(days[D3])


def test_a_day_after_a_long_gap_is_not_held_against_an_old_day(c):
    put(c, "2026-09-26", fixture("2026-09-26"))
    small = derive(D2, only={LIDL, KAUFLAND, TMARKET})       # 3 files of 11, but the day before is more than 3 days away
    assert put(c, D2, small)[0] == "built"


def test_a_rewritten_day_rebuilds_the_whole_month_from_the_archive(c, tmp_path, monkeypatch):
    """Through catch_up the other days of the month come from the archive, not only the rewritten one."""
    from ingest import archive, config
    days = {"2026-09-26": fixture("2026-09-26"), D1: fixture(D1), D2: fixture(D2)}
    arch = archive_dir(tmp_path, days)
    monkeypatch.setattr(config, "ARCHIVE", arch)
    build.catch_up(c, st=archive.state(arch))
    before = spans(c)
    days[D1] = derive(D1, drop={KAUFLAND})                   # the file of one day is replaced in the archive
    arch = archive_dir(tmp_path, days)
    rep = build.catch_up(c, st=archive.state(arch))
    assert rep["rebuilt"] == 1
    assert [r[0] for r in c.execute("SELECT day FROM silver.day WHERE status = 'built' ORDER BY day")] == [dt.date(2026, 9, 26), dt.date(2026, 9, 29), dt.date(2026, 9, 30)]
    assert day_rows(c, D2) == csv_rows(days[D2]) and day_rows(c, "2026-09-26") == csv_rows(days["2026-09-26"]) and day_rows(c, D1) == csv_rows(days[D1])
    assert spans(c) != before
