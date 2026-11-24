"""The serving layer, the replay of the archive and the proof against the old database (the move to the shared one)."""
import datetime as dt

from ingest import gold, parity
from tests.helpers import derive, fixture, publish, put, run, run_

D0, D1, D2, D3 = "2026-09-26", "2026-09-29", "2026-09-30", "2026-10-01"
LIDL, KAUFLAND, TMARKET = "131071587", "131129282", "131324923"

SPANS = """SELECT store_id, product_id, from_day, to_day, retail, promo, category, flags FROM {} ORDER BY 1, 2, 3, 5, 6 NULLS FIRST, 7 NULLS FIRST, 8"""


def same_as_clean(c):
    """The serving layer holds exactly the clean prices and observations."""
    assert c.execute(SPANS.format("pub_pazar.price_span")).fetchall() == c.execute(SPANS.format("pazar.price_span")).fetchall()
    q = "SELECT * FROM {} ORDER BY day, scope, key, category"
    assert c.execute(q.format("pub_pazar.category_day")).fetchall() == c.execute(q.format("pazar.category_day")).fetchall()
    assert c.execute("SELECT day, status, valid FROM pub_pazar.day ORDER BY day").fetchall() == \
        c.execute("SELECT day, status, valid FROM pazar.day ORDER BY day").fetchall()


def test_each_day_is_published_in_its_window_and_the_layer_equals_the_clean_tables(c):
    put(c, D0, fixture(D0))
    put(c, D1, fixture(D1))
    gold.build_missing(c)
    publish(c)
    same_as_clean(c)
    first = c.execute("SELECT max(publication_id) FROM pub.publication WHERE tool = 'pazar'").fetchone()[0]
    # the next day closes some spans of the day before and opens its own: the window starts the day before it, and it
    # holds the spans that began earlier in the month and end now
    put(c, D2, fixture(D2))
    assert c.execute("SELECT count(*) FROM pazar.price_span WHERE from_day = %s AND to_day = %s", (D0, D1)).fetchone()[0] > 0
    gold.build_missing(c)
    from ingest import run as steps
    since = steps.since_of(D2)
    assert since == dt.date(2026, 9, 29)
    publish(c, since)
    same_as_clean(c)
    # a new month, and a day that changes nothing: no new version
    put(c, D3, fixture(D3))
    gold.build_missing(c)
    publish(c, steps.since_of(D3))
    same_as_clean(c)
    last = c.execute("SELECT max(publication_id) FROM pub.publication WHERE tool = 'pazar'").fetchone()[0]
    publish(c, steps.FAR)
    assert c.execute("SELECT max(publication_id) FROM pub.publication WHERE tool = 'pazar'").fetchone()[0] in (last, last + 1) and last > first
    # every partition of the serving layer is registered to the dataset, or the guard would refuse the publication
    assert c.execute("SELECT count(*) FROM core.serving_violation").fetchone()[0] == 0


def test_a_rebuilt_month_is_published_whole(c):
    for d in (D0, D1, D2):
        put(c, d, fixture(d))
    gold.build_missing(c)
    publish(c)
    put(c, D1, derive(D1, drop={KAUFLAND}))     # a rewritten day rebuilds its month
    gold.build_missing(c)
    from ingest import run as steps
    publish(c, steps.since_of("2026-09-01"))
    same_as_clean(c)


def test_two_prices_of_one_shop_and_code_on_one_day_are_both_served(c):
    from ingest import parse
    from tests.helpers import csv_of, make_zip
    files = {}
    for f in parse.parse_day(fixture(D1)).files:
        rows = list(f.rows)
        if f.eik == LIDL:
            x = next(r for r in rows if r.code == "0001292")
            rows.append(parse.Row(x.place_raw, x.ekatte, x.district, x.store, x.code, x.name, x.category, x.retail + 100, x.promo, 0))
        files[f.member] = csv_of(rows)
    put(c, D1, make_zip(files))
    two = c.execute("""SELECT count(*) FROM pazar.price_span s JOIN pazar.product p USING (product_id)
                       WHERE p.code = '0001292' AND s.flags & 8 > 0""").fetchone()[0]
    assert two >= 2
    publish(c)
    same_as_clean(c)
    assert c.execute("SELECT max(seq) FROM pub_pazar.price_span").fetchone()[0] >= 2


