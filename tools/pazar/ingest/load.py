"""One day of "Колко струва" into silver.

A price is a span (store, product, from_day, to_day, price): one row lasts while the price stays the same, a change
closes it and opens a new one. A span never crosses a month, so a day is read from one partition and a month can be
rebuilt alone. A chain that filed no usable file on a day does not close its spans: that day is a break, not a
vanished price (silver.chain_day tells who filed).

The day is built in one transaction and checked against the file before it is committed: the rows that the spans
give back for the day must be, chain by chain, as many as the valid rows of the CSV and add up to the same prices.
"""
import collections
import datetime as dt
import json
import time

from . import config, parse

CURRENCY_FROM = dt.date(2026, 1, 1)
IDENT_MASK = 255 & ~(parse.ROUNDED | parse.DECIMAL_COMMA | parse.JUMP)   # flags that make a span a different span
MATCH = """n.store_id = s.store_id AND n.product_id = s.product_id AND n.retail = s.retail
           AND n.promo IS NOT DISTINCT FROM s.promo AND n.category IS NOT DISTINCT FROM s.category
           AND (n.flags & %(mask)s) = (s.flags & %(mask)s)"""
MEMORY_DAYS = 7  # a chain's currency is remembered this long; after a longer break the date decides (EUR from 2026)
MIN_MATCHED = 5  # rows with an earlier price needed to find a change of currency (a few prices at the rate cannot be chance)
BAND = 0.015     # the euro rate is found when a price has moved by 1/1.95583 or by 1.95583 within this tolerance
DOWN = 1 / config.BGN_PER_EUR


class Mismatch(Exception):
    """What the spans give back for the day is not what the file holds: nothing is written."""


def default_currency(day):
    return "EUR" if day >= CURRENCY_FROM else "BGN"


def _ensure_chains(c, files, day):
    ids = {}
    for f in files:
        if not f.eik:
            continue
        row = c.execute(
            """INSERT INTO silver.chain (eik, eik_valid, name, first_day) VALUES (%s, %s, %s, %s)
               ON CONFLICT (eik) DO UPDATE SET name = EXCLUDED.name, eik_valid = EXCLUDED.eik_valid RETURNING chain_id""",
            (f.eik, f.eik_valid, f.chain_name, day)).fetchone()
        ids[f.eik] = row[0]
    return ids


