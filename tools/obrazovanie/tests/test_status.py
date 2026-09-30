import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from ingest.parse import ShapeError
from ingest.status import LAYOUTS, parse_status


FIX = Path(__file__).parent / "fixtures/mon"
PROTECTED = "bfc18bd8-66b1-4be7-abd9-5292969d5dba"
CENTRAL = "3d798ab3-4404-46d3-94f2-a3abbeda6c8f"


def sample(uri, kind, monkeypatch):
    monkeypatch.setitem(LAYOUTS, uri, (*LAYOUTS[uri][:-1], 3))
    return (FIX / f"{kind}-{uri[:8]}-sample.json").read_bytes()


def test_real_status_rows_keep_institution_scope(monkeypatch):
    protected = parse_status(sample(PROTECTED, "protected", monkeypatch), PROTECTED)
    central = parse_status(sample(CENTRAL, "central", monkeypatch), CENTRAL)
    assert len(protected) == len(central) == 3
    assert not protected[0].is_school and protected[1].is_school
    assert protected[1].code == "102003"
    assert central[0].is_school and not central[1].is_school
    assert central[0].code == "102003"


@pytest.mark.parametrize("uri", list(LAYOUTS))
def test_each_reviewed_layout_parses_real_rows(uri, monkeypatch):
    kind = LAYOUTS[uri][0]
    rows = parse_status(sample(uri, kind, monkeypatch), uri)
    assert len(rows) == 3
    assert all(row.code.isdigit() and row.name and row.town for row in rows)


def test_status_shape_change_is_rejected(monkeypatch):
    raw = sample(PROTECTED, "protected", monkeypatch)
    payload = json.loads(raw)
    payload["data"][3][5] = ""
    with pytest.raises(ShapeError, match="invalid institution"):
        parse_status(json.dumps(payload).encode(), PROTECTED)
    payload = json.loads(raw)
    payload["data"][3].append("new column")
    with pytest.raises(ShapeError, match="changed columns"):
        parse_status(json.dumps(payload).encode(), PROTECTED)


def test_profile_labels_year_scope_and_source(monkeypatch):
    from test_pages import sample_snapshot
    monkeypatch.setattr(main, "snapshot", sample_snapshot)
    monkeypatch.setattr(main, "school_history", lambda code, before: [])
    monkeypatch.setattr(main, "school_dzi", lambda code: [])
    monkeypatch.setattr(main, "school_nvo", lambda code: [])
    monkeypatch.setattr(main, "school_status", lambda code: [dict(kind="protected", year="2025/2026",
        resource=PROTECTED, updated="2025-12-16", name="Основно училище", town="Места",
        scope="учениците от I до VII клас включително")])
    page = TestClient(main.app).get("/uchilishta/105201")
    assert page.status_code == 200
    assert "Защитено · 2025/2026" in page.text
    assert "учениците от I до VII клас включително" in page.text
    assert PROTECTED[:8] in page.text