def test_a_failed_data_rule_stops_the_publication(c):
    put(c, D1, fixture(D1))
    gold.build_missing(c)
    c.execute("UPDATE pazar.day SET valid = valid + 1 WHERE day = %s", (D1,))     # the records no longer add up
    from ingest import run as steps
    with run(c, "publish") as r:
        problems = steps.checked_publish(c, r, None)
    assert any("Не е публикувано" in p for p in problems) and any("записите не са" in p for p in problems)
    assert c.execute("SELECT count(*) FROM pub_pazar.day").fetchone()[0] == 0


def test_a_replay_builds_a_small_day_that_a_nightly_build_would_hold(c):
    put(c, D1, fixture(D1))
    small = derive(D2, only={LIDL, KAUFLAND, TMARKET})
    assert put(c, D2, small, replay=True)[0] == "built"
    assert c.execute("SELECT count(*) FROM ops.hold WHERE tool = 'pazar'").fetchone()[0] == 0


def old_database(c):
    """The old database as the old step left it, shaped by views over this one: schemas old_silver and old_gold."""
    c.execute("CREATE SCHEMA old_silver; CREATE SCHEMA old_gold")
    for t in ("chain", "store", "product", "price_span", "chain_day", "day"):
        c.execute(f"CREATE VIEW old_silver.{t} AS SELECT * FROM pazar.{t}")
    c.execute("""CREATE VIEW old_silver.fuel_week AS SELECT f.* FROM pazar_src.fuel_week f
                 JOIN ops.load l ON l.load_id = f.load_id AND l.is_current""")
    for t in ("category_day", "municipality_day", "promo_day"):
        c.execute(f"CREATE VIEW old_gold.{t} AS SELECT * FROM pazar.{t}")


def compare(c):
    with run(c, "parity") as r:
        return parity.compare(c, r, None, old=c, schemas=("old_silver", "old_gold", "old_silver.fuel_week"))


def test_parity_is_clean_for_the_same_data_and_finds_a_changed_price(c):
    for d in (D1, D2):
        put(c, d, fixture(d))
    gold.build_missing(c)
    old_database(c)
    out = compare(c)
    assert out["problems"] == [], out["problems"]
    assert out["parity_spans"][0] == out["parity_spans"][1] > 0
    assert c.execute("""SELECT count(*), count(*) FILTER (WHERE ok) FROM ops.check_latest
                        WHERE tool = 'pazar' AND kind = 'parity' AND checked_at IS NOT NULL""").fetchone()[0] >= 15
    # one price of one span differs in the old database: its month and chain are named, the rest stays equal
    c.execute("""CREATE OR REPLACE VIEW old_silver.price_span AS
                 SELECT store_id, product_id, from_day, to_day, retail + CASE WHEN store_id = 1 AND product_id = 1 THEN 1 ELSE 0 END AS retail,
                        promo, category, flags FROM pazar.price_span""")
    out = compare(c)
    assert any(p.startswith("parity_spans_differ") for p in out["problems"])
    assert any(p.startswith("parity_spans_retail") for p in out["problems"])
    assert len(out["spans_differ"]) == 1 and out["spans_differ"][0][0] == "2026-09"
    # a day the old database holds as held and the new one built (the days are compared up to the last built in both)
    c.execute("""CREATE OR REPLACE VIEW old_silver.day AS SELECT day, CASE WHEN day = '2026-09-29' THEN 'held' ELSE status END AS status,
                 load_id, zip_sha256, zip_path, zip_bytes, files, chains, stores, records, valid, dups, bad, blank, spans_opened, spans_closed,
                 anomalies, note, built_at, build_secs FROM pazar.day""")
    assert any(p.startswith("parity_days_differ") and "2026-09-29" in p for p in compare(c)["problems"])
