"""Checks against a cut of MON's real NVO VII resource response."""

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from ingest.parse import HEADER, ShapeError, parse_nvo7


FIXTURE = Path(__file__).parent / "fixtures/mon/nvo7-resource-08157f0e.json"
RESOURCE = "08157f0e-6a9d-45e9-8e45-e53be6b788e3"


def sample():
    return FIXTURE.read_bytes()


def changed(change):
    payload = json.loads(sample())
    change(payload)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def test_real_source_sample():
    # MON getResourceData, resource 08157f0e..., checked against the 29.09.2026 response.
    results = parse_nvo7(sample())
    assert len(results) == 8
    assert (results[0].neispuo, results[0].subject, results[0].takers, results[0].score) == (
        "105201", "БЕЛ", 84, Decimal("56.38")
    )
    assert (results[1].takers, results[1].score) == (84, Decimal("33.13"))
    assert (results[2].neispuo, results[2].takers, results[2].score) == (
        "909612", 0, Decimal("0")
    )
    assert (results[4].neispuo, results[4].takers, results[4].score) == (
        "2902103", 32, Decimal("70.28")
    )
    assert all(result.scale == "points100" for result in results)


@pytest.mark.parametrize("raw", [b"", b"<html>error</html>", b'{"success":', b"\xff"])
def test_invalid_response(raw):
    with pytest.raises(ShapeError):
        parse_nvo7(raw)


@pytest.mark.parametrize("mutation", [
    lambda p: p["data"][0].append("Непозната колона"),
    lambda p: p["data"][0].remove(HEADER[7]),
    lambda p: p["data"][1].pop(),
    lambda p: p["data"][1].__setitem__(5, 84),
    lambda p: p["data"].append(copy.copy(p["data"][1])),
    lambda p: p.__setitem__("success", False),
    lambda p: p.__setitem__("data", [HEADER]),
])
def test_changed_shape_is_rejected(mutation):
    with pytest.raises(ShapeError):
        parse_nvo7(changed(mutation))


@pytest.mark.parametrize("column,value", [
    (4, "105201.0"),
    (4, "12345"),
    (5, "-1"),
    (5, "1.5"),
    (6, "100.01"),
    (6, "56,38"),
    (6, "NaN"),
])
def test_invalid_values_are_rejected(column, value):
    with pytest.raises(ShapeError):
        parse_nvo7(changed(lambda p: p["data"][1].__setitem__(column, value)))


def test_missing_is_distinct_from_zero():
    results = parse_nvo7(changed(lambda p: (
        p["data"][1].__setitem__(5, ""),
        p["data"][1].__setitem__(6, ""),
    )))
    assert (results[0].takers, results[0].score) == (None, None)
    assert (results[2].takers, results[2].score) == (0, Decimal("0"))


def test_leading_zero_code_remains_text():
    results = parse_nvo7(changed(lambda p: p["data"][1].__setitem__(4, "005201")))
    assert results[0].neispuo == "005201"
