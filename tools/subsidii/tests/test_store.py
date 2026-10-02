"""Silver: spans, the check of every file against itself, holds, a file that arrives late, a year that leaves. Runs on the
server against subsidii_test."""
import datetime as dt
import hashlib

import pytest

from ingest import archive, build, checks, load, parse
from tests.helpers import archive_dir, count, csv_of, derive, fixture, put, rows_of

D1, D2, D3 = "2026-09-28", "2026-10-05", "2026-10-12"


def span_rows(c, fy, day):
    """The recipients and payments of a snapshot as the spans give them back, by natural keys."""
    day = dt.date.fromisoformat(day) if isinstance(day, str) else day
    day = c.execute("SELECT max(day) FROM silver.snapshot WHERE fy = %s AND status = 'built' AND day <= %s", (fy, day)).fetchone()[0]   # a day between two reads is the earlier read
    holders = c.execute("""SELECT b.name, b.surname, b.oblast, b.obshtina, h.efgz, h.ezfrs, h.nb, h.ezfrs_nb, h.total FROM silver.holder h
                           JOIN silver.beneficiary b ON b.id = h.beneficiary_id
                           WHERE h.fy = %s AND h.from_day <= %s AND (h.to_day IS NULL OR h.to_day >= %s)""", (fy, day, day)).fetchall()
    pays = c.execute("""SELECT b.name, b.surname, b.oblast, b.obshtina, m.code, m.name, m.objective, p.starts, p.ends, p.efgz, p.ezfrs, p.nb
                        FROM silver.payment p JOIN silver.beneficiary b ON b.id = p.beneficiary_id JOIN silver.measure m ON m.id = p.measure_id
                        WHERE p.fy = %s AND p.from_day <= %s AND (p.to_day IS NULL OR p.to_day >= %s)""", (fy, day, day)).fetchall()
    return holders, pays


def file_rows(raw):
    """The same two sets, straight from the file (not through the loader's code)."""
    A = parse.amount
    holders, pays = [], []
    for ln in parse.read_lines(raw):
        f = ln.f
        if ln.total:
            holders.append((f[0], f[1], f[3], f[4], A(f[11]), A(f[13]), A(f[15]), A(f[16]), A(f[17])))
        else:
            pays.append((ln.owner[0], ln.owner[1], ln.owner[2], ln.owner[3], f[5], f[6], f[7], parse.date(f[8]), parse.date(f[9]), A(f[10]), A(f[12]), A(f[14])))
    return holders, pays


def srt(rows):
    return sorted(rows, key=lambda r: tuple("" if v is None else str(v) for v in r))


def test_a_file_is_stored_as_the_file_says(c):
    raw = fixture(2024)
    state, rep = put(c, 2024, D1, raw)
    assert state == "built"
    holders, pays = span_rows(c, 2024, D1)
    want_h, want_p = file_rows(raw)
    assert srt(holders) == srt(want_h) and srt(pays) == srt(want_p)
    row = c.execute("SELECT status, rows_file, holders, payments, block_diffs, encoding, total_all, bytes FROM silver.snapshot WHERE fy = 2024 AND day = %s", (D1,)).fetchone()
    assert row[:6] == ("built", 346, 42, 304, 3, "cp1251") and row[7] == len(raw)
    assert str(row[6]) == str(parse.parse(raw).stats["total"]["total"])
    assert (rep["rows"], rep["added"], rep["removed"]) == (346, 346, 0)


def test_the_year_is_the_financial_year_from_16_10_to_15_10(c):
    put(c, 2025, D1, fixture(2025))
    assert c.execute("SELECT starts, ends FROM silver.fiscal_year WHERE fy = 2025").fetchone() == (dt.date(2024, 10, 16), dt.date(2025, 10, 15))


def test_the_currency_of_a_year_comes_from_the_reference_not_from_the_year(c):
    put(c, 2025, D1, fixture(2025))
    put(c, 2026, D1, _as_year(fixture(2025), 2026))
    load.load_units(c)
    got = dict(c.execute("SELECT fy, currency FROM silver.fiscal_year").fetchall())
    assert got[2025] == "BGN" and got[2026] is None            # FY2026 is a mix of leva and euro: not decided from the year


