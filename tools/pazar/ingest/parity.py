"""The proof of the move: the old database of the tool (schemas silver and gold) against the shared one, compared by
the natural keys, because the numbers of the stores and the products are given again in the shared database.

Every comparison is a result of a rule of kind `parity` in ops.check_result: the old number as expected, the new one
as actual, and in the detail the first keys that differ. The spans are compared per month and chain by their count,
their sums and an order-independent hash of every span with its natural keys; the daily observations per day by the
same kind of hash; the days, the files of the chains, the stores, the products and the fuel prices row by row.
"""
import datetime as dt

import psycopg

from . import pipeline

SPAN_KEY = "c.eik || '|' || st.place_raw || '|' || st.name || '|' || p.code"
SPAN_ROW = (SPAN_KEY + " || '|' || s.from_day || '|' || coalesce(s.to_day::text, '') || '|' || s.retail || '|' || coalesce(s.promo::text, '')"
            " || '|' || coalesce(s.category::text, '') || '|' || s.flags")


def spans(conn, silver, first, last):
    """{(month, ЕИК): (spans, sum retail, sum promo, open, sum of hashes)}"""
    return {(r[0], r[1]): tuple(r[2:]) for r in conn.execute(f"""
        SELECT to_char(s.from_day, 'YYYY-MM'), c.eik, count(*), sum(s.retail), sum(coalesce(s.promo, 0)), count(*) FILTER (WHERE s.to_day IS NULL),
               sum(hashtextextended({SPAN_ROW}, 0))
        FROM {silver}.price_span s JOIN {silver}.store st USING (store_id) JOIN {silver}.product p USING (product_id)
        JOIN {silver}.chain c ON c.chain_id = st.chain_id
        WHERE s.from_day >= date_trunc('month', %s::date) AND s.from_day <= %s GROUP BY 1, 2""", (first, last))}


def days(conn, silver, first, last):
    return {r[0]: tuple(r[1:]) for r in conn.execute(f"""
        SELECT day, status, zip_sha256, files, chains, stores, records, valid, dups, bad, blank, anomalies->'flags'
        FROM {silver}.day WHERE day BETWEEN %s AND %s""", (first, last))}


def chain_days(conn, silver, first, last):
    return {(r[0], r[1]): tuple(r[2:]) for r in conn.execute(f"""
        SELECT cd.day, c.eik, cd.file_name, cd.truncated, cd.records, cd.valid, cd.dups, cd.bad, cd.stores, cd.filed, cd.error, cd.copy_of,
               cd.currency, cd.matched, round(cd.switch_share, 6)
        FROM {silver}.chain_day cd JOIN {silver}.chain c USING (chain_id) WHERE cd.day BETWEEN %s AND %s""", (first, last))}


def observations(conn, gold, first, last):
    """{day: (rows, hash of category_day, rows and hash of municipality_day, rows and hash of promo_day)}"""
    out = {}
    for day, n, h in conn.execute(f"""SELECT day, count(*), sum(hashtextextended(concat_ws('|', scope, key, category, n_prices, n_stores, n_chains,
            min_eur, median_eur, max_eur, promo_prices, promo_share, median_effective_eur, matched, changed, sum_ln), 0))
            FROM {gold}.category_day WHERE day BETWEEN %s AND %s GROUP BY day""", (first, last)):
        out[day] = [n, h, 0, 0, 0, 0]
    for day, n, h in conn.execute(f"""SELECT day, count(*), sum(hashtextextended(concat_ws('|', municipality_id, n_stores, n_chains), 0))
            FROM {gold}.municipality_day WHERE day BETWEEN %s AND %s GROUP BY day""", (first, last)):
        out.setdefault(day, [0, 0, 0, 0, 0, 0])[2:4] = [n, h]
    for day, n, h in conn.execute(f"""SELECT day, count(*), sum(hashtextextended(concat_ws('|', chain_eik, n_promos, n_real, n_none, n_raised,
            n_nohistory, avg_declared, avg_real, window_days), 0)) FROM {gold}.promo_day WHERE day BETWEEN %s AND %s GROUP BY day""", (first, last)):
        out.setdefault(day, [0, 0, 0, 0, 0, 0])[4:6] = [n, h]
    return {k: tuple(v) for k, v in out.items()}


def chains(conn, silver):
    return {r[0]: tuple(r[1:]) for r in conn.execute(f"SELECT eik, eik_valid, name, first_day FROM {silver}.chain")}


def stores(conn, silver):
    return {(r[0], r[1], r[2]): tuple(r[3:]) for r in conn.execute(f"""
        SELECT c.eik, s.place_raw, s.name, s.ekatte, s.district, s.first_day FROM {silver}.store s JOIN {silver}.chain c USING (chain_id)""")}


def products(conn, silver):
    return {(r[0], r[1]): tuple(r[2:]) for r in conn.execute(f"""
        SELECT c.eik, p.code, p.name, p.category, p.first_day FROM {silver}.product p JOIN {silver}.chain c USING (chain_id)""")}


