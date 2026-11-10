"""The pages as rendered, with the fixture archive in the test database: every page answers, the number of the page,
the CSV and the JSON of one scope agree, and no page or export names a person. Runs on the server and in CI."""
import csv
import io
import json
import re
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

D = Decimal


@pytest.fixture(scope="module")
def site(built):
    from app import main
    return TestClient(main.app)


def rows_of(resp):
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/csv")
    return list(csv.DictReader(io.StringIO(resp.text.lstrip("﻿"))))


def text_of(html):
    return re.sub(r"\s+", " ", re.sub(r"<script[\s\S]*?</script>|<[^>]+>", " ", html))


PAGES = ["/", "/?e=79", "/?e=95&d=2026-09-29", "/izbori", "/izbori/79", "/izbori/81", "/izbori/93", "/izbori/95", "/izbori/95?d=2026-09-29",
         "/izbori/95?a=2026-09-29&b=2026-09-30", "/izbori/96", "/izbori/68", "/uchastnici", "/uchastnici?q=народ", "/uchastnici?e=81", "/uchastnici/79-1-1215",
         "/uchastnici/81-3-117586", "/agencii", "/agencii?q=ет", "/agencii/116050625", "/sources", "/how"]


def test_every_page_answers_and_carries_the_header_and_the_footer(site):
    for url in PAGES:
        r = site.get(url)
        assert r.status_code == 200, url
        html = r.text
        assert 'lang="bg"' in html and "партии и пари<span" in html and "Всички инструменти" in html, url
        header = html[html.index("<header"):html.index("</header>")]
        assert header.rstrip().endswith("</div>") and header.rindex("Обратна връзка") > header.rindex("Всички инструменти"), url      # the button is the last of the header
        footer = html[html.index("<footer"):html.index("</footer>")]
        assert footer.rindex("Подкрепи проекта") > footer.rindex("Източници") and "/podkrepi" in footer, url
        assert "—" not in html and "С прости думи" in html or url in ("/sources", "/how"), url
    assert site.get("/healthz").json()["ok"] is True and site.get("/healthz").json()["publication"] >= 1
    assert site.get("/favicon.svg").headers["content-type"].startswith("image/svg")


def test_what_does_not_exist_is_not_found(site):
    for url in ("/izbori/999", "/uchastnici/79-1-999999", "/uchastnici/xyz", "/agencii/000000000", "/izbori/79/otchet/party/999", "/export-izbori.xml",
                "/export-dareniya.csv?e=999", "/export-otchet.csv?e=79&l=party&n=999"):
        assert site.get(url).status_code == 404, url


def test_no_page_and_no_export_names_a_person(site):
    urls = PAGES + ["/export-dareniya.csv?e=79", "/export-dareniya.json?e=95", "/export-kandidati.csv?e=79", "/export-kandidati.json?e=68", "/export-poluchateli.csv?e=79",
                    "/export-sravnenie.csv?e=95&a=2026-09-29&b=2026-09-30", "/export-uchastnici.csv", "/sources.json", "/api/dni.json?e=79"]
    for url in urls:
        body = site.get(url).text
        for placeholder in ("Физическо лице ", "Кандидат 1", "Лице 1"):      # how the fixtures name every person
            assert placeholder not in body, url
    out = rows_of(site.get("/export-dareniya.csv?e=79"))
    assert {r["donor"] for r in out} == {"физическо лице"}


def test_the_page_the_csv_and_the_json_of_an_election_say_the_same(site):
    html = text_of(site.get("/izbori/79").text)
    assert "39 дарения" in html and "35 369,13 €" in html
    out = rows_of(site.get("/export-dareniya.csv?e=79"))
    api = site.get("/export-dareniya.json?e=79").json()
    assert len(out) == 39 == len(api["rows"]) and list(out[0]) == api["columns"]
    assert sum(D(r["money_eur"]) for r in out) == D("35369.1271736296099356") == sum(D(r["money_eur"]) for r in api["rows"])      # the source's total, to the last digit
    assert [r["id"] for r in out] == [r["id"] for r in api["rows"]] and len({r["id"] for r in out}) == 39
    assert api["election"]["erik_id"] == 79 and api["unit"] == "EUR" and api["source_url"].endswith("electionId=79")
    rec = rows_of(site.get("/export-poluchateli.csv?e=79"))
    assert sum(D(r["money_eur"]) for r in rec) == D("35369.1271736296099356") and sum(int(r["donations"]) for r in rec) == 39
    top = max(rec, key=lambda r: D(r["money_eur"]))
    assert top["name"] == "ИМА ТАКЪВ НАРОД" and "28 632 €" in html                # the bar of the page is the same number, rounded to the euro


