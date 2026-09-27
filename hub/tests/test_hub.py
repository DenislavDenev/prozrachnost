import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app as hub  # noqa: E402

client = TestClient(hub.app)
REG = json.loads((ROOT / "tools.json").read_text(encoding="utf-8"))


def test_registry_shape():
    tools = REG["tools"]
    assert len(tools) == 27
    assert len({t["slug"] for t in tools}) == 27
    keys = {g["key"] for g in REG["groups"]}
    assert keys == {"pari", "vlast", "obshtestvo", "sreda", "pazari", "obshti"}
    for t in tools:
        assert t["group"] in keys and t["status"] in ("active", "new", "soon")
        if t["status"] == "soon":
            assert list((ROOT / "docs" / "plans").glob(f"{t['no']:02d}-{t['slug']}.md")), f"no plan for {t['slug']}"
            assert len(t["questions"]) >= 3 and t["sources"]


def test_home_lists_every_tool_by_category():
    html = client.get("/").text
    for g in REG["groups"]:
        assert f'id="{g["key"]}"' in html and f'href="#{g["key"]}"' in html
    for t in REG["tools"]:
        assert t["name"] in html
        if t["status"] == "soon":
            assert f'href="/instrumenti/{t["slug"]}"' in html
    assert "brand-mark" not in html  # the header is the name and a green dot, nothing else
    assert 'прозрачност<span class="brand-dot">.</span>' in html


def test_live_cards_link_out():
    html = client.get("/").text
    assert 'href="https://tender.denev.work"' in html


def test_tool_page_and_404():
    r = client.get("/instrumenti/pazar")
    assert r.status_code == 200
    for s in next(t for t in REG["tools"] if t["slug"] == "pazar")["sources"]:
        assert s["url"] in r.text
    assert client.get("/instrumenti/nyama").status_code == 404
    assert client.get("/instrumenti/tender").status_code == 404  # working tools have their own site


def test_json_and_health():
    assert client.get("/tools.json").json()["groups"] == REG["groups"]
    assert client.get("/healthz").json()["tools"] == 27


@pytest.mark.parametrize("t", [t for t in REG["tools"] if t["status"] == "soon"], ids=lambda t: t["slug"])
def test_illustrations(t):
    svg = (ROOT / "static" / "illustrations" / f"{t['slug']}.svg").read_text(encoding="utf-8")
    root = ET.fromstring(svg)
    assert root.get("viewBox")
    assert "<script" not in svg.lower() and "http" not in svg.replace("http://www.w3.org/2000/svg", "").replace("http://www.w3.org/1999/xlink", "")
    assert "#6c63ff" not in svg.lower()  # unDraw's default purple is recoloured to the hub green


def test_broken_registry_refuses_to_start(tmp_path):
    bad = dict(REG, tools=REG["tools"] + [dict(REG["tools"][1])])  # duplicate slug
    p = tmp_path / "tools.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(AssertionError):
        hub.load_registry(p)