def _as_year(raw, fy):
    """The file of another year: the same blocks with the dates moved into that financial year."""
    rows = rows_of(raw)
    for r in rows[1:]:
        for i in (parse.START, parse.END):
            if r[i] != "-":
                d, m, y = r[i].split(".")
                r[i] = f"{d}.{m}.{int(y) + (fy - 2025)}"
    return csv_of(rows)


def test_a_second_run_of_the_same_file_changes_nothing(c):
    raw = fixture(2024)
    put(c, 2024, D1, raw)
    before = (count(c, "SELECT count(*) FROM silver.holder"), count(c, "SELECT count(*) FROM silver.payment"), count(c, "SELECT count(*) FROM ops.change_log"))
    state, _ = put(c, 2024, D1, raw)
    assert state == "unchanged"
    assert before == (count(c, "SELECT count(*) FROM silver.holder"), count(c, "SELECT count(*) FROM silver.payment"), count(c, "SELECT count(*) FROM ops.change_log"))


def test_two_builds_from_the_same_files_give_the_same_tables(c):
    def digest():
        h, p = span_rows(c, 2024, D2)
        return hashlib.sha256(repr((srt(h), srt(p))).encode()).hexdigest()
    a, b = fixture(2024), derive(2024, edit=lambda i, r: r.__setitem__(parse.NB, "7.00") if i == 3 and r[parse.NB] not in ("-",) and r[parse.MEASURE] != "ОБЩО" else None)
    put(c, 2024, D1, a)
    put(c, 2024, D2, b)
    first = digest()
    c.execute("TRUNCATE silver.block_diff, silver.payment, silver.holder, silver.beneficiary, silver.measure, silver.snapshot, silver.fiscal_year RESTART IDENTITY CASCADE")
    c.execute("TRUNCATE ops.held, ops.change_log")
    put(c, 2024, D1, a)
    put(c, 2024, D2, b)
    assert digest() == first


def test_a_changed_amount_closes_one_span_and_opens_another_and_the_old_day_stays_readable(c):
    old = fixture(2024)
    put(c, 2024, D1, old)
    new = derive(2024, edit=lambda i, r: r.__setitem__(parse.NB, "1.11") if i == 0 and r[parse.MEASURE].startswith("Държавна помощ") and r[parse.START] == "30.10.2023" else None)
    state, rep = put(c, 2024, D2, new)
    assert state == "built" and rep["added"] == 1 and rep["removed"] == 1
    assert count(c, "SELECT count(*) FROM silver.payment WHERE to_day = %s", dt.date.fromisoformat(D1)) == 1
    h1, p1 = span_rows(c, 2024, D1)
    h2, p2 = span_rows(c, 2024, D2)
    assert srt(p1) == srt(file_rows(old)[1]) and srt(p2) == srt(file_rows(new)[1])      # any day: the file of the newest snapshot not after it
    assert srt(span_rows(c, 2024, "2026-10-03")[1]) == srt(p1)                            # between two reads the first still holds
    assert count(c, "SELECT count(*) FROM silver.payment WHERE fy = 2024") == 304 + 1


def test_a_recipient_that_leaves_and_comes_back_has_two_spans(c):
    put(c, 2024, D1, fixture(2024))
    put(c, 2024, D2, derive(2024, drop={5}))
    state, rep = put(c, 2024, D3, fixture(2024))
    assert state == "built" and rep["added"] > 0
    assert count(c, "SELECT count(*) FROM silver.holder h WHERE fy = 2024 AND to_day IS NOT NULL") == 1
    assert count(c, "SELECT count(*) FROM silver.holder WHERE fy = 2024 AND to_day IS NULL") == 42
    assert count(c, "SELECT count(*) FROM silver.holder WHERE fy = 2024") == 43
    assert len(span_rows(c, 2024, D2)[0]) == 41


def test_two_recipients_with_one_name_in_one_municipality_stay_two(c):
    put(c, 2024, D1, fixture(2024))
    two = c.execute("""SELECT count(*) FROM silver.holder h JOIN silver.beneficiary b ON b.id = h.beneficiary_id
                       WHERE b.name = 'физическо лице №9' GROUP BY b.id""").fetchall()
    assert two == [(2,)]           # one beneficiary (the same four cells), two ОБЩО rows: people are not merged and not counted by name


