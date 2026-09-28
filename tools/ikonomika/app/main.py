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

from ingest import checks
from ingest.config import BGN_PER_EUR
from ingest import ecb, mf
from ingest.eurostat import EURO_AREA, MEMBERS, indicators

from . import feedback

HERE = Path(__file__).parent
ROOT = HERE.parent
DSN = os.environ.get("IKONOMIKA_DSN", "dbname=ikonomika")
HUB_URL = os.environ.get("HUB_URL", "http://localhost:8001")
ASSET_V = "6"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
app.add_middleware(GZipMiddleware, minimum_size=1000)   # the map of Europe is ~0.5 MB of paths
T = Jinja2Templates(directory=HERE / "templates")
app.include_router(feedback.router("DenislavDenev/prozrachnost", os.getenv("STATE_DIRECTORY", ROOT / ".data"), "Икономика"))

IND = {i["id"]: i for i in indicators() + ecb.INDICATORS + mf.INDICATORS}
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


T.env.globals.update(src_html=lambda ind: src_html(ind))


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


# every source: (name, the page of a data set, what to call the data set on the page when not its code)
SOURCE_PAGE = {"eurostat": ("Eurostat", "https://ec.europa.eu/eurostat/databrowser/view/{dataset}/default/table", None),
               "ecb": ("ЕЦБ", "https://data.ecb.europa.eu/data/datasets/{dataset}", None),
               "mf": ("Министерство на финансите", "https://data.egov.bg/data/view/{dataset}", "прогнозите на data.egov.bg")}


def src_of(indicator):
    """{name, dataset, url} of an indicator's source, without the database."""
    ind = IND.get(indicator) or {}
    name, page, shown = SOURCE_PAGE[ind.get("source", "eurostat")]
    return {"name": name, "dataset": shown or ind.get("dataset", indicator),
            "url": page.format(dataset=ind["dataset"]) if ind else ""}


def src_html(indicator):
    s = src_of(indicator)
    return Markup('<div class="src">{} · <a href="{}" rel="noopener">{}</a> · <a href="/csv/{}.csv">CSV</a></div>').format(
        s["name"], s["url"], s["dataset"], indicator)


def source(indicator):
    """What a panel says under its chart: source, dataset, link, the source's update, our last read."""
    st = q("SELECT updated, last_ok FROM ops.source_state WHERE source = %s AND ref = %s",
           (IND.get(indicator) or {}).get("source", "eurostat"), indicator)
    updated, read = st[0] if st else (None, None)
    return {**src_of(indicator), "updated": updated[:10] if updated else None, "read": read}


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
INFL_PICKED = ["TOTAL", "CP01", "NRG"]                  # drawn when the page opens: at most 3, the chart stays readable
INFL_SUB = ["CP01113", "CP0451", "CP07221"]             # bread, electricity, diesel


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
                picked=INFL_PICKED, sub=sub, src=source("hicp_rch_a"), panels=inflation_panels(month))


def inflation_panels(month):
    """What each of the 13 groups adds to the annual rate (its weight in the year times its own rate), producer prices
    and the price of electricity."""
    items, total = [], None
    if month:
        w = dict(q("""SELECT dims->>'coicop18', value FROM live.series WHERE indicator = 'hicp_w' AND geo = 'BG' AND time = %s""",
                   month[:4]))
        r = dict(q("""SELECT dims->>'coicop18', value FROM live.series WHERE indicator = 'hicp_rch_a' AND geo = 'BG' AND time = %s""",
                   month))
        for code in (f"CP{n:02d}" for n in range(1, 14)):
            items.append((label("coicop18", code), round(float(w[code]) * float(r[code]) / 1000, 2)
                          if w.get(code) is not None and r.get(code) is not None else None, ""))
        total = r.get("TOTAL")
    got = [v for _, v, _ in items if v is not None]
    note = (f"Сборът е {fnum(sum(got), 2)} процентни пункта; общата инфлация е {fnum(total)}%. Разликата е от закръглянето и от"
            f" това, че Eurostat смята общия индекс верижно, не като сбор." if got and total is not None else None)
    return [
        bars("hicp_w", f"Какво движи инфлацията{', ' + fperiod(month) if month else ''}",
             "принос на всяка група към годишната инфлация, процентни пункта (теглото ѝ по промяната на цените ѝ)", items, digits=2,
             note=note, cls="s12"),
        chart("ppi", "Цени на производител", "промишленост, % спрямо година по-рано; идват преди цените в магазина",
              chips=("nace_r2", ["B-E36", "MIG_NRG", "MIG_ING", "MIG_COG", "C10-C12"]), picked=["B-E36", "MIG_NRG", "C10-C12"],
              s_adj="NSA", unit="PCH_SM", zero=True, cls="s12"),
        chart("elec", "Цена на тока за домакинствата", "€ за 1 kWh с данъците, 2 500-4 999 kWh на година, по полугодия", digits=4,
              geo=f"BG,EU27_2020,{euro_area('elec')}", tax="I_TAX", currency="EUR", more=("В ЕС →", "/es?p=tok"), cls="s12"),
    ]


