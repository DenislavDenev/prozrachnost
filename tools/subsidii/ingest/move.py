"""The move from the old database `subsidii` (schemas silver, gold, ops: db/legacy) into the shared one.

The data is not copied: the step replay reads every file of the archive again through the same parser. This module
proves that the result is the same as the old database (the step parity), group by group, with every result in
ops.check_result (kind parity). Rows are compared by an order-free fingerprint per group (the count and the sum of
the first 60 bits of the md5 of each row's text), so neither side has to send its 700 000 payment rows anywhere.
The old database is only read.
"""
import os

import psycopg

from . import pipeline

OLD_DSN = os.environ.get("SUBSIDII_OLD_DSN", "dbname=subsidii")

FP = "count(*), coalesce(sum(('x' || substr(md5({row}), 1, 15))::bit(60)::bigint), 0)"

# the ОБЩО rows and the payment rows with their spans and the recipient's four cells, the same text on both sides
OLD_HOLDERS = f"""SELECT h.fy, {FP.format(row="concat_ws('|', h.fy, h.key, h.occ, b.name, b.surname, b.oblast, b.obshtina, h.grp, h.efgz, h.ezfrs, h.nb, h.ezfrs_nb, h.total, h.from_day, h.to_day)")}
  FROM silver.holder h JOIN silver.beneficiary b ON b.id = h.beneficiary_id GROUP BY h.fy"""
NEW_HOLDERS = f"""SELECT h.fy, {FP.format(row="concat_ws('|', h.fy, h.key, h.occ, b.name, b.surname, b.oblast, b.obshtina, h.grp, h.efgz, h.ezfrs, h.nb, h.ezfrs_nb, h.total, h.from_day, h.to_day)")}
  FROM subsidii_src.holder h JOIN subsidii_src.beneficiary b USING (beneficiary_id) GROUP BY h.fy"""
PAY = "concat_ws('|', p.fy, p.key, p.occ, b.name, b.surname, b.oblast, b.obshtina, p.name_variant, p.surname_variant, m.code, m.name, m.objective, p.starts, p.ends, p.efgz, p.ezfrs, p.nb, p.from_day, p.to_day)"
OLD_PAYMENTS = f"""SELECT p.fy, {FP.format(row=PAY)} FROM silver.payment p JOIN silver.beneficiary b ON b.id = p.beneficiary_id
  JOIN silver.measure m ON m.id = p.measure_id GROUP BY p.fy"""
NEW_PAYMENTS = f"""SELECT p.fy, {FP.format(row=PAY)} FROM subsidii_src.payment p JOIN subsidii_src.beneficiary b USING (beneficiary_id)
  JOIN subsidii_src.measure m USING (measure_id) GROUP BY p.fy"""
# a recipient: its four cells, the kind, the search key, the org and the municipality (by its ЕИК, the old key)
BEN = "concat_ws('|', b.name, b.surname, b.oblast, b.obshtina, g.kind, g.name_norm, g.org_id, {mun})"
OLD_BENEFICIARIES = f"""SELECT g.kind, {FP.format(row=BEN.format(mun='g.municipality_id'))} FROM silver.beneficiary b
  JOIN gold.beneficiary g ON g.beneficiary_id = b.id GROUP BY g.kind"""
NEW_BENEFICIARIES = f"""SELECT g.kind, {FP.format(row=BEN.format(mun='m.eik'))} FROM subsidii_src.beneficiary b
  JOIN subsidii.beneficiary g USING (beneficiary_id) LEFT JOIN core.municipality m USING (municipality_code) GROUP BY g.kind"""
SNAP = ("status", "sha256", "bytes", "encoding", "rows_file", "holders", "payments", "total_efgz", "total_ezfrs", "total_nb", "total_all",
        "paid_efgz", "paid_ezfrs", "paid_nb", "block_diffs", "negatives", "rows_added", "rows_removed")
OLD_SNAPSHOTS = f"SELECT fy, day, {', '.join(SNAP)} FROM silver.snapshot"
NEW_SNAPSHOTS = f"SELECT fy, day, {', '.join(SNAP)} FROM subsidii.snapshot"
# the aggregates of every snapshot, keyed by the municipality's ЕИК
OLD_AGG = [("municipality_fy", "SELECT fy, snap_day, municipality_id, kind, recipients, efgz, ezfrs, nb, total FROM gold.municipality_fy"),
           ("payment_agg", """SELECT a.fy, a.snap_day, a.municipality_id, m.code || '|' || m.name || '|' || m.objective, a.kind, a.payments, a.efgz, a.ezfrs, a.nb, a.total
                              FROM gold.payment_agg a JOIN gold.measure m USING (measure_id)"""),
           ("summary", "SELECT fy, snap_day, scope, recipients, payments, efgz, ezfrs, nb, total, top1_n, top1_total FROM gold.summary")]
