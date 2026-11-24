"""One file of one financial year into the landing zone: the file is staged, checked against itself, and the spans are
moved inside pipeline.load, so the shared ledger (ops.load, ops.hold, ops.change_log) records every file.

A row of subsidii_src lives from the day of the file that first has it to the day of the last file that has it (to_day
NULL while it is in the newest file). A row that leaves and comes back is two spans. Nothing is overwritten, so the state
on any day D is "the rows whose span contains the newest file day <= D".

What pipeline.load compares is the summary of the file (its sha256, counts and sums), one entry per year: the same file
is `unchanged`, a new file is `loaded` and every changed count or sum is logged, and a file much smaller than the one in
force is `held` until a second read of the archive at least 20 hours later shows the same file (`confirmed`).
"""
import csv
import datetime as dt
import time
from decimal import Decimal

from . import archive, config, parse, pipeline

SOURCE = "dfz"
HOLD = dt.timedelta(hours=20)


class Mismatch(Exception):
    """The file does not agree with itself or with the archive: it is not loaded (status invalid)."""


STAGE = ("total", "block", "n", "key", "name", "surname", "grp", "oblast", "obshtina", "o_name", "o_surname", "code", "measure",
         "objective", "starts", "ends", "efgz", "ezfrs", "nb", "ezfrs_nb", "total_amount", "efgz_t", "ezfrs_t", "nb_t")
SUMMARY = ("sha256", "rows", "holders", "payments", "total_efgz", "total_ezfrs", "total_nb", "total_all", "paid_efgz", "paid_ezfrs", "paid_nb",
           "block_diffs", "negatives")


