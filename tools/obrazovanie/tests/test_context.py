import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from ingest.context import parse_context
from ingest.parse import ShapeError


FIX = Path(__file__).parent / "fixtures/mon"


def inputs():
    return ((FIX / "pupils-context-2025.json").read_bytes(),
            (FIX / "classes-context-2025.json").read_bytes())


def test_latest_real_national_counts_and_source_anomaly():
    rows = parse_context(*inputs(), "2025/2026")
    assert len(rows) == 15
    assert sum(sum(r.grades[g] for g in range(1, 13)) for r in rows) == 709413
    assert sum(r.reported_groups for r in rows) == Decimal("33955.5")
    assert sum(r.institutions for r in rows) == 2319
    assert sum(r.grades[14] + r.grades[15] for r in rows) == 65


def test_changed_columns_or_totals_stop_context_import():
    pupils, classes = inputs()
    payload = json.loads(pupils)
    payload["data"][0]["br_stds_klas_1"] += 1
    with pytest.raises(ShapeError, match="do not reconcile"):
        parse_context(json.dumps(payload).encode(), classes, "2025/2026")
    payload = json.loads(pupils)
    payload["data"][0]["new_field"] = 1
    with pytest.raises(ShapeError, match="columns"):
        parse_context(json.dumps(payload).encode(), classes, "2025/2026")


def test_national_page_explains_granularity(monkeypatch):
    from test_pages import sample_snapshot
    monkeypatch.setattr(main, "snapshot", sample_snapshot)
    monkeypatch.setattr(main, "context_snapshot", lambda: dict(year="2025/2026", students=709413,
        institutions=2319, groups=Decimal("33955.5"), kind_count=15,
        pupils_updated="2026-02-25", classes_updated="2026-02-25",
        pupils_resource="a5ed412d", classes_resource="a08b426f", special_students=65,
        kinds=[dict(name="основно", institutions=1103, students=215176, groups=Decimal("11686"))],
        grades=[dict(grade=1, students=58032, width=92)]))
    page = TestClient(main.app).get("/context")
    assert page.status_code == 200
    assert "709 413" in page.text
    assert "основно" in page.text
    assert "вид училище" in page.text
    assert "a5ed412d" in page.text and "a08b426f" in page.text
