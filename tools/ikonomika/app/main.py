"""Икономика: server-rendered pages over live.series; the line charts read /api/<indicator>.json."""
import csv
import datetime as dt
import io
import json
import math
import os
from functools import lru_cache
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from ingest.config import BGN_PER_EUR
from ingest.eurostat import EURO_AREA, MEMBERS, indicators

from . import feedback

HERE = Path(__file__).parent
ROOT = HERE.parent
DSN = os.environ.get("IKONOMIKA_DSN", "dbname=ikonomika")
HUB_URL = os.environ.get("HUB_URL", "http://localhost:8001")
ASSET_V = "1"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
app.add_middleware(GZipMiddleware, minimum_size=1000)   # the map of Europe is ~0.5 MB of paths
T = Jinja2Templates(directory=HERE / "templates")
app.include_router(feedback.router("DenislavDenev/prozrachnost", os.getenv("STATE_DIRECTORY", ROOT / ".data"), "Икономика"))

IND = {i["id"]: i for i in indicators()}
MONTHS = "януари февруари март април май юни юли август септември октомври ноември декември".split()
ROMAN = {"1": "I", "2": "II", "3": "III", "4": "IV"}


def q(sql, *args):
    with psycopg.connect(DSN) as c:
        return c.execute(sql, args).fetchall()


@lru_cache(maxsize=1)
def ref_labels():
    with open(ROOT / "db" / "ref" / "labels.csv", encoding="utf-8", newline="") as f:
        return {(r["dim"], r["code"]): r["label"] for r in csv.DictReader(f)}


def label(dim, code, indicator=None):
    """Bulgarian name from db/ref/labels.csv, else the source's (English) name, else the code."""
    got = ref_labels().get((dim, code))
    if got:
        return got
    if indicator:
        r = q("SELECT label FROM live.dim_label WHERE indicator = %s AND dim = %s AND code = %s", indicator, dim, code)
        if r:
            return r[0][0]
    return code


# ---------- formatting ----------

def fnum(v, d=1):
    if v is None:
        return "няма данни"
    s = f"{float(v):,.{d}f}".replace(",", " ").replace(".", ",")
    return s.replace("-", "−")


def fperiod(t):
    t = str(t)
    if len(t) == 7 and t[4] == "-" and t[5:].isdigit():
        return f"{MONTHS[int(t[5:]) - 1]} {t[:4]}"
    if "-Q" in t:
        return f"{ROMAN[t[-1]]} тримесечие {t[:4]}"
    if "-S" in t:
        return f"{'първо' if t[-1] == '1' else 'второ'} полугодие {t[:4]}"
    if len(t) == 10:
        return f"{t[8:]}.{t[5:7]}.{t[:4]}"
    return t


def fdate(v):
    if not v:
        return "никога"
    if isinstance(v, str):
        v = dt.datetime.fromisoformat(v)
    return v.astimezone(dt.timezone(dt.timedelta(hours=3))).strftime("%d.%m.%Y") if isinstance(v, dt.datetime) else v.strftime("%d.%m.%Y")


def flagtext(f):
    return label("flag", f) if f else ""


# the state of a source, in the words every tool uses (AGENTS.md 7)
STATUS = {"ok": "наред", "held": "задържан до второ четене", "invalid": "невалиден отговор",
          "gone": "липсва при източника", "error": "грешка при четене", None: "не е четен"}


def fstatus(s):
    return STATUS.get(s, s)


T.env.filters.update(num=fnum, period=fperiod, date=fdate, flag=flagtext, status=fstatus)
T.env.globals.update(v=ASSET_V, hub_url=HUB_URL, feedback_button=Markup(feedback.BUTTON),
                     support_link=Markup(feedback.support_link(HUB_URL)), label=label, IND=IND)


def page(request, name, nav, **ctx):
    return T.TemplateResponse(request, name, {"nav": nav, "fresh": freshness_line(), **ctx})


def freshness_line():
    r = q("SELECT max(last_ok) FROM ops.source_state WHERE source = 'eurostat'")
    return r[0][0] if r else None


# ---------- data ----------

def where(dims):
    parts, args = [], []
    for k, v in dims.items():
        parts.append("dims->>%s = %s")
        args += [k, v]
    return (" AND " + " AND ".join(parts)) if parts else "", args


