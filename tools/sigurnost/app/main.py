import csv
import datetime as dt
import io
import json
import os
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from ingest import checks, config, db
from . import feedback, queries as Q

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Престъпност и пожари", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.include_router(feedback.router("DenislavDenev/prozrachnost", Path(os.environ.get("SIGURNOST_FEEDBACK", str(config.DATA / "feedback"))), "Престъпност и пожари"))
t = Jinja2Templates(directory=ROOT / "templates")
MENU = [("Табло", "/"), ("Карта", "/karta"), ("Видове", "/vidove"), ("Области", "/oblasti"), ("Пожари", "/pozhari"), ("Източници", "/sources")]
V = "1"
MAP_MEASURES = {"per100k": "Регистрирани на 100 хил. души", "reg": "Регистрирани престъпления", "clearance": "Разкриваемост, %"}


def number(v, d=0):
    return "няма данни" if v is None else f"{Decimal(str(v)):,.{d}f}".replace(",", " ").replace(".", ",")


def pct(v, d=1):
    return "няма данни" if v is None else number(v, d) + "%"


def signed(v, d=0):
    if v is None:
        return "няма данни"
    s = number(abs(v), d)
    return ("+" if v > 0 else "−" if v < 0 else "") + s


t.env.filters.update(num=number, pct=pct, signed=signed)


def hub():
    return os.environ.get("HUB_URL", "https://prozrachnost.denev.work")


def help_json():
    ind = Q.indicators()
    cols = {k: dict(title=v["title"], text=v["definition"] + (" Формула: " + v["formula"] + "." if v["formula"] else ""),
                    example=("Условен пример: " + v["example"]) if v["example"] else "",
                    source="https://data.egov.bg/organisation/profile/113") for k, v in ind.items()}
    cols["rows"] = dict(title="Редове „в това число“", text="Редовете със знак „·“ или „-“ са част от точката над тях и може да са само част от нея. Не се добавят към точките и не се попълва остатък. Номерацията е на МВР и в оригинала има отместени номера.",
                        example="", source="/how")
    return Markup(json.dumps(dict(rows=[], columns=cols), ensure_ascii=False).replace("</", "<\\/"))


def render(request, name, **ctx):
    return t.TemplateResponse(request=request, name=name, context=dict(
        v=V, hub_url=hub(), feedback_button=Markup(feedback.BUTTON), support_link=Markup(feedback.support_link(hub())), menu=MENU,
        patna_url=os.environ.get("PATNA_URL", "http://192.168.1.68:8002"), help_json=help_json(), ind=Q.indicators(), **ctx))


def serial(data):
    return JSONResponse(json.loads(json.dumps(data, default=lambda v: Q.number(v) if isinstance(v, Decimal) else str(v))))


def csv_response(rows, headers, name):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(headers)
    for r in rows:
        w.writerow(["" if r.get(h) is None else r.get(h) for h in headers])
    return Response(("﻿" + out.getvalue()).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def table_of(rows, cols):
    """The data of a table once, for the page, the CSV and the JSON: dicts with the same keys."""
    return [{c: (Q.number(r.get(c)) if not isinstance(r.get(c), str) else r.get(c)) for c in cols} for r in rows]


def need_year(y):
    year = Q.year_or_latest(y)
    return year


def chart_series(code):
    """Lines of the reg / solved counts by year; a changed template is a new line (no line across a break)."""
    by = {}
    for r in Q.series(code):
        by.setdefault((r["template"], r["indicator"]), []).append((str(r["year"]), Q.number(r["value"])))
    names = {"reg": "Регистрирани", "solved": "Разкрити (от регистрираните през годината)", "per100k": "Регистрирани на 100 хил. души", "clearance": "Разкриваемост, %"}
    out = []
    eras = sorted({k[0] for k in by}, key=lambda tpl: min(p[0] for (a, _), pts in by.items() if a == tpl for p in pts))
    for ind in ("reg", "solved"):
        for tpl in eras:
            pts = by.get((tpl, ind))
            if pts:
                span = pts[0][0] if pts[0][0] == pts[-1][0] else f"{pts[0][0]}-{pts[-1][0]}"
                out.append(dict(name=names[ind] + (f" ({span})" if len(eras) > 1 else ""), geo="BG-" + ind, points=pts, symbol=True,
                                color="#0b7a5e" if ind == "reg" else "#121417", dashed=ind == "solved"))
    return dict(unit="брой", series=out)


@app.get("/healthz")
def health():
    with db.connect() as c:
        c.execute("SELECT 1")
    return {"ok": True}


@app.get("/favicon.svg")
def favicon():
    return Response((ROOT / "static/favicon.svg").read_text(), media_type="image/svg+xml")


# ---------- dashboard and the table by oblast ----------

OBL_COLS = ["code", "name", "year", "reg", "reg_unknown", "per100k", "solved", "clearance", "solved_unknown"]


def oblast_table(year):
    return table_of(Q.by_structure(year), OBL_COLS)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, y: str = None):
    year = need_year(y)
    if year is None:
        return render(request, "empty.html", nav="Табло")
    rows = Q.by_structure(year)
    country = next((d for d in rows if d["code"] == "BG"), None)
    return render(request, "home.html", nav="Табло", year=year, years=Q.years(), country=country, rows=[d for d in rows if d["code"] != "BG"],
                  table=Q.source_table(year, "structures"))


