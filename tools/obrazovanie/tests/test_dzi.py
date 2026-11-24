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


def catalog():
    return json.loads((FIX / "dzi-catalog.json").read_bytes())


def test_complete_official_catalog_is_read_whole():
    listing = dzi_catalog(encoded(catalog()))
    assert len(listing.resources) == len(LAYOUTS) == 17
    assert listing.new == [] and listing.gone == []


def test_a_vanished_resource_is_named_and_the_others_stay():
    value = catalog()
    gone = value["resources"].pop()
    value["total_records"] -= 1
    listing = dzi_catalog(encoded(value))
    assert listing.gone == [gone["uri"]] and listing.new == []
    assert len(listing.resources) == 16 and gone["uri"] not in {item.uri for item in listing.resources}


def test_a_new_resource_is_named_and_not_read():
    value = catalog()
    new = dict(value["resources"][0], uri="00000000-0000-0000-0000-000000000001", updated_at="2027-06-12 10:00:00",
               name=value["resources"][0]["name"].replace("2025/2026", "2026/2027"))
    value["resources"].append(new)
    value["total_records"] += 1
    listing = dzi_catalog(encoded(value))
    assert [(item.uri, item.name) for item in listing.new] == [(new["uri"], new["name"])]
    assert len(listing.resources) == 17 and listing.gone == []


@pytest.mark.parametrize("change", ["count", "empty", "dataset", "name", "session", "duplicate", "key"])
def test_changed_catalog_still_holds(change):
    value = catalog()
    first = value["resources"][0]
    if change == "count":
        value["total_records"] -= 1
    elif change == "empty":
        value["resources"], value["total_records"] = [], 0
    elif change == "dataset":
        value["resources"][-1]["dataset_uri"] = "other"
    elif change == "name":
        value["resources"][-1]["name"] = None
    elif change == "session":
        first["name"] = first["name"].replace("август-септември", "май-юни")
    elif change == "duplicate":
        value["resources"][1] = dict(first)
    else:
        value["page"] = 1
    with pytest.raises(ShapeError, match="DZI resource"):
        dzi_catalog(encoded(value))


def test_grades_only_session_has_no_count_and_zero_is_no_result():
    """August 2025/2026: one column per subject with the mean alone; "0" is a subject the school did not sit."""
    uri, value = source("ffa356b9")
    table = parse_dzi(encoded(value), uri)
    assert (table.year, table.session, table.kind, table.rows) == ("2025/2026", "august", "mandatory", 4)
    assert all(item.takers is None for item in table.results)
    zeros = sum(cell == "0" for row in value["data"][1:] for cell in row[5:])
    grades = sum(cell != "0" for row in value["data"][1:] for cell in row[5:])
    assert len(table.results) == grades and zeros > 0
    first = [(item.subject, item.score) for item in table.results if item.row_number == 2]
    assert first == [("БЕЛ(ООП) З", Decimal("2")), ("ДИППК-п.р З", Decimal("3.33"))]
    assert table.anomalies == {"suppressed_takers": 0, "missing_grade": 0, "source_placeholder": 0}


def test_grades_only_session_holds_a_count_or_a_bad_grade():
    uri, value = source("ffa356b9")
    for bad in ("1.50", "12", "-"):
        changed = json.loads(encoded(value))
        changed["data"][1][6] = bad
        with pytest.raises(ShapeError, match="invalid grade|grade outside"):
            parse_dzi(encoded(changed), uri)
    changed = json.loads(encoded(value))
    changed["data"][0][6] = "Бр.\nМат(ПП)\nЗ"
    with pytest.raises(ShapeError, match="Changed DZI columns"):
        parse_dzi(encoded(changed), uri)