def test_a_payment_row_listed_twice_is_two_rows_with_two_numbers(c):
    raw = fixture(2024)
    put(c, 2024, D1, raw)
    dup = c.execute("SELECT key, beneficiary_id, count(*), max(occ) FROM silver.payment GROUP BY key, beneficiary_id HAVING count(*) > 1").fetchall()
    assert dup and all(r[2] == r[3] for r in dup)
    assert sum(r[2] - 1 for r in dup) >= 3
    # a third copy appears next week: one new span, the two earlier stay open
    extra = rows_of(raw)
    i = next(i for i in range(1, len(extra)) if extra[i] == extra[i + 1] and extra[i][parse.MEASURE] != "ОБЩО")
    extra.insert(i, list(extra[i]))
    # the ОБЩО row of that block is left as it was: it adds a source difference, which is allowed
    state, rep = put(c, 2024, D2, csv_of(extra))
    assert state == "built" and (rep["added"], rep["removed"]) == (1, 0)


def test_the_name_on_a_payment_row_that_differs_from_its_block_is_kept_and_the_row_stays_in_its_block(c):
    put(c, 2024, D1, fixture(2024))
    rows = c.execute("""SELECT b.name, p.name_variant FROM silver.payment p JOIN silver.beneficiary b ON b.id = p.beneficiary_id
                        WHERE p.name_variant IS NOT NULL""").fetchall()
    agro = [r for r in rows if r[0].startswith("АГРОСЕРВИЗ КОМТРАК")]
    assert agro and all("- " not in r[1] and r[0] != r[1] for r in agro)      # the dash the encoding lost
    assert count(c, "SELECT count(*) FROM silver.beneficiary WHERE name LIKE 'АГРОСЕРВИЗ%%'") == 1


def test_the_recipients_whose_rows_do_not_add_up_are_recorded_as_the_source_has_them(c):
    put(c, 2024, D1, fixture(2024))
    rows = c.execute("SELECT line_no, fund, stated, summed FROM silver.block_diff ORDER BY line_no").fetchall()
    assert [(r[1], str(r[2]), str(r[3])) for r in rows] == [("efgz", "399911.64", "465806.69"), ("efgz", "30245.35", "50750.46"), ("efgz", "2777.24", "3166.94")]
    assert count(c, "SELECT block_diffs FROM silver.snapshot WHERE fy = 2024") == 3
    # the numbers of the source are not corrected: the holder row is the stated one
    assert count(c, "SELECT count(*) FROM silver.holder WHERE efgz = 399911.64") == 1


# ---------- a file that does not agree with itself writes nothing ----------

def test_a_failed_check_writes_nothing(c):
    rows = rows_of(fixture(2024))
    rows[1][parse.TOTAL_T] = "3810450.33"
    state, rep = put(c, 2024, D1, csv_of(rows))
    assert state == "invalid" and "ОБЩО" in rep["error"]
    assert count(c, "SELECT count(*) FROM silver.holder") == 0 and count(c, "SELECT count(*) FROM silver.payment") == 0
    assert c.execute("SELECT status FROM silver.snapshot WHERE fy = 2024").fetchone()[0] == "invalid"
    assert count(c, "SELECT count(*) FROM ops.change_log WHERE cause = 'invalid'") == 1


def test_a_payment_date_outside_the_financial_year_is_not_loaded(c):
    state, rep = put(c, 2024, D1, fixture(2025))       # the file of 2025 under the label 2024
    assert state == "invalid" and "извън финансовата година" in rep["error"]
    assert count(c, "SELECT count(*) FROM silver.payment") == 0


def test_the_count_of_the_archive_must_agree_with_the_rows_read(c):
    state, rep = put(c, 2024, D1, fixture(2024), archive_rows=345)
    assert state == "invalid" and "записан от архива" in rep["error"]
    state, _ = put(c, 2024, D1, fixture(2024), archive_rows=346)
    assert state == "built"