def test_money_and_non_monetary_value_are_never_one_number(site):
    html = text_of(site.get("/izbori/81").text)
    assert "3 067,75 €" in html and "1 227,10 €" in html and "4 294,85" not in html
    out = rows_of(site.get("/export-dareniya.csv?e=81"))
    assert sum(D(r["money_eur"]) for r in out) == D("3067.7512871773109115") and sum(D(r["in_kind_eur"]) for r in out) == D("1227.1005148709243646")
    assert [r["kind"] for r in out if D(r["in_kind_eur"])] == ["in_kind"] * 3


def test_the_chosen_day_changes_the_number_the_table_the_chart_and_the_exports_alike(site):
    for day, n, money in (("2026-09-29", 2, "10 000,00 €"), ("2026-09-30", 45, "190 000,00 €")):
        html = text_of(site.get(f"/izbori/95?d={day}").text)
        assert f"{n} дарения" in html and money in html
        assert f"{n} дарения" in text_of(site.get(f"/?e=95&d={day}").text)
        assert len(rows_of(site.get(f"/export-dareniya.csv?e=95&d={day}"))) == n == len(site.get(f"/export-dareniya.json?e=95&d={day}").json()["rows"])
        pts = site.get(f"/api/dni.json?e=95&d={day}").json()["series"][0]["points"]
        assert pts[-1][1] == float(money.replace(" ", "").replace(",", ".").replace("€", ""))
    assert "45 дарения" in text_of(site.get("/izbori/95").text)                  # no day: the newest read
    assert len(rows_of(site.get("/export-dareniya.csv?e=95&d=2026-09-01"))) == 0   # before the first read: nothing


def test_two_days_are_compared_with_the_same_scope_and_unit(site):
    out = rows_of(site.get("/export-sravnenie.csv?e=95&a=2026-09-29&b=2026-09-30"))
    api = site.get("/export-sravnenie.json?e=95&a=2026-09-29&b=2026-09-30").json()["rows"]
    assert len(out) == len(api) and sum(D(r["money_b_eur"]) for r in out) == D("190000") and sum(D(r["money_a_eur"] or 0) for r in out) == D("10000")
    assert sum(D(r["money_diff_eur"]) for r in out) == D("180000")
    html = text_of(site.get("/izbori/95?a=2026-09-29&b=2026-09-30").text)
    assert "Сравнение на два дни" in html and "Парични към 29.09.2026" in html and "Парични към 30.09.2026" in html


def test_a_table_that_was_not_read_is_no_data_not_zero(site):
    html = text_of(site.get("/izbori/93").text)                                   # the fixtures have only the reports of this election
    assert "Регистърът на дарителите още не е прочетен" in html and "таблицата не е прочетена" in html and "0 дарения" not in html
    row = next(r for r in rows_of(site.get("/export-izbori.csv")) if r["erik_id"] == "93")
    assert row["donations"] == "" and row["money_eur"] == ""
    zero = next(r for r in rows_of(site.get("/export-izbori.csv")) if r["erik_id"] == "96")
    assert zero["candidate_rows"] == "0" and zero["candidate_money_eur"] == "0"   # read and empty: a real zero


def test_the_report_keeps_what_was_filed_with_the_euro_beside_it(site):
    out = rows_of(site.get("/export-otcheti.csv?e=79"))
    itn = next(r for r in out if r["participant_label"] == "ИМА ТАКЪВ НАРОД")
    assert itn["currency"] == "BGN" and itn["participant"] and abs(D(itn["income_eur"]) * D("1.95583") - D(itn["income"])) < D("0.0001")
    api = site.get("/export-otcheti.json?e=79").json()
    assert len(api["rows"]) == len(out) and "съвпадат при лева" in api["currency_evidence"]
    n = next(r["n"] for r in __import__("app.queries", fromlist=["x"]).reports(79) if r["participant_label"] == "ИМА ТАКЪВ НАРОД")
    page = site.get(f"/izbori/79/otchet/party/{n}")
    assert page.status_code == 200 and "Обобщен ред" in page.text and "Включен подред" in page.text and "budget-subtotal" in page.text
    lines = rows_of(site.get(f"/export-otchet.csv?e=79&l=party&n={n}"))
    assert len(lines) == 56 and {r["section"] for r in lines} == {"income", "expense", "in_kind", "total"}
    total = next(r for r in lines if r["code"] == "income.total")
    assert D(total["amount"]) == D(itn["income"]) and total["currency"] == "BGN"
    helps = json.loads(re.search(r'<script id="state-help" type="application/json">(.*?)</script>', page.text, re.S).group(1))
    assert len(helps["rows"]) == 56 and helps["rows"][0]["source"].startswith("https://erik.bulnao.government.bg")


