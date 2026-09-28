"""Bank interest rates, loans and deposits in Bulgaria from the ECB Data Portal API (Bulgaria is in the euro area
from 1.1.2026): https://data-api.ecb.europa.eu/service/data/<flow>/<key>, CSV.

Bulgaria's rates (MIR) are in euro from 2026-01 and in leva before it, from 2007 (see BG_BEFORE, BG_PARTS); its
loans and deposits (BSI) start in 2022-01; the euro area (`U2`, stored as `EA`) has its rates from 2003, drawn beside
Bulgaria's.

Two indicators in live.series, with the series in `dims`: `rates` (% a year, new business) and `bank` (stocks,
million €). Checks: every series is monthly without a gap, and overnight deposits never exceed all deposits.
"""
import csv
import io

from . import http, store
from .config import ECB
from .jsonstat import ShapeError

SOURCE = "ecb"
# the indicators of this source, in the shape of db/ref/indicators.csv (the pages, /api, /csv and freshness read them)
INDICATORS = [
    {"id": "rates", "dataset": "MIR", "source": SOURCE, "geo": "BG", "stale_days": "70", "read_days": "2",
     "label": "Лихви на банките по нови кредити и депозити", "unit_label": "% годишно"},
    {"id": "bank", "dataset": "BSI", "source": SOURCE, "geo": "BG", "stale_days": "70", "read_days": "2",
     "label": "Кредити и депозити в банките (салда)", "unit_label": "млн. €"},
]
# (indicator, flow, key with {g} for the place, code, Bulgarian name); the rates for Bulgaria and the euro area
SERIES = [
    ("rates", "MIR", "M.{g}.B.A2C.AM.R.A.2250.EUR.N", "housing", "Жилищни кредити (цена на кредита)"),
    ("rates", "MIR", "M.{g}.B.A2B.A.R.A.2250.EUR.N", "consumer", "Потребителски кредити"),
    ("rates", "MIR", "M.{g}.B.A2Z.A.R.A.2250.EUR.N", "cards", "Овърдрафти и кредитни карти на домакинствата"),
    ("rates", "MIR", "M.{g}.B.A2I.AM.R.A.2240.EUR.N", "business", "Кредити за фирми (цена на кредита)"),
    ("rates", "MIR", "M.{g}.B.L22.A.R.A.2250.EUR.N", "dep_hh", "Срочни депозити на домакинствата"),
    ("rates", "MIR", "M.{g}.B.L22.A.R.A.2240.EUR.N", "dep_nfc", "Срочни депозити на фирмите"),
    ("rates", "MIR", "M.{g}.B.L21.A.R.A.2250.EUR.N", "dep_hh_on", "Депозити на виждане на домакинствата"),
    ("bank", "BSI", "M.{g}.N.A.A20.A.1.U2.2250.Z01.E", "loans_hh", "Кредити на домакинствата"),
    ("bank", "BSI", "M.{g}.N.A.A20.A.1.U2.2240.Z01.E", "loans_nfc", "Кредити на фирмите"),
    ("bank", "BSI", "M.{g}.N.A.L20.A.1.U2.2250.Z01.E", "dep_hh", "Депозити на домакинствата"),
    ("bank", "BSI", "M.{g}.N.A.L20.A.1.U2.2240.Z01.E", "dep_nfc", "Депозити на фирмите"),
    ("bank", "BSI", "M.{g}.N.A.L21.A.1.U2.2250.Z01.E", "dep_hh_on", "Депозити на виждане на домакинствата"),
    ("bank", "BSI", "M.{g}.N.A.L21.A.1.U2.2240.Z01.E", "dep_nfc_on", "Депозити на виждане на фирмите"),
]
PLACES = {"rates": {"BG": "BG", "U2": "EA"}, "bank": {"BG": "BG"}}   # ECB code -> our geo
# Bulgaria's rates before the euro: the EUR series go back to 2007 but cover only the loans and deposits in euro, a small
# part of all (the consumer rate would "jump" from about 4% to 9% in 2026-01, checked 28.09.2026), so they are kept only
# from the euro on. Before it the ECB has the same rates in leva, the currency of most loans and deposits then (from
# 2017-08, the overnight deposits from 2007): these are Bulgaria's rates until 2025-12. They join smoothly: housing
# 2.48% in 2025-12 in leva, 2.46% in 2026-01 in euro; consumer 9.43 / 9.05; business 4.09 / 3.95.
BG_RATES_FROM = "2026-01"
BG_BEFORE = {"housing": "M.BG.B.A2C.A.R.A.2250.BGN.N", "consumer": "M.BG.B.A2B.A.R.A.2250.BGN.N",
             "business": "M.BG.B.A2A.A.R.A.2240.BGN.N", "dep_hh_on": "M.BG.B.L21.A.R.A.2250.BGN.N",
             "cards": "M.BG.B.A2Z.A.R.A.2250.BGN.N"}