def last(indicator, geo="BG", **dims):
    """(time, value, flag) of the latest period with a value, or None."""
    w, a = where(dims)
    r = q(f"""SELECT time, value, flag FROM live.series WHERE indicator = %s AND geo = %s AND value IS NOT NULL {w}
              ORDER BY time DESC LIMIT 1""", indicator, geo, *a)
    return r[0] if r else None


def at(indicator, time, geo="BG", **dims):
    w, a = where(dims)
    r = q(f"SELECT value, flag FROM live.series WHERE indicator = %s AND geo = %s AND time = %s {w}", indicator, geo, time, *a)
    return r[0] if r else (None, None)


def source(indicator):
    """What a panel says under its chart: dataset, link, the source's update, our last read."""
    ind = IND.get(indicator)
    st = q("SELECT updated, last_ok FROM ops.source_state WHERE source = 'eurostat' AND ref = %s", indicator)
    updated, read = st[0] if st else (None, None)
    return {"name": "Eurostat", "dataset": ind["dataset"] if ind else indicator,
            "url": f"https://ec.europa.eu/eurostat/databrowser/view/{ind['dataset']}/default/table" if ind else "",
            "updated": updated[:10] if updated else None, "read": read}


def euro_area(indicator):
    """The euro area code this indicator has: EA (changing composition), else EA21 or EA20."""
    have = {r[0] for r in q("SELECT DISTINCT geo FROM live.series WHERE indicator = %s AND geo = ANY(%s)", indicator, EURO_AREA)}
    return next((g for g in EURO_AREA if g in have), "EA")


# ---------- pages ----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    k = {}
    k["hicp"] = last("hicp_rch_a", coicop18="TOTAL")
    k["hicp_eu"] = at("hicp_eu", k["hicp"][0], "EU27_2020", coicop18="TOTAL")[0] if k["hicp"] else None
    k["gdp"] = last("gdp_a", na_item="B1GQ", unit="CP_MEUR")
    k["gdp_growth"] = at("gdp_a", k["gdp"][0], na_item="B1GQ", unit="CLV_PCH_PRE") if k["gdp"] else (None, None)
    k["gdp_q"] = last("gdp_q", na_item="B1GQ", unit="CLV_PCH_SM")
    k["unemp"] = last("unemp", age="TOTAL")
    k["debt"] = last("debt", na_item="GD", unit="PC_GDP")
    k["pps"] = last("gdp_pps")
    k["min_wage"] = last("min_wage")
    k["salary"] = last("salary")
    ea = {i: euro_area(i) for i in ("hicp_eu", "gdp_q", "unemp", "debt", "hpi")}
    return page(request, "home.html", "Табло", k=k, ea=ea)


INFL_MAIN = ["TOTAL", "CP01", "CP02", "CP03", "CP04", "CP05", "CP06", "CP07", "CP08", "CP09", "CP10", "CP11", "CP12", "CP13",
             "GD", "SERV", "FOOD", "FOOD_NP", "NRG", "ELC_GAS", "FUEL", "IGD_NNRG", "TOT_X_NRG_FOOD", "AP"]
INFL_PICKED = ["TOTAL", "CP01", "NRG", "SERV"]          # drawn when the page opens
INFL_SUB = ["CP01113", "CP01122", "CP0451", "CP07221"]  # bread, meat, electricity, diesel


@app.get("/inflaciya", response_class=HTMLResponse)
def inflation(request: Request):
    lt = last("hicp_rch_a", coicop18="TOTAL")
    month = lt[0] if lt else None
    rows = []
    if month:
        vals = {}
        for ind in ("hicp_rch_a", "hicp_rch_m", "hicp_i15"):
            for code, v, f in q("""SELECT dims->>'coicop18', value, flag FROM live.series
                                   WHERE indicator = %s AND geo = 'BG' AND time = %s""", ind, month):
                vals.setdefault(code, {})[ind] = (v, f)
        for code, v in vals.items():
            rows.append({"code": code, "name": label("coicop18", code, "hicp_rch_a"), "main": code in INFL_MAIN,
                         "a": v.get("hicp_rch_a", (None, None)), "m": v.get("hicp_rch_m", (None, None)),
                         "i": v.get("hicp_i15", (None, None))})
    order = {c: i for i, c in enumerate(INFL_MAIN)}
    main = sorted((r for r in rows if r["main"]), key=lambda r: order[r["code"]])
    rest = sorted((r for r in rows if not r["main"]), key=lambda r: (not r["code"].startswith("CP"), r["code"]))   # the classification first
    sub = [c for c in INFL_SUB if any(r["code"] == c for r in rest)]
    return page(request, "inflaciya.html", "Инфлация", month=month, main=main, rest=rest, groups=INFL_MAIN,
                picked=INFL_PICKED, sub=sub, src=source("hicp_rch_a"))


