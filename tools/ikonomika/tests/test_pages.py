"""Every page rendered through the app, on an empty database and on real answers (STANDARD 4, AGENTS 4):
a template error is a 500 for everyone, and "няма данни" is never a 0.

Runs only when IKONOMIKA_TEST_DSN points at a disposable database (it is wiped).
"""
import os
import re
from pathlib import Path

import pytest

DSN = os.environ.get("IKONOMIKA_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="IKONOMIKA_TEST_DSN not set")
FX = Path(__file__).parent / "fixtures"

PAGES = ["/", "/inflaciya", "/karta", "/karta?m=THS", "/karta?l=rayoni", "/karta?l=makrorayoni", "/karta?o=eu",
         "/karta?o=eu&l=oblasti", "/karta?m=EMP", "/karta?m=COE&o=eu", "/es", "/es?p=dalg", "/es?p=bezrabotica", "/es?p=lihva",
         "/es?p=bednost", "/es?p=tok", "/rastezh", "/zaetost", "/finansi", "/vanshen", "/pari", "/kursove", "/sources", "/how"]


def wipe():
    from ingest import db
    with db.connect(autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS live, ops CASCADE; DROP TABLE IF EXISTS public.schema_migrations")
        db.migrate(c)


def load():
    """Real answers into the database: GDP, the regions, the HICP of 2024-2026, the BNB day and a quarter."""
    from ingest import bnb, db, jsonstat, store
    with db.connect(autocommit=True) as c:
        def put(ind, name, pinned, unit=None):
            p = jsonstat.parse((FX / name).read_bytes(), pinned)
            rows = [r for r in p["rows"] if unit is None or r[0].get("unit") == unit]
            store.apply(c, "eurostat", ind, ind, name, rows, p["labels"])
            store.state(c, "eurostat", ind, updated=p["updated"], label=p["label"])
        put("gdp_a", "eurostat/gdp_bg_since2023.json", {"unit", "na_item"})
        # the regions: M€ of Bulgaria and € per person of some of Europe, in one answer as the import has it
        mio, eu = (jsonstat.parse((FX / n).read_bytes(), {"unit"}) for n in ("eurostat/gdp_nuts_mio.json", "eurostat/gdp_nuts_europe.json"))
        store.apply(c, "eurostat", "gdp_nuts", "gdp_nuts", "nuts", mio["rows"] + eu["rows"], {**mio["labels"], **eu["labels"]})
        store.state(c, "eurostat", "gdp_nuts", updated=mio["updated"], label=mio["label"])
        for ind, unit in (("hicp_i15", "I15"), ("hicp_rch_m", "RCH_M"), ("hicp_rch_a", "RCH_A")):
            put(ind, "eurostat/hicp_rates_2024.json", {"unit", "coicop18"}, unit)
        for ind, name, pinned in (("gdp_full", "eurostat/nama_10_gdp-2.json", {"unit"}), ("bop", "eurostat/bop_c6_q-2.json", set()),
                                  ("trade_a", "eurostat/ext_lt_intratrd-2.json", set()), ("hicp_w", "eurostat/prc_hicp_iw-2.json", set())):
            put(ind, name, pinned)
        from ingest import ecb, mf
        for key, code in (("MIR.M.BG.B.A2C.AM.R.A.2250.EUR.N", "housing"), ("MIR.M.U2.B.A2C.AM.R.A.2250.EUR.N", "housing")):
            geo = "BG" if ".BG." in key else "EA"
            rows = [({"series": code}, geo, t, v, f) for t, v, f in ecb.parse((FX / "ecb" / f"{key}.csv").read_bytes(), key)]
            store.apply(c, "ecb", "rates", f"rates-{geo}", key, rows, where="AND geo = %s", args=(geo,))
        items, first, _ = mf.parse((FX / "mf/2026-04-03.json").read_bytes())
        store.apply(c, "mf", "mf_forecast", "mf_forecast", "mf", [({"vintage": "2026-04-03", "item": k}, "BG", str(y), v, "f" if y >= first else None)
                                                                    for k, ys in items.items() for y, v in ys.items()],
                    {"vintage": {"2026-04-03": mf.short_name("Пролетна макроикономическа прогноза 2026 г.", "2026-04-03")}})
        for name in ("bnb/today.csv", "bnb/q2025-4.csv"):
            rows = bnb.parse((FX / name).read_bytes())
            for ind in {r[0] for r in rows}:
                store.apply(c, "bnb", ind, name, name, [(d, "BG", str(t), v, None) for i, d, t, v in rows if i == ind])


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fastapi.testclient import TestClient
    from ingest import store
    store.RAW = tmp_path_factory.mktemp("raw")
    wipe()
    from app import main
    return TestClient(main.app)


def check_page(r):
    assert r.status_code == 200, r.text[:2000]
    html = r.text
    head = html[html.index('<header'):html.index('</header>')]
    assert head.rindex('class="fb-open"') > head.rindex('class="hub"')      # Обратна връзка is last in the header
    assert re.findall(r'<a href="(/[^"]*)"', head)[:2] == ["/", "/karta"]   # the map is second in the menu (AGENTS 7)
    foot = html[html.index('<footer'):html.index('</footer>')]
    assert re.search(r'class="fb-support"[^>]*>Подкрепи проекта</a>\s*</div>$', foot.strip()), foot    # last in the footer
    assert ">0<" not in html.replace(" ", "")
    return html


def test_every_page_renders_on_an_empty_database(client):
    for path in PAGES:
        html = check_page(client.get(path))
        assert "None" not in re.sub(r"<script.*?</script>", "", html, flags=re.S), path
    assert client.get("/healthz").json() == {"ok": True, "rows": 0}


def test_pages_on_real_answers(client):
    load()
    for path in PAGES:
        check_page(client.get(path))
    home = client.get("/").text
    assert "116,02 млрд. €" in home and "предварителни данни" in home             # GDP 2025 is provisional
    infl = client.get("/inflaciya").text
    assert "Хранителни продукти и безалкохолни напитки" in infl and "август 2026" in infl
    assert 'value="CP01" aria-pressed="true"' in infl and 'value="CP02" aria-pressed="false"' in infl   # the picked groups are on
    assert 'value="SERV" aria-pressed="false"' in infl
    import re as _re
    for path in ("/", "/rastezh", "/zaetost", "/finansi", "/vanshen", "/pari"):   # every value in the lede is bold, dates are not
        lede = _re.search(r'<p class="lede">(.*?)</p>', client.get(path).text, _re.S).group(1)
        rest = _re.sub(r"<b>.*?</b>|\b(19|20)\d\d\b", "", lede)
        assert ("<b>" in lede or lede.strip() == "Още няма данни.") and not _re.search(r"\d", rest), (path, lede)
    for path in ("/inflaciya", "/rastezh", "/zaetost", "/finansi", "/vanshen", "/pari"):   # a chart is full width, or 2/3
        for size, body in _re.findall(r'<section class="p (s\d+)[^"]*"[^>]*>(.*?)</section>', client.get(path).text, _re.S):
            assert size in ("s12", "s8") or ('class="chart' not in body and 'class="hb"' not in body), (path, size, body[:120])
    for path in ("/inflaciya", "/rastezh", "/zaetost", "/finansi", "/vanshen", "/pari"):   # at most 3 lines on per chart
        html = client.get(path).text
        for box in _re.findall(r'<div class="chips".*?</div>\s*</div>', html, _re.S):
            assert box.count('aria-pressed="true"') <= 3, (path, box[:200])
    page = client.get("/karta?m=MIO_EUR&y=2024").text                    # the page: a shell, drawn by karta.js
    assert 'id="k-svg"' in page and "/static/karta.js" in page and 'id="k-data"' in page and "EuroGeographics" in page
    assert "медиана" not in page and "mkey" not in page                     # no legend under any map (AGENTS 7)

    def api(qs):
        r = client.get("/api/karta.json?" + qs)
        assert r.status_code == 200, r.text
        return r.json()
    import json
    europe = json.loads((FX.parent.parent / "app/static/europe.json").read_text(encoding="utf-8"))
    bg = api("m=MIO_EUR&y=2024")
    by = {it["code"]: it for it in bg["items"]}
    assert len(by) == 28 == len(europe["bg"]["3"]) and by["BG411"]["name"] == "София (столица)" and by["BG411"]["rank"] == 1
    assert bg["items"][0]["code"] == "BG411" and bg["levels"][0] == ["oblasti", "Области"]          # by rank
    reg = api("m=MIO_EUR&y=2024&l=rayoni")
    assert [it["name"] for it in reg["items"]][0] == "Югозападен район" and len(reg["items"]) == 6 and reg["single"] == "Район"
    assert len(api("m=MIO_EUR&y=2024&l=makrorayoni")["items"]) == 2
    for bad in ("y=1999", "m=X", "l=obshtini", "o=bg&l=darzhavi", "o=xx"):
        assert client.get("/karta?" + bad).status_code == 404 and client.get("/api/karta.json?" + bad).status_code == 404
    eu = api("o=eu&y=2024")                                              # the countries of Europe
    by = {it["code"]: it for it in eu["items"]}
    assert set(europe["0"]) <= set(by) and by["DE"]["name"] == "Германия" and by["DE"]["v"] == 51800
    assert by["NO"]["v"] is None                                         # 2024 not yet out for Norway: "няма данни"
    assert by["BY"]["name"] == "Беларус" and by["BY"]["v"] is None and by["MD"]["v"] is None      # outside NUTS, still listed
    obl = api("o=eu&l=oblasti&y=2024")
    by = {it["code"]: it for it in obl["items"]}
    assert len(by) == len(europe["3"]) and by["DE111"]["name"] == "Stuttgart, Stadtkreis [DE]"      # the country's code with the name
    assert by["BG411"]["name"] == "София (столица) [BG]" and "BY" not in by
    moved = client.get("/oblasti?m=THS", follow_redirects=False)                # the old address still leads there
    assert moved.status_code == 301 and moved.headers["location"] == "/karta?m=THS"
    from ingest import db
    with db.connect(autocommit=True) as c:                    # a region without a value is None ("няма данни"), not 0
        c.execute("DELETE FROM live.series WHERE indicator = 'gdp_nuts' AND geo = 'BG311' AND time = '2024'")
    it = next(it for it in api("m=MIO_EUR&y=2024")["items"] if it["code"] == "BG311")
    assert it["v"] is None and it["change"] is None and "rank" not in it
    with db.connect(autocommit=True) as c:                    # and on a page: a missing monthly rate is "няма данни"
        c.execute("DELETE FROM live.series WHERE indicator = 'hicp_rch_m' AND dims->>'coicop18' = 'CP01' AND time = '2026-08'")
    infl = client.get("/inflaciya").text
    row = infl[infl.index("Хранителни продукти и безалкохолни напитки <span"):]
    assert "няма данни" in row[:row.index("</tr>")]
    fx = client.get("/pari?code=USD").text
    assert "1,1403" in fx and "Щатски долар" in fx
    assert client.get("/pari?code=XYZ").status_code == 404
    moved = client.get("/kursove?code=USD", follow_redirects=False)                # Курсове is now Пари
    assert moved.status_code == 301 and moved.headers["location"] == "/pari?code=USD"
    assert "Жилищните кредити в България струват <b>2,43% годишно</b> (юли 2026), в еврозоната <b>3,54%</b>." in fx
    assert "СОФИБОР" in fx and 'int_rt=IRT_DTD,IRT_M3' in fx                         # the interbank market: why the lines stop
    fin = client.get("/finansi").text
    assert 'data-src="/api/prognoza/gdp_growth.json"' in fin                        # one point a year, three lines
    j = client.get("/api/prognoza/gdp_growth.json").json()
    assert [s["name"] for s in j["series"]] == ["Какво стана (Eurostat)", "Прогноза на МФ от пролетта на същата година",
                                                "Прогноза на МФ от есента на предходната година"]
    assert j["series"][1]["points"] == [["2026", 2.6, "f"]]                          # the spring 2026 forecast for 2026
    assert ["2025", 3.1, "p"] in j["series"][0]["points"]                            # Eurostat's 2025, provisional
    assert client.get("/api/prognoza/nope.json").status_code == 404
    infl = client.get("/inflaciya").text
    assert "Какво движи инфлацията, август 2026" in infl and "процентни пункта" in infl
    ext = client.get("/vanshen").text
    assert "Текущата сметка за" in ext and "Износ по стокови групи, 2025" in ext
    assert "млрд. €" in ext and not _re.search(r"\d \d{3} млн\.", ext)               # a thousand millions is a billion
    grow = client.get("/rastezh").text
    assert "През 2025 г. БВП на България расте реално" in grow
    emp = api("m=EMP&l=oblasti")                                                    # the survey stops at the regions
    assert emp["l"] == "rayoni" and [k for k, _ in emp["levels"]] == ["rayoni", "makrorayoni"]
    page = client.get("/karta?m=EMP").text                                        # and the page offers only those levels
    assert 'data-l="rayoni"' in page and 'data-l="oblasti"' not in page
    src = client.get("/sources").text
    assert "наред" in src and ">ok<" not in src and "help/copyright-notice" in src         # the state in Bulgarian


def test_api_and_csv(client):
    load()
    j = client.get("/api/gdp_a.json?geo=BG&na_item=B1GQ").json()
    assert j["series"][0]["points"][-1] == ["2025", 116018.3, "p"] and j["source"]["dataset"] == "nama_10_gdp"
    j = client.get("/api/hicp_rch_a.json?geo=BG&coicop18=TOTAL,CP01").json()
    food = "Хранителни продукти и безалкохолни напитки"
    assert [s["name"] for s in j["series"]] == ["Общо", food]        # in the order asked for
    j = client.get("/api/hicp_rch_a.json?geo=BG&coicop18=CP01,TOTAL").json()
    assert [s["name"] for s in j["series"]] == [food, "Общо"]
    usd = client.get("/api/fx/USD.json").json()["points"]
    assert usd[-1] == ["2026-09-25", 1.1403, "eur"]
    assert ["2025-10-01", round(1.95583 / 1.66823, 6), "bgn"] in usd                # leva turned into euro by the fixed rate
    r = client.get("/csv/gdp_nuts.csv")
    lines = r.text.lstrip("﻿").splitlines()
    assert lines[0] == "indicator,unit,geo,time,value,flag" and len(lines) == 926 + 13   # + the € per person of some of Europe
    assert client.get("/api/nope.json").status_code == 404 and client.get("/csv/nope.csv").status_code == 404
    assert client.get("/no-such-page").status_code == 404
