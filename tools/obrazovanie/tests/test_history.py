"""The six earlier NVO VII layouts, reduced from MON responses read on 29.09.2026."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from ingest.history import parse_nvo7_year
from ingest.parse import ShapeError


FIX = Path(__file__).parent / "fixtures/mon"
CASES = [
    ("2017/2018", 4, "100020", Decimal("38.1764705882353"), "points65", "2902701"),
    ("2018/2019", 4, "105201", Decimal("48.17"), "points100", "2851560"),
    ("2019/2020", 4, "100020", Decimal("65.01"), "points100", "2902701"),
    ("2020/2021", 4, "105201", Decimal("67.13"), "points100", "2811506"),
    ("2021/2022", 4, "105201", Decimal("59.88"), "points100", "2811518"),
    ("2022/2023", 4, "105201", Decimal("52.4"), "points100", "2811518"),
]


def raw(year):
    return (FIX / f"nvo7-{year.replace('/', '-')}.json").read_bytes()


@pytest.mark.parametrize("year,count,first,score,scale,last", CASES)
def test_real_historical_resource_samples(year, count, first, score, scale, last):
    rows = parse_nvo7_year(raw(year), year)
    assert len(rows) == count * 2
    assert (rows[0].neispuo, rows[0].subject, rows[0].score, rows[0].scale) == (
        first, "БЕЛ", score, scale)
    assert rows[-2].neispuo == last
    assert {r.subject for r in rows} == {"БЕЛ", "МАТ"}


@pytest.mark.parametrize("year", [case[0] for case in CASES])
def test_changed_columns_stop_the_historical_import(year):
    data = json.loads(raw(year))
    header = {"2017/2018": 3, "2022/2023": 4}.get(year, 0)
    data["data"][header][5] = "Непозната колона"
    with pytest.raises(ShapeError, match="columns"):
        parse_nvo7_year(json.dumps(data, ensure_ascii=False).encode(), year)


def test_2018_point_scale_must_stay_at_65():
    data = json.loads(raw("2017/2018"))
    data["data"][1][2] = "100"
    with pytest.raises(ShapeError, match="scale"):
        parse_nvo7_year(json.dumps(data, ensure_ascii=False).encode(), "2017/2018")
    data = json.loads(raw("2017/2018"))
    data["data"][4][6] = "66"
    with pytest.raises(ShapeError, match="exceeds"):
        parse_nvo7_year(json.dumps(data, ensure_ascii=False).encode(), "2017/2018")


def test_historical_codes_counts_and_scores_keep_their_meaning():
    data = json.loads(raw("2020/2021"))
    first = data["data"][2]
    first[4] = "010 020"
    first[5], first[6] = "", ""
    first[7], first[8] = "0", "0"
    rows = parse_nvo7_year(json.dumps(data, ensure_ascii=False).encode(), "2020/2021")
    assert (rows[0].neispuo, rows[0].takers, rows[0].score) == ("010020", None, None)
    assert (rows[1].takers, rows[1].score) == (0, Decimal(0))
    data["data"][3][4] = "010 020"
    with pytest.raises(ShapeError, match="duplicate"):
        parse_nvo7_year(json.dumps(data, ensure_ascii=False).encode(), "2020/2021")