# measure -> (indicator, dimensions, title, unit, digits, the NUTS levels it has, per: (indicator, dimensions, factor) or None)
ALL_LEVELS, TO_REGIONS = (0, 1, 2, 3), (0, 1, 2)
MAP_MEASURES = {
    "EUR_HAB": ("gdp_nuts", {"unit": "EUR_HAB"}, "БВП на човек", "€ на човек", 0, ALL_LEVELS, None),
    "PPS_EU27_2020_HAB": ("gdp_nuts", {"unit": "PPS_EU27_2020_HAB"}, "БВП на човек по покупателна способност", "СПС на човек", 0, ALL_LEVELS, None),
    "MIO_EUR": ("gdp_nuts", {"unit": "MIO_EUR"}, "БВП", "млн. €", 0, ALL_LEVELS, None),
    "THS": ("pop_nuts", {"unit": "THS"}, "Население (средногодишно)", "хил. души", 1, ALL_LEVELS, None),
    # the labour force survey and the regional accounts go down to the regions (NUTS 2), not the oblasts
    "EMP": ("emp_reg", {"sex": "T", "age": "Y20-64"}, "Заетост на хората от 20 до 64 години", "% от населението на тази възраст", 1, TO_REGIONS, None),
    "UNEMP": ("unemp_reg", {"age": "Y15-74"}, "Безработица", "% от работната сила", 1, TO_REGIONS, None),
    "YOUTH": ("unemp_reg", {"age": "Y15-29"}, "Безработица при хората до 29 години", "% от работната сила", 1, TO_REGIONS, None),
    "COE": ("coe_reg", {}, "Компенсация на един нает (заплата и осигуровки)", "€ на година", 0, TO_REGIONS, ("sal_reg", {}, 1000)),
}
# the map covers Bulgaria or Europe on one plane (EPSG:3035), so the page can fly from one to the other; the levels
# are named as on the Тендер map, from the smallest: scope -> (name, {level: (plural, singular, NUTS level)})
SCOPES = {"bg": ("България", {"oblasti": ("Области", "Област", 3), "rayoni": ("Райони", "Район", 2),
                              "makrorayoni": ("Макрорайони", "Макрорайон", 1)}),
          "eu": ("Европа", {"oblasti": ("Области", "Област", 3), "rayoni": ("Райони", "Район", 2),
                            "makrorayoni": ("Макрорайони", "Макрорайон", 1), "darzhavi": ("Държави", "Държава", 0)})}
# European countries without NUTS regions: on the map and in the list of countries, with no data
NON_NUTS = ["AD", "BY", "MC", "MD", "RU", "SM", "UK", "VA"]


@lru_cache(maxsize=1)
def europe_shapes():
    """{h, names, world, "0".."3", bg}: Eurostat GISCO outlines (tools/build_europe_map.py)."""
    return json.loads((HERE / "static" / "europe.json").read_text(encoding="utf-8"))


def area_name(code, o="bg"):
    """Bulgarian for Bulgaria and the countries, the Latin name of GISCO for the other regions; in Europe every
    region carries its country's code, since the names alone do not say where they are."""
    if len(code) == 2:
        return label("geo", code)
    name = label("geo", code) + (" район" if len(code) == 4 else "") if code.startswith("BG")         else europe_shapes()["names"].get(code, code)
    return f"{name} [{code[:2]}]" if o == "eu" else name


@app.get("/oblasti")
def regions_moved(request: Request):
    return RedirectResponse(f"/karta?{request.url.query}" if request.url.query else "/karta", status_code=301)


