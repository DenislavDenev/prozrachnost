"""Gold: the daily figures, recomputed in plain Python from the CSV and compared with what the SQL wrote."""
import datetime as dt
import math
import statistics
from decimal import Decimal

import pytest

from ingest import config, gold, parse
from tests.helpers import derive, fixture, put

D1, D2 = "2026-09-29", "2026-09-30"
OLD = "2025-10-16"
EBAG = "204786976"
LIDL = "131071587"


@pytest.fixture
def g(c):
    gold.load_reference(c)
    return c


def build(c, *days):
    for d in days:
        put(c, d, fixture(d))
    gold.build_missing(c)


def expected(raw, scope="country", key="", rate=1.0):
    """{category: (n, min, median, max, promo_n, median_effective)} from the CSV alone."""
    out = {}
    for f in parse.parse_day(raw).files:
        if f.copy_of or f.error:
            continue
        for r in f.rows:
            if r.flags & 29 or not 1 <= (r.category or 0) <= 101:
                continue
            if scope == "chain" and f.eik != key:
                continue
            retail = Decimal(r.retail) / 10000 / Decimal(str(rate))
            promo = Decimal(r.promo) / 10000 / Decimal(str(rate)) if r.promo and r.promo < r.retail and not r.flags & 31 else None
            out.setdefault(r.category, []).append((retail, promo))
    res = {}
    for cat, v in out.items():
        prices = sorted(a for a, _ in v)
        eff = sorted(p if p is not None else a for a, p in v)
        res[cat] = (len(v), prices[0], Decimal(str(statistics.median(prices))), prices[-1], sum(1 for _, p in v if p is not None),
                    Decimal(str(statistics.median(eff))))
    return res


def rows(c, day, scope, key=""):
    return {r[0]: r[1:] for r in c.execute("""SELECT category, n_prices, min_eur, median_eur, max_eur, promo_prices, median_effective_eur
                                              FROM gold.category_day WHERE day = %s AND scope = %s AND key = %s""", (day, scope, key))}


def close(a, b):
    return abs(Decimal(a) - Decimal(b)) <= Decimal("0.00006")


def test_country_figures_equal_the_plain_recomputation(g):
    build(g, D2)
    want, got = expected(fixture(D2)), rows(g, D2, "country")
    assert set(want) == set(got) and len(got) > 20
    for cat, w in want.items():
        n, lo, med, hi, pn, eff = got[cat]
        assert (n, pn) == (w[0], w[4]), cat
        assert close(lo, w[1]) and close(med, w[2]) and close(hi, w[3]) and close(eff, w[5]), cat


def test_a_chain_has_its_own_figures_and_a_copy_of_another_chain_has_none(g):
    build(g, D2)
    assert rows(g, D2, "chain", LIDL) and {k: v[0] for k, v in rows(g, D2, "chain", LIDL).items()} == {k: v[0] for k, v in expected(fixture(D2), "chain", LIDL).items()}
    # the Billa file of 30.09 repeats the file of Nove Farm: nothing for Billa, and the pharmacy rows are counted once
    assert not rows(g, D2, "chain", "130007884")
    assert rows(g, D2, "chain", "203105528")
    assert g.execute("SELECT count(*) FROM gold.category_day WHERE day = %s AND scope = 'chain' AND key = '130007884'", (D2,)).fetchone()[0] == 0


def test_anomalies_are_out_of_the_figures_but_stay_in_silver(g):
    build(g, "2026-09-26")
    # the stray category 1972047017 of Zhanet is in silver and in no category of gold
    assert g.execute("SELECT count(*) FROM silver.price_span WHERE category = 1972047017").fetchone()[0] > 0
    assert g.execute("SELECT count(*) FROM gold.category_day WHERE category > 101 OR category < 1").fetchone()[0] == 0
    assert expected(fixture("2026-09-26")) and {k: v[0] for k, v in rows(g, "2026-09-26", "country").items()} == {k: v[0] for k, v in expected(fixture("2026-09-26")).items()}


def test_promotion_equal_to_the_price_is_not_a_promotion(g):
    build(g, D2)
    # the pharmacy chain writes the same number in both columns: its category 86 has prices but no promotions
    n, lo, med, hi, pn, eff = rows(g, D2, "chain", "106609436")[86]
    assert n >= 1 and pn == 0 and close(eff, med)


def test_a_price_in_leva_is_shown_in_euro(g):
    build(g, OLD)
    want = expected(fixture(OLD), rate=config.BGN_PER_EUR)
    got = rows(g, OLD, "country")
    assert set(got) == set(want)
    for cat, w in want.items():
        assert close(got[cat][2], w[2]), cat
    # the same category costs about the same in euro eleven months later; without the conversion it would be double
    build(g, D2)
    now = rows(g, D2, "country")
    common = set(got) & set(now)
    assert len(common) >= 5
    assert statistics.median(abs(math.log(float(got[k][2]) / float(now[k][2]))) for k in common) < 0.3


