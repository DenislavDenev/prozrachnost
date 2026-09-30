import json
from decimal import Decimal
from pathlib import Path

import pytest

from ingest.dzi import LAYOUTS, dzi_catalog, parse_dzi
from ingest.parse import ShapeError


FIX = Path(__file__).parent / "fixtures" / "mon"


@pytest.mark.parametrize("uri", sorted(LAYOUTS))
def test_all_observed_dzi_layouts_parse_real_rows(uri):
    table = parse_dzi((FIX / f"dzi-{uri[:8]}.json").read_bytes(), uri)
    assert table.rows == 4
    assert table.year == LAYOUTS[uri]["year"]
    assert table.results
    assert all(item.score is None or Decimal("2") <= item.score <= Decimal("6")
               for item in table.results)


def source(prefix):
    uri = next(uri for uri in LAYOUTS if uri.startswith(prefix))
    value = json.loads((FIX / f"dzi-{prefix}.json").read_bytes())
    return uri, value


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode()


def test_dzi_column_change_holds():
    uri, value = source("1387affe")
    value["data"][0][5] = "Брой ученици"
    with pytest.raises(ShapeError, match="Changed DZI columns"):
        parse_dzi(encoded(value), uri)


def test_zero_takers_is_not_missing_and_bad_grade_holds():
    uri, value = source("1387affe")
    value["data"][1][5:7] = ["0", "4.50"]
    with pytest.raises(ShapeError, match="grade without takers"):
        parse_dzi(encoded(value), uri)


def test_suppressed_takers_keep_grade():
    uri, value = source("e98e4650")
    value["data"][1][5:7] = ["-", "5.31"]
    table = parse_dzi(encoded(value), uri)
    item = next(item for item in table.results if item.neispuo == value["data"][1][4]
                and item.subject == "Мат(ООП) И")
    assert item.takers is None and item.score == Decimal("5.31")
    assert table.anomalies["suppressed_takers"] >= 1


def test_2016_rows_are_not_joined_by_generic_name():
    uri, value = source("f1ae52dc")
    value["data"][2][:5] = value["data"][1][:5]
    table = parse_dzi(encoded(value), uri)
    assert table.schools >= 2
    assert all(item.neispuo is None for item in table.results)


def test_unknown_resource_holds():
    uri, value = source("1387affe")
    with pytest.raises(ShapeError, match="Unknown DZI resource"):
        parse_dzi(encoded(value), "00000000-0000-0000-0000-000000000000")


def test_complete_official_catalog_and_changed_resource_hold():
    value = json.loads((FIX / "dzi-catalog.json").read_bytes())
    assert len(dzi_catalog(encoded(value))) == 16
    value["resources"].pop()
    value["total_records"] -= 1
    with pytest.raises(ShapeError, match="catalog"):
        dzi_catalog(encoded(value))
