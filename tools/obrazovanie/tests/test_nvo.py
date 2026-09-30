import json
from decimal import Decimal
from pathlib import Path

import pytest

from ingest import db
from ingest.nvo import LAYOUTS, nvo_catalog, parse_nvo
from ingest.parse import ShapeError
from ingest.sources import Resource


FIX = Path(__file__).parent / "fixtures/mon"


@pytest.mark.parametrize("uri", sorted(LAYOUTS))
def test_all_observed_nvo_layouts_parse_real_rows(uri):
    layout = LAYOUTS[uri]
    table = parse_nvo((FIX / f"{layout['exam']}-{uri[:8]}.json").read_bytes(), uri)
    assert table.exam == layout["exam"] and table.year == layout["year"]
    assert table.rows >= 3 and len(table.results) == table.rows * len(table.subjects)
    assert all(item.scale == "points100" and
               (item.score is None or Decimal("0") <= item.score <= Decimal("100"))
               for item in table.results)


@pytest.mark.parametrize("exam,count", [("nvo4", 8), ("nvo10", 6)])
def test_complete_catalog_and_new_resource_hold(exam, count):
    raw = json.loads((FIX / f"{exam}-catalog.json").read_bytes())
    assert len(nvo_catalog(json.dumps(raw, ensure_ascii=False).encode(), exam)) == count
    raw["resources"].pop()
    raw["total_records"] -= 1
    with pytest.raises(ShapeError, match="catalog"):
        nvo_catalog(json.dumps(raw, ensure_ascii=False).encode(), exam)


def test_changed_scale_header_and_zero_vs_missing():
    uri = next(uri for uri in LAYOUTS if uri.startswith("ff360c5a"))
    raw = json.loads((FIX / "nvo4-ff360c5a.json").read_bytes())
    raw["data"][0][6] = "Средна оценка по шестобалната система"
    with pytest.raises(ShapeError, match="columns or scale"):
        parse_nvo(json.dumps(raw, ensure_ascii=False).encode(), uri)
    raw = json.loads((FIX / "nvo4-ff360c5a.json").read_bytes())
    raw["data"][1][5:7] = ["0", "0"]
    table = parse_nvo(json.dumps(raw, ensure_ascii=False).encode(), uri)
    bel = next(item for item in table.results if item.neispuo == raw["data"][1][4]
               and item.subject == "БЕЛ")
    assert bel.takers == 0 and bel.score == Decimal("0")
    raw["data"][1][5:7] = ["", ""]
    table = parse_nvo(json.dumps(raw, ensure_ascii=False).encode(), uri)
    bel = next(item for item in table.results if item.neispuo == raw["data"][1][4]
               and item.subject == "БЕЛ")
    assert bel.takers is None and bel.score is None


def test_invalid_code_and_score_hold():
    uri = next(uri for uri in LAYOUTS if uri.startswith("ce0ed1d8"))
    raw = json.loads((FIX / "nvo10-ce0ed1d8.json").read_bytes())
    raw["data"][1][4] = "105201; DROP"
    with pytest.raises(ShapeError, match="NEISPUO"):
        parse_nvo(json.dumps(raw, ensure_ascii=False).encode(), uri)
    raw = json.loads((FIX / "nvo10-ce0ed1d8.json").read_bytes())
    raw["data"][1][6] = "101"
    with pytest.raises(ShapeError, match="exceeds"):
        parse_nvo(json.dumps(raw, ensure_ascii=False).encode(), uri)


def test_duplicate_code_and_mismatch_threshold_hold():
    uri = next(uri for uri in LAYOUTS if uri.startswith("ce0ed1d8"))
    raw = json.loads((FIX / "nvo10-ce0ed1d8.json").read_bytes())
    raw["data"][2][4] = raw["data"][1][4]
    with pytest.raises(ShapeError, match="duplicate"):
        parse_nvo(json.dumps(raw, ensure_ascii=False).encode(), uri)
    table = parse_nvo((FIX / "nvo10-ce0ed1d8.json").read_bytes(), uri)
    resource = Resource(uri, table.year, "НВО X", "2026-07-07")
    with pytest.raises(ShapeError, match="unmatched"):
        db.publish_nvo(None, resource, "sha", table, schools={})
