"""The parsers and reconciliations on real answers (tests/fixtures, downloaded 28.09.2026, not written by hand)."""
import datetime as dt
import json
from pathlib import Path

import pytest

from ingest import bnb, checks, eurostat, jsonstat

FX = Path(__file__).parent / "fixtures"


def raw(name):
    return (FX / name).read_bytes()


def rows_by(parsed):
    return {(tuple(sorted(d.items())), g, t): (v, f) for d, g, t, v, f in parsed["rows"]}


# ---------- Eurostat JSON-stat ----------

def test_gdp_values_and_provisional_flag():
    # checked on https://ec.europa.eu/eurostat/databrowser/view/nama_10_gdp (BG, B1GQ, CP_MEUR), 28.09.2026
    p = jsonstat.parse(raw("eurostat/gdp_bg_since2023.json"), pinned={"unit", "na_item"})
    r = rows_by(p)
    assert len(r) == 3
    key = (("na_item", "B1GQ"), ("unit", "CP_MEUR"))
    assert r[(key, "BG", "2023")] == (94525.1, None)
    assert r[(key, "BG", "2024")] == (104767.2, None)
    assert r[(key, "BG", "2025")] == (116018.3, "p")   # provisional: the flag is kept, not dropped
    assert p["updated"].startswith("2026-09-21")


def test_position_is_computed_from_size_across_dimensions():
    # 5 units x 3 months in one answer: I25 fills positions 0-2, I15 3-5 (databrowser prc_hicp_minr, CP011)
    p = jsonstat.parse(raw("eurostat/hicp_cp011_units.json"), pinned={"unit", "coicop18"})
    r = rows_by(p)
    at = lambda unit, t: r[((("coicop18", "CP011"), ("unit", unit)), "BG", t)][0]
    assert at("I15", "2026-06") == 186.36 and at("I15", "2026-07") == 184.8 and at("I15", "2026-08") == 184.63
    assert at("I25", "2026-06") == 102.3
    assert len(r) == 15


def test_an_unpinned_unit_is_refused():
    with pytest.raises(jsonstat.ShapeError, match="unit"):
        jsonstat.parse(raw("eurostat/hicp_cp011_units.json"), pinned={"coicop18"})


@pytest.mark.parametrize("body, why", [
    (b"", "not JSON"),
    (b"<html><body>Service unavailable</body></html>", "not JSON"),
    (None, "not JSON"),                                     # cut in the middle
    (b"[]", "not a JSON object"),
])
def test_broken_answers_are_shape_errors(body, why):
    if body is None:
        body = raw("eurostat/gdp_bg_since2023.json")[:700]
    with pytest.raises(jsonstat.ShapeError, match=why):
        jsonstat.parse(body, pinned={"unit"})


def test_source_error_and_unknown_filter_value():
    with pytest.raises(jsonstat.ShapeError, match="source error"):
        jsonstat.parse(raw("eurostat/not_found.json"))
    # a typo in a filter value is a 200 with an empty dimension, not an empty dataset
    with pytest.raises(jsonstat.ShapeError, match="empty dimension"):
        jsonstat.parse(raw("eurostat/unit_typo.json"), pinned={"unit"})


def test_missing_field_and_value_outside_the_cube():
    d = json.loads(raw("eurostat/gdp_bg_since2023.json"))
    del d["size"]
    with pytest.raises(jsonstat.ShapeError, match="size"):
        jsonstat.parse(json.dumps(d).encode(), pinned={"unit", "na_item"})
    d = json.loads(raw("eurostat/gdp_bg_since2023.json"))
    d["value"]["3"] = 1.0
    with pytest.raises(jsonstat.ShapeError, match="outside"):
        jsonstat.parse(json.dumps(d).encode(), pinned={"unit", "na_item"})
    d = json.loads(raw("eurostat/gdp_bg_since2023.json"))
    d["value"]["0"] = "94525,1"
    with pytest.raises(jsonstat.ShapeError, match="not a number"):
        jsonstat.parse(json.dumps(d).encode(), pinned={"unit", "na_item"})


def test_a_flag_without_a_value_is_no_data_not_zero():
    d = json.loads(raw("eurostat/gdp_bg_since2023.json"))
    del d["value"]["1"]
    d["status"]["1"] = "c"
    r = rows_by(jsonstat.parse(json.dumps(d).encode(), pinned={"unit", "na_item"}))
    assert r[((("na_item", "B1GQ"), ("unit", "CP_MEUR")), "BG", "2024")] == (None, "c")


def test_every_indicator_pins_its_unit_and_has_a_known_geo():
    ids = set()
    for ind in eurostat.indicators():
        assert ind["id"] not in ids
        ids.add(ind["id"])
        assert ind["geo"] in eurostat.GEO and int(ind["stale_days"]) > 0 and ind["label"]
        assert "unit=" in ind["filters"] or ind["dataset"] in ("tec00114", "earn_mw_cur"), ind["id"]
        assert eurostat.url(ind).startswith("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/")


# ---------- reconciliations ----------

def test_regions_add_up_to_the_country():
    p = jsonstat.parse(raw("eurostat/gdp_nuts_mio.json"), pinned={"unit"})
    assert checks.nuts_sum(p["rows"]) == []
    assert len({t for _, _, t, _, _ in p["rows"]}) == 25         # 2000-2024
    rows = [(d, g, t, v + 1 if (g, t) == ("BG311", "2020") else v, f) for d, g, t, v, f in p["rows"]]
    assert checks.nuts_sum(rows) == ["2020: сборът на областите 61856.58 ≠ страната 61855.55 млн. €"]


