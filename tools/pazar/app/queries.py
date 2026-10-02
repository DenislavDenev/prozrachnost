"""Every read of the site. One basket definition (`resolve`) feeds the number, the chart, the table and the CSV of a page,
so they always cover the same categories. Days are parameters (`day`, `first`, `last`): "the latest" is just the newest day.
Prices are in euro, as gold writes them; a figure that has no data is None, never 0."""
import datetime as dt
import math
import re
from dataclasses import dataclass
from decimal import Decimal

from ingest import config, db, parse

NATIONAL = ""                       # the key of the country scope in gold.category_day
EXCLUDE = 29                        # retail anomalies (see ingest/parse.py)
MIN_STORES, MIN_CHAINS = 3, 2       # a municipality is on the map only with 3 shops of 2 chains
FLAG_TEXT = {parse.RETAIL_HIGH: "цена над 5 000", parse.PROMO_NOT_BELOW: "промоцията не е под цената",
             parse.CATEGORY_UNLISTED: "категория извън списъка", parse.CONFLICT: "две различни цени за един обект и код в деня",
             parse.JUMP: "скок над 5 пъти спрямо предишната цена", parse.PROMO_ZERO: "промоция 0 (няма промоция)",
             parse.ROUNDED: "повече от 4 знака след точката", parse.DECIMAL_COMMA: "десетична запетая"}
ANOMALY_BITS = (parse.RETAIL_HIGH, parse.PROMO_NOT_BELOW, parse.CATEGORY_UNLISTED, parse.CONFLICT, parse.JUMP)


def q(sql, *args, one=False):
    with db.connect() as c:
        cur = c.execute(sql, args)
        return cur.fetchone() if one else cur.fetchall()


def f(v):
    return None if v is None else float(v)


# ---------- categories and baskets ----------

def categories():
    return [dict(code=r[0], name=r[1], group=r[2], source=r[3]) for r in q("SELECT code, name_bg, group_bg, source_url FROM gold.category ORDER BY code")]


FOOD = ("Хляб и зърнени храни", "Мляко, млечни продукти и яйца", "Животински и растителни масла и мазнини", "Месо и месни продукти",
        "Риба и други морски храни", "Зеленчуци", "Захар и захарни изделия", "Други хранителни продукти", "Плодове",
        "Кафе, чай и какао", "Вода и безалкохолни")


@dataclass
class Basket:
    slug: str
    label: str
    cats: list
    custom: bool = False

    @property
    def param(self):
        return ("c=" + ",".join(map(str, self.cats))) if self.custom else "k=" + self.slug


def groups():
    out = {}
    for c in categories():
        out.setdefault(c["group"], []).append(c["code"])
    return out


def baskets():
    g = groups()
    out = {"hrani": Basket("hrani", "Храни и напитки", [c for k in FOOD for c in g.get(k, [])]),
           "vsichki": Basket("vsichki", "Цялата кошница", sorted(c for v in g.values() for c in v))}
    for i, (name, cats) in enumerate(g.items(), 1):
        out[f"g{i}"] = Basket(f"g{i}", name, cats)
    return out


def resolve(k=None, c=None):
    """A basket from the query: `c=1,6,12` is the visitor's own, `k=` a named one; the food basket by default."""
    allb = baskets()
    if c:
        valid = {x["code"] for x in categories()}
        cats = sorted({int(x) for x in re.findall(r"\d+", c) if int(x) in valid})
        if cats:
            return Basket("moya", "Моята кошница (%d категории)" % len(cats), cats, True)
    return allb.get(k or "hrani", allb["hrani"])


# ---------- days ----------

def latest():
    r = q("SELECT max(day) FROM gold.day", one=True)
    return r[0] if r else None


def day_or_latest(d):
    """`d` as a date if it is a day with data, else the newest day."""
    try:
        day = dt.date.fromisoformat(d) if d else None
    except ValueError:
        day = None
    if day and q("SELECT 1 FROM gold.day WHERE day = %s", day, one=True):
        return day
    return latest()


def built_days():
    return [r[0] for r in q("SELECT day FROM gold.day ORDER BY day")]


def day_summary(day):
    r = q("SELECT chains, stores, valid, bad, dups, records FROM silver.day WHERE day = %s AND status = 'built'", day, one=True)
    if not r:
        return None
    return dict(day=day, chains=r[0], stores=r[1], valid=r[2], bad=r[3], dups=r[4], records=r[5])


