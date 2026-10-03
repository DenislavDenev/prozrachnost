"""Every page rendered through the app, on an empty database and on the real fixtures (AGENTS 4): a template error is a 500
for everyone, "няма данни" is never a 0, the feedback button and the support link are in place, and every list has the
same scope as its CSV and JSON.

Runs only when ZAKONI_TEST_DSN points at a disposable database (it is wiped).
"""
import csv
import datetime as dt
import io
import json
import os
import re

import pytest

from tests.test_store import FX, T0, arch, build, conn  # noqa: F401  (fixtures)

DSN = os.environ.get("ZAKONI_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="ZAKONI_TEST_DSN not set")

PAGES = ["/", "/karta", "/aktove", "/konsultacii", "/strategii", "/sravnenie", "/tarsene", "/tarsene?q=закон", "/sources", "/how"]
API = ["/api/karta.json", "/api/sedmici.json", "/aktove.json", "/konsultacii.json", "/strategii.json", "/tarsene.json?q=закон", "/sravnenie.json", "/sources.json"]
CSVS = ["/karta.csv", "/aktove.csv", "/konsultacii.csv", "/strategii.csv", "/sravnenie.csv"]


@pytest.fixture
def client(conn):  # noqa: F811
    from fastapi.testclient import TestClient
    from app import main
    return TestClient(main.app)


@pytest.fixture
def loaded(conn, arch):  # noqa: F811
    from ingest import gold
    build(conn)
    gold.build(conn, {}, 1)


def test_every_page_renders_on_an_empty_database(client):
    for path in PAGES + API + CSVS:
        r = client.get(path)
        assert r.status_code == 200, (path, r.text[:300])
    assert client.get("/aktove/1").status_code == 404 and client.get("/aktove/1.json").status_code == 404
    assert client.get("/konsultacii/1-K").status_code == 404 and client.get("/konsultacii/1-K.json").status_code == 404
    assert client.get("/healthz").json() == {"ok": True}


def test_every_page_renders_on_real_data_and_has_the_feedback_button_and_the_support_link(client, loaded):
    for path in PAGES + ["/aktove/171000", "/aktove/170625", "/aktove/129997", "/konsultacii/12692-K", "/konsultacii/12367-K",
                         "/konsultacii?q=наредба", "/konsultacii?short=da", "/konsultacii?na=2026-09-20", "/aktove?q=закон&year=2026",
                         "/strategii?q=план", "/karta?m=short&l=oblasti", "/sravnenie?a=2026&b=2015", "/", "/?na=2026-09-14"]:
        r = client.get(path)
        assert r.status_code == 200, (path, r.text[:400])
        assert "Обратна връзка" in r.text and "Подкрепи проекта" in r.text, path
        head, foot = r.text.split("</header>")[0], r.text.split("<footer")[1]
        assert head.rstrip().endswith("</div>") and r.text.index("Обратна връзка") < r.text.index("</header>"), path
        assert foot.index("Подкрепи проекта") > foot.index("Как работи"), path           # the support link is the last in the footer
        assert "None" not in re.sub(r"<script.*?</script>", "", r.text, flags=re.S), path   # no Python None on a page


def test_no_data_is_never_a_zero(client, loaded):
    t = client.get("/aktove/129997").text                 # an archive act: no importer, no legal reason, no protocol
    assert "няма данни" in t
    s = client.get("/sravnenie?a=2015&b=2026").text
    assert "няма данни" in s and "преди правилото за 30 дни" in s or "преди 04.11.2016" in s


def test_the_home_sentence_has_its_numbers_in_bold(client, loaded):
    t = client.get("/?na=2026-09-14").text
    lede = re.search(r'<p class="lede">(.*?)</p>', t, re.S).group(1)
    assert re.search(r"<b>\d+ ак[та]+</b>", lede) and re.search(r"<b>\d+ проект", lede)


def test_a_list_has_the_same_scope_as_its_csv_and_json(client, loaded):
    for path, key in (("/aktove", "pris_id"), ("/konsultacii", "reg_num"), ("/strategii", "doc_key")):
        for qs in ("", "?year=2026", "?q=наредба", "?q=закон&year=2026"):
            j = client.get(f"{path}.json{qs}").json()
            c = list(csv.DictReader(io.StringIO(client.get(f"{path}.csv{qs}").content.decode("utf-8-sig"))))
            assert j["total"] == len(j["data"]) == len(c), (path, qs)
            assert [str(x[key]) for x in j["data"]] == [x[key] for x in c], (path, qs)
            assert j["licence"].startswith("CC-BY") and j["source"].startswith("https://data.egov.bg/") and j["build"], (path, qs)
            page = client.get(f"{path}{qs}").text
            assert f"<b>{j['total']} " in page or j["total"] == 0 or f"<b>{j['total']:,}".replace(",", " ") in page, (path, qs)


def test_type_and_year_filters_apply_to_the_table_the_csv_and_the_json(client, loaded):
    j = client.get("/aktove.json?type=Постановления").json()
    assert j["total"] and all(r["act_type"] == "Постановления" for r in j["data"])
    j = client.get("/aktove.json?year=1988").json()
    assert j["total"] and all(r["accepted"].startswith("1988") for r in j["data"])
    j = client.get("/aktove.json?gazette=da").json()
    assert all(r["gazette_number"] is not None for r in j["data"])


def test_oblast_and_municipality_are_one_pair_in_the_table_the_csv_and_the_json(client, loaded):
    from ingest import db
    with db.connect() as c:
        devin, oblast = c.execute("SELECT id, oblast FROM ref.municipality WHERE name_bg = 'Девин'").fetchone()
    base = client.get("/konsultacii.json").json()["total"]
    a = client.get(f"/konsultacii.json?oblast={oblast}&municipality={devin}").json()
    assert a["total"] >= 1 and all(r["municipality_id"] == devin and r["oblast"] == oblast for r in a["data"])
    assert client.get(f"/konsultacii.json?oblast=Варна&municipality={devin}").json()["total"] == 0       # not the same pair: nothing
    only_oblast = client.get(f"/konsultacii.json?oblast={oblast}").json()
    assert 1 <= only_oblast["total"] < base and all(r["oblast"] == oblast for r in only_oblast["data"])
    csv_rows = list(csv.DictReader(io.StringIO(client.get(f"/konsultacii.csv?oblast={oblast}&municipality={devin}").content.decode("utf-8-sig"))))
    assert len(csv_rows) == a["total"]
    page = client.get(f"/konsultacii?oblast={oblast}").text
    assert 'id="f-oblast"' in page and 'id="f-municipality"' in page and f'data-oblast="{oblast}"' in page


def test_short_term_filter_and_the_open_on_a_day(client, loaded):
    j = client.get("/konsultacii.json?short=da").json()
    assert j["total"] and all(r["short_term_applies"] and r["days"] < 30 and r["opened"] >= "2016-11-04" for r in j["data"])
    on = client.get("/konsultacii.json?na=2026-09-20").json()
    assert on["total"] and all(r["opened"] <= "2026-09-20" <= r["closes"] for r in on["data"])


def test_one_act_and_one_consultation(client, loaded):
    a = client.get("/aktove/171000.json").json()["data"]
    assert a["pris_id"] == 171000 and a["doc_num"] == "716" and a["accepted"] == "2026-09-14" and a["act_type"] == "Решения"
    p = client.get("/aktove/170625").text
    assert "Актът е поверителен" in p
    c = client.get("/konsultacii/12692-K.json").json()["data"]
    assert (c["opened"], c["closes"], c["days"], c["short_term_applies"]) == ("2026-09-15", "2026-10-15", 30, False)
    assert c["source_url"] == "https://strategy.bg/bg/public-consultations/12692"
    t = client.get("/konsultacii/12692-K").text
    assert "https://strategy.bg/bg/public-consultations/12692" in t and "Срокът е поне 30 дни" in t


def test_the_map_data_is_the_municipal_consultations_by_municipality_and_oblast(client, loaded):
    p = client.get("/api/karta.json?m=n&l=obshtini&y=2026").json()
    assert p["o"] == "bg" and p["nuts"] == 4 and p["y"] == "2026" and len(p["items"]) == 265
    assert sum(i["n"] for i in p["items"]) == _matched_municipal(2026) >= 1
    no = [i for i in p["items"] if i["n"] == 0]
    assert no and all(i["v"] == 0 for i in no)                                  # a count of nothing is 0; the share has no data
    share = client.get("/api/karta.json?m=share&l=obshtini&y=2026").json()
    assert all(i["v"] is None for i in share["items"] if i["n"] == 0)
    ob = client.get("/api/karta.json?m=n&l=oblasti&y=2026").json()
    assert ob["nuts"] == 3 and len(ob["items"]) == 28 and sum(i["n"] for i in ob["items"]) == sum(i["n"] for i in p["items"])
    assert client.get("/api/karta.json?m=nothing").status_code == 400
    c = list(csv.DictReader(io.StringIO(client.get("/karta.csv?m=n&l=obshtini&y=2026").content.decode("utf-8-sig"))))
    assert len(c) == 265


def _matched_municipal(year):
    from ingest import db
    with db.connect() as c:
        return c.execute("SELECT count(*) FROM gold.consultation WHERE municipality_id IS NOT NULL AND extract(year FROM opened) = %s", (year,)).fetchone()[0]


def test_search_finds_words_from_the_beginning_and_survives_odd_input(client, loaded):
    j = client.get("/tarsene.json?q=законопроект").json()["data"]
    assert j["counts"]["acts"] >= 2 and all("ЗАКОНОПРОЕКТ" in a["about"].upper() for a in j["acts"])
    j = client.get("/tarsene.json?q=закон").json()["data"]
    assert j["counts"]["acts"] >= 2          # "закон" is the beginning of "законопроект" too
    for q in ("'; DROP TABLE gold.act; --", "<script>alert(1)</script>", "&", "   ", "а" * 5000, "ъ:* | !"):
        assert client.get("/tarsene", params={"q": q}).status_code == 200, q
        assert client.get("/aktove", params={"q": q}).status_code == 200, q
    assert client.get("/aktove.json").json()["total"] > 0


def test_compare_gives_the_same_measures_for_both_periods_and_says_when_a_period_has_no_rule(client, loaded):
    j = client.get("/sravnenie.json?a=2026&b=2015").json()["data"]
    assert j["a"]["consultations"]["n"] >= 1 and j["notes"]
    assert j["a"]["frm"] == "2026-01-01" and j["b"]["to"] == "2015-12-31"
    same = client.get("/sravnenie.json?a=2026&b=2026").json()["data"]
    assert same["a"]["acts_total"] == same["b"]["acts_total"] and same["a"]["consultations"] == same["b"]["consultations"]
    m = client.get("/sravnenie.json?a=2026-03&b=2026-03-01:2026-03-31").json()["data"]
    assert m["a"]["frm"] == m["b"]["frm"] == "2026-03-01" and m["a"]["to"] == m["b"]["to"] == "2026-03-31"
    assert client.get("/sravnenie?a=x&b=y").status_code == 200        # nonsense falls back to the default periods


def test_sources_lists_every_resource_the_json_has_a_licence_and_the_reconciliations(client, loaded):
    j = client.get("/sources.json").json()
    assert len(j["data"]) == 14 and j["licence"].startswith("CC-BY") and len(j["reconciliations"]) >= 5
    t = client.get("/sources").text
    assert "CC-BY" in t and "Обществени консултации, обединена справка" in t and "известна разлика" in t


def test_a_static_file_and_the_favicon(client):
    assert client.get("/favicon.svg").status_code == 200 and client.get("/static/app.js").status_code == 200
    assert client.get("/static/karta.js").status_code == 200 and client.get("/static/bg-municipalities.json").status_code == 200
