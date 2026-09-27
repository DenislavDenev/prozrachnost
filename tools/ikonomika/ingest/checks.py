"""Reconciliation with the source (STANDARD 1Б) and freshness (1В).

eurostat(): checks on the answers of one run, before anything is written; an indicator named here is
not written. freshness(): what is late, held or broken now; empty when all is well.
"""
from collections import defaultdict

NUTS3 = ("BG311 BG312 BG313 BG314 BG315 BG321 BG322 BG323 BG324 BG325 BG331 BG332 BG333 BG334 BG341 BG342 "
         "BG343 BG344 BG411 BG412 BG413 BG414 BG415 BG421 BG422 BG423 BG424 BG425").split()
NUTS_TOLERANCE = 0.1  # million €: the regions are published rounded to 0.01


def nuts_sum(rows):
    """[problems]: for every year with all 28 regions and the country, the sum of the regions is the country."""
    by = defaultdict(dict)
    for dims, geo, time, value, _ in rows:
        if dims.get("unit") == "MIO_EUR" and value is not None:
            by[time][geo] = value
    bad = []
    for time, g in sorted(by.items()):
        if "BG" in g and all(n in g for n in NUTS3):
            s = sum(g[n] for n in NUTS3)
            if abs(s - g["BG"]) > NUTS_TOLERANCE:
                bad.append(f"{time}: сборът на областите {s:.2f} ≠ страната {g['BG']:.2f} млн. €")
    return bad


def prev(time, months):
    y, m = map(int, time.split("-"))
    m -= months
    while m < 1:
        y, m = y - 1, m + 12
    return f"{y}-{m:02d}"


def rate_bounds(a, b, rate):
    """Where the published rate of change from index b to index a may lie. Both indices are printed with 2
    decimals; Eurostat computes the rate from the unrounded index, so 0.1 pp more is allowed (the plan's
    tolerance), and 2% of the rate for the hyperinflation of 1997-1999, when the 2015-based index is below
    10 and a rate of hundreds of % rests on a few hundredths (measured on the whole series, 28.09.2026)."""
    slack = 0.1 + 0.02 * abs(rate)
    return ((a - 0.005) / (b + 0.005) - 1) * 100 - slack, ((a + 0.005) / (b - 0.005) - 1) * 100 + slack


def hicp_rates(i15, rch_m, rch_a):
    """[problems]: every published monthly and annual rate agrees with the index it comes from."""
    idx = {(d.get("coicop18"), g, t): v for d, g, t, v, _ in i15 if v is not None}
    bad = []
    for rows, months, name in ((rch_m, 1, "месечна"), (rch_a, 12, "годишна")):
        for d, g, t, v, _ in rows:
            a, b = idx.get((d.get("coicop18"), g, t)), idx.get((d.get("coicop18"), g, prev(t, months)))
            if v is None or a is None or b is None or b <= 0.005:
                continue
            lo, hi = rate_bounds(a, b, v)
            if not lo <= v <= hi:
                bad.append(f"{d.get('coicop18')} {t}: {name} промяна {v} не следва от индекса ({a} / {b})")
    return bad


def eurostat(parsed):
    """{indicator: problem} for the answers of one run (parsed: {indicator: jsonstat.parse() result})."""
    out = {}
    if "gdp_nuts" in parsed:
        bad = nuts_sum(parsed["gdp_nuts"]["rows"])
        if bad:
            out["gdp_nuts"] = "; ".join(bad[:5])
    if {"hicp_i15", "hicp_rch_m", "hicp_rch_a"} <= parsed.keys():
        bad = hicp_rates(parsed["hicp_i15"]["rows"], parsed["hicp_rch_m"]["rows"], parsed["hicp_rch_a"]["rows"])
        if bad:
            msg = f"{len(bad)} несъответствия: " + "; ".join(bad[:5])
            out.update({k: msg for k in ("hicp_i15", "hicp_rch_m", "hicp_rch_a")})
    return out


def freshness(conn, indicators):
    """[problems] now: an indicator not read in two days, not ok, held over a day, or without a new period
    for longer than its stale_days; the exchange rates older than 5 days or their last read not ok."""
    bad = []
    st = {r[0]: r[1:] for r in conn.execute(
        """SELECT ref, status, error, last_ok, last_new_period FROM ops.source_state WHERE source = 'eurostat'""")}
    for ind in indicators:
        s = st.get(ind["id"])
        if not s:
            bad.append(f"Икономика: {ind['id']} още не е четен")
            continue
        status, error, last_ok, last_new = s
        if status not in ("ok", "held"):
            bad.append(f"Икономика: {ind['id']} {status}: {error}")
        stale = conn.execute("SELECT %s < now() - make_interval(days => %s)", (last_new, int(ind["stale_days"]))).fetchone()[0]
        if stale:
            bad.append(f"Икономика: {ind['id']} няма нов период от {last_new:%d.%m.%Y}")
        late = conn.execute("SELECT %s IS NULL OR %s < now() - interval '2 days'", (last_ok, last_ok)).fetchone()[0]
        if late:
            bad.append(f"Икономика: {ind['id']} не е четен успешно от {last_ok:%d.%m.%Y}" if last_ok else
                       f"Икономика: {ind['id']} не е четен успешно")
    for source, ref, since in conn.execute("SELECT source, ref, first_at FROM ops.held WHERE first_at < now() - interval '1 day'"):
        bad.append(f"Икономика: {source} {ref} е задържан от {since:%d.%m.%Y}")
    last_fx = conn.execute("SELECT max(time) FROM live.series WHERE indicator = 'fx_eur'").fetchone()[0]
    if not last_fx or last_fx < str(conn.execute("SELECT (now() - interval '5 days')::date").fetchone()[0]):
        bad.append(f"Икономика: курсовете на БНБ са към {last_fx or 'никога'}")
    fx = conn.execute("SELECT status, error FROM ops.source_state WHERE source = 'bnb' AND ref = 'fx'").fetchone()
    if fx and fx[0] != "ok":
        bad.append(f"Икономика: курсовете на БНБ {fx[0]}: {fx[1]}")
    return bad