# ---------- the basket: cost, index, change ----------

def cost(day, basket, scope="country", key=NATIONAL):
    """The sum of the median prices of the basket's categories on a day, with how many of them had data."""
    r = q("""SELECT count(*), sum(median_eur) FROM gold.category_day WHERE day = %s AND scope = %s AND key = %s AND category = ANY(%s)""",
          day, scope, key, basket.cats, one=True)
    complete = r[0] == len(basket.cats)
    return dict(n=r[0], of=len(basket.cats), value=f(r[1]) if complete else None, complete=complete)    # a partial sum is not the price of the basket


def index_series(basket, first=None, last=None, scope="country", key=NATIONAL):
    """[(day, index)] chained from the price changes of the same shop and product between filings (Jevons), 100 on the
    first day with data. A day without a matching price is a break (no point), not a fall."""
    rows = q("""SELECT day, sum(sum_ln), sum(matched) FROM gold.category_day WHERE scope = %s AND key = %s AND category = ANY(%s)
                  AND day >= coalesce(%s::date, 'epoch') AND day <= coalesce(%s::date, 'infinity') GROUP BY day ORDER BY day""",
             scope, key, basket.cats, first, last)
    out, level = [], 0.0
    for day, s, m in rows:
        if m and m > 0:
            level += float(s) / m
        elif out:
            continue
        out.append((day, 100 * math.exp(level)))
    return out


def change(basket, day, days=30, scope="country", key=NATIONAL):
    """Percent change of the basket over `days` up to `day`, by the same shops and products."""
    first = day - dt.timedelta(days=days)
    r = q("""SELECT sum(sum_ln / matched) FROM (SELECT day, sum(sum_ln) AS sum_ln, sum(matched) AS matched FROM gold.category_day
             WHERE scope = %s AND key = %s AND category = ANY(%s) AND day > %s AND day <= %s GROUP BY day HAVING sum(matched) > 0) t""",
          scope, key, basket.cats, first, day, one=True)
    covered = q("SELECT count(DISTINCT day) FROM gold.category_day WHERE scope = %s AND key = %s AND category = ANY(%s) AND day > %s AND day <= %s AND matched > 0",
                scope, key, basket.cats, first, day, one=True)[0]
    if r[0] is None:
        return None
    return dict(pct=(math.exp(float(r[0])) - 1) * 100, days=days, covered=covered)


def cost_series(basket, first=None, last=None, scope="country", key=NATIONAL):
    rows = q("""SELECT day, sum(median_eur) FROM gold.category_day WHERE scope = %s AND key = %s AND category = ANY(%s)
                  AND day >= coalesce(%s::date, 'epoch') AND day <= coalesce(%s::date, 'infinity') GROUP BY day HAVING count(*) = %s ORDER BY day""",
             scope, key, basket.cats, first, last, len(basket.cats))
    return [(r[0], f(r[1])) for r in rows]


def movers(day, basket, days=30, limit=6):
    """Categories whose price rose and fell most over `days`, by the same shops and products."""
    first = day - dt.timedelta(days=days)
    rows = q("""SELECT d.category, c.name_bg, exp(sum(d.sum_ln / d.matched)) - 1 AS ch, avg(d.matched)
                FROM gold.category_day d JOIN gold.category c ON c.code = d.category
                WHERE d.scope = 'country' AND d.key = '' AND d.category = ANY(%s) AND d.day > %s AND d.day <= %s AND d.matched > 0
                GROUP BY d.category, c.name_bg HAVING avg(d.matched) >= 30 ORDER BY ch DESC""", basket.cats, first, day)
    rows = [dict(code=r[0], name=r[1], pct=float(r[2]) * 100, matched=float(r[3])) for r in rows]
    return rows[:limit], rows[::-1][:limit]


# ---------- the chains ----------