def test_nothing_filed_is_shown_as_such(site):
    html = site.get("/izbori/81").text
    assert "няма подадено" in html
    out = rows_of(site.get("/export-otcheti.csv?e=81"))
    assert any(r["filled_cells"] == "0" and r["income"] == "" for r in out) and any(r["income"] not in ("", "0") for r in out)


def test_a_participant_page_adds_up_to_its_rows(site):
    html = text_of(site.get("/uchastnici/81-3-117586").text)
    assert "НИЕ ИДВАМЕ" in html and "3 дарения" in html and "0,00 € парични" in html and "1 227,10 € непарични" in html
    out = rows_of(site.get("/export-dareniya.csv?e=81&p=81-3-117586"))
    assert len(out) == 3 and sum(D(r["in_kind_eur"]) for r in out) == D("1227.1005148709243646") and {r["participant"] for r in out} == {"81-3-117586"}
    listed = rows_of(site.get("/export-uchastnici.csv?e=81"))
    me = next(r for r in listed if r["participant"] == "81-3-117586")
    assert D(me["donated_in_kind_eur"]) == D("1227.10") and me["registered_in"] == "ОИК"


def test_the_search_for_participants_feeds_the_page_and_the_export_alike(site):
    out = rows_of(site.get("/export-uchastnici.csv?q=народ"))
    api = site.get("/export-uchastnici.json?q=народ").json()["rows"]
    assert out and len(out) == len(api) and all("НАРОД" in r["name"].upper() for r in out)
    html = site.get("/uchastnici?q=народ").text
    assert html.count('href="/uchastnici/') >= len(out)


def test_agencies_have_their_eik_and_their_users(site):
    html = text_of(site.get("/agencii/116050625").text)
    assert "ЕИК 116050625" in html and "ИМА ТАКЪВ НАРОД - Партия" in html and "Тендер" in html
    out = rows_of(site.get("/export-agencii.csv?eik=116050625"))
    assert out and {r["agency_eik"] for r in out} == {"116050625"}
    assert len(rows_of(site.get("/export-agencii.csv?e=81"))) == len(site.get("/export-agencii.json?e=81").json()["rows"]) > 0
    assert "BG1080620844" in site.get("/agencii").text and "ЕИК с грешна контролна цифра" in site.get("/agencii").text


def test_the_sources_page_says_what_was_read_and_what_is_wrong(site):
    html = text_of(site.get("/sources").text)
    assert "Няма посочен лиценз" in html and "Избори и сверка на даренията" in html and "sources.json" in html
    j = site.get("/sources.json").json()
    assert j["tool"] == "partii" and len(j["elections"]) == 21 and {d["id"] for d in j["datasets"]} == {"partii.donations", "partii.candidates", "partii.agencies", "partii.reports"}
    assert all(d["distributed"] is False and d["licence"] == "не е посочен" for d in j["datasets"])
    e95 = next(e for e in j["elections"] if e["erik_id"] == 95)
    assert e95["donations"] == 45 and D(e95["money_eur"]) == D("190000") and e95["reads"] == 3 and len(e95["raw_sha256"]) == 64
    assert isinstance(j["problems"], list)


def test_every_help_has_a_text_and_a_source_and_every_trigger_has_a_help(site):
    from app import help as H
    for k, h in H.HELP["columns"].items():
        assert h["title"] and len(h["text"]) > 40 and h["source"], k
        assert "—" not in h["text"] + h["example"]
    for url in PAGES:
        html = site.get(url).text
        for k in set(re.findall(r'data-help="([a-z_]+)"', html)):
            assert k in H.HELP["columns"], (url, k)


def test_the_pages_read_only_the_serving_layer_and_say_which_publication(site):
    from pathlib import Path
    from app import queries
    src = Path(queries.__file__).read_text(encoding="utf-8")
    assert set(re.findall(r"(?:FROM|JOIN)\s+([a-z_]+)\.[a-z_]+", src)) == {"pub", "pub_partii"}
    r = site.get("/izbori/79")
    n = int(r.headers["x-publication"])
    assert n >= 1 and r.headers["etag"] == f'W/"pub-{n}"' and r.headers["last-modified"].endswith("GMT")
    assert site.get("/export-dareniya.csv?e=79").headers["x-publication"] == str(n)      # the page and its export are one publication