# Where that total in leva is missing (loans before 2017-08, term deposits always) the ECB has, from 2007, its parts
# by how long the rate is fixed or the money is tied, each with its rate (R) and amount of new business (B). The
# total is their average weighted by the amounts, as the ECB builds its own: checked 28.09.2026 against the published
# total over 2017-08..2025-12, never more than 0.01 points apart (housing, consumer). Loans to firms have the parts
# only up to EUR 1 million before 2017-08, so they start then.
BG_PARTS = {"housing": "M.BG.B.A2C.F+I+O+P.R+B.A.2250.BGN.N", "consumer": "M.BG.B.A2B.F+I+J.R+B.A.2250.BGN.N",
            "dep_hh": "M.BG.B.L22.F+G+H.R+B.A.2250.BGN.N", "dep_nfc": "M.BG.B.L22.F+G+H.R+B.A.2240.BGN.N"}
PARTS_TOLERANCE = 0.02   # points between the weighted average and the published total, where both exist
FLAGS = {"A": None, "P": "p", "E": "e", "F": "f"}                      # OBS_STATUS -> the flags of live.series
HEAD = {"KEY", "TIME_PERIOD", "OBS_VALUE", "OBS_STATUS"}


def url(flow, key):
    return f"{ECB}/{flow}/{key}?format=csvdata"


def parse(raw, want_key):
    """-> [(time, value, flag)] of one series. ShapeError for anything but the ECB CSV of exactly that series."""
    got = parse_many(raw)
    if set(got) != {want_key}:
        raise ShapeError(f"a row of another series: {sorted(set(got) - {want_key})[:3]}")
    return got[want_key]