def chain_table(day, basket):
    """One row per chain on a day: its basket cost on the categories every listed chain has, and its size."""
    rows = q("""SELECT d.key, ch.name_bg, d.category, d.median_eur, d.n_prices, d.n_stores, d.promo_prices
                FROM gold.category_day d JOIN gold.chain ch ON ch.eik = d.key
                WHERE d.day = %s AND d.scope = 'chain' AND d.category = ANY(%s)""", day, basket.cats)
    by = {}
    for eik, name, cat, med, n, stores, promo in rows:
        e = by.setdefault(eik, dict(eik=eik, name=name, cats={}, prices=0, promo=0, stores=0))
        e["cats"][cat] = float(med)
        e["prices"] += n
        e["promo"] += promo
        e["stores"] = max(e["stores"], stores)
    need = max(1, math.ceil(len(basket.cats) * 0.6))
    full = [e for e in by.values() if len(e["cats"]) >= need]
    common = set(basket.cats)
    for e in full:
        common &= set(e["cats"])
    country = {r[0]: float(r[1]) for r in q("SELECT category, median_eur FROM gold.category_day WHERE day = %s AND scope = 'country' AND key = '' AND category = ANY(%s)", day, list(common))} if common else {}
    out = []
    for e in sorted(by.values(), key=lambda e: e["name"]):
        ok = e in full and len(common) >= 5
        v = sum(e["cats"][c] for c in common) if ok else None
        base = sum(country[c] for c in common) if ok and country else None
        out.append(dict(eik=e["eik"], name=e["name"], value=v, vs_country=(v / base - 1) * 100 if v and base else None,
                        n_cats=len(e["cats"]), stores=e["stores"], prices=e["prices"], promo_share=e["promo"] / e["prices"] if e["prices"] else None))
    return dict(rows=out, common=len(common), listed=len(full))


# ---------- the map: municipalities ----------

def municipality_levels(day, basket):
    """Per municipality, the price level against the country on the categories it has (a ratio of sums of medians over
    the same categories), only where there are 3 shops of 2 chains and at least half of the basket has a price."""
    country = {r[0]: float(r[1]) for r in q("SELECT category, median_eur FROM gold.category_day WHERE day = %s AND scope = 'country' AND key = '' AND category = ANY(%s)", day, basket.cats)}
    muni = q("SELECT id, name_bg, oblast FROM gold.municipality ORDER BY name_bg")
    size = {r[0]: (r[1], r[2]) for r in q("SELECT municipality_id, n_stores, n_chains FROM gold.municipality_day WHERE day = %s", day)}
    sums = {}
    for mid, cat, med in q("SELECT key, category, median_eur FROM gold.category_day WHERE day = %s AND scope = 'municipality' AND category = ANY(%s)", day, basket.cats):
        if cat in country:
            a = sums.setdefault(mid, [0.0, 0.0, 0])
            a[0] += float(med)
            a[1] += country[cat]
            a[2] += 1
    need = max(1, math.ceil(len(basket.cats) * 0.5))
    items = []
    for mid, name, oblast in muni:
        stores, chains = size.get(mid, (0, 0))
        s = sums.get(mid)
        v, why = None, None
        if stores < MIN_STORES or chains < MIN_CHAINS:
            why = f"недостатъчно данни: {stores} обекта от {chains} вериги (трябват поне {MIN_STORES} от {MIN_CHAINS})"
        elif not s or s[2] < need:
            why = f"недостатъчно данни: цени за {s[2] if s else 0} от {len(basket.cats)} категории"
        else:
            v = (s[0] / s[1] - 1) * 100
        items.append(dict(code=mid, name=name, oblast=oblast, v=v, stores=stores, chains=chains, cats=s[2] if s else 0, why=why))
    ranked = sorted((i for i in items if i["v"] is not None), key=lambda i: i["v"], reverse=True)
    for n, i in enumerate(ranked, 1):
        i["rank"] = n
    return items


# ---------- products ----------

def search_products(text, chain=None, category=None, limit=100):
    like = "%" + re.sub(r"[%_\\]", lambda m: "\\" + m.group(0), text.strip()) + "%"
    where, args = ["p.name ILIKE %s"], [like]
    if chain:
        where.append("p.chain_eik = %s")
        args.append(chain)
    if category:
        where.append("p.category = %s")
        args.append(category)
    rows = q(f"""SELECT p.product_id, p.chain_eik, ch.name_bg, p.code, p.name, p.category FROM gold.product p JOIN gold.chain ch ON ch.eik = p.chain_eik
                 WHERE {' AND '.join(where)} ORDER BY p.name, ch.name_bg LIMIT {int(limit) + 1}""", *args)
    return [dict(id=r[0], eik=r[1], chain=r[2], code=r[3], name=r[4], category=r[5]) for r in rows[:limit]], len(rows) > limit


