import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from ingest.parse import parse_nvo7
from ingest.sources import parse_schools, reconcile


FIX = Path(__file__).parent / "fixtures/mon"


@pytest.fixture(autouse=True)
def default_nvo_reads(monkeypatch):
    monkeypatch.setattr(main, "nvo_sources", lambda: [])
    monkeypatch.setattr(main, "school_nvo", lambda code: [])
    monkeypatch.setattr(main, "school_identity", lambda code: None)
    monkeypatch.setattr(main, "school_status", lambda code: [])
    monkeypatch.setattr(main, "status_sources", lambda: [])
    monkeypatch.setattr(main, "context_snapshot", lambda: None)


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
    monkeypatch.setattr(main, "school_dzi", lambda code: [])
    monkeypatch.setattr(main, "source_history", lambda: [])
    monkeypatch.setattr(main, "dzi_sources", lambda: [])
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


def test_municipality_map_uses_weighted_scores_and_keeps_text_fallback(monkeypatch):
    monkeypatch.setattr(main, "snapshot", sample_snapshot)
    rows, unmapped = main.municipalities.scores(sample_snapshot())
    assert len(rows) == 265 and not unmapped
    bansko = next(row for row in rows if row["name"] == "Банско")
    assert bansko["bel"] == Decimal("56.38") and bansko["bel_takers"] == 84
    page = TestClient(main.app).get("/karta")
    assert page.status_code == 200
    assert "Карта на резултатите" in page.text
    assert "© EuroGeographics" in page.text
    assert 'data-bel="56.38"' in page.text
    assert "municipality-map.js" in page.text
    assert "municipality-map.js" not in TestClient(main.app).get("/").text


def test_municipality_reference_has_exact_capital_and_region_aliases():
    ref = main.municipalities.reference()
    assert len(ref) == 265
    assert ref[main.municipalities.key("СОФИЯ-ГРАД", "СТОЛИЧНА")]["name_bg"] == "Столична"
    assert ref[main.municipalities.key("СОФИЯ-ОБЛАСТ", "АНТОН")]["name_bg"] == "Антон"


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
    monkeypatch.setattr(main, "school_dzi", lambda code: [])
    monkeypatch.setattr(main, "school_history", lambda code, before: [
        dict(year="2024/2025", resource="new-resource", updated="2025-07-01", scale="points100", matched=True, verification="code",
             subjects={"БЕЛ": dict(score=Decimal("57.50"), takers=80),
                       "МАТ": dict(score=Decimal("42.00"), takers=80)}),
        dict(year="2017/2018", resource="old-resource", updated="2018-07-01", scale="points65", matched=False, verification="no-code",
             subjects={"БЕЛ": dict(score=Decimal("45.00"), takers=70),
                       "МАТ": dict(score=Decimal("35.00"), takers=70)})])
    page = TestClient(main.app).get("/uchilishta/105201")
    assert page.status_code == 200
    assert "Скала до 100 точки" in page.text
    assert "Скала до 65 точки" in page.text
    assert "2024/2025" in page.text and "2017/2018" in page.text
    assert "Без сверка с регистъра" in page.text


def test_matura_page_and_school_group_render(monkeypatch):
    monkeypatch.setattr(main, "snapshot", sample_snapshot)
    monkeypatch.setattr(main, "school_history", lambda code, before: [])
    monkeypatch.setattr(main, "dzi_sources", lambda: [dict(year="2025/2026", session="may",
        kind="mandatory", resource="1387affe", updated="2026-07-01", schools=976,
        verification="code", anomalies={})])
    monkeypatch.setattr(main, "matura_snapshot", lambda year, session, kind: dict(
        year=year, session=session, kind=kind, resource="1387affe", updated="2026-07-01",
        schools=976, results=3614, matched=974, unmatched=["2900001", "2900102"],
        verification="code", anomalies={"suppressed_takers": 0},
        subjects=[dict(subject="БЕЛ(ООП) З", schools=975, takers=41000,
                       mean=Decimal("4.12"), hidden=0, missing_grade=0)]))
    monkeypatch.setattr(main, "school_dzi", lambda code: [dict(year="2025/2026", session="may",
        kind="mandatory", resource="1387affe", updated="2026-07-01", verification="code",
        matched=True, subjects=[dict(subject="БЕЛ(ООП) З", takers=50, score=Decimal("4.20"))])])
    client = TestClient(main.app)
    assert client.get("/matura", follow_redirects=False).status_code == 307
    page = client.get("/matura/2025-2026")
    assert page.status_code == 200
    assert "Български език и литература" in page.text
    assert "4.12" in page.text
    assert "втора изпитна сесия" in page.text
    assert "ДИППК-Д.Пр З" in page.text and "профилирана подготовка" in page.text
    profile = client.get("/uchilishta/105201")
    assert profile.status_code == 200
    assert "Матури по години" in profile.text
    assert "4.20" in profile.text