def ensure_year(c, fy):
    s, e = archive.fiscal_year_bounds(fy)
    c.execute("INSERT INTO subsidii.source_year (fy, starts, ends) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (fy, s, e))


def load_units(c):
    """The currency of each year, from db/ref/dfz_units.csv: a year that is not there has no currency and no euro amount."""
    with (config.ROOT / "db" / "ref" / "dfz_units.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            assert r["currency"] in ("BGN", "EUR")
            c.execute("UPDATE subsidii.source_year SET currency = %s, unit_evidence = %s WHERE fy = %s", (r["currency"], r["evidence"], int(r["fy"])))


def previous_day(c, fy, day):
    return c.execute("SELECT max(day) FROM subsidii.snapshot WHERE fy = %s AND status = 'built' AND day < %s", (fy, day)).fetchone()[0]


def newest_built(c, fy):
    return c.execute("SELECT max(day) FROM subsidii.snapshot WHERE fy = %s AND status = 'built'", (fy,)).fetchone()[0]


def summary(stats, sha):
    """The file as pipeline.load compares it: plain text and numbers, so two reads compare exactly."""
    t, p = stats["total"], stats["paid"]

    def cents(v):      # as numeric(18,2) gives it back, so a summary read from the table is the same text
        return str(Decimal(v).quantize(Decimal("0.01")))
    return dict(sha256=sha, rows=stats["rows"], holders=stats["holders"], payments=stats["payments"],
                total_efgz=cents(t["efgz"]), total_ezfrs=cents(t["ezfrs"]), total_nb=cents(t["nb"]), total_all=cents(t["total"]),
                paid_efgz=cents(p["efgz"]), paid_ezfrs=cents(p["ezfrs"]), paid_nb=cents(p["nb"]),
                block_diffs=len(_diff_blocks(stats)), negatives=stats["negatives"])


def stored_summary(c, where, args):
    """The summary of a file already written, from subsidii.snapshot: the same shape as summary()."""
    row = c.execute(f"SELECT fy, {', '.join('rows_file' if k == 'rows' else k for k in SUMMARY)} FROM subsidii.snapshot WHERE {where}", args).fetchone()
    if not row:
        return None
    return {str(row[0]): {k: (str(v) if isinstance(v, Decimal) else v) for k, v in zip(SUMMARY, row[1:])}}


def current(conn, load_id):
    return stored_summary(conn, "load_id = %s", (load_id,)) or {}


def hold_rule(old, rows):
    """hold_when of pipeline.load: a file with clearly fewer recipients, payments or money than the one in force waits for a
    second read. Returns how many recipients count as missing (1 when only the money fell); 0 accepts the file."""
    (fy, new), = rows.items()
    prev = old.get(fy)
    why = reason(prev, new)
    if not why:
        return 0
    return max(prev["holders"] - new["holders"], 1)


def reason(prev, new):
    if not prev:
        return None
    if prev["holders"] and new["holders"] < config.HOLD_ROWS * prev["holders"]:
        return f"получателите паднаха от {prev['holders']} на {new['holders']}"
    if prev["payments"] and new["payments"] < config.HOLD_ROWS * prev["payments"]:
        return f"плащанията паднаха от {prev['payments']} на {new['payments']}"
    if Decimal(prev["total_all"]) and Decimal(new["total_all"]) < config.HOLD_SUM * Decimal(prev["total_all"]):
        return f"общата сума падна от {prev['total_all']} на {new['total_all']}"
    return None


def stage(c, fy, day, raw, archive_rows=None):
    """The lines of the file into subsidii_src.line, checked. Returns the stats; raises parse.ShapeError or Mismatch."""
    ensure_year(c, fy)
    with c.transaction():
        c.execute("TRUNCATE subsidii_src.line")
        tally = parse.Tally()
        cols = ", ".join(STAGE)
        with c.cursor() as cur, cur.copy(f"COPY subsidii_src.line ({cols}) FROM STDIN") as cp:
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
        _check(fy, raw, stats, archive_rows)
    return stats


def apply(c, r, fy, day, raw, sha, path, raw_file_id=None, now=None, archive_rows=None):
    """The file of `day` into the landing zone through pipeline.load. Returns (Loaded, report).
    Raises parse.ShapeError or Mismatch, and then nothing is written."""
    t0 = time.monotonic()
    stats = stage(c, fy, day, raw, archive_rows)
    rows = {str(fy): summary(stats, sha)}
    prev = previous_day(c, fy, day)
    rep = {}

    def write(conn, load_id, _rows):
        conn.execute("SET LOCAL work_mem = '256MB'")
        rep.update(_write(conn, load_id, fy, day, prev, sha, path, len(raw), raw, stats))

    done = pipeline.load(c, r, SOURCE, str(fy), rows, write, raw_file_id=raw_file_id, current=current, now=now, hold=HOLD, hold_when=hold_rule)
    if done.status in ("loaded", "confirmed"):
        rep["secs"] = round(time.monotonic() - t0, 1)
        c.execute("UPDATE subsidii.snapshot SET build_secs = %s, note = %s WHERE fy = %s AND day = %s",
                  (rep["secs"], "confirmed after a second read" if done.status == "confirmed" else None, fy, day))
    elif done.status == "held":
        why = reason(next(iter((current(c, done.load_id) or {}).values()), None), rows[str(fy)]) or "по-малък файл"
        rep.update(fy=fy, day=str(day), note=why)
        # the shared hold says how many rows are missing; the reason of this source is the fall of its counts or money
        c.execute("UPDATE ops.hold SET reason = %s WHERE tool = %s AND source = %s AND ref = %s", (why, r.tool, SOURCE, str(fy)))
    return done, rep


def _check(fy, raw, stats, archive_rows):
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


def _write(c, load_id, fy, day, prev, sha, path, size, raw, stats):
    c.execute("""INSERT INTO subsidii_src.beneficiary (name, surname, oblast, obshtina, first_day, load_id)
                 SELECT DISTINCT o_name, o_surname, oblast, obshtina, %s::date, %s::bigint FROM subsidii_src.line ON CONFLICT DO NOTHING""", (day, load_id))
    c.execute("""UPDATE subsidii_src.line s SET beneficiary_id = b.beneficiary_id FROM subsidii_src.beneficiary b
                 WHERE b.name = s.o_name AND b.surname = s.o_surname AND b.oblast = s.oblast AND b.obshtina = s.obshtina""")
    c.execute("""INSERT INTO subsidii_src.measure (code, name, objective)
                 SELECT DISTINCT code, measure, objective FROM subsidii_src.line WHERE NOT total ON CONFLICT DO NOTHING""")
    c.execute("""UPDATE subsidii_src.line s SET measure_id = m.measure_id FROM subsidii_src.measure m
                 WHERE NOT s.total AND m.code = s.code AND m.name = s.measure AND m.objective = s.objective""")
    c.execute("""UPDATE subsidii_src.line s SET occ = r.rn FROM
                 (SELECT n, row_number() OVER (PARTITION BY total, key, beneficiary_id ORDER BY n) AS rn FROM subsidii_src.line) r WHERE r.n = s.n""")
    bad = c.execute("SELECT count(*) FROM subsidii_src.line WHERE beneficiary_id IS NULL OR occ IS NULL OR (NOT total AND measure_id IS NULL)").fetchone()[0]
    if bad:
        raise Mismatch(f"{bad} реда без получател, мярка или пореден номер")
    if prev is None:
        removed = 0
    else:
        removed = c.execute("""UPDATE subsidii_src.holder h SET to_day = %s WHERE h.fy = %s AND h.to_day IS NULL
                               AND NOT EXISTS (SELECT 1 FROM subsidii_src.line s WHERE s.total AND s.key = h.key AND s.occ = h.occ)""", (prev, fy)).rowcount
        removed += c.execute("""UPDATE subsidii_src.payment p SET to_day = %s WHERE p.fy = %s AND p.to_day IS NULL
                               AND NOT EXISTS (SELECT 1 FROM subsidii_src.line s WHERE NOT s.total AND s.key = p.key AND s.beneficiary_id = p.beneficiary_id AND s.occ = p.occ)""", (prev, fy)).rowcount
    added = c.execute("""INSERT INTO subsidii_src.holder (fy, key, occ, beneficiary_id, grp, efgz, ezfrs, nb, ezfrs_nb, total, from_day, load_id)
                         SELECT %s, s.key, s.occ, s.beneficiary_id, s.grp, s.efgz_t, s.ezfrs_t, s.nb_t, s.ezfrs_nb, s.total_amount, %s, %s
                         FROM subsidii_src.line s WHERE s.total AND NOT EXISTS
                           (SELECT 1 FROM subsidii_src.holder h WHERE h.fy = %s AND h.key = s.key AND h.occ = s.occ AND h.to_day IS NULL)""",
                      (fy, day, load_id, fy)).rowcount
    added += c.execute("""INSERT INTO subsidii_src.payment (fy, key, occ, beneficiary_id, name_variant, surname_variant, measure_id, starts, ends, efgz, ezfrs, nb, from_day, load_id)
                          SELECT %s, s.key, s.occ, s.beneficiary_id, CASE WHEN s.name <> s.o_name THEN s.name END,
                                 CASE WHEN s.surname <> s.o_surname THEN s.surname END, s.measure_id, s.starts, s.ends, s.efgz, s.ezfrs, s.nb, %s, %s
                          FROM subsidii_src.line s WHERE NOT s.total AND NOT EXISTS
                            (SELECT 1 FROM subsidii_src.payment p WHERE p.fy = %s AND p.key = s.key AND p.beneficiary_id = s.beneficiary_id AND p.occ = s.occ AND p.to_day IS NULL)""",
                       (fy, day, load_id, fy)).rowcount
    t, p = stats["total"], stats["paid"]
    c.execute("""INSERT INTO subsidii.snapshot (fy, day, status, sha256, path, bytes, encoding, rows_file, holders, payments,
                    total_efgz, total_ezfrs, total_nb, total_all, paid_efgz, paid_ezfrs, paid_nb, block_diffs, negatives, rows_added, rows_removed, note, load_id)
                 VALUES (%s, %s, 'built', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s)
                 ON CONFLICT (fy, day) DO UPDATE SET status = 'built', sha256 = EXCLUDED.sha256, path = EXCLUDED.path, bytes = EXCLUDED.bytes,
                    encoding = EXCLUDED.encoding, rows_file = EXCLUDED.rows_file, holders = EXCLUDED.holders, payments = EXCLUDED.payments,
                    total_efgz = EXCLUDED.total_efgz, total_ezfrs = EXCLUDED.total_ezfrs, total_nb = EXCLUDED.total_nb, total_all = EXCLUDED.total_all,
                    paid_efgz = EXCLUDED.paid_efgz, paid_ezfrs = EXCLUDED.paid_ezfrs, paid_nb = EXCLUDED.paid_nb, block_diffs = EXCLUDED.block_diffs,
                    negatives = EXCLUDED.negatives, rows_added = EXCLUDED.rows_added, rows_removed = EXCLUDED.rows_removed, note = NULL,
                    load_id = EXCLUDED.load_id, built_at = now()""",
              (fy, day, sha, path, size, parse.decode(raw)[1], stats["rows"], stats["holders"], stats["payments"], t["efgz"], t["ezfrs"], t["nb"], t["total"],
               p["efgz"], p["ezfrs"], p["nb"], len(_diff_blocks(stats)), stats["negatives"], added, removed, load_id))
    c.execute("DELETE FROM subsidii_src.block_diff WHERE fy = %s AND day = %s", (fy, day))
    with c.cursor() as cur:
        cur.executemany("""INSERT INTO subsidii_src.block_diff (fy, day, line_no, beneficiary_id, fund, stated, summed)
                           SELECT %s, %s, %s, beneficiary_id, %s, %s, %s FROM subsidii_src.line WHERE total AND n = %s""",
                        [(fy, day, line_no, fund, stated, summed, line_no) for line_no, fund, stated, summed in stats["diffs"]])
    return dict(fy=fy, day=str(day), rows=stats["rows"], holders=stats["holders"], payments=stats["payments"], added=added, removed=removed,
                block_diffs=len(_diff_blocks(stats)), total=str(t["total"]))
