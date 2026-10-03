"""One file of one financial year into silver: the file is staged, checked against itself, and the spans are moved.

A row of silver lives from the day of the file that first has it to the day of the last file that has it (to_day NULL
while it is in the newest file). A row that leaves and comes back is two spans. Nothing is deleted or rewritten, so the
state on any day D is "the rows whose span contains the newest file day <= D" (gold.recipient, gold.payment_row).
"""
import csv
import datetime as dt
import time

from . import archive, config, parse


class Mismatch(Exception):
    """The file does not agree with itself or with the archive: it is not published (status invalid)."""


class Held(Exception):
    """The file is much smaller than the previous one of the year: it waits for a second read (status held)."""

    def __init__(self, reason, stats):
        super().__init__(reason)
        self.reason, self.stats = reason, stats


STAGE = ("total", "block", "n", "key", "name", "surname", "grp", "oblast", "obshtina", "o_name", "o_surname", "code", "measure",
         "objective", "starts", "ends", "efgz", "ezfrs", "nb", "ezfrs_nb", "total_amount", "efgz_t", "ezfrs_t", "nb_t")


def log(c, ref, field, old, new, cause):
    c.execute("INSERT INTO ops.change_log (source, ref, field, old, new, cause) VALUES ('dfz', %s, %s, %s, %s, %s)",
              (str(ref), field, None if old is None else str(old), None if new is None else str(new), cause))


def raw_file(c, fy, day, sha, path, size):
    c.execute("INSERT INTO ops.raw_file (source, ref, path, sha256, bytes) VALUES ('dfz', %s, %s, %s, %s) ON CONFLICT DO NOTHING",
              (f"{fy}/{day}", path, sha, size))