def fuel(conn, silver):
    return {(r[0], r[1], r[2]): (r[3], r[4]) for r in conn.execute(f"SELECT week, geo, fuel, round(with_tax, 4), round(wo_tax, 4) FROM {silver}")}


def differ(old, new, fields=None):
    """(keys that differ, keys only in the old, keys only in the new); `fields` picks the positions compared."""
    pick = (lambda v: v) if fields is None else (lambda v: tuple(v[i] for i in fields))
    both = old.keys() & new.keys()
    return sorted(k for k in both if pick(old[k]) != pick(new[k])), sorted(old.keys() - new.keys()), sorted(new.keys() - old.keys())


def compare(conn, r, old_dsn, first=None, last=None, old=None, schemas=("silver", "gold", "silver.fuel_week")):
    """Every comparison as a rule result. `old` is an open connection to the old database (tests), else `old_dsn`."""
    silver, goldsch, fuelt = schemas
    own = old is None
    old = old or psycopg.connect(old_dsn, autocommit=True)
    try:
        last = last or min(old.execute(f"SELECT max(day) FROM {silver}.day WHERE status = 'built'").fetchone()[0],
                           conn.execute("SELECT max(day) FROM pazar.day WHERE status = 'built'").fetchone()[0] or dt.date.min)
        first = first or old.execute(f"SELECT min(day) FROM {silver}.day").fetchone()[0]
        out = {"first": str(first), "last": str(last), "problems": []}

        def record(name, expected, actual, detail=None):
            ok = pipeline.record_check(conn, r, name, expected, actual, (detail or "")[:2000] or None)
            out[name] = [expected, actual]
            if not ok:
                out["problems"].append(f"{name}: старата база {expected}, общата {actual}" + (f" ({detail[:300]})" if detail else ""))

        def show(keys):
            return "; ".join(str(k) for k in keys[:20])

        # the days and the files of the chains
        o, n = days(old, silver, first, last), days(conn, "pazar", first, last)
        diff, gone, new = differ(o, n)
        record("parity_days", len(o), len(n), show(gone + new))
        record("parity_days_differ", 0, len(diff), show(diff))
        record("parity_valid_rows", sum(v[6] or 0 for v in o.values() if v[0] == "built"), sum(v[6] or 0 for v in n.values() if v[0] == "built"))
        o, n = chain_days(old, silver, first, last), chain_days(conn, "pazar", first, last)
        diff, gone, new = differ(o, n)
        record("parity_chain_days", len(o), len(n), show(gone + new))
        record("parity_chain_days_differ", 0, len(diff), show(diff))
        # the spans, month by month and chain by chain
        o, n = spans(old, silver, first, last), spans(conn, "pazar", first, last)
        diff, gone, new = differ(o, n)
        record("parity_spans", sum(v[0] for v in o.values()), sum(v[0] for v in n.values()), show(gone + new))
        record("parity_spans_retail", sum(v[1] for v in o.values()), sum(v[1] for v in n.values()))
        record("parity_spans_differ", 0, len(diff) + len(gone) + len(new), show(diff + gone + new))
        out["spans_differ"] = [list(k) + [o.get(k), n.get(k)] for k in (diff + gone + new)[:50]]
        # the daily observations
        o, n = observations(old, goldsch, first, last), observations(conn, "pazar", first, last)
        diff, gone, new = differ(o, n)
        record("parity_observations", sum(v[0] for v in o.values()), sum(v[0] for v in n.values()), show(gone + new))
        record("parity_observation_days_differ", 0, len(diff) + len(gone) + len(new), show(diff + gone + new))
        out["observation_days_differ"] = [str(k) for k in diff + gone + new]
        # the registers: chains, stores, products (their ids differ: compared by ЕИК, place and name, code). The last
        # name and the first day depend on the order the days were built in, which in the old database was not the
        # order of the days: they are counted and shown, not judged
        for name, fn, fields in (("chains", chains, (0,)), ("stores", stores, (0, 1)), ("products", products, ())):
            o, n = fn(old, silver), fn(conn, "pazar")
            diff, gone, new = differ(o, n, fields)
            record(f"parity_{name}", len(o), len(n), show(gone + new))
            record(f"parity_{name}_differ", 0, len(diff), show(diff))
            later = differ(o, n)[0]
            out[f"{name}_name_or_first_day_differ"] = len(later)
            out[f"{name}_name_or_first_day_examples"] = [[str(k), o[k], n[k]] for k in later[:10]]
        o, n = fuel(old, fuelt), fuel(conn, "pazar_src.fuel_week f JOIN ops.load l ON l.load_id = f.load_id AND l.is_current")
        diff, gone, new = differ(o, n)
        record("parity_fuel", len(o), len(n), show(gone + new))
        record("parity_fuel_differ", 0, len(diff), show(diff))
        return out
    finally:
        if own:
            old.close()