def measure_values(m, time, codes):
    """{place: value} of a map measure for one period; a measure per something is divided by it."""
    ind, dims, *_, per = MAP_MEASURES[m]
    w, a = where(dims)
    vals = {g: float(v) for g, v in q(f"""SELECT geo, value FROM live.series WHERE indicator = %s AND time = %s
                                          AND geo = ANY(%s) AND value IS NOT NULL {w}""", ind, time, codes, *a)}
    if per:
        ind2, dims2, factor = per
        w2, a2 = where(dims2)
        den = {g: float(v) for g, v in q(f"""SELECT geo, value FROM live.series WHERE indicator = %s AND time = %s
                                             AND geo = ANY(%s) AND value IS NOT NULL {w2}""", ind2, time, codes, *a2)}
        vals = {g: v / den[g] * factor for g, v in vals.items() if den.get(g)}
    return vals


def map_data(m, y, o, l):
    """Everything the map shows for one choice; HTTPException(404) for an unknown one. A level the measure does not
    have (the oblasts of a survey that stops at the regions) gives the measure's smallest level instead."""
    if m not in MAP_MEASURES or o not in SCOPES:
        raise HTTPException(404)
    ind, dims, title, unit, digits, nuts_levels, per = MAP_MEASURES[m]
    levels = {k: lv for k, lv in SCOPES[o][1].items() if lv[2] in nuts_levels}
    l = l or ("oblasti" if o == "bg" else "darzhavi")
    if l not in SCOPES[o][1]:
        raise HTTPException(404)
    if l not in levels:
        l = min(levels, key=lambda k: -levels[k][2])
    plural, single, nuts = levels[l]
    shapes = europe_shapes()["bg" if o == "bg" else str(nuts)]
    shapes = shapes[str(nuts)] if o == "bg" else shapes
    codes = sorted(shapes) + (NON_NUTS if nuts == 0 else [])
    w, a = where(dims)
    counts = q(f"""SELECT time, count(*) FROM live.series WHERE indicator = %s {w}
                   AND geo = ANY(%s) AND value IS NOT NULL GROUP BY 1 ORDER BY 1 DESC""", ind, *a, codes)
    years = [t for t, _ in counts]
    if y is None and years:
        # the latest year with most of the map: a year where big countries are still missing would be mostly grey
        most = max(n for _, n in counts)
        y = next(t for t, n in counts if n >= 0.8 * most)
    if y is not None and y not in years:
        raise HTTPException(404)
    base = str(int(y) - 10) if y and str(int(y) - 10) in years else (years[-1] if years else None)
    vals = measure_values(m, y, codes + ["BG"]) if y else {}
    old = measure_values(m, base, codes) if base else {}
    items = []
    for code in codes:
        v, was = vals.get(code), old.get(code)
        items.append({"code": code, "name": area_name(code, o), "v": None if v is None else float(v),
                      "change": (float(v) / float(was) - 1) * 100 if v is not None and was else None})
    for i, it in enumerate(sorted((it for it in items if it["v"] is not None), key=lambda it: -it["v"]), 1):
        it["rank"] = i
    items.sort(key=lambda it: (it.get("rank", 10 ** 6), it["name"]))   # by rank, so the list reads with the map
    st = source(ind)
    return {"m": m, "y": y, "o": o, "l": l, "years": years, "base": base, "title": title, "unit": unit, "digits": digits,
            "plural": plural, "single": single, "nuts": nuts, "levels": [[k, lv[0]] for k, lv in levels.items()], "bg": None if vals.get("BG") is None else float(vals["BG"]),
            "updated": st["updated"], "dataset": st["dataset"], "url": st["url"], "csv": f"/csv/{ind}.csv", "items": items,
            "countries": {c: label("geo", c) for c in [*europe_shapes()["0"], *NON_NUTS]}, "source": src_of(ind)["name"]}


@app.get("/api/karta.json")
def api_map(m: str = "EUR_HAB", y: str | None = None, o: str = "bg", l: str | None = None):
    return JSONResponse(map_data(m, y, o, l))


@app.get("/karta", response_class=HTMLResponse)
def area_map(request: Request, m: str = "EUR_HAB", y: str | None = None, o: str = "bg", l: str | None = None):
    d = map_data(m, y, o, l)   # the page is drawn in the browser (static/karta.js) from /api/karta.json; here: 404s and the start
    return page(request, "karta.html", "Карта", d=d, scopes=SCOPES, measures=MAP_MEASURES)


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
      "zhilishta": ("hpi", {"purchase": "TOTAL", "unit": "RCH_A"}, "Цени на жилищата", "% спрямо година по-рано", 1),
      "lihva": ("ltrate", {}, "Дългосрочна лихва", "% годишно (10-годишни държавни облигации)", 2),
      "bednost": ("poverty", {"age": "TOTAL"}, "Под прага на бедност", "% от населението", 1),
      "tok": ("elec", {"tax": "I_TAX", "currency": "EUR"}, "Цена на тока за домакинствата", "€ за 1 kWh, с данъците", 4)}


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