def ensure_year(c, fy):
    s, e = archive.fiscal_year_bounds(fy)
    c.execute("INSERT INTO silver.fiscal_year (fy, starts, ends) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (fy, s, e))


def load_units(c):
    """The currency of each year, from db/ref/dfz_units.csv: a year that is not there has no currency and no euro amount."""
    with (config.ROOT / "db" / "ref" / "dfz_units.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            assert r["currency"] in ("BGN", "EUR")
            c.execute("UPDATE silver.fiscal_year SET currency = %s, unit_evidence = %s WHERE fy = %s", (r["currency"], r["evidence"], int(r["fy"])))


def previous_day(c, fy, day):
    return c.execute("SELECT max(day) FROM silver.snapshot WHERE fy = %s AND status = 'built' AND day < %s", (fy, day)).fetchone()[0]


def newest_built(c, fy):
    return c.execute("SELECT max(day) FROM silver.snapshot WHERE fy = %s AND status = 'built'", (fy,)).fetchone()[0]


def apply(c, fy, day, raw, sha, path, decide=None, archive_rows=None, note=None):
    """The file of `day` into silver, in one transaction. `decide(stats, previous)` may return a reason to hold the file;
    `archive_rows` is the count the archive recorded for this file (state/dfz.json), when it is the newest.
    Returns the report. Raises parse.ShapeError, Mismatch or Held, and then nothing is written."""
    t0 = time.monotonic()
    ensure_year(c, fy)
    size = len(raw)
    with c.transaction():
        c.execute("TRUNCATE stage.line")
        tally = parse.Tally()
        cols = ", ".join(STAGE)
        with c.cursor() as cur, cur.copy(f"COPY stage.line ({cols}) FROM STDIN") as cp:
            for ln in parse.read_lines(raw):
                tally.add(ln)
                f = ln.f
                a = parse.amount
                if ln.total:
                    row = (True, ln.block, ln.n, ln.key, f[parse.NAME], f[parse.SURNAME], f[parse.GROUP], f[parse.OBLAST], f[parse.OBSHTINA],
                           ln.owner[0], ln.owner[1], None, None, None, None, None, None, None, None,
                           a(f[parse.EZFRS_NB_T]), a(f[parse.TOTAL_T]), a(f[parse.EFGZ_T]), a(f[parse.EZFRS_T]), a(f[parse.NB_T]))
                else:
                    row = (False, ln.block, ln.n, ln.key, f[parse.NAME], f[parse.SURNAME], f[parse.GROUP], f[parse.OBLAST], f[parse.OBSHTINA],
                           ln.owner[0], ln.owner[1], f[parse.CODE], f[parse.MEASURE], f[parse.OBJECTIVE], parse.date(f[parse.START]),
                           parse.date(f[parse.END]), a(f[parse.EFGZ]), a(f[parse.EZFRS]), a(f[parse.NB]), None, None, None, None, None)
                cp.write_row(row)
        stats = tally.result()
        _check(c, fy, day, raw, stats, archive_rows)
        prev = previous_day(c, fy, day)
        if decide:
            why = decide(stats, c.execute("SELECT holders, payments, total_all FROM silver.snapshot WHERE fy = %s AND day = %s", (fy, prev)).fetchone() if prev else None)
            if why:
                raise Held(why, stats)
        c.execute("SET LOCAL work_mem = '256MB'")
        rep = _write(c, fy, day, prev, sha, path, size, raw, stats, note)
    rep["secs"] = round(time.monotonic() - t0, 1)
    c.execute("UPDATE silver.snapshot SET build_secs = %s WHERE fy = %s AND day = %s", (rep["secs"], fy, day))
    return rep


def _check(c, fy, day, raw, stats, archive_rows):
    s, e = archive.fiscal_year_bounds(fy)
    if stats["rows"] != parse.newline_rows(raw):
        raise Mismatch(f"редовете не съвпадат с броя на новите редове във файла: {stats['rows']} срещу {parse.newline_rows(raw)}")
    if archive_rows is not None and stats["rows"] != archive_rows:
        raise Mismatch(f"редовете ({stats['rows']}) не съвпадат с броя, записан от архива ({archive_rows})")
    if stats["first_start"] and stats["first_start"] < s or stats["last_end"] and stats["last_end"] > e:
        raise Mismatch(f"датите на плащанията ({stats['first_start']} до {stats['last_end']}) са извън финансовата година {fy} ({s} до {e})")
    if stats["identity_fail"]:
        raise Mismatch(f"{stats['identity_fail']} реда ОБЩО не събират фондовете си (ЕЗФРС + НБ, ЕФГЗ + ЕЗФРС и НБ)")
    limit = max(config.MIN_BLOCK_DIFF, int(config.MAX_BLOCK_DIFF * stats["holders"]))
    if len(_diff_blocks(stats)) > limit:
        raise Mismatch(f"{len(_diff_blocks(stats))} от {stats['holders']} получатели нямат сбор на плащанията, равен на реда ОБЩО (допустими {limit})")


def _diff_blocks(stats):
    return {d[0] for d in stats["diffs"]}


def _write(c, fy, day, prev, sha, path, size, raw, stats, note):
    c.execute("""INSERT INTO silver.beneficiary (name, surname, oblast, obshtina, first_day)
                 SELECT DISTINCT o_name, o_surname, oblast, obshtina, %s FROM stage.line ON CONFLICT DO NOTHING""", (day,))
    c.execute("""UPDATE stage.line s SET beneficiary_id = b.id FROM silver.beneficiary b
                 WHERE b.name = s.o_name AND b.surname = s.o_surname AND b.oblast = s.oblast AND b.obshtina = s.obshtina""")
    c.execute("""INSERT INTO silver.measure (code, name, objective)
                 SELECT DISTINCT code, measure, objective FROM stage.line WHERE NOT total ON CONFLICT DO NOTHING""")
    c.execute("""UPDATE stage.line s SET measure_id = m.id FROM silver.measure m
                 WHERE NOT s.total AND m.code = s.code AND m.name = s.measure AND m.objective = s.objective""")
    c.execute("""UPDATE stage.line s SET occ = r.rn FROM
                 (SELECT n, row_number() OVER (PARTITION BY total, key, beneficiary_id ORDER BY n) AS rn FROM stage.line) r WHERE r.n = s.n""")
    bad = c.execute("SELECT count(*) FROM stage.line WHERE beneficiary_id IS NULL OR occ IS NULL OR (NOT total AND measure_id IS NULL)").fetchone()[0]
    if bad:
        raise Mismatch(f"{bad} реда без получател, мярка или пореден номер")
    if prev is None:
        removed = 0
    else:
        removed = c.execute("""UPDATE silver.holder h SET to_day = %s WHERE h.fy = %s AND h.to_day IS NULL
                               AND NOT EXISTS (SELECT 1 FROM stage.line s WHERE s.total AND s.key = h.key AND s.occ = h.occ)""", (prev, fy)).rowcount
        removed += c.execute("""UPDATE silver.payment p SET to_day = %s WHERE p.fy = %s AND p.to_day IS NULL
                               AND NOT EXISTS (SELECT 1 FROM stage.line s WHERE NOT s.total AND s.key = p.key AND s.beneficiary_id = p.beneficiary_id AND s.occ = p.occ)""", (prev, fy)).rowcount
    added = c.execute("""INSERT INTO silver.holder (fy, key, occ, beneficiary_id, grp, efgz, ezfrs, nb, ezfrs_nb, total, from_day)
                         SELECT %s, s.key, s.occ, s.beneficiary_id, s.grp, s.efgz_t, s.ezfrs_t, s.nb_t, s.ezfrs_nb, s.total_amount, %s
                         FROM stage.line s WHERE s.total AND NOT EXISTS
                           (SELECT 1 FROM silver.holder h WHERE h.fy = %s AND h.key = s.key AND h.occ = s.occ AND h.to_day IS NULL)""",
                      (fy, day, fy)).rowcount
    added += c.execute("""INSERT INTO silver.payment (fy, key, occ, beneficiary_id, name_variant, surname_variant, measure_id, starts, ends, efgz, ezfrs, nb, from_day)
                          SELECT %s, s.key, s.occ, s.beneficiary_id, CASE WHEN s.name <> s.o_name THEN s.name END,
                                 CASE WHEN s.surname <> s.o_surname THEN s.surname END, s.measure_id, s.starts, s.ends, s.efgz, s.ezfrs, s.nb, %s
                          FROM stage.line s WHERE NOT s.total AND NOT EXISTS
                            (SELECT 1 FROM silver.payment p WHERE p.fy = %s AND p.key = s.key AND p.beneficiary_id = s.beneficiary_id AND p.occ = s.occ AND p.to_day IS NULL)""",
                       (fy, day, fy)).rowcount
    t, p = stats["total"], stats["paid"]
    c.execute("""INSERT INTO silver.snapshot (fy, day, status, sha256, path, bytes, encoding, rows_file, holders, payments,
                    total_efgz, total_ezfrs, total_nb, total_all, paid_efgz, paid_ezfrs, paid_nb, block_diffs, negatives, rows_added, rows_removed, note)
                 VALUES (%s, %s, 'built', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                 ON CONFLICT (fy, day) DO UPDATE SET status = 'built', sha256 = EXCLUDED.sha256, path = EXCLUDED.path, bytes = EXCLUDED.bytes,
                    encoding = EXCLUDED.encoding, rows_file = EXCLUDED.rows_file, holders = EXCLUDED.holders, payments = EXCLUDED.payments,
                    total_efgz = EXCLUDED.total_efgz, total_ezfrs = EXCLUDED.total_ezfrs, total_nb = EXCLUDED.total_nb, total_all = EXCLUDED.total_all,
                    paid_efgz = EXCLUDED.paid_efgz, paid_ezfrs = EXCLUDED.paid_ezfrs, paid_nb = EXCLUDED.paid_nb, block_diffs = EXCLUDED.block_diffs,
                    negatives = EXCLUDED.negatives, rows_added = EXCLUDED.rows_added, rows_removed = EXCLUDED.rows_removed, note = EXCLUDED.note, built_at = now()""",
              (fy, day, sha, path, size, parse.decode(raw)[1], stats["rows"], stats["holders"], stats["payments"], t["efgz"], t["ezfrs"], t["nb"], t["total"],
               p["efgz"], p["ezfrs"], p["nb"], len(_diff_blocks(stats)), stats["negatives"], added, removed, note))
    c.execute("DELETE FROM silver.block_diff WHERE fy = %s AND day = %s", (fy, day))
    for line_no, fund, stated, summed in stats["diffs"]:
        c.execute("""INSERT INTO silver.block_diff (fy, day, line_no, beneficiary_id, fund, stated, summed)
                     SELECT %s, %s, %s, beneficiary_id, %s, %s, %s FROM stage.line WHERE total AND n = %s""", (fy, day, line_no, fund, stated, summed, line_no))
    raw_file(c, fy, day, sha, path, size)
    return dict(fy=fy, day=str(day), rows=stats["rows"], holders=stats["holders"], payments=stats["payments"], added=added, removed=removed,
                block_diffs=len(_diff_blocks(stats)), total=str(t["total"]))