MAP_MEASURES = {"EUR_HAB": ("gdp_nuts", "БВП на човек", "€ на човек", 0),
                "PPS_EU27_2020_HAB": ("gdp_nuts", "БВП на човек по покупателна способност", "СПС на човек", 0),
                "MIO_EUR": ("gdp_nuts", "БВП", "млн. €", 0),
                "THS": ("pop_nuts", "Население (средногодишно)", "хил. души", 1)}
# the map covers Bulgaria or Europe; the levels are named as on the Тендер map and listed from the smallest:
# scope -> {level: (plural, singular, where the outlines are)}
SCOPES = {"bg": ("България", {"oblasti": ("Области", "Област", ("bg", "oblasts")), "rayoni": ("Райони", "Район", ("bg", "regions")),
                              "makrorayoni": ("Макрорайони", "Макрорайон", ("bg", "macros"))}),
          "eu": ("Европа", {"oblasti": ("Области", "Област", ("eu", "3")), "rayoni": ("Райони", "Район", ("eu", "2")),
                            "makrorayoni": ("Макрорайони", "Макрорайон", ("eu", "1")), "darzhavi": ("Държави", "Държава", ("eu", "0"))})}
MAP_MIN = 8  # % of the accent on the lowest value, as on the Тендер map


@lru_cache(maxsize=1)
def map_shapes():
    """{h, oblasts, regions, macros, names}: the outlines of the Тендер map (geoBoundaries, NUTS 2024)."""
    return json.loads((HERE / "static" / "bg-oblasti.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def europe_shapes():
    """{h, names, "0".."3": {code: path}}: Eurostat GISCO, NUTS 2024 (tools/build_europe_map.py)."""
    return json.loads((HERE / "static" / "europe.json").read_text(encoding="utf-8"))


def area_name(code):
    """Bulgarian for Bulgaria and the countries, the Latin name of GISCO for the other regions."""
    if len(code) == 2:
        return label("geo", code)
    if code.startswith("BG"):
        s = map_shapes()
        return s["oblasts"][code]["n"] if len(code) == 5 else s["names"][code] + (" район" if len(code) == 4 else "")
    return europe_shapes()["names"].get(code, code)


def outlines(where):
    """(height, {code: SVG path}) of a level."""
    src, key = where
    if src == "bg":
        return map_shapes()["h"], {c: v["d"] for c, v in map_shapes()[key].items()}
    return europe_shapes()["h"], europe_shapes()[key]


def shade(t):
    """The fill for a value at t (0..1) of the scale: one continuous gradient of the accent."""
    return f"color-mix(in srgb, var(--accent) {round(MAP_MIN + t * (100 - MAP_MIN))}%, #fff)"


@app.get("/oblasti")
def regions_moved(request: Request):
    return RedirectResponse(f"/karta?{request.url.query}" if request.url.query else "/karta", status_code=301)


@app.get("/karta", response_class=HTMLResponse)
def area_map(request: Request, m: str = "EUR_HAB", y: str | None = None, o: str = "bg", l: str | None = None):
    if m not in MAP_MEASURES or o not in SCOPES:
        raise HTTPException(404)
    levels = SCOPES[o][1]
    l = l or ("oblasti" if o == "bg" else "darzhavi")
    if l not in levels:
        raise HTTPException(404)
    ind, title, unit, digits = MAP_MEASURES[m]
    plural, single, where = levels[l]
    h, shapes = outlines(where)
    codes = sorted(shapes)
    counts = q("""SELECT time, count(*) FROM live.series WHERE indicator = %s AND dims->>'unit' = %s
                  AND geo = ANY(%s) AND value IS NOT NULL GROUP BY 1 ORDER BY 1 DESC""", ind, m, codes)
    years = [t for t, _ in counts]
    if y is None and years:
        # the latest year with most of the map: a year where big countries are still missing would be mostly grey
        most = max(n for _, n in counts)
        y = next(t for t, n in counts if n >= 0.8 * most)
    if y is not None and y not in years:
        raise HTTPException(404)
    base = str(int(y) - 10) if y and str(int(y) - 10) in years else (years[-1] if years else None)
    vals = dict(q("SELECT geo, value FROM live.series WHERE indicator = %s AND dims->>'unit' = %s AND time = %s AND geo = ANY(%s)",
                  ind, m, y, codes + ["BG"])) if y else {}
    old = dict(q("SELECT geo, value FROM live.series WHERE indicator = %s AND dims->>'unit' = %s AND time = %s AND geo = ANY(%s)",
                 ind, m, base, codes)) if base else {}
    present = sorted(float(vals[c]) for c in codes if vals.get(c) is not None and vals[c] > 0)
    lo, hi = (present[0], present[-1]) if present else (1.0, 1.0)
    # log scale, as on the Тендер map for money and counts: the capital would wash out the rest on a linear one
    at_ = lambda v: math.log(max(float(v), lo) / lo) / (math.log(hi / lo) or 1)  # noqa: E731 - the place of a value
    table = []
    for code in codes:
        v, old_v = vals.get(code), old.get(code)
        table.append({"code": code, "name": area_name(code), "v": v, "fill": None if v is None else shade(at_(v)),
                      "bg": o == "eu" and code.startswith("BG"),
                      "change": (float(v) / float(old_v) - 1) * 100 if v is not None and old_v else None})
    ranked = sorted((r for r in table if r["v"] is not None), key=lambda r: -float(r["v"]))
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    if o == "eu":   # over a thousand places: by rank, so the list reads with the map
        table.sort(key=lambda r: r.get("rank", 10 ** 6))
    ticks = [(lo, "a"), (hi, "z")] if present else []
    return page(request, "karta.html", "Карта", m=m, y=y, o=o, l=l, years=years, base=base, title=title, unit=unit, digits=digits,
                table=table, bg=vals.get("BG"), shapes=shapes, h=h, scopes=SCOPES, levels=levels, plural=plural, single=single,
                grad=(shade(0), shade(1)), ticks=ticks, measures=MAP_MEASURES, src=source(ind))


# the comparisons with the EU: (indicator, dims, title, unit, digits)
ES = {"inflaciya": ("hicp_eu", {"coicop18": "TOTAL"}, "Инфлация (ХИПЦ)", "% спрямо година по-рано", 1),
      "bvp-pps": ("gdp_pps", {}, "БВП на човек по покупателна способност", "ЕС = 100", 0),
      "bvp-chovek": ("gdp_pc", {}, "БВП на човек", "€ на човек", 0),
      "rast": ("gdp_a_eu", {}, "Реален ръст на БВП", "% спрямо предходната година", 1),
      "bezrabotica": ("unemp", {"age": "TOTAL"}, "Безработица", "% от работната сила", 1),
      "mladezhka": ("unemp", {"age": "Y_LT25"}, "Безработица под 25 години", "% от работната сила", 1),
      "minimalna": ("min_wage", {}, "Минимална заплата", "€ на месец", 0),
      "zaplata": ("salary", {}, "Средна заплата на пълно работно време", "€ на година", 0),
      "dalg": ("debt", {"na_item": "GD", "unit": "PC_GDP"}, "Държавен дълг", "% от БВП", 1),
      "saldo": ("debt", {"na_item": "B9", "unit": "PC_GDP"}, "Бюджетно салдо", "% от БВП (дефицитът е с минус)", 1),
      "zhilishta": ("hpi", {"purchase": "TOTAL", "unit": "RCH_A"}, "Цени на жилищата", "% спрямо година по-рано", 1)}


@app.get("/es", response_class=HTMLResponse)
def eu(request: Request, p: str = "inflaciya"):
    if p not in ES:
        raise HTTPException(404)
    ind, dims, title, unit, digits = ES[p]
    lt = last(ind, **dims)
    rows = []
    if lt:
        w, a = where(dims)
        got = {g: (v, f) for g, v, f in q(f"SELECT geo, value, flag FROM live.series WHERE indicator = %s AND time = %s {w}",
                                          ind, lt[0], *a)}
        ea = euro_area(ind)
        for g in ["EU27_2020", ea, *MEMBERS]:
            v, f = got.get(g, (None, None))
            rows.append({"geo": g, "name": label("geo", g), "v": v, "f": f, "agg": g in ("EU27_2020", ea),
                         "cls": {"BG": "bg", "EU27_2020": "eu", ea: "ea"}.get(g, "")})
    vals = [float(r["v"]) for r in rows if r["v"] is not None]
    lo, hi = (min(0.0, *vals), max(0.0, *vals)) if vals else (0.0, 1.0)
    for r in rows:
        if r["v"] is not None:
            r["x0"] = (min(0.0, float(r["v"])) - lo) / ((hi - lo) or 1) * 100
            r["w"] = abs(float(r["v"])) / ((hi - lo) or 1) * 100
    bars = sorted((r for r in rows if r["v"] is not None), key=lambda r: -float(r["v"]))
    members = [r for r in bars if not r["agg"]]
    rank = next((i for i, r in enumerate(members, 1) if r["geo"] == "BG"), None)
    qs = "&".join(f"{k}={v}" for k, v in dims.items())
    return page(request, "es.html", "ЕС", p=p, pages=ES, ind=ind, title=title, unit=unit, digits=digits, period=lt[0] if lt else None,
                rows=rows, bars=bars, rank=rank, n=len(members), api=f"/api/{ind}.json?geo=BG,EU27_2020,{euro_area(ind)}&{qs}", src=source(ind))


def fx_series(code):
    """[(day, currency per 1 euro, how)]: from 1999 the leva rate turned into euro by the fixed rate
    1.95583 (the lev was tied to the euro), from 2026 the ECB reference rate the BNB publishes."""
    out = []
    for t, v, units in q("""SELECT time, value, dims->>'units' FROM live.series WHERE indicator = 'fx_bgn' AND dims->>'code' = %s
                            AND time >= '1999-01-01' AND value IS NOT NULL ORDER BY time""", code):
        out.append((t, round(BGN_PER_EUR * float(units) / float(v), 6), "bgn"))
    out += [(t, float(v), "eur") for t, v in q("""SELECT time, value FROM live.series WHERE indicator = 'fx_eur' AND dims->>'code' = %s
                                                  AND value IS NOT NULL ORDER BY time""", code)]
    return out


@app.get("/kursove", response_class=HTMLResponse)
def rates(request: Request, code: str = "USD"):
    latest = q("""SELECT DISTINCT ON (dims->>'code') dims->>'code', time, value FROM live.series WHERE indicator = 'fx_eur'
                  ORDER BY dims->>'code', time DESC""")
    codes = sorted({c for c, _, _ in latest} | {r[0] for r in q("SELECT DISTINCT dims->>'code' FROM live.series WHERE indicator = 'fx_bgn'")})
    if code not in codes and codes:
        raise HTTPException(404)
    s = fx_series(code) if codes else []
    span = q("SELECT min(time), max(time), count(*) FROM live.series WHERE indicator = 'fx_bgn'")[0]
    year_ago = str(dt.date.fromisoformat(s[-1][0]) - dt.timedelta(days=365)) if s else ""
    return page(request, "kursove.html", "Курсове", code=code, codes=codes, latest=latest, recent=[x for x in reversed(s) if x[0] > year_ago],
                first=s[0] if s else None, span=span)


@app.get("/sources", response_class=HTMLResponse)
def sources(request: Request):
    st = {r[0]: r for r in q("""SELECT ref, status, error, last_read, last_ok, last_change, updated, rows
                                FROM ops.source_state WHERE source = 'eurostat'""")}
    span = {r[0]: r[1:] for r in q("SELECT indicator, min(time), max(time), count(*) FROM live.series GROUP BY 1")}
    items = [{"ind": i, "st": st.get(i["id"]), "span": span.get(i["id"])} for i in IND.values()]
    fx = q("SELECT status, error, last_read, last_ok FROM ops.source_state WHERE source = 'bnb' AND ref = 'fx'")
    held = q("SELECT source, ref, rows, first_at FROM ops.held ORDER BY first_at")
    changes = q("""SELECT source, cause, count(*) FROM ops.change_log WHERE detected_at > now() - interval '30 days'
                   GROUP BY 1, 2 ORDER BY 1, 2""")
    return page(request, "sources.html", "Източници", items=items, fx=fx[0] if fx else None,
                fx_span={k: span.get(k) for k in ("fx_bgn", "fx_eur")}, held=held, changes=changes)


@app.get("/how", response_class=HTMLResponse)
def how(request: Request):
    return page(request, "how.html", "Как работи")


# ---------- data for everyone: JSON and CSV ----------

@app.get("/api/fx/{code}.json")
def api_fx(code: str):
    s = fx_series(code)
    if not s:
        raise HTTPException(404)
    return JSONResponse({"code": code, "name": label("code", code), "unit": f"{code} за 1 евро",
                         "source": "БНБ", "points": [[t, v, h] for t, v, h in s]})


@app.get("/api/{indicator}.json")
def api(indicator: str, request: Request, geo: str = "BG", since: str = ""):
    """The series of an indicator, filtered by geo (comma-separated) and any dimension (?unit=RCH_A,RCH_M)."""
    if indicator not in IND:
        raise HTTPException(404)
    dims = {k: v.split(",") for k, v in request.query_params.items() if k not in ("geo", "since")}
    sql, args = ["indicator = %s", "geo = ANY(%s)", "time >= %s"], [indicator, geo.split(","), since]
    for k, v in dims.items():
        sql.append("dims->>%s = ANY(%s)")
        args += [k, v]
    rows = q(f"SELECT dims, geo, time, value, flag FROM live.series WHERE {' AND '.join(sql)} ORDER BY geo, dims::text, time", *args)
    series = {}
    for d, g, t, v, f in rows:
        key = json.dumps({**d, "geo": g}, sort_keys=True)
        series.setdefault(key, {"dims": d, "geo": g, "points": []})["points"].append([t, None if v is None else float(v), f])
    geos = {s["geo"] for s in series.values()}
    for s in series.values():
        s["name"] = ", ".join(([label("geo", s["geo"])] if len(geos) > 1 else [])
                              + [label(k, c, indicator) for k, c in s["dims"].items()
                                 if len({x["dims"].get(k) for x in series.values()}) > 1]) or label("geo", s["geo"])
    # in the order asked for: ?coicop18=TOTAL,CP01 draws TOTAL first
    wanted = {"geo": geo.split(","), **dims}

    def rank(s):
        got = [s["geo"] if k == "geo" else s["dims"].get(k) for k in wanted]
        return [w.index(v) if v in w else len(w) for v, w in zip(got, wanted.values())]
    ind, src = IND[indicator], source(indicator)
    src["read"] = src["read"].isoformat() if src["read"] else None
    return JSONResponse({"indicator": indicator, "label": ind["label"], "unit": ind["unit_label"], "source": src,
                         "series": sorted(series.values(), key=rank)})


@app.get("/csv/{indicator}.csv")
def csv_export(indicator: str):
    if indicator not in IND and indicator not in ("fx_bgn", "fx_eur"):
        raise HTTPException(404)

    def gen():
        rows = q("SELECT dims, geo, time, value, flag FROM live.series WHERE indicator = %s ORDER BY dims::text, geo, time", indicator)
        keys = sorted({k for d, *_ in rows for k in d})
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["indicator", *keys, "geo", "time", "value", "flag"])
        for d, g, t, v, f in rows:
            w.writerow([indicator, *(d.get(k, "") for k in keys), g, t, "" if v is None else v, f or ""])
        yield "﻿" + buf.getvalue()
    return StreamingResponse(gen(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="ikonomika-{indicator}.csv"'})


@app.get("/healthz")
def healthz():
    n = q("SELECT count(*) FROM live.series")[0][0]
    return {"ok": True, "rows": n}


@app.get("/favicon.svg", include_in_schema=False)
def favicon():
    return FileResponse(HERE / "static" / "favicon.svg", media_type="image/svg+xml")


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith(("/api/", "/csv/")):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return T.TemplateResponse(request, "404.html", {"nav": None, "fresh": None}, status_code=404)