def test_matura_school_search_and_csv(monkeypatch):
    monkeypatch.setattr(main, "snapshot", sample_snapshot)
    monkeypatch.setattr(main, "matura_snapshot", lambda year, session, kind: dict(
        year=year, session=session, kind=kind, resource="test-resource", updated="2026-07-01",
        verification="code", subjects=[dict(subject="БЕЛ(ООП) З", takers=None, hidden=1,
             schools=1, mean=None, missing_grade=0)]))
    seen = []
    def school_rows(resource, subject="", q="", page=1, limit=50, sort="name"):
        seen.append((resource, subject, q, page, limit, sort))
        return 1, [dict(code="105201", school="Примерно училище", town="София",
                        subject="БЕЛ(ООП) З", takers=None, score=Decimal("4.20"), matched=True)]
    monkeypatch.setattr(main, "matura_schools", school_rows)
    client = TestClient(main.app)
    page = client.get("/matura/2025-2026/uchilishta?subject=БЕЛ(ООП)+З&q=105201")
    assert page.status_code == 200
    assert "Примерно училище" in page.text and "няма данни" in page.text
    assert seen[0] == ("test-resource", "БЕЛ(ООП) З", "105201", 1, 50, "name")
    csv_page = client.get("/matura/2025-2026/uchilishta.csv?subject=БЕЛ(ООП)+З")
    assert csv_page.status_code == 200 and "Примерно училище" in csv_page.text
    assert seen[-1][-2] == 100000
    assert client.get("/matura/2025-2026/uchilishta?subject=НЕПОЗНАТ").status_code == 404


def test_nvo_pages_school_search_and_elementary_profile(monkeypatch):
    monkeypatch.setattr(main, "snapshot", sample_snapshot)
    monkeypatch.setattr(main, "school_history", lambda code, before: [])
    monkeypatch.setattr(main, "school_dzi", lambda code: [])
    monkeypatch.setattr(main, "nvo_sources", lambda: [dict(exam="nvo4", year="2025/2026",
        resource="ff360c5a", updated="2026-07-07", schools=1738, subjects=2,
        verification="code", matched=1736)])
    monkeypatch.setattr(main, "nvo_snapshot", lambda exam, year: dict(
        exam=exam, year=year, resource="ff360c5a", updated="2026-07-07", schools=1738,
        subject_count=2, results=3476, verification="code", matched=1736,
        unmatched=["2900001", "2900102"],
        subjects=[dict(subject="БЕЛ", schools=1738, takers=50000, mean=Decimal("72.50"))]))
    monkeypatch.setattr(main, "nvo_schools", lambda resource, subject="", q="", page=1,
                        limit=50, sort="name": (1, [dict(code="105204", school="Начално училище",
                        town="Банско", subject="БЕЛ", takers=50, score=Decimal("80.00"), matched=True)]))
    monkeypatch.setattr(main, "school_identity", lambda code: dict(code=code, name="Начално училище",
        oblast="Благоевград", municipality="Банско", town="Банско", matched=True, subjects={})
                      if code == "105204" else None)
    monkeypatch.setattr(main, "school_nvo", lambda code: [dict(exam="nvo4", year="2025/2026",
        resource="ff360c5a", updated="2026-07-07", verification="code", matched=True,
        subjects=[dict(subject="БЕЛ", takers=50, score=Decimal("80.00"))])]
                      if code == "105204" else [])
    client = TestClient(main.app)
    assert client.get("/nvo", follow_redirects=False).status_code == 307
    page = client.get("/nvo/4/2025-2026")
    assert page.status_code == 200 and "72.50" in page.text
    listing = client.get("/nvo/4/2025-2026/uchilishta?subject=БЕЛ")
    assert listing.status_code == 200 and "Начално училище" in listing.text
    profile = client.get("/uchilishta/105204")
    assert profile.status_code == 200 and "НВО IV и X клас по години" in profile.text
    csv_page = client.get("/nvo/4/2025-2026/uchilishta.csv?subject=БЕЛ")
    assert csv_page.status_code == 200 and "105204" in csv_page.text
    assert client.get("/nvo/4/2025-2026/uchilishta?subject=НЕПОЗНАТ").status_code == 404
