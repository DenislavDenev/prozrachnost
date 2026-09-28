"""Every page rendered through the app, on an empty database and on two real sittings (AGENTS 4): a template error is a
500 for everyone, "няма данни" is never a 0, and every value in the lede is bold (AGENTS 7).

Runs only when PARLAMENT_TEST_DSN points at a disposable database (it is wiped).
"""
import os
import re

import pytest

DSN = os.environ.get("PARLAMENT_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="PARLAMENT_TEST_DSN not set")

PAGES = ["/", "/glasuvaniya", "/glasuvaniya?q=кодекс", "/glasuvaniya?ns=52", "/deputati", "/grupi", "/sources", "/how",
         "/zasedaniya", "/tarsene", "/sabraniya"]
# the pages of the sittings, the stenogram and the people, on the two real sittings (31.07.2026 with its stenogram)
MORE = ["/zasedaniya?g=2026", "/zasedaniya?ns=52", "/zasedanie/11159", "/zasedanie/11174", "/zasedanie/11159/stenograma",
        "/izkazvane/11159/3", "/tarsene?q=заседание", "/tarsene?q=заседание&ns=52", "/tarsene?q=нищо-такова", "/grupa/52/ПБ",
        "/grupi?ns=52&date=2026-08-01", "/chovek/5237", "/chovek/4842"]


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
    from tests.test_store import FX, everyone
    src = Source()
    src.files["pl-sten/11159"] = (FX / "sten-310726-text.json").read_bytes()
    with db.connect(autocommit=True) as c:
        run(c, src)
        everyone(c, src)


def test_every_page_renders_on_an_empty_database(client):
    wipe()
    for path in [p for p in PAGES if "ns=" not in p]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert "Още няма" in r.text or path in ("/sources", "/how", "/tarsene", "/sabraniya"), path
    assert client.get("/glasuvane/1/1").status_code == 404 and client.get("/glasuvaniya?ns=52").status_code == 404
    assert client.get("/deputati/52/1").status_code == 404
    for path in ("/zasedanie/1", "/zasedanie/1/stenograma", "/izkazvane/1/1", "/chovek/1", "/grupa/52/ПБ", "/zasedaniya?ns=52"):
        assert client.get(path).status_code == 404, path
    assert client.get("/healthz").json() == {"ok": True}


def test_every_page_renders_on_real_sittings(client, loaded):
    for path in PAGES + MORE + ["/glasuvane/11174/2", "/deputati/52/3839", "/glasuvaniya?ns=52&q=нищо такова"]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert "None" not in r.text and "nan" not in r.text, path
    assert client.get("/glasuvaniya?ns=40").status_code == 404
    assert client.get("/glasuvane/11174/99").status_code == 404
    for path in ("/zasedanie/11174/stenograma", "/izkazvane/11159/999", "/grupa/52/НЯМА", "/zasedaniya?g=1700"):
        assert client.get(path).status_code == 404, path


def test_the_lede_says_every_value_in_bold(client, loaded):
    for path in ("/", "/glasuvaniya", "/deputati", "/grupi", "/glasuvane/11174/2", "/deputati/52/3839", "/zasedaniya",
                 "/zasedanie/11159", "/grupa/52/ПБ", "/chovek/5237", "/chovek/4842"):
        lede = re.search(r'<p class="lede">(.*?)</p>', client.get(path).text, re.S).group(1)
        # what is not a value: the dates, the years, the assembly's number
        rest = re.sub(r"<b>.*?</b>|\b\d\d\.\d\d\.\d{4}\b|\b\d{1,2} [а-я]+ (18|19|20)\d\d\b|\b(18|19|20)\d\d\b|\d+-(во|ро|о) ", "",
                      re.sub(r"<(?!/?b>)[^>]+>", "", re.sub(r"<span[^>]*>.*?</span>", "", lede)))
        assert "<b>" in lede and not re.search(r"\d", rest), (path, lede)


def test_the_vote_page_the_hall_and_the_csv(client, loaded):
    html = client.get("/glasuvane/11174/2").text
    assert "ЗИД на Наказателния кодекс – първо гласуване" in html and "<b>183 за</b>" in html
    assert 'data-vote="/api/glasuvane/11174/2.json"' in html
    j = client.get("/api/glasuvane/11174/2.json").json()
    assert j["totals"] == {"yes": 183, "no": 10, "abstain": 0, "voted": 193} and len(j["seats"]) == 240
    assert j["groups"][0] == {"grp": "ПБ", "name": "Прогресивна България", "color": "#034A3F"}
    assert {s["code"] for s in j["seats"]} <= {"+", "-", "=", "0"}
    assert next(s for s in j["seats"] if s["mp"] == 3839) == {"mp": 3839, "name": "Стефан Апостолов Апостолов", "grp": "ГЕРБ-СДС", "code": "+"}
    assert html.count('class="v0"') == 183 and html.count('class="v2"') == 10          # a dot per MP: for, against
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
    assert sum(g["n"] for g in j["groups"]) == 240
    assert j["groups"][0] == {"grp": "ПБ", "n": 131, "name": "Прогресивна България", "color": "#034A3F"}
    assert client.get("/api/zala/40.json").status_code == 404
    assert client.get("/api/grupi/52/edinstvo.json").status_code == 200


def test_the_sitting_its_video_and_its_stenogram(client, loaded):
    html = client.get("/zasedanie/11159").text
    assert "Заседание от 31 юли 2026 г." in html and "archive-2026_07_31_1.mp4" in html
    assert 'href="/zasedanie/11159/stenograma" target="_blank"' in html              # the stenogram opens in a new tab
    assert html.count('class="sp') == 39 and 'id="i1"' in html and 'href="/chovek/5237"' in html   # the chair is linked
    assert '<em class="stage">' in html                                            # "(Звъни.)" and the like set apart
    assert "/glasuvane/11159/" in html                                             # the votes of the day
    steno = client.get("/zasedanie/11159/stenograma").text
    assert steno.count('class="sp') == 39
    one = client.get("/izkazvane/11159/3").text
    assert 'id="i3"' in one and 'id="i2"' in one and 'id="i4"' in one            # with the one before and after
    empty = client.get("/zasedanie/11174").text                                    # not published yet
    assert "до 7 дни" in empty and 'class="sp' not in empty
    assert "/zasedanie/11159" in client.get("/zasedaniya?g=2026").text


def test_the_search_the_person_and_the_group(client, loaded):
    html = client.get("/tarsene?q=заседани").text                                  # by the beginning of the word
    assert "<mark>" in html and "/izkazvane/11159/" in html
    assert "Няма изказвания" in client.get("/tarsene?q=нищо-такова").text
    p = client.get("/chovek/4842").text                                            # Атанас Атанасов: the 51st and the 52nd
    assert "Атанас Петров Атанасов" in p and "51-во НС" in p and "52-ро НС" in p
    g = client.get("/grupa/52/ПБ").text
    assert "Прогресивна България" in g and 'data-src="/api/grupa/52/%D0%9F%D0%91.json"' in g
    j = client.get("/api/grupa/52/ПБ.json").json()["series"][0]
    assert j["color"] == "#034A3F" and [x[0] for x in j["points"]] == ["2026-07-31", "2026-09-24"]
    assert client.get("/api/zala/52.json?date=2026-08-01").json()["date"] == "2026-08-01"