def test_too_many_recipients_that_do_not_add_up_are_not_loaded(c):
    def spoil(i, r):
        if r[parse.MEASURE] == parse.TOTAL_WORD and r[parse.EFGZ_T] != "-" and i % 2 == 0:
            r[parse.EFGZ_T] = str(float(r[parse.EFGZ_T]) + 10)
            r[parse.TOTAL_T] = str(float(r[parse.TOTAL_T]) + 10)
    state, rep = put(c, 2024, D1, derive(2024, edit=spoil))
    assert state == "invalid" and "допустими" in rep["error"]


def test_an_unreadable_file_is_invalid_and_leaves_what_was_there(c):
    put(c, 2024, D1, fixture(2024))
    before = count(c, "SELECT count(*) FROM silver.holder")
    state, rep = put(c, 2024, D2, b"<html>Session expired</html>\n")
    assert state == "invalid" and count(c, "SELECT count(*) FROM silver.holder") == before
    assert c.execute("SELECT status FROM silver.snapshot WHERE fy = 2024 AND day = %s", (D2,)).fetchone()[0] == "invalid"
    assert c.execute("SELECT status FROM silver.snapshot WHERE fy = 2024 AND day = %s", (D1,)).fetchone()[0] == "built"


# ---------- the hold rule: a smaller file waits for a second read ----------

def state_of(day, sha, last_ok):
    return {"seen": {"fy2024": {"sha": sha, "file": "x", "at": day}}, "last_ok": last_ok, "run_at": last_ok, "last": {"2024": {"rows": 0}}}


def test_a_much_smaller_file_is_held_until_the_second_read_a_day_later(c):
    put(c, 2024, D1, fixture(2024))
    small = derive(2024, keep=set(range(10)))
    sha = hashlib.sha256(small).hexdigest()
    t0 = dt.datetime(2026, 10, 5, 0, 40, tzinfo=dt.timezone.utc)
    state, rep = put(c, 2024, D2, small, now=t0)
    assert state == "held" and "получателите паднаха" in rep["note"]
    assert count(c, "SELECT count(*) FROM silver.holder WHERE to_day IS NULL") == 42          # nothing was replaced
    assert c.execute("SELECT status FROM silver.snapshot WHERE fy = 2024 AND day = %s", (D2,)).fetchone()[0] == "held"
    assert count(c, "SELECT count(*) FROM ops.held") == 1 and count(c, "SELECT count(*) FROM ops.change_log WHERE cause = 'held'") == 1
    # the archive read the source again six hours later and has the same file: not yet a day
    same_day = state_of(D2, sha, "2026-10-05T06:40:00Z")
    assert put(c, 2024, D2, small, now=t0 + dt.timedelta(hours=6), st=same_day)[0] == "held"
    # a different file in the meantime does not confirm it
    other = state_of(D2, "f" * 64, "2026-10-06T06:40:00Z")
    assert put(c, 2024, D2, small, now=t0 + dt.timedelta(hours=30), st=other)[0] == "held"
    # the same file, read again a day later: confirmed and loaded
    again = state_of(D2, sha, "2026-10-06T00:41:00Z")
    state, rep = put(c, 2024, D2, small, now=t0 + dt.timedelta(hours=24), st=again)
    assert state == "built"
    assert count(c, "SELECT count(*) FROM silver.holder WHERE fy = 2024 AND to_day IS NULL") == 10
    assert count(c, "SELECT count(*) FROM ops.held") == 0 and count(c, "SELECT count(*) FROM ops.change_log WHERE cause = 'confirmed'") == 1
    assert len(span_rows(c, 2024, D1)[0]) == 42        # the first day is still what it was


def test_a_smaller_total_is_held_too(c):
    put(c, 2024, D1, fixture(2024))
    state, rep = put(c, 2024, D2, derive(2024, drop={0}))          # one recipient of three million: few rows, a third of the money
    assert state == "held" and "общата сума падна" in rep["note"]


def test_a_bigger_file_is_not_held(c):
    put(c, 2024, D1, derive(2024, keep=set(range(10))))
    state, rep = put(c, 2024, D2, fixture(2024))
    assert state == "built" and count(c, "SELECT count(*) FROM ops.held") == 0