@app.get("/kursove")
def rates_moved(request: Request):
    return RedirectResponse(f"/pari?{request.url.query}" if request.url.query else "/pari", status_code=301)


@app.get("/pari", response_class=HTMLResponse)
def money(request: Request, code: str = "USD"):
    latest = q("""SELECT DISTINCT ON (dims->>'code') dims->>'code', time, value FROM live.series WHERE indicator = 'fx_eur'
                  ORDER BY dims->>'code', time DESC""")
    codes = sorted({c for c, _, _ in latest} | {r[0] for r in q("SELECT DISTINCT dims->>'code' FROM live.series WHERE indicator = 'fx_bgn'")})
    if code not in codes and codes:
        raise HTTPException(404)
    s = fx_series(code) if codes else []
    span = q("SELECT min(time), max(time), count(*) FROM live.series WHERE indicator = 'fx_bgn'")[0]
    year_ago = str(dt.date.fromisoformat(s[-1][0]) - dt.timedelta(days=365)) if s else ""
    hs, ea = last("rates", series="housing"), last("rates", "EA", series="housing")
    loans = last("bank", series="loans_hh")
    lede = " ".join(x for x in [
        hs and f"Жилищните кредити в България струват {fnum(hs[1], 2)}% годишно ({fperiod(hs[0])})"
               + (f", в еврозоната {fnum(ea[1], 2)}%." if ea and ea[0] == hs[0] else "."),
        loans and f"Домакинствата дължат на банките {fnum(float(loans[1]) / 1000)} млрд. € ({fperiod(loans[0])})."] if x)
    return page(request, "pari.html", "Пари", code=code, codes=codes, latest=latest, recent=[x for x in reversed(s) if x[0] > year_ago],
                first=s[0] if s else None, span=span, lede=lede, panels=money_panels())


# ---------- the tabs made of panels (templates/_panels.html) ----------

MAX_PICKED = 3   # on when a chart opens: at most 3 lines, the rest are buttons (AGENTS.md 7)


def chart(ind, title, sub, digits=1, geo="BG", chips=None, picked=None, cls="s6", zero=False, tall=False, more=None, **dims):
    """A line chart over /api/<ind>.json. chips=(dimension, [codes]): buttons that pick the series, `picked` on."""
    ch = None
    if chips:
        dim, codes = chips
        # only the buttons that have a line: a combination the source does not publish would be a pressed button with nothing
        w, a = where({k: v for k, v in dims.items() if isinstance(v, str)})
        have = {r[0] for r in q(f"""SELECT DISTINCT dims->>%s FROM live.series WHERE indicator = %s AND geo = ANY(%s)
                                    AND value IS NOT NULL {w}""", dim, ind, geo.split(","), *a)}
        codes = [c for c in codes if c in have] or codes   # an empty database keeps them all
        picked = ([c for c in (picked or codes) if c in codes] or codes)[:MAX_PICKED]
        ch = {"param": dim, "items": [(c, label(dim, c, ind)) for c in codes], "picked": picked}
        dims = {**dims, dim: picked}
    query = "&".join(f"{k}={','.join(v) if isinstance(v, (list, tuple)) else v}" for k, v in {"geo": geo, **dims}.items())
    return {"kind": "chart", "ind": ind, "title": title, "sub": sub, "api": f"/api/{ind}.json?{query}", "digits": digits,
            "chips": ch, "cls": cls, "zero": zero, "tall": tall, "more": more}


def bars(ind, title, sub, items, digits=1, cls="s6", more=None, note=None):
    """Horizontal bars of (name, value, css class): the longest first, a value that is missing is "няма данни"."""
    vals = [float(v) for _, v, _ in items if v is not None]
    lo, hi = (min(0.0, *vals), max(0.0, *vals)) if vals else (0.0, 1.0)
    rows = []
    for name, v, cls_ in items:
        r = {"name": name, "v": v, "cls": cls_}
        if v is not None:
            r["x0"] = (min(0.0, float(v)) - lo) / ((hi - lo) or 1) * 100
            r["w"] = abs(float(v)) / ((hi - lo) or 1) * 100
        rows.append(r)
    rows.sort(key=lambda r: (r["v"] is None, -float(r["v"] or 0)))
    return {"kind": "bars", "ind": ind, "title": title, "sub": sub, "rows": rows, "digits": digits, "cls": cls, "more": more,
            "note": note}