NEW_AGG = [("municipality_fy", """SELECT f.fy, f.snap_day, m.eik, f.kind, f.recipients, f.efgz, f.ezfrs, f.nb, f.total
                                  FROM subsidii.municipality_fy f JOIN core.municipality m USING (municipality_code)"""),
           ("payment_agg", """SELECT a.fy, a.snap_day, c.eik, m.code || '|' || m.name || '|' || m.objective, a.kind, a.payments, a.efgz, a.ezfrs, a.nb, a.total
                              FROM subsidii.payment_agg a JOIN subsidii.measure m USING (measure_id) JOIN core.municipality c USING (municipality_code)"""),
           ("summary", "SELECT fy, snap_day, scope, recipients, payments, efgz, ezfrs, nb, total, top1_n, top1_total FROM subsidii.summary")]
OLD_OBS = """SELECT indicator, place_id, scope, period_start, published_at, value, value_original, unit_original, status FROM gold.observation"""
NEW_OBS = """SELECT o.indicator_code, m.eik, o.scope, o.period_start, o.published_at, o.value_eur, o.value_original, o.currency, o.status
             FROM subsidii.observation o JOIN core.municipality m USING (municipality_code)"""


def _old(dsn):
    c = psycopg.connect(dsn, autocommit=True)
    c.read_only = True
    return c


def _rows(c, sql, key_len):
    return {tuple(r[:key_len]): tuple(r[key_len:]) for r in c.execute(sql)}


def _compare(old, new):
    """(groups in the old, groups equal in the new, the first keys that differ)."""
    bad = [k for k in old if new.get(k) != old[k]] + [k for k in new if k not in old]
    return len(old), sum(1 for k in old if new.get(k) == old[k]), bad


def parity(conn, r, old_dsn=OLD_DSN):
    rep, problems = {}, []

    def record(name, old, new):
        n, same, bad = _compare(old, new)
        ok = pipeline.record_check(conn, r, name, n, same - len([k for k in bad if k not in old]),
                                   "; ".join(map(str, bad[:20])) or None)
        rep[name] = dict(groups=n, same=same, differ=[str(k) for k in bad[:20]])
        if not ok:
            problems.append(f"{name}: {n} групи в старата база, {same} еднакви; различни: {', '.join(map(str, bad[:10]))}")

    with _old(old_dsn) as o:
        record("parity_snapshots", _rows(o, OLD_SNAPSHOTS, 2), _rows(conn, NEW_SNAPSHOTS, 2))
        spans_old = {("holder",) + k: v for k, v in _rows(o, OLD_HOLDERS, 1).items()}
        spans_old.update({("payment",) + k: v for k, v in _rows(o, OLD_PAYMENTS, 1).items()})
        spans_new = {("holder",) + k: v for k, v in _rows(conn, NEW_HOLDERS, 1).items()}
        spans_new.update({("payment",) + k: v for k, v in _rows(conn, NEW_PAYMENTS, 1).items()})
        record("parity_spans", spans_old, spans_new)
        agg_old, agg_new = {}, {}
        for (name, sql), (_, sql_new) in zip(OLD_AGG, NEW_AGG):
            key_len = 3 if name == "summary" else (5 if name == "payment_agg" else 4)
            agg_old.update({(name,) + k: v for k, v in _rows(o, sql, key_len).items()})
            agg_new.update({(name,) + k: v for k, v in _rows(conn, sql_new, key_len).items()})
        record("parity_aggregates", agg_old, agg_new)
        record("parity_beneficiaries", _rows(o, OLD_BENEFICIARIES, 1), _rows(conn, NEW_BENEFICIARIES, 1))
        record("parity_observations", _rows(o, OLD_OBS, 5), _rows(conn, NEW_OBS, 5))
        rep["old_counts"] = dict(o.execute("""SELECT 'holder', count(*) FROM silver.holder UNION ALL SELECT 'payment', count(*) FROM silver.payment
                                              UNION ALL SELECT 'beneficiary', count(*) FROM silver.beneficiary""").fetchall())
    rep["new_counts"] = dict(conn.execute("""SELECT 'holder', count(*) FROM subsidii_src.holder UNION ALL SELECT 'payment', count(*) FROM subsidii_src.payment
                                             UNION ALL SELECT 'beneficiary', count(*) FROM subsidii_src.beneficiary""").fetchall())
    rep["problems"] = problems
    return rep
