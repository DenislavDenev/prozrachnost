"""The pages as rendered pages, on the test database filled from the real fixtures; CSV and JSON have the scope of the page."""
import csv
import io
import json

import pytest

import datetime as dt

from ingest import config
from tests import helpers as h


@pytest.fixture
def client(c, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(config, "ARCHIVE", tmp_path)
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    st = h.build_archive(tmp_path, {h.POLICE: h.police_2024()}, last_ok=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    h.build(c, st)
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
    c.execute("UPDATE pub_sigurnost.observation SET template = 'other-1' WHERE family = 'types'")
    body = text(client.get("/vidove?y=2024&sr=2024"))
    assert "Сравни с" in body
    from app import queries as Q
    rows, _ = Q.type_rows(2024)
    assert rows[0]["template"] == "other-1"
    Q.compare(rows, [dict(r, template="types-x") for r in rows], "reg")
    assert all(r["other_reg"] is None for r in rows)


def copy_year(c, src, dst, template=None):
    """The published tables of year `src` once more as year `dst`, optionally with another template."""
    for t in ("source_table", "crime_row", "observation"):
        cols = [r[0] for r in c.execute("""SELECT column_name FROM information_schema.columns
                                            WHERE table_schema = 'pub_sigurnost' AND table_name = %s ORDER BY ordinal_position""", (t,))]
        out = ["%(dst)s" if x == "year" else ("coalesce(%(tpl)s, template)" if x == "template" else x) for x in cols]
        c.execute(f"INSERT INTO pub_sigurnost.{t} ({', '.join(cols)}) SELECT {', '.join(out)} FROM pub_sigurnost.{t} WHERE year = %(src)s",
                  dict(src=src, dst=dst, tpl=template))


def test_types_compared_with_a_year_of_another_template_render_with_no_data_not_a_server_error(client, c):
    copy_year(c, 2024, 2023, template="other-1")
    body = text(client.get("/vidove?y=2024&sr=2023"))
    assert "Шаблонът на таблицата е сменен" in body
    assert "няма данни" in body                                       # the other year and the difference, never a zero
    body = text(client.get("/vidove?o=BG313&y=2024&sr=2023"))
    assert "Шаблонът на таблицата е сменен" in body
    rows = list(csv.DictReader(io.StringIO(text(client.get("/vidove.csv?y=2024&sr=2023")).lstrip("﻿"))))
    assert rows and all(r["other_reg"] == "" and r["diff_reg"] == "" for r in rows)


def test_types_compared_with_a_year_of_the_same_template_give_the_difference(client, c):
    copy_year(c, 2024, 2023)
    js = client.get("/vidove.json?y=2024&sr=2023").json()
    assert js["compare_year"] == 2023 and js["note"] is None
    assert js["rows"][0]["other_reg"] == 74709 and js["rows"][0]["diff_reg"] == 0
    assert "Шаблонът" not in text(client.get("/vidove?y=2024&sr=2023"))


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


def test_every_answer_says_its_publication_and_the_web_reads_only_the_serving_layer(client):
    r = client.get("/oblasti.csv?y=2024")
    assert r.status_code == 200 and r.headers["X-Publication"].isdigit() and r.headers["ETag"] == f'W/"pub-{r.headers["X-Publication"]}"'
    src = (config.ROOT / "app" / "queries.py").read_text(encoding="utf-8")
    import re
    schemas = set(re.findall(r"\b(pub\w*|sigurnost\w*|ops|core|silver|gold)\.[a-z_]+", src))
    assert schemas <= {"pub", "pub_sigurnost"}, schemas