def tab(request, name, h1, lede, panels):
    return page(request, "tab.html", name, h1=h1, lede=lede, panels=panels)


def sentence(*parts):
    return " ".join(p for p in parts if p)


GVA10 = ["A", "B-E", "F", "G-I", "J", "K", "L", "M_N", "O-Q", "R-U"]


@app.get("/rastezh", response_class=HTMLResponse)
def growth(request: Request):
    g = last("gdp_full", na_item="B1GQ", unit="CLV_PCH_PRE")
    ip = last("ind_prod", nace_r2="B-D", s_adj="CA", unit="PCH_SM")
    rt = last("retail", nace_r2="G47", s_adj="CA", unit="PCH_SM")
    lede = sentence(g and f"През {g[0]} г. БВП на България расте реално с {fnum(g[1])}%.",
                    ip and f"Промишленото производство през {fperiod(ip[0])} е с {fnum(ip[1])}% спрямо година по-рано,",
                    rt and f"а продажбите на дребно с {fnum(rt[1])}%.") or "Още няма данни."
    euro = lambda ind: f"BG,EU27_2020,{euro_area(ind)}"   # noqa: E731
    panels = [
        chart("gdp_full", "Ръстът на БВП и откъде идва", "принос към реалния ръст, процентни пункта; сборът на частите е ръстът на БВП",
              chips=("na_item", ["B1GQ", "P31_S14", "P3_S13", "P51G", "P52_P53", "P6", "P7"]), picked=["B1GQ", "P31_S14", "P51G"],
              unit="CON_PPCH_PRE", zero=True),
        chart("gdp_full", "Реален ръст по компоненти", "% спрямо предходната година",
              chips=("na_item", ["B1GQ", "P31_S14", "P3_S13", "P51G", "P6", "P7"]), picked=["B1GQ", "P31_S14", "P51G"],
              unit="CLV_PCH_PRE", zero=True),
        chart("gva", "Добавена стойност по отрасли", "% от общата добавена стойност, по текущи цени",
              chips=("nace_r2", GVA10), picked=["B-E", "G-I", "J"], unit="PC_TOT", cls="s12"),
        chart("ind_prod", "Промишлено производство", "индекс, 2021 = 100, сезонно и календарно изгладен",
              geo=euro("ind_prod"), nace_r2="B-D", s_adj="SCA", unit="I21"),
        chart("ind_prod", "Промишленост по вид продукция", "% спрямо година по-рано, календарно изгладено",
              chips=("nace_r2", ["B-D", "MIG_ING", "MIG_CAG", "MIG_COG", "MIG_NRG_X_E"]), picked=["B-D", "MIG_COG", "MIG_NRG_X_E"],
              s_adj="CA", unit="PCH_SM", zero=True),
        chart("retail", "Търговия на дребно", "обем на продажбите, индекс, 2021 = 100, сезонно и календарно изгладен",
              geo=euro("retail"), nace_r2="G47", s_adj="SCA", unit="I21"),
        chart("retail", "Продажби по вид стоки", "обем, % спрямо година по-рано, календарно изгладен",
              chips=("nace_r2", ["G47", "G47_FOOD", "G47_NFOOD", "G473"]), picked=["G47_FOOD", "G47_NFOOD", "G473"],
              s_adj="CA", unit="PCH_SM", zero=True),
        chart("constr", "Строителство", "индекс, 2021 = 100, сезонно и календарно изгладен",
              chips=("nace_r2", ["F", "F_CC1", "F_CC2"]), s_adj="SCA", unit="I21"),
        chart("sentiment", "Икономически настроения", "общ индикатор, дългосрочното средно = 100",
              geo=euro("sentiment"), indic="BS-ESI-I"),
        chart("sentiment", "Доверие по сектори в България", "салдо: положителни минус отрицателни отговори, %",
              chips=("indic", ["BS-ICI-BAL", "BS-SCI-BAL", "BS-RCI-BAL", "BS-CCI-BAL", "BS-CSMCI-BAL"]),
              picked=["BS-ICI-BAL", "BS-RCI-BAL", "BS-CSMCI-BAL"], zero=True),
        chart("tourism", "Нощувки в хотелите и другите места за настаняване", "брой нощувки на месец",
              chips=("c_resid", ["TOTAL", "DOM", "FOR"]), picked=["DOM", "FOR"], nace_r2="I551-I553", unit="NR", digits=0, cls="s12"),
    ]
    return tab(request, "Растеж", "Растеж", lede, panels)