@app.get("/api/series.json")
def series_api(o: str = "BG"):
    if not Q.structure(o):
        raise HTTPException(404, "Няма такава структура")
    return serial(chart_series(o))


@app.get("/oblasti.csv")
def oblasti_csv(y: str = None):
    year = need_year(y)
    return csv_response(oblast_table(year), OBL_COLS, f"sigurnost-oblasti-{year}.csv")


@app.get("/oblasti.json")
def oblasti_json(y: str = None):
    year = need_year(y)
    return serial(dict(year=year, source=source_info(year, "structures"), rows=oblast_table(year)))


def source_info(year, family):
    s = Q.source_table(year, family)
    if not s:
        return None
    return dict(resource_uri=s["resource_uri"], sha256=s["sha256"], set_uri=s["set_uri"], template=s["template"], family=family,
                url=f"https://data.egov.bg/data/resourceView/{s['resource_uri']}")


@app.get("/oblasti", response_class=HTMLResponse)
def oblasti(request: Request, y: str = None):
    year = need_year(y)
    rows = [d for d in Q.by_structure(year) if d["code"] != "BG"]
    return render(request, "oblasti.html", nav="Области", year=year, years=Q.years(), rows=rows, table=Q.source_table(year, "structures"))


def compare_rows(rows, other, ind):
    if rows and other and rows[0]["template"] != other[0]["template"]:
        Q.compare(rows, [], ind)
        return "Шаблонът на таблицата е сменен между двете години: числата не се сравняват."
    Q.compare(rows, other, ind)
    return None


TYPE_COLS = ["row_no", "code", "text", "level", "year", "reg", "per100k", "solved", "clearance"]


def types_data(year, code, sr=None):
    rows, inds = Q.type_rows(year, code)
    note = None
    if sr and sr != year:
        note = compare_rows(rows, Q.type_rows(sr, code)[0], "reg")
    return rows, inds, note


@app.get("/vidove", response_class=HTMLResponse)
def vidove(request: Request, y: str = None, o: str = "BG", sr: str = None):
    s = Q.structure(o)
    if not s or s["kind"] == "gd":
        raise HTTPException(404, "Няма такава структура")
    year = need_year(y)
    sr_year = Q.year_or_latest(sr) if sr else None
    rows, inds, note = types_data(year, o, sr_year)
    return render(request, "vidove.html", nav="Видове", s=s, year=year, years=Q.years(), sr=sr_year if sr_year != year else None, rows=rows, inds=inds,
                  note=note, oblasts=[x for x in Q.structures() if x["kind"] in ("oblast", "country")],
                  table=Q.source_table(year, "types" if o == "BG" else "types_by_structure"))


@app.get("/vidove.csv")
def vidove_csv(y: str = None, o: str = "BG", sr: str = None):
    year = need_year(y)
    sr_year = Q.year_or_latest(sr) if sr else None
    rows, inds, _ = types_data(year, o, sr_year if sr_year != year else None)
    cols = TYPE_COLS + (["other_reg", "diff_reg"] if sr_year and sr_year != year else [])
    return csv_response(table_of(rows, cols), cols, f"sigurnost-vidove-{o}-{year}.csv")


@app.get("/vidove.json")
def vidove_json(y: str = None, o: str = "BG", sr: str = None):
    year = need_year(y)
    sr_year = Q.year_or_latest(sr) if sr else None
    rows, inds, note = types_data(year, o, sr_year if sr_year != year else None)
    fam = "types" if o == "BG" else "types_by_structure"
    cols = TYPE_COLS + (["other_reg", "diff_reg"] if sr_year and sr_year != year else [])
    return serial(dict(year=year, compare_year=sr_year if sr_year != year else None, structure=o, note=note, source=source_info(year, fam), rows=table_of(rows, cols)))


@app.get("/oblasti/{code}.csv")
def oblast_csv(code: str, y: str = None):
    s = Q.structure(code)
    if not s or code == "BG":
        raise HTTPException(404, "Няма такава структура")
    year = need_year(y)
    rows, _, _ = types_data(year, code)
    cols = ["row_no", "code", "text", "level", "year"] + Q.BLOCK
    return csv_response(table_of(rows, cols), cols, f"sigurnost-{code}-{year}.csv")


