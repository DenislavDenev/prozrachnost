"""Manual verification against the chains' own websites (docs/verification/<day>.csv).

`sample` draws products at random from the stored prices of the chosen chains, the draw fixed by a seed so that anyone can
repeat it. A person (or a browser) then reads the same product's price on the chain's site the same day; `protocol` puts our
price of the day beside it. Nothing here corrects a price: a difference is written down with the reason.
"""
import csv
import datetime as dt

COLUMNS = ["day", "chain", "eik", "store", "place", "code", "name", "category", "our_retail_eur", "our_promo_eur", "currency",
           "site_price", "site_promo", "site_url", "site_checked_at_utc", "result", "note"]


def sample(c, day, seed, chains, n=6):
    """n random (store, product) rows per chain on `day`, repeatable: the order is md5(seed, store, product)."""
    out = []
    for eik in chains:
        rows = c.execute("""SELECT sc.name, sc.eik, st.name, st.place_raw, p.code, p.name, s.category, s.retail, s.promo, cd.currency, s.flags
                            FROM silver.price_span s JOIN silver.store st USING (store_id) JOIN silver.product p USING (product_id)
                            JOIN silver.chain sc ON sc.chain_id = st.chain_id
                            JOIN silver.chain_day cd ON cd.chain_id = sc.chain_id AND cd.day = %(d)s AND cd.filed AND cd.copy_of IS NULL
                            WHERE sc.eik = %(e)s AND s.from_day >= %(ms)s AND s.from_day <= %(d)s AND coalesce(s.to_day, 'infinity') >= %(d)s
                              AND (s.flags & 29) = 0 AND s.category BETWEEN 1 AND 101
                            ORDER BY md5(%(seed)s || st.store_id::text || ':' || p.product_id::text) LIMIT %(n)s""",
                          dict(d=day, e=eik, ms=day.replace(day=1), seed=str(seed), n=n)).fetchall()
        for r in rows:
            rate = 1.95583 if r[9] == "BGN" else 1
            out.append(dict(day=str(day), chain=r[0], eik=r[1], store=r[2], place=r[3], code=r[4], name=r[5], category=r[6],
                            our_retail_eur=round(r[7] / 10000 / rate, 4), our_promo_eur=None if r[8] is None else round(r[8] / 10000 / rate, 4), currency=r[9]))
    return out


def our_price(c, day, eik, store, code):
    """(retail, promo, currency) of the row on `day`, as submitted, or None."""
    r = c.execute("""SELECT s.retail, s.promo, cd.currency FROM silver.price_span s JOIN silver.store st USING (store_id)
                     JOIN silver.product p USING (product_id) JOIN silver.chain sc ON sc.chain_id = st.chain_id
                     JOIN silver.chain_day cd ON cd.chain_id = sc.chain_id AND cd.day = %(d)s AND cd.filed
                     WHERE sc.eik = %(e)s AND st.name = %(s)s AND p.code = %(c)s AND s.from_day >= %(ms)s AND s.from_day <= %(d)s
                       AND coalesce(s.to_day, 'infinity') >= %(d)s ORDER BY s.retail LIMIT 1""",
                  dict(d=day, e=eik, s=store, c=code, ms=day.replace(day=1))).fetchone()
    return None if not r else (r[0] / 10000, None if r[1] is None else r[1] / 10000, r[2])


def protocol(c, day, rows, path):
    """Writes the protocol: the rows read on the sites, each with our price of the day and the verdict."""
    out = []
    for r in rows:
        ours = our_price(c, day, r["eik"], r["store"], r["code"])
        row = {k: r.get(k) for k in COLUMNS}
        row["day"] = str(day)
        if ours:
            row["our_retail_eur"], row["our_promo_eur"] = (round(ours[0] / (1.95583 if ours[2] == "BGN" else 1), 4),
                                                           None if ours[1] is None else round(ours[1] / (1.95583 if ours[2] == "BGN" else 1), 4))
            row["currency"] = ours[2]
        site = r.get("site_price")
        if not site:
            row["result"] = r.get("result") or "няма онлайн"
        elif ours:
            same = abs(float(site) - row["our_retail_eur"]) < 0.005 or (r.get("site_promo") and abs(float(site) - float(r["site_promo"])) < 0.005 and row["our_promo_eur"] is not None and abs(float(r["site_promo"]) - row["our_promo_eur"]) < 0.005)
            row["result"] = r.get("result") or ("съвпада" if same else "различава се")
        else:
            row["result"] = "няма наш ред за деня"
        out.append(row)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    return out


def summary(rows):
    done = [r for r in rows if r["result"] in ("съвпада", "различава се")]
    return dict(checked=len(done), same=sum(1 for r in done if r["result"] == "съвпада"), no_online=sum(1 for r in rows if r["result"] == "няма онлайн"))
