import json
from pathlib import Path

import pytest

from ingest.parse import ShapeError
from ingest.registry import parse_code_free_register


FIX = Path(__file__).parent / "fixtures" / "mon"


@pytest.mark.parametrize("year", ["2017-2018", "2018-2019", "2019-2020", "2020-2021"])
def test_real_code_free_register_samples(year):
    assert parse_code_free_register((FIX / f"registry-{year}.json").read_bytes(),
                                    year.replace("-", "/")) >= 4


def test_code_column_change_holds():
    value = json.loads((FIX / "registry-2018-2019.json").read_bytes())
    for row in value["data"]:
        row.insert(4, "123456")
    with pytest.raises(ShapeError, match="shape"):
        parse_code_free_register(json.dumps(value, ensure_ascii=False).encode(), "2018/2019")


def test_unexpected_merged_row_holds():
    value = json.loads((FIX / "registry-2017-2018.json").read_bytes())
    value["data"][3][5] = "unexpected overflow"
    with pytest.raises(ShapeError, match="identity"):
        parse_code_free_register(json.dumps(value, ensure_ascii=False).encode(), "2017/2018")
