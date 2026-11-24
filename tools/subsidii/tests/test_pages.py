"""The pages as rendered pages, the CSV and the JSON of the same scope, the rule for names, the map, the version of every
answer. The pages read the serving layer only. Runs against subsidii_test."""
import csv
import io
import re

import pytest
from fastapi.testclient import TestClient

from ingest import names
from tests.helpers import built, count, derive, fixture, population, publish

D1, D2 = "2026-09-28", "2026-10-05"


@pytest.fixture
def client(c):
    from app import main, queries
    queries._REF = None
    built(c, (2024, D1, fixture(2024)), (2025, D1, fixture(2025)), (2025, D2, derive(2025, drop={6})))
    publish(c)
    population(c, "2025-12-15", 10000)
    population(c, "2024-12-31", 9000)
    yield TestClient(main.app)
    c.execute("SELECT set_config('subsidii.today', '', false)")


def rows_csv(r):
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    return list(csv.DictReader(io.StringIO(r.text.lstrip("﻿"))))


def test_every_page_is_a_page(client):
    for path in ["/", "/karta", "/merki", "/poluchateli", "/obshtini", "/sravnenie", "/sources", "/how", "/?fy=2024", "/poluchateli?tip=natural", "/merki?fond=nb",
                 "/obshtini?oblast=Видин", "/karta?l=oblasti&per=resident", "/sources.json", "/healthz", "/api/karta.json?l=rayoni", "/api/tablo.json", "/api/sravnenie.json"]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert "Traceback" not in r.text


def test_the_header_the_footer_and_the_menu(client):
    html = client.get("/").text
    assert re.search(r'class="word" href="/">субсидии<span class="dot">\.</span>', html)
    assert html.index("Обратна връзка") > html.index("Всички инструменти")
    assert html.rindex("Подкрепи проекта") > html.index("<footer")
    menu = re.findall(r'<nav class="menu".*?</nav>', html, re.S)[0]
    assert [m for m in re.findall(r">([^<]+)</a>", menu)][:2] == ["Табло", "Карта"]


def test_the_numbers_on_the_dashboard_are_the_gold_numbers(client, c):
    html = client.get("/?fy=2025&snap=" + D1).text
    total = c.execute("SELECT total FROM pub_subsidii.summary WHERE fy = 2025 AND snap_day = %s AND scope = 'all'", (D1,)).fetchone()[0]
    from app.main import money
    from decimal import Decimal
    assert money(total / Decimal('1.95583')) in html
    assert "45" in html      # recipient rows of the 2025 fixture


def test_the_snapshot_is_a_parameter_and_any_day_resolves_to_the_read_before_it(client):
    a = client.get("/api/tablo.json?fy=2025&snap=" + D1).json()
    b = client.get("/api/tablo.json?fy=2025").json()
    assert a["snapshot"] == D1 and b["snapshot"] == D2
    assert a["recipients"] == 45 and b["recipients"] == 44
    assert client.get("/api/tablo.json?fy=2025&snap=2026-10-03").json()["snapshot"] == D1
    assert client.get("/api/tablo.json?fy=2025&snap=2020-01-01").json()["snapshot"] == D2       # before the first read: the default, not a guess


def test_the_names_of_people_are_masked_in_the_list_only_and_only_in_the_term(client, c):
    inside = client.get("/poluchateli?fy=2025&tip=natural&snap=" + D1).text
    assert "физическо ***" in inside and "физическо лице №" not in inside and " ФЛ" not in inside
    c.execute("SELECT set_config('subsidii.today', '2030-01-01', false)")
    publish(c)
    late = client.get("/poluchateli?fy=2025&tip=natural&snap=" + D1).text
    assert "***" not in late and "физическо лице" in late


def test_people_below_the_threshold_are_said_but_are_not_rows(client, c):
    html = client.get("/poluchateli?fy=2024&tip=natural").text
    n = count(c, "SELECT sum(recipients) FROM pub_subsidii.below_threshold WHERE fy = 2024 AND kind = 'natural'")
    assert n and f"<b>{n}</b>" in html and "1 250 €" in html
    js = client.get("/api/poluchateli.json?fy=2024&tip=natural&size=500").json()
    assert js["count"] == count(c, "SELECT count(*) FROM pub_subsidii.recipient WHERE fy = 2024 AND kind = 'natural'")
    assert "1 250 €" not in client.get("/poluchateli?fy=2024&tip=legal").text


def test_every_answer_says_its_publication(client, c):
    first = client.get("/api/tablo.json")
    n = count(c, "SELECT max(publication_id) FROM pub.publication")
    assert first.headers["X-Publication"] == str(n) and first.headers["ETag"] == f'W/"pub-{n}"' and "GMT" in first.headers["Last-Modified"]
    assert client.get("/healthz").json() == {"ok": True, "publication": n}
    c.execute("SELECT set_config('subsidii.today', '2030-01-01', false)")
    publish(c)
    assert client.get("/").headers["X-Publication"] == str(count(c, "SELECT max(publication_id) FROM pub.publication")) != str(n)