def product(eik, code):
    r = q("""SELECT p.product_id, p.chain_eik, ch.name_bg, p.code, p.name, p.category, c.name_bg, p.first_day FROM gold.product p
             JOIN gold.chain ch ON ch.eik = p.chain_eik LEFT JOIN gold.category c ON c.code = p.category WHERE p.chain_eik = %s AND p.code = %s""", eik, code, one=True)
    if not r:
        return None
    return dict(id=r[0], eik=r[1], chain=r[2], code=r[3], name=r[4], category=r[5], category_name=r[6], first_day=r[7])


EXPAND = """FROM silver.price_span s JOIN silver.store st USING (store_id)
            CROSS JOIN LATERAL generate_series(s.from_day, least(coalesce(s.to_day, %(last)s), %(last)s,
                   (date_trunc('month', s.from_day) + interval '1 month' - interval '1 day')::date), interval '1 day') g(d)
            JOIN silver.chain_day cd ON cd.chain_id = st.chain_id AND cd.day = g.d::date AND cd.filed AND cd.copy_of IS NULL
            WHERE s.product_id = %(pid)s AND (s.flags & 29) = 0"""
EUR = "s.retail::numeric / 10000 / CASE WHEN cd.currency = 'BGN' THEN %(rate)s ELSE 1 END"


def product_series(pid, first=None, last=None):
    """[(day, min, median, max, shops)] of the retail price of one product in euro, as submitted."""
    last = last or latest()
    with db.connect() as c:
        rows = c.execute(f"""SELECT g.d::date, min({EUR}), percentile_cont(0.5) WITHIN GROUP (ORDER BY {EUR}), max({EUR}), count(*)
                             {EXPAND} AND g.d::date >= coalesce(%(first)s::date, 'epoch') GROUP BY 1 ORDER BY 1""",
                         dict(pid=pid, last=last, first=first, rate=config.BGN_PER_EUR)).fetchall()
    return [(r[0], f(r[1]), f(r[2]), f(r[3]), r[4]) for r in rows]


def product_stores(pid, day):
    rows = q("""SELECT st.name, st.place_raw, s.retail, s.promo, s.flags, cd.currency, pl.name, pl.municipality_id
                FROM silver.price_span s JOIN silver.store st USING (store_id)
                JOIN silver.chain_day cd ON cd.chain_id = st.chain_id AND cd.day = %s AND cd.filed
                LEFT JOIN gold.place pl ON pl.ekatte = st.ekatte
                WHERE s.product_id = %s AND s.from_day >= %s AND s.from_day <= %s AND coalesce(s.to_day, 'infinity') >= %s
                ORDER BY s.retail, st.name""", day, pid, day.replace(day=1), day, day)
    return [dict(store=r[0], place=r[1], retail=r[2] / 10000, promo=None if r[3] is None else r[3] / 10000, flags=flags_text(r[4]),
                 currency=r[5], place_name=r[6]) for r in rows]


def flags_text(flags):
    return [t for bit, t in FLAG_TEXT.items() if flags & bit]


# ---------- categories ----------

def category(code):
    r = q("SELECT code, name_bg, group_bg, source_url FROM gold.category WHERE code = %s", code, one=True)
    return dict(code=r[0], name=r[1], group=r[2], source=r[3]) if r else None


def category_table(day):
    now = {r[0]: r[1:] for r in q("""SELECT category, median_eur, min_eur, max_eur, n_prices, n_stores, n_chains, promo_share
                                     FROM gold.category_day WHERE day = %s AND scope = 'country' AND key = ''""", day)}
    out = []
    for c in categories():
        n = now.get(c["code"])
        out.append(dict(c, median=f(n[0]) if n else None, min=f(n[1]) if n else None, max=f(n[2]) if n else None,
                        prices=n[3] if n else None, stores=n[4] if n else None, chains=n[5] if n else None, promo_share=f(n[6]) if n else None))
    return out


def category_by(day, code, scope):
    names = {"chain": "SELECT eik, name_bg FROM gold.chain", "oblast": "SELECT DISTINCT nuts3, oblast FROM gold.municipality",
             "municipality": "SELECT id, name_bg FROM gold.municipality"}[scope]
    label = dict(q(names))
    rows = q("""SELECT key, median_eur, min_eur, max_eur, n_prices, n_stores FROM gold.category_day WHERE day = %s AND scope = %s AND category = %s ORDER BY median_eur""", day, scope, code)
    return [dict(key=r[0], name=label.get(r[0], r[0]), median=f(r[1]), min=f(r[2]), max=f(r[3]), prices=r[4], stores=r[5]) for r in rows]


