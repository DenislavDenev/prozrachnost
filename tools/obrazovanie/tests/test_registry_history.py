"""Historical register shapes sampled from MON's published resources."""

import json
from pathlib import Path

import pytest

from ingest.parse import ShapeError
from ingest.registry import parse_schools_year


FIXTURES = Path(__file__).parent / "fixtures" / "mon"


@pytest.mark.parametrize("year", ["2021-2022", "2022-2023", "2023-2024", "2024-2025"])
def test_real_register_samples(year):
    schools = parse_schools_year((FIXTURES / f"registry-{year}.json").read_bytes())
    assert "100101" in schools
    assert schools["100101"].oblast == "Благоевград"
    assert len(schools) == 4


def sample():
    return json.loads((FIXTURES / "registry-2021-2022.json").read_bytes())


def encode(value):
    return json.dumps(value, ensure_ascii=False).encode()


def test_changed_columns_hold():
    value = sample()
    value["data"][0][4] = "Код"
    with pytest.raises(ShapeError, match="columns"):
        parse_schools_year(encode(value))


def test_missing_code_holds():
    value = sample()
    value["data"][1][4] = ""
    with pytest.raises(ShapeError, match="code"):
        parse_schools_year(encode(value))


def test_duplicate_code_holds():
    value = sample()
    value["data"][2][4] = value["data"][1][4]
    with pytest.raises(ShapeError, match="duplicate"):
        parse_schools_year(encode(value))


def test_code_remains_text():
    value = sample()
    value["data"][1][4] = "0123456"
    schools = parse_schools_year(encode(value))
    assert "0123456" in schools


def test_code_free_layout_holds():
    value = sample()
    value["data"] = [row[:4] + row[5:] for row in value["data"]]
    with pytest.raises(ShapeError, match="shape"):
        parse_schools_year(encode(value))