def region_bars(ind, title, sub, **dims):
    """The six regions of Bulgaria and the country, for the latest year with all of them."""
    codes = ["BG", *checks.NUTS2]
    w, a = where(dims)
    t = q(f"""SELECT time FROM live.series WHERE indicator = %s AND geo = ANY(%s) AND value IS NOT NULL {w}
              GROUP BY 1 HAVING count(*) = %s ORDER BY 1 DESC LIMIT 1""", ind, codes, *a, len(codes))
    if not t:
        return bars(ind, title, sub, [])
    got = dict(q(f"SELECT geo, value FROM live.series WHERE indicator = %s AND time = %s AND geo = ANY(%s) {w}", ind, t[0][0], codes, *a))
    return bars(ind, f"{title}, {t[0][0]}", sub, [(area_name(c), got.get(c), "bg" if c == "BG" else "") for c in codes])


@app.get("/zaetost", response_class=HTMLResponse)
def jobs(request: Request):
    u = last("unemp_bg", sex="T", age="TOTAL", unit="PC_ACT")
    y = last("unemp_bg", sex="T", age="Y_LT25", unit="PC_ACT")
    e = last("emp_ind", nace_r2="TOTAL", unit="THS_PER")
    lede = sentence(u and f"Безработицата е {fnum(u[1])}% ({fperiod(u[0])})" + (f", при хората под 25 години {fnum(y[1])}%." if y else "."),
                    e and f"Заетите са {fnum(float(e[1]) / 1000, 2)} млн. ({fperiod(e[0])}).") or "Още няма данни."
    panels = [
        chart("unemp_bg", "Безработица по възраст", "% от работната сила, месечно, сезонно изгладена",
              chips=("age", ["TOTAL", "Y_LT25", "Y25-74"]), sex="T", unit="PC_ACT"),
        chart("unemp_bg", "Безработица при мъжете и жените", "% от работната сила, месечно, сезонно изгладена",
              chips=("sex", ["T", "M", "F"]), age="TOTAL", unit="PC_ACT"),
        chart("emp_ind", "Заети по отрасли", "хил. души, тримесечно, сезонно изгладено",
              chips=("nace_r2", GVA10), picked=["B-E", "G-I", "O-Q"], unit="THS_PER", cls="s12", tall=True),
        region_bars("emp_reg", "Заетост по райони", "% от хората от 20 до 64 години", sex="T", age="Y20-64")
        | {"more": ("На картата →", "/karta?m=EMP&o=bg&l=rayoni")},
        region_bars("unemp_reg", "Безработица по райони", "% от работната сила", age="Y15-74")
        | {"more": ("На картата →", "/karta?m=UNEMP&o=bg&l=rayoni")},
        chart("poverty", "Под прага на бедност", "% от хората с доход под 60% от медианния", geo=f"BG,EU27_2020,{euro_area('poverty')}",
              age="TOTAL", more=("В ЕС →", "/es?p=bednost")),
        chart("poverty", "Бедност по възраст в България", "% под прага на бедност",
              chips=("age", ["TOTAL", "Y_LT18", "Y_GE65"])),
    ]
    return tab(request, "Заетост", "Заетост и доходи", lede, panels)


FORECAST_ACTUAL = {   # item of the MF forecast -> (indicator, dimensions) of what happened, from Eurostat
    "gdp_growth": ("gdp_full", {"na_item": "B1GQ", "unit": "CLV_PCH_PRE"}),
    "hicp": ("hicp_avg", {"coicop18": "TOTAL"}),
    "unemployment": ("unemp_a", {}),
}


FORECAST_COLORS = ["#121417", "#8a5cb8", "#c2587a", "#7a8b2c", "#5b6b7f", "#b0413e", "#8d6e63", "#9aa1aa"]


def vintages():
    return [r[0] for r in q("SELECT DISTINCT dims->>'vintage' FROM live.series WHERE indicator = 'mf_forecast' ORDER BY 1 DESC")]