def test_an_export_has_no_name_of_a_person_and_the_json_neither(client):
    for path in ["/export-poluchateli.csv?fy=2024&tip=natural", "/export-poluchateli.csv?fy=2025", "/api/poluchateli.json?fy=2025&size=500", "/api/poluchateli.json?fy=2024&tip=sole_trader"]:
        text = client.get(path).text
        assert "физическо лице №" not in text and "ЕТ Образец" not in text and "***" not in text, path
    rows = rows_csv(client.get("/export-poluchateli.csv?fy=2025"))
    assert {r["recipient"] for r in rows if r["kind"] == "natural"} == {"физическо лице"}
    assert all(r["org_id"] == "" for r in rows if r["kind"] != "legal")


def test_a_search_finds_firms_only_by_name_and_never_a_person(client):
    r = client.get("/poluchateli?fy=2025&q=агро")
    assert r.status_code == 200 and "БГ Агро" in r.text
    none = client.get("/poluchateli?fy=2025&q=" + "физическо")
    assert "физическо лице №" not in none.text
    rows = rows_csv(client.get("/export-poluchateli.csv?fy=2025&q=агро"))
    assert rows and all(r["kind"] == "legal" for r in rows)
    assert client.get("/api/tarsene.json?q=агро").json()["rows"]
    assert client.get("/api/tarsene.json?q=ФЛ").json()["rows"] == []


def test_the_page_the_csv_and_the_json_have_one_scope(client):
    html = client.get("/poluchateli?fy=2025&oblast=Видин&tip=legal").text
    rows = rows_csv(client.get("/export-poluchateli.csv?fy=2025&oblast=Видин&tip=legal"))
    js = client.get("/api/poluchateli.json?fy=2025&oblast=Видин&tip=legal&size=500").json()
    assert len(rows) == js["count"] > 0
    assert {r["oblast"] for r in rows} == {"Видин"} and {r["kind"] for r in rows} == {"legal"}
    assert [r["recipient"] for r in rows] == [r["recipient"] for r in js["rows"]]
    assert js["licence"] is None and "не се раздава" in js["licence_note"]


def test_the_oblast_limits_the_municipality_and_a_foreign_municipality_is_dropped(client, c):
    vidin = c.execute("SELECT m.eik FROM pub.municipality m JOIN pub.oblast o USING (oblast_code) WHERE o.label_bg = 'Видин' LIMIT 1").fetchone()[0]
    assert client.get(f"/api/poluchateli.json?fy=2025&oblast=Видин&obshtina={vidin}").json()["filters"]["obshtina"] == vidin
    f = client.get(f"/api/poluchateli.json?fy=2025&oblast=Русе&obshtina={vidin}").json()["filters"]
    assert f["oblast"] == "Русе" and "obshtina" not in f


def test_the_measures_page_csv_and_json_agree(client):
    rows = rows_csv(client.get("/export-merki.csv?fy=2025"))
    js = client.get("/api/merki.json?fy=2025").json()
    assert len(rows) == len(js["rows"]) > 5 and rows[0]["currency"] == "BGN"
    assert all(r["total_eur"] for r in rows)
    nb = rows_csv(client.get("/export-merki.csv?fy=2025&fond=nb"))
    assert 0 < len(nb) < len(rows) and all(float(r["nb_eur"]) > 0 for r in nb)


def test_the_map_has_every_level_the_values_and_the_gaps(client):
    d = client.get("/api/karta.json?l=obshtini&fy=2025").json()
    assert d["total"] == 265 and 0 < d["shown"] < 265 and d["unit"] == "€" and [l[0] for l in d["levels"]] == ["obshtini", "oblasti", "rayoni", "makrorayoni"]
    assert all(i["v"] is None for i in d["items"] if i["recipients"] == 0)         # no recipients is no data, not zero
    ob = client.get("/api/karta.json?l=oblasti&fy=2025").json()
    assert ob["total"] == 28 and ob["nuts"] == 3 and all(i["code"].startswith("BG") for i in ob["items"])
    assert abs(sum(i["v"] or 0 for i in ob["items"]) - sum(i["v"] or 0 for i in d["items"])) < 1       # the oblasts add up to the municipalities
    assert client.get("/api/karta.json?l=rayoni&fy=2025").json()["total"] == 6 and client.get("/api/karta.json?l=makrorayoni&fy=2025").json()["total"] == 2


def test_per_inhabitant_needs_the_inhabitants_and_says_their_date(client, c):
    d = client.get("/api/karta.json?l=obshtini&fy=2025&per=resident").json()
    assert d["unit"] == "€ на жител" and d["population_date"] == "2024-12-31" and d["population_late"] is False      # the newest on or before 15.10.2025
    item = next(i for i in d["items"] if i["v"] is not None)
    plain = client.get("/api/karta.json?l=obshtini&fy=2025").json()
    assert abs(item["v"] - next(i["v"] for i in plain["items"] if i["code"] == item["code"]) / 9000) < 0.01
    c.execute("TRUNCATE pub_naselenie.population")
    gone = client.get("/api/karta.json?l=obshtini&fy=2025&per=resident").json()
    assert gone["shown"] == 0 and gone["population_date"] is None            # without the inhabitants nothing per inhabitant is shown
    # FY2024 ended 15.10.2024: the inhabitants of 31.12.2024 are the first after it, and that is said
    population(c, "2024-12-31", 9000)
    assert client.get("/api/karta.json?l=obshtini&fy=2024&per=resident").json()["population_late"] is True