def parse_many(raw):
    """-> {key: [(time, value, flag)]} of an ECB CSV of one or more series. ShapeError for anything else."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ShapeError(f"not UTF-8: {e}") from None
    if text.lstrip()[:1] == "<":
        raise ShapeError("an HTML or XML page instead of CSV")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or not HEAD <= set(rows[0]):
        raise ShapeError(f"unexpected CSV header: {text[:200]!r}")
    out = {}
    for r in rows:
        t, v = r["TIME_PERIOD"], r["OBS_VALUE"].strip()
        if len(t) != 7 or t[4] != "-" or not (t[:4] + t[5:]).isdigit():
            raise ShapeError(f"not a month: {t!r}")
        try:
            value = float(v) if v else None
        except ValueError:
            raise ShapeError(f"not a number: {v!r}") from None
        out.setdefault(r["KEY"], []).append((t, value, FLAGS.get(r.get("OBS_STATUS") or "A", r.get("OBS_STATUS"))))
    return out


def matches(key, query):
    """Is the series `MIR.M.BG...` one the query `M.BG.B.A2C.F+I.R+B...` asked for?"""
    k, q = key.split(".")[1:], query.split(".")
    return len(k) == len(q) and all(a in b.split("+") for a, b in zip(k, q))


def weighted(series):
    """{key: [(time, value, flag)]} of the parts' rates (R) and amounts of new business (B) -> {time: rate}, their
    average weighted by the amounts. ShapeError for an amount without its rate."""
    rate, amount = {}, {}
    for key, pts in series.items():
        part, kind = key.split(".")[5:7]
        for t, v, _ in pts:
            if v is not None:
                (rate if kind == "R" else amount)[part, t] = v
    sums = {}
    for (part, t), b in amount.items():
        if b:
            if (part, t) not in rate:
                raise ShapeError(f"{part} {t}: an amount without its rate")
            n, d = sums.get(t, (0.0, 0.0))
            sums[t] = (n + rate[part, t] * b, d + b)
    return {t: round(n / d, 2) for t, (n, d) in sums.items()}


def months_between(a, b):
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:]) - int(a[5:])


def check(rows):
    """[problems]: every series is monthly without a gap; overnight deposits are never more than all deposits."""
    series, bad = {}, []
    for dims, geo, t, v, _ in rows:
        series.setdefault((dims["series"], geo), {})[t] = v
    for (s, geo), pts in sorted(series.items()):
        ts = sorted(pts)
        if months_between(ts[0], ts[-1]) + 1 != len(ts):
            bad.append(f"{s} {geo}: {len(ts)} месеца от {ts[0]} до {ts[-1]}, с дупки")
    for part, whole in (("dep_hh_on", "dep_hh"), ("dep_nfc_on", "dep_nfc")):
        a, b = series.get((part, "BG"), {}), series.get((whole, "BG"), {})
        for t in sorted(a.keys() & b.keys()):
            if a[t] is not None and b[t] is not None and a[t] > b[t] + 0.01:
                bad.append(f"{t}: {part} {a[t]} > {whole} {b[t]}")
    return bad


def load(conn, stats, get=None):
    """Every series, then per indicator: checked, then written under the hold rule. A series that cannot be read
    or parsed leaves its indicator as it was (a partial answer would look like removed rows)."""
    get = get or http.get
    rows, raws, failed, problems = {}, {}, set(), []
    for ind, flow, key, code, _ in SERIES:
        for ecb_geo, geo in PLACES[ind].items():
            parts = [(key.format(g=ecb_geo), lambda t: True)]
            if ind == "rates" and geo == "BG":   # in euro from the euro on; in leva before it, where the ECB has them
                parts = [(parts[0][0], lambda t: t >= BG_RATES_FROM)]
                if code in BG_BEFORE:
                    parts.append((BG_BEFORE[code], lambda t: t < BG_RATES_FROM))
            for k, keep in parts:
                try:
                    raw = get(url(flow, k))
                    rows.setdefault(ind, []).extend(({"series": code}, geo, t, v, f) for t, v, f in parse(raw, f"{flow}.{k}") if keep(t))
                    raws.setdefault(ind, []).append(raw)
                except (http.Gone, ShapeError) as e:
                    failed.add(ind)
                    problems.append(f"Икономика: ЕЦБ {flow}.{k}: {e}")
            if ind == "rates" and geo == "BG" and code in BG_PARTS:   # the months with no total in leva
                k = BG_PARTS[code]
                try:
                    raw = get(url(flow, k))
                    got = parse_many(raw)
                    if not all(matches(s, k) for s in got):
                        raise ShapeError(f"a row of another series: {[s for s in got if not matches(s, k)][:3]}")
                    avg = {t: v for t, v in weighted(got).items() if t < BG_RATES_FROM}
                    have = {t: v for d, g, t, v, _ in rows.get(ind, []) if d["series"] == code and g == geo}
                    off = [t for t in sorted(avg.keys() & have.keys())
                           if have[t] is not None and abs(avg[t] - have[t]) > PARTS_TOLERANCE]
                    if off:
                        raise ShapeError(f"{off[0]}: средното по срочност {avg[off[0]]} срещу общото {have[off[0]]}")
                    rows.setdefault(ind, []).extend(({"series": code}, geo, t, v, None)
                                                    for t, v in sorted(avg.items()) if t not in have)
                    raws.setdefault(ind, []).append(raw)
                except (http.Gone, ShapeError) as e:
                    failed.add(ind)
                    problems.append(f"Икономика: ЕЦБ {flow}.{k}: {e}")
    out = {}
    for ind in dict.fromkeys(i for i, *_ in SERIES):
        bad = [] if ind in failed else check(rows.get(ind, []))
        if ind in failed or bad:
            problems += [f"Икономика: ЕЦБ {ind}: {b}" for b in bad[:5]]
            store.state(conn, SOURCE, ind, status="invalid", error=("; ".join(bad[:5]) or "серия не се прочете")[:2000])
            out[ind] = "invalid"
            continue
        sha = store.save_raw(conn, SOURCE, ind, f"{ind}.csv", b"\n".join(raws[ind]))
        labels = {"series": {code: name for i, _, _, code, name in SERIES if i == ind}}
        out[ind] = store.apply(conn, SOURCE, ind, ind, sha, rows[ind], labels)
    stats["indicators"], stats["problems"] = out, problems
    return stats
