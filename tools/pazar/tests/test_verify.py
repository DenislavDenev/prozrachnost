"""The manual check against the chains' sites: a repeatable random draw and a protocol with our price beside the site's."""
import csv

from ingest import verify
from tests.helpers import fixture, put

DAY = "2026-09-30"
import datetime as dt
D = dt.date.fromisoformat(DAY)


def test_the_draw_is_repeatable_and_depends_on_the_seed(c):
    put(c, DAY, fixture(DAY))
    chains = ["131071587", "131324923", "131129282"]
    a, b = verify.sample(c, D, 7, chains, 4), verify.sample(c, D, 7, chains, 4)
    assert a == b and len(a) == 12 and {r["eik"] for r in a} == set(chains)
    assert verify.sample(c, D, 8, chains, 4) != a
    assert all(r["our_retail_eur"] > 0 and 1 <= r["category"] <= 101 for r in a)


def test_the_protocol_puts_our_price_of_the_day_beside_the_site_price(c, tmp_path):
    put(c, DAY, fixture(DAY))
    row = verify.sample(c, D, 1, ["131071587"], 3)
    ours = row[0]["our_retail_eur"]
    rows = [dict(row[0], site_price=f"{ours:.2f}", site_url="https://example.invalid/p", site_checked_at_utc="2026-09-30T10:00:00Z"),
            dict(row[1], site_price=f"{row[1]['our_retail_eur'] + 0.5:.2f}", site_url="https://example.invalid/q", site_checked_at_utc="2026-09-30T10:01:00Z"),
            dict(row[2])]
    out = verify.protocol(c, D, rows, tmp_path / "p.csv")
    assert [r["result"] for r in out] == ["съвпада", "различава се", "няма онлайн"]
    assert verify.summary(out) == dict(checked=2, same=1, no_online=1)
    back = list(csv.DictReader((tmp_path / "p.csv").open(encoding="utf-8")))
    assert back[0]["our_retail_eur"] == str(ours) and back[0]["site_url"].startswith("https://")
