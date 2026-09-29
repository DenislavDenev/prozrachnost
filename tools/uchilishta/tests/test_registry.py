import json
from pathlib import Path

import pytest

from ingest.parse import ShapeError, parse_nvo7
from ingest.sources import NVO7_DATASET, SCHOOLS_DATASET, catalog, current_pair, parse_schools, reconcile


FIX = Path(__file__).parent / "fixtures/mon"


def raw(name):
    return (FIX / name).read_bytes()


def edit(name, change):
    value = json.loads(raw(name))
    change(value)
    return json.dumps(value, ensure_ascii=False).encode()


def test_catalog_picks_same_academic_year_and_latest_register():
    nvo, school = current_pair(raw("nvo7-catalog.json"), raw("school-catalog.json"))
    assert (nvo.year, nvo.uri) == ("2025/2026", "08157f0e-6a9d-45e9-8e45-e53be6b788e3")
    assert (school.year, school.uri) == ("2025/2026", "236b21ed-25c2-4ebe-8688-c01587fc660d")
    assert len(catalog(raw("nvo7-catalog.json"), NVO7_DATASET)) == 8
    assert len(catalog(raw("school-catalog.json"), SCHOOLS_DATASET)) == 10


def test_real_register_rows_and_reconciliation():
    schools = parse_schools(raw("school-register-236b21ed.json"))
    assert len(schools) == 4
    assert schools["105201"].name == 'Средно училище "Неофит Рилски"'
    results = parse_nvo7(raw("nvo7-resource-08157f0e.json"))
    assert reconcile(results, schools) == {"schools": 4, "matched": 4, "unmatched": []}


@pytest.mark.parametrize("change", [
    lambda d: d["data"][0].__setitem__("Extra", "new"),
    lambda d: d["data"][0].pop("InstId"),
    lambda d: d["data"][0].__setitem__("InstId", "105201"),
    lambda d: d["data"].append(d["data"][0]),
    lambda d: d.__setitem__("success", False),
])
def test_changed_register_is_rejected(change):
    with pytest.raises(ShapeError):
        parse_schools(edit("school-register-236b21ed.json", change))


def test_no_name_based_matching_and_threshold():
    results = parse_nvo7(raw("nvo7-resource-08157f0e.json"))
    schools = parse_schools(raw("school-register-236b21ed.json"))
    schools.pop("105201")
    with pytest.raises(ShapeError, match="unmatched"):
        reconcile(results, schools)


def test_incomplete_catalog_is_rejected():
    with pytest.raises(ShapeError, match="Incomplete"):
        catalog(edit("nvo7-catalog.json", lambda d: d["resources"].pop()), NVO7_DATASET)


def test_latest_exam_without_same_year_register_is_rejected():
    missing = edit("school-catalog.json", lambda d: (
        d.__setitem__("resources", [r for r in d["resources"] if "2025/2026" not in r["name"]]),
        d.__setitem__("total_records", d["total_records"] - 2),
    ))
    with pytest.raises(ShapeError, match="academic year"):
        current_pair(raw("nvo7-catalog.json"), missing)
