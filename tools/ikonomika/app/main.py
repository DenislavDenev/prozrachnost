"""Икономика: server-rendered pages over live.series; the line charts read /api/<indicator>.json."""
import csv
import datetime as dt
import io
import json
import os
from functools import lru_cache
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from ingest.config import BGN_PER_EUR
from ingest.eurostat import MEMBERS, indicators
from ingest.checks import NUTS3

from . import feedback

HERE = Path(__file__).parent
ROOT = HERE.parent
DSN = os.environ.get("IKONOMIKA_DSN", "dbname=ikonomika")
HUB_URL = os.environ.get("HUB_URL", "http://localhost:8001")
ASSET_V = "1"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
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


T.env.filters.update(num=fnum, period=fperiod, date=fdate, flag=flagtext)
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
    return page(request, "home.html", "Табло", k=k)


INFL_MAIN = ["TOTAL", "CP01", "CP02", "CP03", "CP04", "CP05", "CP06", "CP07", "CP08", "CP09", "CP10", "CP11", "CP12", "CP13",
             "GD", "SERV", "FOOD", "FOOD_NP", "NRG", "ELC_GAS", "FUEL", "IGD_NNRG", "TOT_X_NRG_FOOD", "AP"]


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
        names = dict(q("SELECT code, label FROM live.dim_label WHERE indicator = 'hicp_rch_a' AND dim = 'coicop18'"))
        for code, v in vals.items():
            rows.append({"code": code, "name": ref_labels().get(("coicop18", code)), "en": names.get(code, code),
                         "main": code in INFL_MAIN, "a": v.get("hicp_rch_a", (None, None)), "m": v.get("hicp_rch_m", (None, None)),
                         "i": v.get("hicp_i15", (None, None))})
    order = {c: i for i, c in enumerate(INFL_MAIN)}
    main = sorted((r for r in rows if r["main"]), key=lambda r: order[r["code"]])
    rest = sorted((r for r in rows if not r["main"]), key=lambda r: r["code"])
    return page(request, "inflaciya.html", "Инфлация", month=month, main=main, rest=rest, groups=INFL_MAIN,
                src=source("hicp_rch_a"))


OBL_MEASURES = {"EUR_HAB": ("gdp_nuts", "БВП на човек", "€ на човек", 0),
                "PPS_EU27_2020_HAB": ("gdp_nuts", "БВП на човек по покупателна способност", "СПС на човек", 0),
                "MIO_EUR": ("gdp_nuts", "БВП", "млн. €", 0),
                "THS": ("pop_nuts", "Население (средногодишно)", "хил. души", 1)}


@lru_cache(maxsize=1)
def oblast_shapes():
    return json.loads((HERE / "static" / "bg-oblasti.json").read_text(encoding="utf-8"))


@app.get("/oblasti", response_class=HTMLResponse)
def regions(request: Request, m: str = "EUR_HAB", y: str | None = None):
    if m not in OBL_MEASURES:
        raise HTTPException(404)
    ind, title, unit, digits = OBL_MEASURES[m]
    years = [r[0] for r in q("""SELECT DISTINCT time FROM live.series WHERE indicator = %s AND dims->>'unit' = %s
                                AND geo = ANY(%s) AND value IS NOT NULL ORDER BY 1 DESC""", ind, m, NUTS3)]
    if y is None and years:
        y = years[0]
    if y is not None and y not in years:
        raise HTTPException(404)
    base = str(int(y) - 10) if y and str(int(y) - 10) in years else (years[-1] if years else None)
    vals = dict(q("SELECT geo, value FROM live.series WHERE indicator = %s AND dims->>'unit' = %s AND time = %s AND geo = ANY(%s)",
                  ind, m, y, NUTS3 + ["BG"])) if y else {}
    old = dict(q("SELECT geo, value FROM live.series WHERE indicator = %s AND dims->>'unit' = %s AND time = %s AND geo = ANY(%s)",
                 ind, m, base, NUTS3)) if base else {}
    shapes = oblast_shapes()
    present = sorted((float(v) for g, v in vals.items() if g != "BG" and v is not None))
    # five classes by rank (quintiles): equal counts of oblasts, so the capital does not wash out the rest
    cuts = [present[min(len(present) - 1, round(len(present) * i / 5))] for i in (1, 2, 3, 4)] if present else []
    TINT = ["#e2f2ec", "#b4dccb", "#7fc0a6", "#3f9a7a", "#0b7a5e"]

    def cls(v):
        return None if v is None else sum(float(v) >= c for c in cuts)
    table = []
    for code in NUTS3:
        v = vals.get(code)
        o = old.get(code)
        table.append({"code": code, "name": shapes["oblasts"][code]["n"], "v": v, "c": cls(v),
                      "change": (float(v) / float(o) - 1) * 100 if v is not None and o else None})
    ranked = sorted((r for r in table if r["v"] is not None), key=lambda r: -float(r["v"]))
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    return page(request, "oblasti.html", "Области", m=m, y=y, years=years, base=base, title=title, unit=unit, digits=digits,
                table=table, bg=vals.get("BG"), shapes=shapes, tint=TINT, cuts=cuts, measures=OBL_MEASURES, src=source(ind))


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
        for g in ["EU27_2020", "EA", *MEMBERS]:
            v, f = got.get(g, (None, None))
            rows.append({"geo": g, "name": label("geo", g), "v": v, "f": f, "agg": g in ("EU27_2020", "EA")})
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
                rows=rows, bars=bars, rank=rank, n=len(members), api=f"/api/{ind}.json?geo=BG,EU27_2020&{qs}", src=source(ind))


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
    return page(request, "kursove.html", "Курсове", code=code, codes=codes, latest=latest, recent=list(reversed(s[-30:])),
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