def category_series(code, first=None, last=None, scope="country", key=NATIONAL):
    rows = q("""SELECT day, min_eur, median_eur, max_eur, n_prices FROM gold.category_day WHERE scope = %s AND key = %s AND category = %s
                  AND day >= coalesce(%s::date, 'epoch') AND day <= coalesce(%s::date, 'infinity') ORDER BY day""", scope, key, code, first, last)
    return [(r[0], f(r[1]), f(r[2]), f(r[3]), r[4]) for r in rows]


# ---------- chains ----------

def chains_list(day):
    rows = q("""SELECT ch.eik, ch.name_bg, ch.national_price, ch.eik_valid, min(cd.day), max(cd.day) FILTER (WHERE cd.filed), count(*) FILTER (WHERE cd.filed),
                       count(*) FILTER (WHERE cd.copy_of IS NOT NULL), count(*) FILTER (WHERE cd.truncated), coalesce(max(cd.stores) FILTER (WHERE cd.day = %s), 0)
                FROM gold.chain ch JOIN silver.chain sc ON sc.eik = ch.eik JOIN silver.chain_day cd ON cd.chain_id = sc.chain_id GROUP BY ch.eik, ch.name_bg, ch.national_price, ch.eik_valid
                ORDER BY 10 DESC, 2""", day)
    total = q("SELECT count(*) FROM silver.day WHERE status = 'built'", one=True)[0]
    return [dict(eik=r[0], name=r[1], national=r[2], eik_valid=r[3], first=r[4], last=r[5], days=r[6], copies=r[7], truncated=r[8], stores=r[9],
                 of=total, missed=total - r[6]) for r in rows]


def chain(eik):
    r = q("SELECT eik, name_bg, national_price, eik_valid, first_day FROM gold.chain WHERE eik = %s", eik, one=True)
    return dict(eik=r[0], name=r[1], national=r[2], eik_valid=r[3], first_day=r[4]) if r else None


def chain_days(eik):
    rows = q("""SELECT cd.day, cd.filed, cd.valid, cd.bad, cd.stores, cd.copy_of, cd.truncated, cd.currency, cd.error FROM silver.chain_day cd
                JOIN silver.chain sc USING (chain_id) WHERE sc.eik = %s ORDER BY cd.day""", eik)
    built = [r[0] for r in q("SELECT day FROM silver.day WHERE status = 'built' ORDER BY day")]
    have = {r[0]: r for r in rows}
    out = []
    for d in built:
        r = have.get(d)
        out.append(dict(day=d, filed=bool(r and r[1]), valid=r[2] if r else None, bad=r[3] if r else None, stores=r[4] if r else None,
                        copy_of=r[5] if r else None, truncated=bool(r and r[6]), currency=r[7] if r else None, error=r[8] if r else None, present=bool(r)))
    return out


def chain_stores(eik, day):
    rows = q("""SELECT st.name, st.place_raw, st.ekatte, pl.name, mu.name_bg FROM silver.store st JOIN silver.chain sc USING (chain_id)
                LEFT JOIN gold.place pl ON pl.ekatte = st.ekatte LEFT JOIN gold.municipality mu ON mu.id = pl.municipality_id
                WHERE sc.eik = %s AND EXISTS (SELECT 1 FROM silver.price_span s WHERE s.store_id = st.store_id AND s.from_day >= %s AND s.from_day <= %s
                                              AND coalesce(s.to_day, 'infinity') >= %s) ORDER BY mu.name_bg, st.name""", eik, day.replace(day=1), day, day)
    return [dict(name=r[0], place=r[1], ekatte=r[2], place_name=r[3], municipality=r[4]) for r in rows]


def chain_summary(eik, day):
    r = q("""SELECT sum(n_prices), sum(promo_prices), count(DISTINCT category) FROM gold.category_day WHERE day = %s AND scope = 'chain' AND key = %s""", day, eik, one=True)
    prod = q("SELECT count(*) FROM gold.product WHERE chain_eik = %s", eik, one=True)[0]
    return dict(prices=r[0], promo=r[1], cats=r[2], products=prod, promo_share=(r[1] / r[0]) if r[0] else None)


# ---------- verification and sources ----------