def _ensure_stores(c, chain_id, rows, day):
    have = {(p, n): i for i, p, n in c.execute("SELECT store_id, place_raw, name FROM silver.store WHERE chain_id = %s", (chain_id,))}
    new = {}
    for r in rows:
        k = (r.place_raw, r.store)
        if k not in have and k not in new:
            new[k] = (r.ekatte, r.district)
    if new:
        with c.cursor() as cur:
            cur.executemany("INSERT INTO silver.store (chain_id, place_raw, ekatte, district, name, first_day) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                            [(chain_id, p, e, d, n, day) for (p, n), (e, d) in new.items()])
        have = {(p, n): i for i, p, n in c.execute("SELECT store_id, place_raw, name FROM silver.store WHERE chain_id = %s", (chain_id,))}
    return have


def _ensure_products(c, chain_id, rows, day):
    have = {code: (i, n, cat) for i, code, n, cat in c.execute("SELECT product_id, code, name, category FROM silver.product WHERE chain_id = %s", (chain_id,))}
    seen = {}
    for r in rows:
        seen[r.code] = (r.name, r.category)          # the last row of the file wins
    new = [(chain_id, code, n, cat, day) for code, (n, cat) in seen.items() if code not in have]
    if new:
        with c.cursor() as cur:
            cur.executemany("INSERT INTO silver.product (chain_id, code, name, category, first_day) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING", new)
        have = {code: (i, n, cat) for i, code, n, cat in c.execute("SELECT product_id, code, name, category FROM silver.product WHERE chain_id = %s", (chain_id,))}
    changed = [(n, cat, have[code][0], code, have[code][1], have[code][2]) for code, (n, cat) in seen.items()
               if code in have and (have[code][1] != n or have[code][2] != cat)]
    if changed:
        with c.cursor() as cur:
            cur.executemany("UPDATE silver.product SET name = %s, category = %s WHERE product_id = %s", [(n, cat, i) for n, cat, i, *_ in changed])
            cur.executemany("INSERT INTO ops.change_log (source, ref, field, old, new, cause) VALUES ('kolkostruva', %s, %s, %s, %s, 'rewritten')",
                            [(f"{chain_id}/{code}", "name" if old_n != n else "category", old_n if old_n != n else old_c, n if old_n != n else cat)
                             for n, cat, i, code, old_n, old_c in changed])
    return {code: v[0] for code, v in have.items()}


def build_day(c, day, parsed, sha, zip_path, zip_bytes, note=None, replace=False):
    """Builds the day in silver. `c` is in autocommit mode. Returns the report; raises Mismatch before committing."""
    t0 = time.monotonic()
    marks = {}

    def mark(name):
        marks[name] = round(time.monotonic() - t0 - sum(marks.values()), 1)
    ms = day.replace(day=1)
    prev = day - dt.timedelta(days=1)
    mask = IDENT_MASK
    with c.transaction():
        c.execute("SET LOCAL work_mem = '256MB'")
        c.execute("SET LOCAL synchronous_commit = off")
        if replace:
            for t in ("silver.chain_day", "silver.bad_row", "silver.day"):
                c.execute(f"DELETE FROM {t} WHERE day = %s", (day,))
        elif c.execute("SELECT 1 FROM silver.day WHERE day = %s", (day,)).fetchone():
            raise Mismatch("Денят вече е построен: " + str(day))
        later = c.execute("SELECT max(day) FROM silver.day WHERE status = 'built' AND day > %s AND day >= %s", (day, ms)).fetchone()[0]
        if later and not replace:
            raise Mismatch(f"Има по-нов построен ден в месеца ({later}): месецът се строи наново")
        c.execute("SELECT silver.ensure_month(%s)", (day,))
        c.execute("TRUNCATE stage.day_row, stage.closed")
        chain_ids = _ensure_chains(c, parsed.files, day)
        filing = {f.eik: f for f in parsed.files if f.eik and f.rows and not f.error}
        expect = {}
        anomalies = collections.Counter()
        n_stores = 0
        for eik, f in filing.items():
            cid = chain_ids[eik]
            stores = _ensure_stores(c, cid, f.rows, day)
            products = _ensure_products(c, cid, f.rows, day)
            n_stores += len({(r.place_raw, r.store) for r in f.rows})
            tot_r = tot_p = 0
            with c.cursor() as cur, cur.copy("COPY stage.day_row (chain_id, store_id, product_id, retail, promo, category, flags) FROM STDIN") as cp:
                for r in f.rows:
                    cp.write_row((cid, stores[(r.place_raw, r.store)], products[r.code], r.retail, r.promo, r.category, r.flags))
                    tot_r += r.retail
                    tot_p += r.promo or 0
                    for bit, name in parse.FLAG_NAMES.items():
                        if r.flags & bit:
                            anomalies[name] += 1
            expect[cid] = (len(f.rows), tot_r, tot_p)
        filing_ids = sorted(expect)
        mark("stage")
        c.execute("ANALYZE stage.day_row")
        mark("analyze")
        p = dict(d=day, prev=prev, ms=ms, mask=mask, chains=filing_ids)
        # spans of the filing chains whose price is not in today's file end yesterday
        if day == ms:
            pms = (prev.replace(day=1))
            c.execute("""INSERT INTO stage.closed SELECT s.store_id, s.product_id, s.retail FROM silver.price_span s
                         JOIN silver.store st USING (store_id)
                         WHERE s.from_day >= %s AND s.from_day < %s AND coalesce(s.to_day, 'infinity') >= %s AND st.chain_id = ANY(%s)""",
                      (pms, ms, prev, filing_ids))
        closed = c.execute(f"""WITH closed AS (
              UPDATE silver.price_span s SET to_day = %(prev)s FROM silver.store st
              WHERE st.store_id = s.store_id AND st.chain_id = ANY(%(chains)s) AND s.to_day IS NULL
                AND s.from_day >= %(ms)s AND s.from_day < %(d)s
                AND NOT EXISTS (SELECT 1 FROM stage.day_row n WHERE {MATCH})
              RETURNING s.store_id, s.product_id, s.retail)
            INSERT INTO stage.closed SELECT * FROM closed""", p).rowcount
        c.execute("ANALYZE stage.closed")
        mark("close")
        opened = c.execute(f"""WITH jumped AS (
              SELECT DISTINCT n.store_id, n.product_id FROM stage.day_row n JOIN stage.closed k
                ON k.store_id = n.store_id AND k.product_id = n.product_id AND (n.retail >= 5 * k.retail OR 5 * n.retail <= k.retail))
            INSERT INTO silver.price_span (store_id, product_id, from_day, to_day, retail, promo, category, flags)
            SELECT n.store_id, n.product_id, %(d)s, NULL, n.retail, n.promo, n.category,
                   (n.flags | CASE WHEN j.store_id IS NULL THEN 0 ELSE {parse.JUMP} END)::smallint
            FROM stage.day_row n LEFT JOIN jumped j ON j.store_id = n.store_id AND j.product_id = n.product_id
            WHERE NOT EXISTS (SELECT 1 FROM silver.price_span s WHERE s.to_day IS NULL AND s.from_day >= %(ms)s AND s.from_day < %(d)s
                              AND s.store_id = n.store_id AND s.product_id = n.product_id AND {MATCH})""", p).rowcount
        mark("open")
        # currency: how many of the rows that had a price before moved by the rate of the changeover
        cont = dict(c.execute("""SELECT n.chain_id, count(*) FROM stage.day_row n JOIN silver.price_span s
                                 ON s.to_day IS NULL AND s.from_day >= %(ms)s AND s.from_day < %(d)s AND s.store_id = n.store_id
                                    AND s.product_id = n.product_id AND s.retail = n.retail GROUP BY 1""", p).fetchall())
        moved = {cid: (m, dn, up) for cid, m, dn, up in c.execute(
            """SELECT n.chain_id, count(*), count(*) FILTER (WHERE n.retail::numeric / k.retail BETWEEN %s AND %s),
                      count(*) FILTER (WHERE n.retail::numeric / k.retail BETWEEN %s AND %s)
               FROM stage.day_row n JOIN stage.closed k ON k.store_id = n.store_id AND k.product_id = n.product_id AND k.retail <> n.retail
               GROUP BY 1""", (DOWN * (1 - BAND), DOWN * (1 + BAND), config.BGN_PER_EUR * (1 - BAND), config.BGN_PER_EUR * (1 + BAND)))}
        # the currency of the chain's last filing, if it was within a week; after a longer break the date decides
        last_cur = dict(c.execute("""SELECT DISTINCT ON (chain_id) chain_id, currency FROM silver.chain_day
                                     WHERE day < %s AND day >= %s ORDER BY chain_id, day DESC""", (day, day - dt.timedelta(days=MEMORY_DAYS))).fetchall())
        mark("currency")
        # reconcile: what the spans return for the day is what the file holds
        got = {cid: (n, r, pr) for cid, n, r, pr in c.execute(
            """SELECT st.chain_id, count(*), sum(s.retail), coalesce(sum(s.promo), 0) FROM silver.price_span s JOIN silver.store st USING (store_id)
               WHERE s.from_day >= %s AND s.from_day <= %s AND coalesce(s.to_day, 'infinity') >= %s AND st.chain_id = ANY(%s) GROUP BY 1""",
            (ms, day, day, filing_ids))}
        for cid, want in expect.items():
            if got.get(cid) != want:
                raise Mismatch(f"Сверка {day}, верига {cid}: от интервалите {got.get(cid)}, във файла {want}")
        mark("reconcile")
        # chain_day, bad rows, the day
        by_id = {v: k for k, v in chain_ids.items()}
        cur_of = {}
        switched = []
        with c.cursor() as cur:
            for f in parsed.files:
                if not f.eik:
                    continue
                cid = chain_ids[f.eik]
                before = last_cur.get(cid, default_currency(day))
                m_cont = cont.get(cid, 0)
                m_chg, dn, up = moved.get(cid, (0, 0, 0))
                matched = m_cont + m_chg
                share = None
                now_cur = before
                if matched >= MIN_MATCHED:
                    if before == "BGN" and dn / matched >= 0.6:
                        now_cur, share = "EUR", dn / matched
                    elif before == "EUR" and up / matched >= 0.6:
                        now_cur, share = "BGN", up / matched
                if now_cur != before:
                    switched.append((f.chain_name, before, now_cur))
                cur_of[cid] = now_cur
                filed = bool(f.rows) and not f.error
                cur.execute("""INSERT INTO silver.chain_day (day, chain_id, file_name, delimiter, bom, encoding, truncated, records, valid, dups, bad, stores,
                               filed, error, copy_of, currency, matched, switch_share) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (day, cid, f.member, f.delimiter, f.bom, f.encoding, f.truncated, f.records, len(f.rows), f.dups, len(f.bad),
                             len({(r.place_raw, r.store) for r in f.rows}), filed, f.error, f.copy_of, now_cur, matched or None, share))
        bad_n = 0
        with c.cursor() as cur, cur.copy("COPY silver.bad_row (day, chain_id, file_name, line_no, reason, raw) FROM STDIN") as cp:
            for f in parsed.files:
                cid = chain_ids.get(f.eik)
                for line, reason, raw in f.bad:
                    cp.write_row((day, cid, f.member, line, reason, raw))
                    bad_n += 1
        reasons = collections.Counter(b[1] for f in parsed.files for b in f.bad)
        copies = [(f.chain_name, f.copy_of) for f in parsed.files if f.copy_of]
        report = dict(day=str(day), files=len(parsed.files), chains=len(filing), stores=n_stores, records=parsed.records,
                      valid=parsed.valid, dups=parsed.dups, bad=parsed.bad, blank=parsed.blank, spans_opened=opened,
                      spans_closed=closed, anomalies=dict(anomalies), bad_reasons=dict(reasons.most_common(12)),
                      copies=copies, currency_switch=switched, secs=round(time.monotonic() - t0, 1), timings=marks)
        c.execute("""INSERT INTO silver.day (day, status, zip_sha256, zip_path, zip_bytes, files, chains, stores, records, valid, dups, bad, blank,
                     spans_opened, spans_closed, anomalies, note, build_secs) VALUES (%s,'built',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                  (day, sha, zip_path, zip_bytes, len(parsed.files), len(filing), n_stores, parsed.records, parsed.valid, parsed.dups,
                   parsed.bad, parsed.blank, opened, closed, json.dumps({"flags": dict(anomalies), "bad_reasons": dict(reasons), "copies": copies,
                                                                          "currency_switch": switched}, ensure_ascii=False),
                   note, report["secs"]))
        c.execute("TRUNCATE stage.day_row, stage.closed")
    c.execute(f"ANALYZE silver.price_span_{ms:%Y%m}")        # the statistics the later joins (gold) are planned with
    return report