def test_the_map_page_and_its_csv_have_the_same_numbers(client):
    d = client.get("/api/karta.json?l=obshtini&fy=2025&fond=efgz").json()
    rows = rows_csv(client.get("/export-karta.csv?level=obshtini&fy=2025&fond=efgz"))
    assert {r["code"]: (float(r["value"]) if r["value"] else None) for r in rows} == {i["code"]: i["v"] for i in d["items"]}
    html = client.get("/karta?fy=2025").text
    assert 'id="k-data"' in html and "bg-municipalities" not in html and "EuroGeographics" in html


def test_a_legal_entity_has_a_page_and_a_person_has_none(client, c):
    oid = c.execute("SELECT org_id FROM pub_subsidii.org WHERE name_bg LIKE 'БГ Агро Земеделска%%'").fetchone()[0]
    r = client.get(f"/poluchateli/{oid}")
    assert r.status_code == 200 and "не е доказателство за една и съща фирма" in r.text
    assert "Вълчи Дол" in r.text and len(rows_csv(client.get(f"/export-poluchatel/{oid}.csv"))) >= 2
    assert client.get("/poluchateli/" + names.org_id("физическо лице")).status_code == 404
    person = c.execute("SELECT holder_id FROM pub_subsidii.recipient WHERE kind = 'natural' LIMIT 1").fetchone()[0]
    assert client.get(f"/poluchateli/{person}").status_code == 404


def test_a_municipality_page_and_its_numbers(client, c):
    mid = c.execute("SELECT eik FROM pub.municipality WHERE name_bg = 'Вълчи Дол'").fetchone()[0]
    r = client.get(f"/obshtini/{mid}?fy=2025")
    assert r.status_code == 200 and "Вълчи Дол" in r.text and "физическо лице №" not in r.text and "***" not in r.text
    js = client.get(f"/api/obshtini/{mid}.json?fy=2025").json()
    assert [y["financial_year"] for y in js["years"]] == [2024, 2025]
    assert client.get("/obshtini/000000000").status_code == 404


def test_the_comparison_says_when_the_scope_differs(client):
    j = client.get("/api/sravnenie.json?a_fy=2024&b_fy=2025").json()
    assert j["same_scope"] is False and j["measures_only_in_a"] and j["measures_only_in_b"]
    html = client.get("/sravnenie?a_fy=2024&b_fy=2025").text
    assert "Обхватът е различен" in html and "не се смята" in html
    same = client.get(f"/api/sravnenie.json?a_fy=2025&a_snap={D1}&b_fy=2025&b_snap={D1}").json()
    assert same["same_scope"] is True and all(r["change"] == 0 for r in same["rows"] if r["change"] is not None)
    two_days = client.get(f"/api/sravnenie.json?a_fy=2025&a_snap={D1}&b_fy=2025&b_snap={D2}").json()
    assert any(r["a_eur"] != r["b_eur"] for r in two_days["rows"])


def test_the_sources_page_tells_the_files_the_differences_and_the_licence(client):
    html = client.get("/sources").text
    assert "не е посочен" in html and "Разлики в самата справка" in html
    assert "399 911,64" in html.replace(" ", " ")
    j = client.get("/sources.json").json()
    ds = j["datasets"][0]
    assert ds["id"] == "subsidii.paid" and ds["licence"] == "не е посочен" and ds["distributed"] is False and ds["channel"] == "file"
    assert "1 250 €" in ds["personal_data"]
    assert {y["fy"] for y in j["years"]} == {2024, 2025} and len(j["snapshots"]) == 3
    assert all(s["status"] == "built" for s in j["snapshots"])


def test_missing_is_not_zero_on_the_pages(client, c):
    c.execute("UPDATE pub_subsidii.fiscal_year SET eur_per_unit = NULL, currency = NULL WHERE fy = 2025")
    html = client.get("/poluchateli?fy=2025").text
    assert "няма данни" in html
    rows = rows_csv(client.get("/export-poluchateli.csv?fy=2025"))
    assert all(r["total_eur"] == "" and r["total_original"] for r in rows)       # the original stays, the euro is missing, not 0


def test_a_year_that_does_not_exist_falls_back_and_a_bad_parameter_is_not_a_crash(client):
    assert client.get("/api/tablo.json?fy=1999").json()["financial_year"] == 2025
    assert client.get("/api/tablo.json?fy=abc&snap=zzz&tip=bad&fond=bad").status_code == 200
    assert client.get("/poluchateli?sort=drop%20table&direction=up&page=0").status_code in (200, 422)
