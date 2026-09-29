import json
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from ingest.parse import parse_nvo7
from ingest.sources import parse_schools, reconcile


FIX = Path(__file__).parent / "fixtures/mon"


def sample_snapshot():
    results = parse_nvo7((FIX / "nvo7-resource-08157f0e.json").read_bytes())
    register = parse_schools((FIX / "school-register-236b21ed.json").read_bytes())
    check = reconcile(results, register)
    schools = {}
    for result in results:
        ref = register[result.neispuo]
        item = schools.setdefault(result.neispuo, dict(code=result.neispuo, name=ref.name,
            oblast=ref.oblast, municipality=ref.municipality, town=ref.town, matched=True, subjects={}))
        item["subjects"][result.subject] = dict(takers=result.takers, score=result.score, scale=result.scale)
    return dict(year="2025/2026", exam_resource="08157f0e-6a9d-45e9-8e45-e53be6b788e3",
        register_resource="236b21ed-25c2-4ebe-8688-c01587fc660d", exam_updated="2026-07-07",
        register_updated="2026-02-10", school_count=check["schools"], matched=check["matched"],
        unmatched=[], schools=list(schools.values()))


def test_pages_render_with_real_sample(monkeypatch):
    monkeypatch.setattr(main, "snapshot", sample_snapshot)
    monkeypatch.setattr(main, "school_history", lambda code, before: [])
    client = TestClient(main.app)
    for path in ("/", "/uchilishta", "/uchilishta/105201", "/sources", "/how"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "Образование" in response.text
        assert "Обратна връзка" in response.text
        assert "Подкрепи проекта" in response.text
        assert "\u2014" not in response.text
    school = client.get("/uchilishta/105201").text
    assert "56.38" in school and "33.13" in school
    assert "84 явили се" in school
    assert "2025/2026" in school
    assert client.get("/uchilishta/000000").status_code == 404


def test_filters_and_csv(monkeypatch):
    monkeypatch.setattr(main, "snapshot", sample_snapshot)
    client = TestClient(main.app)
    page = client.get("/uchilishta", params={"q": "Неофит", "oblast": "Благоевград"})
    assert page.status_code == 200
    assert "Неофит Рилски" in page.text
    assert "Панчо Владигеров" not in page.text
    csv = client.get("/uchilishta.csv", params={"q": "105201"})
    assert csv.status_code == 200
    assert csv.text.count("105201") == 1
    assert "56.38" in csv.text
    assert "08157f0e-6a9d-45e9-8e45-e53be6b788e3" in csv.text
    assert client.get("/uchilishta", params={"q": "непознато"}).status_code == 200


def test_empty_state_never_shows_zero(monkeypatch):
    monkeypatch.setattr(main, "snapshot", lambda: None)
    client = TestClient(main.app)
    assert "още се подготвят" in client.get("/").text
    assert "Още няма публикувани" in client.get("/uchilishta").text
    assert "0 училища" not in client.get("/").text
    assert client.get("/uchilishta.csv").status_code == 200


def test_weighted_average_excludes_no_takers():
    rows = sample_snapshot()["schools"]
    expected = sum(r["subjects"]["БЕЛ"]["score"] * r["subjects"]["БЕЛ"]["takers"]
        for r in rows if r["subjects"]["БЕЛ"]["takers"])
    takers = sum(r["subjects"]["БЕЛ"]["takers"] for r in rows)
    assert main.weighted(rows, "БЕЛ") == (expected / takers).quantize(Decimal("0.01"))


def test_history_separates_point_scales(monkeypatch):
    monkeypatch.setattr(main, "snapshot", sample_snapshot)
    monkeypatch.setattr(main, "school_history", lambda code, before: [
        dict(year="2024/2025", resource="new-resource", scale="points100", matched=True,
             subjects={"БЕЛ": dict(score=Decimal("57.50"), takers=80),
                       "МАТ": dict(score=Decimal("42.00"), takers=80)}),
        dict(year="2017/2018", resource="old-resource", scale="points65", matched=False,
             subjects={"БЕЛ": dict(score=Decimal("45.00"), takers=70),
                       "МАТ": dict(score=Decimal("35.00"), takers=70)})])
    page = TestClient(main.app).get("/uchilishta/105201")
    assert page.status_code == 200
    assert "Скала до 100 точки" in page.text
    assert "Скала до 65 точки" in page.text
    assert "2024/2025" in page.text and "2017/2018" in page.text