@app.get("/api/prognoza/{item}.json")
def api_forecast(item: str, v: str = ""):
    """What happened (Eurostat) and the MF forecasts of `item`, one line each (?v= the forecasts' dates)."""
    if item not in FORECAST_ACTUAL:
        raise HTTPException(404)
    ind, dims = FORECAST_ACTUAL[item]
    w, a = where(dims)
    actual = [[t, float(val), f] for t, val, f in q(f"""SELECT time, value, flag FROM live.series WHERE indicator = %s AND geo = 'BG'
                                                      AND value IS NOT NULL AND time >= '2015' {w} ORDER BY time""", ind, *a)]
    # reality in Bulgaria's green, each forecast in its own colour (none of them green, so the two never mix)
    series = [{"name": "Какво стана (Eurostat)", "geo": "BG", "points": actual, "color": "#0b7a5e"}]
    for i, vin in enumerate(v.split(",") if v else vintages()[:MAX_PICKED]):
        pts = [[t, None if val is None else float(val), f] for t, val, f in q(
            """SELECT time, value, flag FROM live.series WHERE indicator = 'mf_forecast' AND dims->>'vintage' = %s
               AND dims->>'item' = %s ORDER BY time""", vin, item)]
        if pts:
            series.append({"name": label("vintage", vin, "mf_forecast"), "geo": f"v{vin}", "points": pts,
                           "color": FORECAST_COLORS[i % len(FORECAST_COLORS)]})
    return JSONResponse({"indicator": "mf_forecast", "label": mf.NAMES[item], "unit": mf.NAMES[item], "source": src_of("mf_forecast"),
                         "series": series})


def forecast_panel(item, title, sub):
    vs = vintages()
    return {"kind": "chart", "ind": "mf_forecast", "title": title, "sub": sub, "api": f"/api/prognoza/{item}.json?v={','.join(vs[:MAX_PICKED])}",
            "digits": 1, "chips": {"param": "v", "items": [(x, label("vintage", x, "mf_forecast")) for x in vs], "picked": vs[:MAX_PICKED]},
            "cls": "s12", "zero": item == "gdp_growth", "tall": True, "more": None}


@app.get("/finansi", response_class=HTMLResponse)
def finance(request: Request):
    b = last("govt", sector="S13", na_item="B9", unit="PC_GDP")
    d = last("govt", sector="S13", na_item="GD", unit="PC_GDP")
    lt = last("ltrate")
    lede = sentence(b and f"Бюджетното салдо на държавата за {b[0]} г. е {fnum(b[1])}% от БВП (минусът е дефицит),",
                    d and f"а дългът {fnum(d[1])}% от БВП.",
                    lt and f"Държавата взима заем за 10 години при {fnum(lt[1], 2)}% ({fperiod(lt[0])}).") or "Още няма данни."
    panels = [
        chart("govt", "Салдо по подсектори", "% от БВП; минусът е дефицит",
              chips=("sector", ["S13", "S1311", "S1313", "S1314"]), picked=["S13", "S1311", "S1313"], na_item="B9", unit="PC_GDP", zero=True),
        chart("govt", "Дълг по инструменти", "% от БВП, в края на годината",
              chips=("na_item", ["GD", "GD_F3", "GD_F4", "GD_F2"]), picked=["GD", "GD_F3", "GD_F4"], sector="S13", unit="PC_GDP", zero=True),
        chart("govt", "Разходи за лихви", "% от БВП", sector="S13", na_item="D41PAY", unit="PC_GDP", zero=True),
        chart("ltrate", "Дългосрочна лихва", "% годишно, 10-годишни държавни облигации", digits=2,
              geo=f"BG,{euro_area('ltrate')}", more=("В ЕС →", "/es?p=lihva")),
        forecast_panel("gdp_growth", "Ръст на БВП: прогнозите на МФ и какво стана", "реален ръст, %; всяка линия е една прогноза, избери кои"),
        forecast_panel("hicp", "Инфлация: прогнозите на МФ и какво стана", "средногодишна инфлация (ХИПЦ), %"),
        forecast_panel("unemployment", "Безработица: прогнозите на МФ и какво стана", "% от работната сила"),
    ]
    return tab(request, "Финанси", "Държавни финанси", lede, panels)


def sitc_bars(indic, title):
    t = q("SELECT max(time) FROM live.series WHERE indicator = 'trade_a' AND value IS NOT NULL")[0][0]
    if not t:
        return bars("trade_a", title, "млн. €", [], digits=0)
    got = q("""SELECT dims->>'sitc06', value FROM live.series WHERE indicator = 'trade_a' AND time = %s AND dims->>'indic_et' = %s
               AND dims->>'partner' = 'WORLD' AND dims->>'sitc06' <> 'TOTAL'""", t, indic)
    return bars("trade_a", f"{title}, {t}", "млн. €, по стокови групи", [(label("sitc06", c, "trade_a"), v, "") for c, v in got], digits=0)