def test_the_online_shop_is_in_the_country_and_the_chain_but_not_on_a_municipality(g):
    build(g, D2)
    assert g.execute("SELECT national_price FROM gold.chain WHERE eik = %s", (EBAG,)).fetchone() == (True,)
    assert g.execute("SELECT count(*) FROM gold.category_day WHERE day = %s AND scope = 'chain' AND key = %s", (D2, EBAG)).fetchone()[0] > 0
    n_sofia = g.execute("""SELECT sum(n_prices) FROM gold.category_day WHERE day = %s AND scope = 'municipality' AND key = '000696327'""", (D2,)).fetchone()[0]
    all_sofia = sum(1 for f in parse.parse_day(fixture(D2)).files if f.eik and not f.copy_of for r in f.rows
                    if r.ekatte == "68134" and not r.flags & 29 and 1 <= (r.category or 0) <= 101 and f.eik != EBAG)
    assert n_sofia == all_sofia          # Sofia without the online shop


def test_municipality_and_oblast_come_from_the_ekatte_of_the_store(g):
    build(g, D2)
    muni = g.execute("SELECT municipality_id, oblast FROM gold.store s WHERE place_raw = '27382' LIMIT 1").fetchone()
    assert muni is not None and muni[0] is not None
    assert g.execute("SELECT name_bg FROM gold.municipality WHERE id = %s", (muni[0],)).fetchone()[0]
    # Bjala Slatina (07702) is a place of the register
    assert g.execute("SELECT municipality_id FROM gold.place WHERE ekatte = '07702'").fetchone() is not None
    assert g.execute("SELECT count(*) FROM gold.category_day WHERE scope = 'oblast'").fetchone()[0] > 0


def test_the_price_index_matches_the_recomputation_from_two_days(g):
    build(g, D1, D2)

    def table(raw):
        t = {}
        for f in parse.parse_day(raw).files:
            if f.copy_of or f.error:
                continue
            for r in f.rows:
                if r.flags & 29 or not 1 <= (r.category or 0) <= 101:
                    continue
                t[(f.eik, r.place_raw, r.store, r.code)] = r.retail
        return t
    a, b = table(fixture(D1)), table(fixture(D2))
    matched = [k for k in b if k in a]
    want_ln = sum(math.log(b[k] / a[k]) for k in matched)
    row = g.execute("SELECT sum(matched), sum(changed), sum(sum_ln) FROM gold.category_day WHERE day = %s AND scope = 'country'", (D2,)).fetchone()
    assert row[0] == len(matched)
    assert row[1] == sum(1 for k in matched if a[k] != b[k])
    assert abs(float(row[2]) - want_ln) < 1e-4
    # the first day has no earlier price to compare with
    assert g.execute("SELECT sum(matched) FROM gold.category_day WHERE day = %s", (D1,)).fetchone()[0] == 0


def test_the_index_across_the_month_boundary_and_across_the_euro(g):
    build(g, D2, "2026-10-01")
    assert g.execute("SELECT sum(matched) FROM gold.category_day WHERE day = '2026-10-01' AND scope = 'country'").fetchone()[0] > 0
    # a day whose chains all moved from leva to euro: prices fell by the rate in the file, not in the shop
    put(g, OLD, fixture(OLD))
    put(g, "2025-10-17", derive(OLD, scale=config.BGN_PER_EUR))
    gold.build_missing(g)
    row = g.execute("SELECT sum(matched), sum(changed), sum(sum_ln) FROM gold.category_day WHERE day = '2025-10-17' AND scope = 'country'").fetchone()
    assert row[0] > 100 and abs(float(row[2]) / row[0]) < 0.001      # no change of price: the currency is converted away


def test_gold_is_rebuilt_from_silver_with_the_same_numbers(g):
    build(g, D1, D2)
    first = g.execute("SELECT * FROM gold.category_day ORDER BY day, scope, key, category").fetchall()
    gold.build_missing(g, rebuild=True)
    assert g.execute("SELECT * FROM gold.category_day ORDER BY day, scope, key, category").fetchall() == first


def test_gold_follows_a_rewritten_day(g):
    build(g, D1)
    before = g.execute("SELECT count(*) FROM gold.category_day WHERE day = %s", (D1,)).fetchone()[0]
    put(g, D1, derive(D1, drop={"131129282"}))
    gold.build_missing(g)
    after = g.execute("SELECT count(*) FROM gold.category_day WHERE day = %s", (D1,)).fetchone()[0]
    assert after < before and not rows(g, D1, "chain", "131129282")


def test_the_reference_tables_are_whole(g):
    assert g.execute("SELECT count(*) FROM gold.category").fetchone()[0] == 101
    assert g.execute("SELECT name_bg FROM gold.category WHERE code = 6").fetchone()[0].startswith("Прясно мляко")
    assert g.execute("SELECT count(*) FROM gold.municipality").fetchone()[0] == 265
    assert g.execute("SELECT count(DISTINCT municipality_id) FROM gold.place").fetchone()[0] == 265
    assert g.execute("SELECT count(*) FROM gold.place").fetchone()[0] > 5000
