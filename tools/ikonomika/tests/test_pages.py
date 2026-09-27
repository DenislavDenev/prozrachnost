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

PAGES = ["/", "/inflaciya", "/oblasti", "/oblasti?m=THS", "/es", "/es?p=dalg", "/es?p=bezrabotica", "/kursove", "/sources", "/how"]


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
        put("gdp_nuts", "eurostat/gdp_nuts_mio.json", {"unit"})
        for ind, unit in (("hicp_i15", "I15"), ("hicp_rch_m", "RCH_M"), ("hicp_rch_a", "RCH_A")):
            put(ind, "eurostat/hicp_rates_2024.json", {"unit", "coicop18"}, unit)
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
    assert "116,0 млрд. €" in home and "предварителни данни" in home              # GDP 2025 is provisional
    infl = client.get("/inflaciya").text
    assert "Храни и безалкохолни напитки" in infl and "август 2026" in infl
    obl = client.get("/oblasti?m=MIO_EUR&y=2024").text
    assert "София (столица)" in obl and 'id="o-BG411"' in obl and obl.count("<path") == 28
    assert client.get("/oblasti?y=1999").status_code == 404 and client.get("/oblasti?m=X").status_code == 404
    from ingest import db
    with db.connect(autocommit=True) as c:                    # a region without a value is "няма данни", not 0
        c.execute("DELETE FROM live.series WHERE indicator = 'gdp_nuts' AND geo = 'BG311' AND time = '2024'")
    obl = client.get("/oblasti?m=MIO_EUR&y=2024").text
    row = obl[obl.index('id="o-BG311"'):]
    assert row[:row.index("</tr>")].count("няма данни") == 2 and 'class="none"' in obl
    fx = client.get("/kursove?code=USD").text
    assert "1,1403" in fx and "Щатски долар" in fx
    assert client.get("/kursove?code=XYZ").status_code == 404


def test_api_and_csv(client):
    load()
    j = client.get("/api/gdp_a.json?geo=BG&na_item=B1GQ").json()
    assert j["series"][0]["points"][-1] == ["2025", 116018.3, "p"] and j["source"]["dataset"] == "nama_10_gdp"
    j = client.get("/api/hicp_rch_a.json?geo=BG&coicop18=TOTAL,CP01").json()
    assert [s["name"] for s in j["series"]] == ["Общо", "Храни и безалкохолни напитки"]        # in the order asked for
    j = client.get("/api/hicp_rch_a.json?geo=BG&coicop18=CP01,TOTAL").json()
    assert [s["name"] for s in j["series"]] == ["Храни и безалкохолни напитки", "Общо"]
    usd = client.get("/api/fx/USD.json").json()["points"]
    assert usd[-1] == ["2026-09-25", 1.1403, "eur"]
    assert ["2025-10-01", round(1.95583 / 1.66823, 6), "bgn"] in usd                # leva turned into euro by the fixed rate
    r = client.get("/csv/gdp_nuts.csv")
    lines = r.text.lstrip("﻿").splitlines()
    assert lines[0] == "indicator,unit,geo,time,value,flag" and len(lines) == 926
    assert client.get("/api/nope.json").status_code == 404 and client.get("/csv/nope.csv").status_code == 404
    assert client.get("/no-such-page").status_code == 404
