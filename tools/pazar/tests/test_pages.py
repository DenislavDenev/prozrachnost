"""The pages as rendered, with real fixture days in the test database: every page answers, and the number, the chart and the CSV
of one scope agree. Runs on the server against pazar_test."""
import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from ingest import gold
from tests.helpers import derive, fixture, put

D1, D2, D3 = "2026-09-29", "2026-09-30", "2026-10-01"
LIDL, KAUFLAND, EBAG = "131071587", "131129282", "204786976"


@pytest.fixture
def site(c):
    from app import main
    gold.load_reference(c)
    for d in (D1, D2, D3):
        put(c, d, fixture(d))
    gold.build_missing(c)
    return TestClient(main.app)


def rows_of(resp):
    return list(csv.DictReader(io.StringIO(resp.text.lstrip("﻿"))))


def test_an_empty_database_still_answers(c):
    from app import main
    gold.load_reference(c)
    cl = TestClient(main.app)
    for url in ("/", "/karta", "/kategorii", "/verigi", "/goriva", "/proverka", "/sources", "/how", "/produkti", "/koshnica", "/promocii", "/healthz"):
        assert cl.get(url).status_code == 200, url


def test_every_page_answers_with_data_and_carries_the_header_and_footer(site):
    for url in ("/", "/karta", "/produkti?q=мляко", "/kategorii", "/kategorii/6", "/verigi", f"/verigi/{LIDL}", "/koshnica", "/koshnica?c=1,6,12",
                "/promocii", "/goriva", "/proverka", "/sources", "/how", "/?k=vsichki", "/?c=6,12&d=2026-09-30"):
        r = site.get(url)
        assert r.status_code == 200, url
        assert "Обратна връзка" in r.text and "Подкрепи проекта" in r.text and "Всички инструменти" in r.text, url
    assert site.get("/kategorii/999").status_code == 404 and site.get(f"/verigi/000000000").status_code == 404
    assert site.get("/produkti/1/2").status_code == 404


def test_the_number_the_chart_and_the_csv_cover_the_same_basket(site):
    html = site.get("/?c=6,12").text
    assert "Моята кошница (2 категории)" in html
    csv_rows = rows_of(site.get("/export-index.csv?c=6,12"))
    api = site.get("/api/index.json?c=6,12").json()["series"][0]["points"]
    cost = site.get("/api/cost.json?c=6,12").json()["series"][0]["points"]
    assert {r["day"] for r in csv_rows} >= {p[0] for p in api} and all(r["categories"] == "6,12" for r in csv_rows)
    by_day = {r["day"]: r for r in csv_rows}
    for day, v in cost:
        assert float(by_day[day]["cost_eur"]) == pytest.approx(v)
    for day, v in api:
        assert float(by_day[day]["index"]) == pytest.approx(v, abs=0.01)
    assert api[0][1] == 100.0


def test_the_cost_of_a_basket_is_the_sum_of_the_medians_and_missing_is_not_zero(site, c):
    from app import queries as Q
    b = Q.resolve(None, "6,12")
    got = Q.cost(__import__("datetime").date(2026, 9, 30), b)
    want = sum(float(r[0]) for r in c.execute("SELECT median_eur FROM gold.category_day WHERE day = '2026-09-30' AND scope = 'country' AND category IN (6, 12)"))
    assert got["complete"] and got["value"] == pytest.approx(want)
    whole = Q.cost(__import__("datetime").date(2026, 9, 30), Q.resolve("vsichki"))
    assert not whole["complete"] and whole["n"] < whole["of"]            # the fixture days do not hold all 101 categories
    page = site.get("/?k=vsichki").text
    assert "обща цена не се показва" in page and "Цената на кошницата" not in page.split("С прости думи")[0]


def test_the_map_shows_only_municipalities_with_three_shops_of_two_chains(site):
    d = site.get("/api/karta.json?k=vsichki&y=2026-09-30").json()
    assert d["total"] == 265
    for i in d["items"]:
        if i["v"] is not None:
            assert i["stores"] >= 3 and i["chains"] >= 2, i
        else:
            assert i["why"], i
    csvr = rows_of(site.get("/export-karta.csv?k=vsichki&d=2026-09-30"))
    assert len(csvr) == 265 and {r["municipality_id"] for r in csvr} == {i["code"] for i in d["items"]}
    assert sum(1 for r in csvr if r["level_pct"]) == d["shown"]


def test_a_day_without_a_chain_is_a_gap_in_its_page_not_a_zero(c):
    from app import main
    gold.load_reference(c)
    put(c, D1, fixture(D1))
    put(c, D2, derive(D2, drop={LIDL}))
    put(c, D3, fixture(D3))
    gold.build_missing(c)
    cl = TestClient(main.app)
    days = {r["day"]: r for r in rows_of(cl.get(f"/export-verigi/{LIDL}.csv"))}
    assert days[D2]["filed"] == "False" and days[D2]["present"] == "False" and days[D1]["filed"] == "True"
    page = cl.get(f"/verigi/{LIDL}").text
    assert "1 дни без използваем файл" in page.replace("1 дни", "1 дни")
    ser = cl.get(f"/export-category/6.csv?d={D2}")
    assert all(r["key"] != LIDL for r in rows_of(ser) if r["scope"] == "chain")


def test_product_page_series_and_csv(site, c):
    from app import queries as Q
    rows = c.execute("SELECT chain_eik, code, name, product_id FROM gold.product WHERE chain_eik = %s ORDER BY product_id", (LIDL,)).fetchall()
    row = next(r for r in rows if len(Q.product_series(r[3])) >= 3)
    r = site.get(f"/produkti/{row[0]}/{row[1]}?d=2026-09-30")
    assert r.status_code == 200 and row[2] in r.text
    pts = site.get(f"/api/product/{row[0]}/{row[1]}.json").json()["series"][1]["points"]
    assert len(pts) >= 3 and all(p[1] is None or p[1] > 0 for p in pts)
    assert len(rows_of(site.get(f"/export-product/{row[0]}/{row[1]}.csv"))) >= 3


def test_the_search_escapes_its_input(site):
    assert site.get("/produkti?q=%25").status_code == 200
    r = site.get("/produkti?q=<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in r.text


def test_verification_page_reports_the_reconciliation(site):
    t = site.get("/proverka").text
    assert "Дни, в които тези числа не се събират: <b>0</b>" in t
    assert "Билла" in t        # the copy of another chain's file is listed, not hidden


def test_sources_json_says_the_price_set_is_not_distributed(site):
    j = site.get("/sources.json").json()
    ds = {d["id"]: d for d in j["datasets"]}
    assert ds["pazar.prices"]["distributed"] is False and ds["pazar.prices"]["licence"] is None
    assert ds["pazar.fuel"]["distributed"] is True