def test_monthly_and_annual_rates_follow_from_the_index():
    for name in ("eurostat/hicp_rates_2024.json", "eurostat/hicp_rates_1997.json"):   # 1997: hyperinflation
        rows = jsonstat.parse(raw(name), pinned={"unit", "coicop18"})["rows"]
        by = {u: [r for r in rows if r[0]["unit"] == u] for u in ("I15", "RCH_M", "RCH_A")}
        assert checks.hicp_rates(by["I15"], by["RCH_M"], by["RCH_A"]) == [], name
    rows = jsonstat.parse(raw("eurostat/hicp_rates_2024.json"), pinned={"unit", "coicop18"})["rows"]
    by = {u: [r for r in rows if r[0]["unit"] == u] for u in ("I15", "RCH_M", "RCH_A")}
    moved = [(d, g, t, v + 0.3 if (d["coicop18"], t) == ("TOTAL", "2025-03") else v, f) for d, g, t, v, f in by["RCH_M"]]
    bad = checks.hicp_rates(by["I15"], moved, by["RCH_A"])
    assert len(bad) == 1 and bad[0].startswith("TOTAL 2025-03: месечна")


def test_checks_name_the_indicators_that_fail():
    p = {"gdp_nuts": jsonstat.parse(raw("eurostat/gdp_nuts_mio.json"), pinned={"unit"})}
    assert checks.eurostat(p) == {}
    p["gdp_nuts"]["rows"][0] = (p["gdp_nuts"]["rows"][0][0], "BG", p["gdp_nuts"]["rows"][0][2], 1.0, None)
    assert list(checks.eurostat(p)) == ["gdp_nuts"]


# ---------- BNB ----------

def test_bnb_day_csv():
    # checked on bnb.bg "Валутни курсове на чуждестранни валути за 25.09.2026"
    rows = bnb.parse(raw("bnb/today.csv"))
    assert len(rows) == 29
    by = {d["code"]: (ind, day, v) for ind, d, day, v in rows}
    assert by["USD"] == ("fx_eur", dt.date(2026, 9, 25), 1.1403)
    assert by["JPY"][2] == 179.70 and by["IDR"][2] == 20427.22


def test_bnb_archive_in_leva_with_units_and_old_leva():
    rows = bnb.parse(raw("bnb/q2025-4.csv"))
    usd = {day: v for ind, d, day, v in rows if d["code"] == "USD"}
    assert usd[dt.date(2025, 10, 1)] == 1.66823 and all(ind == "fx_bgn" for ind, *_ in rows)
    jpy = [d for ind, d, day, v in rows if d["code"] == "JPY"]
    assert jpy[0] == {"code": "JPY", "units": "100"}
    old = bnb.parse(raw("bnb/q1991-1.csv"))                     # 1991: no inverse ("n/a"), JPY per 100
    assert {d["code"] for _, d, _, _ in old} == {"DEM", "JPY", "USD"}
    first = [(d["code"], v) for _, d, day, v in old if day == dt.date(1991, 2, 19)]
    assert first == [("DEM", 19.0556), ("JPY", 21.6937), ("USD", 28.25)]


def test_bnb_archive_in_euro():
    rows = bnb.parse(raw("bnb/q2026-3.csv"))
    first = {d["code"]: v for ind, d, day, v in rows if day == dt.date(2026, 7, 1)}
    assert first == {"USD": 1.1383, "JPY": 185.21, "IDR": 20444.89}
    assert all(ind == "fx_eur" for ind, *_ in rows)


def test_bnb_no_data_is_no_rows_and_a_refused_query_is_an_error():
    assert bnb.parse(raw("bnb/no_data.html")) == []           # RON in 1991: "n/a" on every day
    with pytest.raises(jsonstat.ShapeError, match="HTML"):
        bnb.parse(raw("bnb/window_error.html"))
    with pytest.raises(jsonstat.ShapeError, match="header"):
        bnb.parse("﻿Заглавие\nДата;Код\n".encode())
    with pytest.raises(jsonstat.ShapeError, match="UTF-8"):
        bnb.parse(raw("bnb/today.csv").decode("utf-8-sig").encode("cp1251"))


def test_bnb_rate_and_inverse_must_agree():
    text = raw("bnb/q2025-4.csv").decode("utf-8-sig").replace("1.66823, 0.599438", "1.76823, 0.599438", 1)
    with pytest.raises(jsonstat.ShapeError, match="do not agree"):
        bnb.parse(text.encode())


def test_bnb_currency_list_and_windows():
    codes = bnb.currencies(raw("bnb/search.html"))
    assert len(codes) == 59 and "USD" in codes and "DEM" in codes
    w = list(bnb.windows(dt.date(2025, 11, 20), dt.date(2026, 2, 3)))
    assert w == [(dt.date(2025, 11, 20), dt.date(2025, 12, 31)), (dt.date(2026, 1, 1), dt.date(2026, 2, 3))]
    assert len(list(bnb.windows(bnb.FIRST_DAY, dt.date(2025, 12, 31)))) == 35 * 4
    assert "valutes=USD&valutes=GBP" in bnb.url(dt.date(2025, 10, 1), dt.date(2025, 12, 31), ["USD", "GBP"])