# ---------- a file that arrives late rebuilds the year in order ----------

def test_an_older_file_arriving_late_rebuilds_the_year_in_order(c):
    a, b = fixture(2024), derive(2024, drop={2})
    put(c, 2024, D2, b)
    state, rep = put(c, 2024, D1, a)             # the earlier day comes second
    assert state == "rebuilt" and rep["files"] == 2
    assert srt(span_rows(c, 2024, D1)[1]) == srt(file_rows(a)[1])
    assert srt(span_rows(c, 2024, D2)[1]) == srt(file_rows(b)[1])
    assert count(c, "SELECT count(*) FROM silver.snapshot WHERE status = 'built'") == 2


def test_a_file_that_replaces_the_same_day_is_a_rewrite_logged_and_rebuilt(c):
    a = fixture(2024)
    put(c, 2024, D1, a)
    b = derive(2024, drop={2})
    state, rep = put(c, 2024, D1, b)
    assert state == "rebuilt"
    assert count(c, "SELECT count(*) FROM ops.change_log WHERE cause = 'rewritten'") >= 1
    assert srt(span_rows(c, 2024, D1)[1]) == srt(file_rows(b)[1])


# ---------- the years and the archive ----------

def test_a_year_that_is_no_longer_in_the_form_stays_with_its_data(c):
    put(c, 2024, D1, fixture(2024))
    put(c, 2025, D1, fixture(2025))
    st = {"seen": {}, "run_at": "2026-10-19T00:41:00Z", "last_ok": "2026-10-19T00:41:00Z", "last": {"2025": {"rows": 355}, "2026": {"rows": 10}}}
    out = build.sync_years(c, st)
    assert out["gone"] == [2024]
    row = c.execute("SELECT in_form, gone_at FROM silver.fiscal_year WHERE fy = 2024").fetchone()
    assert row == (False, dt.date(2026, 10, 19))
    assert count(c, "SELECT count(*) FROM silver.holder WHERE fy = 2024 AND to_day IS NULL") == 42       # the data is not touched
    assert count(c, "SELECT count(*) FROM ops.change_log WHERE cause = 'gone'") == 1
    assert build.sync_years(c, st)["gone"] == []                                                         # said once


def test_a_new_year_needs_no_change_in_the_code(c, tmp_path):
    files = {(2024, "2026-09-28"): fixture(2024), (2025, "2026-09-28"): fixture(2025)}
    root = archive_dir(tmp_path, files)
    rep = build.catch_up(c, root=root)
    assert rep["built"] == 2 and not rep["problems"]
    files[(2026, "2026-10-19")] = _as_year(fixture(2025), 2026)
    root = archive_dir(tmp_path, files, last_ok="2026-10-19T00:41:00Z", form=(2025, 2026))
    rep = build.catch_up(c, root=root)
    assert rep["built"] == 1 and rep["unchanged"] == 2 and rep["years_gone"] == [2024]
    assert c.execute("SELECT starts, ends, currency FROM silver.fiscal_year WHERE fy = 2026").fetchone() == (dt.date(2025, 10, 16), dt.date(2026, 10, 15), None)
    assert any("нова финансова година 2026" in i and "2024" in i for i in rep["info"])


def test_the_step_reads_every_file_of_the_archive_once(c, tmp_path):
    a, b = fixture(2024), derive(2024, drop={1})
    root = archive_dir(tmp_path, {(2024, "2026-09-28"): a, (2024, "2026-10-05"): b, (2025, "2026-09-28"): fixture(2025)})
    rep = build.catch_up(c, root=root)
    assert (rep["built"], rep["unchanged"]) == (3, 0) and not rep["problems"]
    assert [f["fy"] for f in rep["files"]] == [2024, 2024, 2025]
    assert build.catch_up(c, root=root)["unchanged"] == 3
    assert count(c, "SELECT count(*) FROM ops.raw_file") == 3


def test_a_file_changed_in_the_archive_is_an_error_not_a_quiet_new_answer(c, tmp_path):
    root = archive_dir(tmp_path, {(2024, "2026-09-28"): fixture(2024)})
    f = next((root / "dfz" / "2024").iterdir())
    f.write_bytes(f.read_bytes()[:-1] + b"\n\n")
    rep = build.catch_up(c, root=root)
    assert rep["built"] == 0 and any("не съвпада" in p for p in rep["problems"])