@app.get("/vanshen", response_class=HTMLResponse)
def external(request: Request):
    ex = last("trade_m", stk_flow="EXP", partner="WORLD", bclas_bec="TOTAL", indic_et="TRD_VAL")
    im = at("trade_m", ex[0], stk_flow="IMP", partner="WORLD", bclas_bec="TOTAL", indic_et="TRD_VAL") if ex else (None, None)
    ca = last("bop", bop_item="CA", stk_flow="BAL")
    lede = sentence(ex and f"През {fperiod(ex[0])} износът на стоки е {fnum(ex[1], 0)} млн. €"
                    + (f", вносът {fnum(im[0], 0)} млн. €." if im[0] is not None else "."),
                    ca and f"Текущата сметка за {fperiod(ca[0])} е {fnum(ca[1], 0)} млн. € (минусът е дефицит).") or "Още няма данни."
    panels = [
        chart("trade_m", "Износ и внос на стоки", "млн. € на месец", chips=("stk_flow", ["EXP", "IMP", "BAL_RT"]), picked=["EXP", "IMP"],
              partner="WORLD", bclas_bec="TOTAL", indic_et="TRD_VAL", digits=0, cls="s12"),
        chart("trade_m", "Износ към ЕС и извън ЕС", "млн. € на месец", chips=("partner", ["EU27_2020", "EXT_EU27_2020"]),
              stk_flow="EXP", bclas_bec="TOTAL", indic_et="TRD_VAL", digits=0),
        chart("trade_m", "Внос по вид стоки", "млн. € на месец", chips=("bclas_bec", ["INT", "CAP", "CONS"]),
              stk_flow="IMP", partner="WORLD", indic_et="TRD_VAL", digits=0),
        sitc_bars("MIO_EXP_VAL", "Износ по стокови групи"),
        sitc_bars("MIO_IMP_VAL", "Внос по стокови групи"),
        chart("bop", "Текуща сметка", "млн. € на тримесечие; минусът е дефицит",
              chips=("bop_item", ["CA", "G", "S", "IN1", "IN2", "KA"]), picked=["CA", "G", "S"], stk_flow="BAL", digits=0, zero=True, cls="s12"),
        chart("bop", "Преки инвестиции, потоци", "млн. € на тримесечие: в България (пасиви) и от България (активи)",
              chips=("stk_flow", ["LIAB", "ASS"]), bop_item="FA__D__F", digits=0, zero=True, cls="s12"),
        # the positions: in Bulgaria is a liability of the country, abroad an asset; the other two are reverse investment
        chart("fdi", "Натрупани преки инвестиции в България", "млн. € в края на годината", nace_r2="FDI", fdi_item="DI__D__F",
              stk_flow="LIAB", digits=0),
        chart("fdi", "Натрупани преки инвестиции от България в чужбина", "млн. € в края на годината", nace_r2="FDI",
              fdi_item="DO__D__F", stk_flow="ASS", digits=0),
    ]
    return tab(request, "Външен сектор", "Външен сектор", lede, panels)


def money_panels():
    return [
        chart("rates", "Лихви по нови кредити", "% годишно; за България от 2026 г. (от влизането в еврозоната)", digits=2,
              chips=("series", ["housing", "consumer", "cards", "business"]), picked=["housing", "consumer", "business"]),
        chart("rates", "Жилищни кредити: България и еврозоната", "цена на кредита, % годишно", digits=2,
              geo="BG,EA", series="housing"),
        chart("rates", "Лихви по депозити", "% годишно", digits=2, chips=("series", ["dep_hh", "dep_nfc", "dep_hh_on"])),
        chart("bank", "Кредити и депозити в банките", "млн. €, в края на месеца", digits=0,
              chips=("series", ["loans_hh", "loans_nfc", "dep_hh", "dep_nfc"]), picked=["loans_hh", "loans_nfc", "dep_hh"]),
        chart("mm_rate", "Лихви на междубанковия пазар", "% годишно, 3 месеца", digits=2,
              geo=f"BG,{euro_area('mm_rate')}", int_rt="IRT_M3", cls="s12"),
    ]


@app.get("/sources", response_class=HTMLResponse)
def sources(request: Request):
    st = {r[0]: r for r in q("""SELECT ref, status, error, last_read, last_ok, last_change, updated, rows
                                FROM ops.source_state WHERE source IN ('eurostat', 'ecb', 'mf')""")}
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
