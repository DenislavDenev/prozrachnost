"""The inhabitants, copied from the tool Население with their dates."""
import datetime as dt

import pytest

from ingest import population

DOC = {"unit": "регистрирани лица", "series": [
    {"name": "Постоянен адрес", "points": [["2024-12-31", 38324], ["2025-12-15", 38197], ["2026-01-15", None]]},
    {"name": "Настоящ адрес", "points": [["2025-12-15", 36472]]}], "sources": []}


def test_the_series_of_both_addresses_are_read_and_a_missing_point_is_left_out():
    got = population.parse_series(DOC)
    assert ("permanent", dt.date(2024, 12, 31), 38324) in got and ("current", dt.date(2025, 12, 15), 36472) in got
    assert len(got) == 3        # the None point is not a zero


def test_an_answer_of_another_shape_is_an_error():
    with pytest.raises(population.PopulationError, match="непознато име"):
        population.parse_series({"series": [{"name": "Друго", "points": [["2025-01-01", 1]]}]})
    with pytest.raises(population.PopulationError):
        population.parse_series({"nothing": 1})
    with pytest.raises(population.PopulationError):
        population.parse_series({"series": [{"name": "Постоянен адрес", "points": [["вчера", 1]]}]})


def test_the_copy_is_stored_once_per_day(c):
    from ingest import gold
    mid = c.execute("SELECT id FROM gold.municipality LIMIT 1").fetchone()[0]
    assert population.store(c, mid, DOC, "/x") == 3
    assert population.store(c, mid, DOC, "/x") == 3
    assert c.execute("SELECT count(*) FROM silver.population").fetchone()[0] == 3


def test_a_municipality_that_fails_is_reported_and_the_rest_go_on(c):
    calls = []
    def get(path):
        calls.append(path)
        if len(calls) == 2:
            raise population.PopulationError("нещо се счупи")
        return DOC
    rep = population.run(c, get=get, pause=0, limit=4)
    assert (rep["municipalities"], rep["of"]) == (3, 4) and rep["problems"] == ["нещо се счупи"]