def verification_overview():
    d = q("""SELECT count(*), min(day), max(day), sum(records), sum(valid), sum(dups), sum(bad), sum(blank),
                    count(*) FILTER (WHERE records <> valid + dups + bad) FROM silver.day WHERE status = 'built'""", one=True)
    status = dict(q("SELECT status, count(*) FROM silver.day GROUP BY status"))
    flags = {}
    for (a,) in q("SELECT anomalies->'flags' FROM silver.day WHERE status = 'built'"):
        for k, v in (a or {}).items():
            flags[k] = flags.get(k, 0) + v
    reasons = {}
    for (a,) in q("SELECT anomalies->'bad_reasons' FROM silver.day WHERE status = 'built'"):
        for k, v in (a or {}).items():
            k = k.split(":")[0] if k.startswith(("file", "columns")) else k
            reasons[k] = reasons.get(k, 0) + v
    copies = q("""SELECT sc.name, count(*), min(cd.day), max(cd.day) FROM silver.chain_day cd JOIN silver.chain sc USING (chain_id) WHERE cd.copy_of IS NOT NULL
                  GROUP BY sc.name ORDER BY 2 DESC""")
    trunc = q("""SELECT cd.day, sc.name, cd.file_name FROM silver.chain_day cd JOIN silver.chain sc USING (chain_id) WHERE cd.truncated ORDER BY 1""")
    switches = q("""SELECT cd.day, count(*) FILTER (WHERE cd.currency = 'EUR' AND p.currency = 'BGN'), count(*) FILTER (WHERE cd.currency = 'BGN' AND p.currency = 'EUR')
                    FROM silver.chain_day cd JOIN LATERAL (SELECT currency FROM silver.chain_day x WHERE x.chain_id = cd.chain_id AND x.day < cd.day ORDER BY x.day DESC LIMIT 1) p ON true
                    GROUP BY 1 HAVING count(*) FILTER (WHERE cd.currency <> p.currency) > 0 ORDER BY 1""")
    return dict(days=d[0], first=d[1], last=d[2], records=d[3], valid=d[4], dups=d[5], bad=d[6], blank=d[7], mismatched=d[8], status=status,
                flags=flags, reasons=reasons, copies=copies, truncated=trunc, switches=switches)


def coverage():
    """Chains that filed, per day: the coverage changes a lot over the year and every comparison over time has to say so."""
    return [(r[0], r[1], r[2]) for r in q("SELECT day, chains, valid FROM silver.day WHERE status = 'built' ORDER BY day")]


def fuel(first=None):
    rows = q("""SELECT week, geo, fuel, with_tax_eur_l, wo_tax_eur_l FROM gold.fuel_week WHERE week >= coalesce(%s::date, 'epoch') ORDER BY week""", first)
    return rows


def sources():
    return dict(
        days=q("SELECT status, count(*), min(day), max(day) FROM silver.day GROUP BY status ORDER BY 1"),
        missing=q("""SELECT count(*) FROM generate_series((SELECT min(day) FROM silver.day), (SELECT max(day) FROM silver.day), interval '1 day') g(d)
                      WHERE NOT EXISTS (SELECT 1 FROM silver.day x WHERE x.day = g.d::date AND x.status = 'built')""", one=True)[0],
        held=q("SELECT ref, first_seen, reason FROM ops.held ORDER BY ref"),
        fuel=q("SELECT max(read_at), max(newest) FROM silver.fuel_read", one=True),
        jobs=q("SELECT step, started_at, finished_at, status FROM ops.job_run ORDER BY id DESC LIMIT 12"))


def promotions(day):
    """Per chain on a day: promotional prices, how many are a real discount against the shop's own last 30 days."""
    rows = q("""SELECT p.chain_eik, ch.name_bg, p.n_promos, p.n_real, p.n_none, p.n_raised, p.n_nohistory, p.avg_declared, p.avg_real, p.window_days
                FROM gold.promo_day p JOIN gold.chain ch ON ch.eik = p.chain_eik WHERE p.day = %s ORDER BY p.n_promos DESC""", day)
    out = []
    for r in rows:
        with_hist = r[3] + r[4]
        out.append(dict(eik=r[0], name=r[1], promos=r[2], real=r[3], none=r[4], raised=r[5], nohistory=r[6],
                        real_share=r[3] / with_hist if with_hist else None, declared=f(r[7]), real_discount=f(r[8]), window=r[9]))
    return out