@app.get("/oblasti/{code}.json")
def oblast_json(code: str, y: str = None):
    s = Q.structure(code)
    if not s or code == "BG":
        raise HTTPException(404, "Няма такава структура")
    year = need_year(y)
    rows, _, _ = types_data(year, code)
    cols = ["row_no", "code", "text", "level", "year"] + Q.BLOCK
    return serial(dict(structure=s, year=year, series=chart_series(code), source=source_info(year, "types_by_structure"), rows=table_of(rows, cols)))


@app.get("/oblasti/{code}", response_class=HTMLResponse)
def oblast(request: Request, code: str, y: str = None, sr: str = None):
    s = Q.structure(code)
    if not s or code == "BG":
        raise HTTPException(404, "Няма такава структура")
    year = need_year(y)
    rows, inds = Q.type_rows(year, code) if s["kind"] == "oblast" else ([], [])
    total = next((d for d in Q.by_structure(year) if d["code"] == code), None)
    other_year = Q.year_or_latest(sr) if sr else None
    if other_year == year:
        other_year = None
    if other_year:
        compare_note = compare_rows(rows, Q.type_rows(other_year, code)[0], "reg")
    else:
        compare_note = None
    return render(request, "oblast.html", nav="Области", s=s, year=year, years=Q.years(), total=total, rows=rows, inds=inds, sr=other_year,
                  compare_note=compare_note, table=Q.source_table(year, "types_by_structure"))


# ---------- the map ----------

def mapdata(year, m):
    if m not in MAP_MEASURES:
        m = "per100k"
    items = Q.measure_items(year, m)
    shown = sum(1 for i in items if i["v"] is not None)
    country = Q.country_row(year)
    unit = {"per100k": "на 100 хил. души", "reg": "брой", "clearance": "%"}[m]
    digits = 1 if m == "clearance" else 0 if m == "reg" else 1
    top = max((i for i in items if i["v"] is not None), key=lambda i: i["v"], default=None)
    lede = (f"Година {year}: за България {number(country.get(m), digits) if country else 'няма данни'} {unit}"
            + (f"; най-високо е в област {top['name']} ({number(top['v'], digits)})." if top else ".")
            + f" Области с данни: {shown} от {len(items)}.")
    return dict(m=m, y=str(year), title=MAP_MEASURES[m], unit=unit, digits=digits, items=items, lede=lede,
                years=[str(x) for x in Q.years()], measures=MAP_MEASURES, csv=f"/karta.csv?y={year}&m={m}")


@app.get("/karta", response_class=HTMLResponse)
def karta(request: Request, y: str = None, m: str = "per100k"):
    year = need_year(y)
    if year is None:
        return render(request, "empty.html", nav="Карта")
    return render(request, "karta.html", nav="Карта", d=mapdata(year, m), year=year, years=Q.years(), measures=MAP_MEASURES)


@app.get("/api/karta.json")
def karta_api(y: str = None, m: str = "per100k"):
    return serial(mapdata(need_year(y), m))


@app.get("/karta.csv")
def karta_csv(y: str = None, m: str = "per100k"):
    year = need_year(y)
    d = mapdata(year, m)
    rows = [dict(code=i["code"], name=i["name"], year=year, measure=d["m"], unit=d["unit"], value=i["v"], rank=i["rank"]) for i in d["items"]]
    return csv_response(rows, ["code", "name", "year", "measure", "unit", "value", "rank"], f"sigurnost-karta-{year}-{d['m']}.csv")


# ---------- fires, sources, how ----------

@app.get("/pozhari", response_class=HTMLResponse)
def pozhari(request: Request):
    return render(request, "pozhari.html", nav="Пожари")


@app.get("/sources", response_class=HTMLResponse)
def sources(request: Request):
    with db.connect() as c:
        problems, info = checks.freshness(c)
    return render(request, "sources.html", nav="Източници", s=Q.sources(), problems=problems, info=info, checks=Q.checks())


@app.get("/sources.json")
def sources_json():
    with db.connect() as c:
        problems, info = checks.freshness(c)
    s = Q.sources()
    return serial(dict(tool="sigurnost", problems=problems, info=info,
                       datasets=[dict(id=d["set_uri"], title=d["title"], kind=d["kind"], year=d["year"], channel="api", licence=d["licence"],
                                      distributed=d["shared"], terms_of_use_id=d["terms_of_use_id"] or None, licence_checked=d["licence_checked"],
                                      resources_listed=d["listed"], resources_built=d["built"], invalid=d["invalid"], empty=d["empty"],
                                      last_read=d["last_read"]) for d in s["datasets"]],
                       tables=[dict(year=x["year"], family=x["family"], resource=x["resource_uri"], sha256=x["sha256"], template=x["template"],
                                    rows=x["n_rows"], issues=x["issues"], note=x["note"]) for x in s["tables"]],
                       fires=dict(status="gap", reason="Условията на mvr.bg не позволяват съхраняване и повторно публикуване без писмено съгласие.")))


@app.get("/how", response_class=HTMLResponse)
def how(request: Request):
    return render(request, "how.html", nav="Източници")
