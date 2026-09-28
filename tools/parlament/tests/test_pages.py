"""Every page rendered through the app, on an empty database and on two real sittings (AGENTS 4): a template error is a
500 for everyone, "няма данни" is never a 0, and every value in the lede is bold (AGENTS 7).

Runs only when PARLAMENT_TEST_DSN points at a disposable database (it is wiped).
"""
import os
import re

import pytest

DSN = os.environ.get("PARLAMENT_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="PARLAMENT_TEST_DSN not set")

PAGES = ["/", "/glasuvaniya", "/glasuvaniya?q=кодекс", "/glasuvaniya?ns=52", "/deputati", "/grupi", "/sources", "/how"]


def wipe():
    from ingest import db
    with db.connect(autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS live, ops CASCADE; DROP TABLE IF EXISTS public.schema_migrations")
        db.migrate(c)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app import main
    return TestClient(main.app)


@pytest.fixture
def loaded(tmp_path, monkeypatch):
    from ingest import db, load
    from tests.test_store import Source, run
    monkeypatch.setattr(load, "RAW", tmp_path / "raw")
    wipe()
    src = Source()
    with db.connect(autocommit=True) as c:
        run(c, src)
        load.roster(c, {}, get=src.get)
        from ingest import stats
        stats.rebuild(c, [52])


def test_every_page_renders_on_an_empty_database(client):
    wipe()
    for path in [p for p in PAGES if "ns=" not in p]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert "Още няма" in r.text or path in ("/sources", "/how"), path
    assert client.get("/glasuvane/1/1").status_code == 404 and client.get("/glasuvaniya?ns=52").status_code == 404
    assert client.get("/deputati/52/1").status_code == 404
    assert client.get("/healthz").json() == {"ok": True}


def test_every_page_renders_on_real_sittings(client, loaded):
    for path in PAGES + ["/glasuvane/11174/2", "/deputati/52/3839", "/glasuvaniya?ns=52&q=нищо такова"]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert "None" not in r.text and "nan" not in r.text, path
    assert client.get("/glasuvaniya?ns=40").status_code == 404
    assert client.get("/glasuvane/11174/99").status_code == 404


def test_the_lede_says_every_value_in_bold(client, loaded):
    for path in ("/", "/glasuvaniya", "/deputati", "/grupi", "/glasuvane/11174/2", "/deputati/52/3839"):
        lede = re.search(r'<p class="lede">(.*?)</p>', client.get(path).text, re.S).group(1)
        # what is not a value: the dates, the years, the assembly's number
        rest = re.sub(r"<b>.*?</b>|\b\d\d\.\d\d\.\d{4}\b|\b\d{1,2} [а-я]+ (19|20)\d\d\b|\b(19|20)\d\d\b|\d+-(во|ро|о) ", "",
                      re.sub(r"<span[^>]*>.*?</span>", "", lede))
        assert "<b>" in lede and not re.search(r"\d", rest), (path, lede)


def test_the_vote_page_the_hall_and_the_csv(client, loaded):
    html = client.get("/glasuvane/11174/2").text
    assert "ЗИД на Наказателния кодекс – първо гласуване" in html and "<b>183 за</b>" in html
    assert 'data-vote="/api/glasuvane/11174/2.json"' in html
    j = client.get("/api/glasuvane/11174/2.json").json()
    assert j["totals"] == {"yes": 183, "no": 10, "abstain": 0, "voted": 193} and len(j["seats"]) == 240
    assert j["groups"][0] == "ПБ" and {s["code"] for s in j["seats"]} <= {"+", "-", "=", "0"}
    assert next(s for s in j["seats"] if s["mp"] == 3839) == {"mp": 3839, "name": "Стефан Апостолов Апостолов", "grp": "ГЕРБ - СДС", "code": "+"}
    rows = client.get("/csv/glasuvane/11174/2.csv").text.splitlines()
    assert len(rows) == 241 and rows[0].lstrip("﻿") == "депутат,група,код,вот"
    assert len(client.get("/csv/glasuvaniya-52.csv").text.splitlines()) == 1 + 15
    assert len(client.get("/csv/deputati-52.csv").text.splitlines()) == 1 + 240


def test_the_mp_page_and_its_strip(client, loaded):
    html = client.get("/deputati/52/3839").text
    assert "Стефан Апостолов Апостолов" in html and 'data-strip="/api/deputat/52/3839.json"' in html
    v = client.get("/api/deputat/52/3839.json").json()["votes"]
    assert len(v) == 15 and v[0][2] == "2026-07-31" and v[-1][2] == "2026-09-24"
    assert [x[3] for x in v if x[2] == "2026-09-24"] == list("++++=000000")


def test_the_hall_of_the_assembly(client, loaded):
    j = client.get("/api/zala/52.json").json()
    assert sum(g["n"] for g in j["groups"]) == 240 and j["groups"][0] == {"grp": "ПБ", "n": 131}
    assert client.get("/api/zala/40.json").status_code == 404
    assert client.get("/api/grupi/52/edinstvo.json").status_code == 200