# ---------- freshness and the alert ----------

def test_freshness_is_quiet_when_everything_is_in_order(c, tmp_path):
    root = archive_dir(tmp_path, {(2024, "2026-09-28"): fixture(2024), (2025, "2026-09-28"): fixture(2025)}, last_ok="2026-10-05T00:41:00Z")
    build.catch_up(c, root=root)
    assert checks.freshness(c, today=dt.date(2026, 10, 6), now=dt.datetime(2026, 10, 6, 8, tzinfo=dt.timezone.utc), root=root)   # no gold, no population yet
    from ingest import gold
    assert gold.build_missing(c)["built"] == 2
    c.execute("INSERT INTO silver.population VALUES ('000351580', 'permanent', '2026-09-15', 38123, '/x', %s)", (dt.datetime(2026, 10, 5, tzinfo=dt.timezone.utc),))
    now = dt.datetime(2026, 10, 6, 8, tzinfo=dt.timezone.utc)
    assert checks.freshness(c, today=now.date(), now=now, root=root) == []


@pytest.mark.parametrize("last_ok,expect", [("2026-09-20T00:41:00Z", "архивът не е четен успешно"), ("2026-10-05T00:41:00Z", None)])
def test_an_archive_older_than_eight_days_is_a_problem(c, tmp_path, last_ok, expect):
    root = archive_dir(tmp_path, {(2024, "2026-09-28"): fixture(2024)}, last_ok=last_ok)
    build.catch_up(c, root=root)
    now = dt.datetime(2026, 10, 6, 8, tzinfo=dt.timezone.utc)
    got = checks.freshness(c, today=now.date(), now=now, root=root)
    assert bool([p for p in got if "архивът не е четен успешно" in p]) == bool(expect)


def test_a_problem_file_is_reported_and_an_old_hold_too(c, tmp_path):
    put(c, 2024, D1, fixture(2024))
    t0 = dt.datetime(2026, 10, 5, 0, 40, tzinfo=dt.timezone.utc)
    put(c, 2024, D2, derive(2024, keep=set(range(10))), now=t0)
    put(c, 2025, D2, b"<html></html>\n")
    root = archive_dir(tmp_path, {(2024, D1): fixture(2024)}, last_ok="2026-10-05T00:41:00Z")
    got = checks.freshness(c, today=dt.date(2026, 10, 7), now=t0 + dt.timedelta(days=2), root=root)
    assert any("задържан над ден" in p for p in got) and any("невалиден" in p for p in got)


def test_the_new_financial_year_is_expected_by_the_end_of_december(c, tmp_path):
    root = archive_dir(tmp_path, {(2025, "2026-09-28"): fixture(2025)}, last_ok="2027-01-04T00:41:00Z", form=(2025,))
    build.catch_up(c, root=root)
    now = dt.datetime(2027, 1, 5, 8, tzinfo=dt.timezone.utc)
    assert any("финансова година 2026 още не се вижда" in p for p in checks.freshness(c, today=now.date(), now=now, root=root))
    now = dt.datetime(2026, 12, 20, 8, tzinfo=dt.timezone.utc)
    assert not any("още не се вижда" in p for p in checks.freshness(c, today=now.date(), now=now, root=root))
    assert checks.last_completed_year(dt.date(2026, 10, 15)) == 2025 and checks.last_completed_year(dt.date(2026, 10, 16)) == 2026


def test_a_year_without_a_confirmed_currency_is_a_problem_not_a_guess(c, tmp_path):
    root = archive_dir(tmp_path, {(2026, "2026-10-19"): _as_year(fixture(2025), 2026)}, last_ok="2026-10-19T00:41:00Z", form=(2026,))
    build.catch_up(c, root=root)
    now = dt.datetime(2026, 10, 20, 8, tzinfo=dt.timezone.utc)
    assert any("валутата на справката не е потвърдена" in p for p in checks.freshness(c, today=now.date(), now=now, root=root))
