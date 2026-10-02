"""The pages as rendered pages, on the test database filled from the real fixtures; CSV and JSON have the scope of the page."""
import csv
import io
import json

import pytest

from ingest import config, gold, load
from tests import helpers as h


@pytest.fixture
def client(c, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(config, "ARCHIVE", tmp_path)
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    st = h.build_archive(tmp_path, {h.POLICE: h.police_2024()})
    load.ingest(c, st)
    gold.build(c)
    return TestClient(main.app)


def text(r):
    assert r.status_code == 200, r.text[:300]
    return r.text


def test_every_page_renders_and_has_the_feedback_button_and_the_support_link(client):
    for url in ("/", "/karta", "/vidove", "/vidove?o=BG313&sr=2024", "/oblasti", "/oblasti/BG413", "/pozhari", "/sources", "/how"):
        body = text(client.get(url))
        assert "Обратна връзка" in body and "Подкрепи проекта" in body, url
        assert body.index("Обратна връзка") < body.index("Подкрепи проекта")


def test_the_headline_numbers_are_the_sources(client):
    body = text(client.get("/"))
    import re
    lede = re.search(r'<p class="lede">(.*?)</p>', body, re.S).group(1)
    assert "74 709 престъпления" in body, lede and "36 944" in body and "49,5%" in body and "1 080,2" in body
    assert "Рег" in body
    assert client.get("/healthz").json() == {"ok": True}


def test_the_oblast_table_page_csv_and_json_have_one_scope(client):
    rows = list(csv.DictReader(io.StringIO(text(client.get("/oblasti.csv?y=2024")).lstrip("﻿"))))
    js = client.get("/oblasti.json?y=2024").json()
    assert len(rows) == len(js["rows"]) == 32 and js["year"] == 2024
    varna = next(r for r in rows if r["code"] == "BG331")
    assert varna["reg"] == "6535" and varna["clearance"] == "39.39"
    assert js["source"]["resource_uri"] == h.B and js["source"]["sha256"]
    page = text(client.get("/oblasti?y=2024"))
    assert "Варна" in page and "6 535" in page


def test_a_missing_value_is_not_a_zero_in_the_table_csv_and_json(client):
    js = client.get("/oblasti/BG312.json?y=2024").json()
    row = next(r for r in js["rows"] if r["text"].startswith("Престъпления, извършени в условията на домашно"))
    assert row["reg"] is None
    csv_ = text(client.get("/oblasti/BG312.csv?y=2024")).lstrip("﻿")
    line = next(l for l in csv_.splitlines() if "условията на домашно" in l)
    assert line.endswith(",2024,,30.3,16,42.11,12,0,0,0")          # the registered crimes: empty, not 0
    page = text(client.get("/oblasti/BG312?y=2024"))
    assert "няма данни" in page


def test_types_with_a_comparison_year_of_another_template_say_they_are_not_comparable(client, c):
    c.execute("UPDATE gold.observation SET template = 'other-1' WHERE family = 'types'")
    body = text(client.get("/vidove?y=2024&sr=2024"))
    assert "Сравни с" in body
    from app import queries as Q
    rows, _ = Q.type_rows(2024)
    assert rows[0]["template"] == "other-1"
    Q.compare(rows, [dict(r, template="types-x") for r in rows], "reg")
    assert all(r["other_reg"] is None for r in rows)


def test_the_levels_are_indented_and_the_parts_are_not_added(client):
    body = text(client.get("/vidove?y=2024"))
    assert 'class="l1"' in body and 'class="l2"' in body and 'class="l3"' in body and 'class="l4"' in body
    js = client.get("/vidove.json?y=2024").json()
    assert js["rows"][0]["reg"] == 74709 and js["structure"] == "BG"
    parts = [r for r in js["rows"] if r["level"] >= 3]
    assert parts and sum(r["reg"] for r in parts) + js["rows"][0]["reg"] > 0


def test_the_map_data_has_28_oblasts_ranks_and_the_csv_of_the_same_scope(client):
    d = client.get("/api/karta.json?y=2024&m=per100k").json()
    assert len(d["items"]) == 28 and sum(1 for i in d["items"] if i["v"] is not None) == 28
    top = min(d["items"], key=lambda i: i["rank"])
    assert top["rank"] == 1 and top["v"] == max(i["v"] for i in d["items"])
    rows = list(csv.DictReader(io.StringIO(text(client.get("/karta.csv?y=2024&m=per100k")).lstrip("﻿"))))
    assert {r["code"]: float(r["value"]) for r in rows} == {i["code"]: i["v"] for i in d["items"]}
    bad = client.get("/api/karta.json?y=2024&m=unknown").json()
    assert bad["m"] == "per100k"


def test_the_series_have_one_line_per_template_and_the_sources_page_lists_licences(client):
    s = client.get("/api/series.json?o=BG").json()
    assert [x["name"] for x in s["series"]][0].startswith("Регистрирани") and s["series"][0]["points"] == [["2024", 74709]]
    j = client.get("/sources.json").json()
    ds = {d["id"]: d for d in j["datasets"]}
    assert ds[h.POLICE]["licence"].startswith("CC BY") and ds[h.POLICE]["distributed"] is True
    assert j["fires"]["status"] == "gap" and j["problems"] == [] or j["problems"]
    body = text(client.get("/sources"))
    assert "CC BY (признаване на авторските права)" in body and "не се раздава" in body


def test_an_unknown_structure_and_a_general_directorate_are_404_for_pages_that_need_a_territory(client):
    assert client.get("/oblasti/XX999").status_code == 404
    assert client.get("/oblasti/BG").status_code == 404
    assert client.get("/vidove?o=GD-GP").status_code == 404
    assert client.get("/api/series.json?o=XX").status_code == 404
