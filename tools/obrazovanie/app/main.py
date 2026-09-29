"""First school pages, backed by a reconciled MON publication."""

import csv
import io
import os
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from ingest.config import DATA
from ingest.db import connect
from ingest.sources import NVO7_DATASET, SCHOOLS_DATASET
from . import feedback


ROOT = Path(__file__).resolve().parent
EXAM_URL = f"https://data.egov.bg/data/view/{NVO7_DATASET}"
REGISTER_URL = f"https://data.egov.bg/data/view/{SCHOOLS_DATASET}"
app = FastAPI(title="Образование", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.include_router(feedback.router("DenislavDenev/prozrachnost",
    Path(os.environ.get("OBRAZOVANIE_FEEDBACK", str(DATA / "feedback"))), "Образование"))
templates = Jinja2Templates(directory=ROOT / "templates")


def fmt(value, digits=0):
    if value is None:
        return "няма данни"
    return f"{value:,.{digits}f}".replace(",", " ")


templates.env.filters["num"] = fmt


def snapshot():
    """Read one published year; unpublished or held data never reaches pages."""
    with connect() as conn:
        publication = conn.execute("""SELECT school_year,exam_resource,register_resource,exam_updated_at,
            register_updated_at,school_count,matched_count,unmatched,published_at
            FROM live.publication ORDER BY school_year DESC LIMIT 1""").fetchone()
        if not publication:
            return None
        year = publication[0]
        schools = {}
        for row in conn.execute("""SELECT s.neispuo,s.name,s.oblast,s.municipality,s.town,s.matched,
            e.subject,e.takers,e.score,e.scale FROM live.school s JOIN live.exam_result e
            ON s.school_year=e.school_year AND s.neispuo=e.neispuo
            WHERE s.school_year=%s ORDER BY s.name,e.subject""", (year,)):
            item = schools.setdefault(row[0], dict(code=row[0], name=row[1], oblast=row[2],
                municipality=row[3], town=row[4], matched=row[5], subjects={}))
            item["subjects"][row[6]] = dict(takers=row[7], score=row[8], scale=row[9])
    return dict(year=year, exam_resource=publication[1], register_resource=publication[2],
        exam_updated=publication[3][:10], register_updated=publication[4][:10],
        school_count=publication[5], matched=publication[6], unmatched=publication[7],
        published_at=publication[8], schools=sorted(schools.values(), key=lambda x: x["name"].casefold()))


def weighted(rows, subject):
    pairs = [(r["subjects"][subject]["score"], r["subjects"][subject]["takers"])
             for r in rows if subject in r["subjects"]]
    pairs = [(score, takers) for score, takers in pairs if score is not None and takers is not None and takers > 0]
    total = sum(takers for _, takers in pairs)
    return (sum(score * takers for score, takers in pairs) / total).quantize(Decimal("0.01")) if total else None


def render(request, page, data=None, **context):
    hub = os.environ.get("HUB_URL", "/")
    return templates.TemplateResponse(request=request, name=page, context=dict(
        hub_url=hub, nav=context.pop("nav", ""), d=data,
        feedback_button=Markup(feedback.BUTTON),
        support_link=Markup(feedback.support_link(hub)),
        exam_url=EXAM_URL, register_url=REGISTER_URL, **context))


@app.get("/healthz")
def health():
    with connect() as conn:
        conn.execute("SELECT 1")
    return {"ok": True}


@app.get("/favicon.svg")
def favicon():
    return Response((ROOT / "static/favicon.svg").read_text(encoding="utf-8"), media_type="image/svg+xml")


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    data = snapshot()
    scores = {subject: weighted(data["schools"], subject) for subject in ("БЕЛ", "МАТ")} if data else {}
    return render(request, "home.html", data, nav="Табло", scores=scores)


def selected(data, q="", oblast="", municipality=""):
    if not data:
        return []
    query = q.strip().casefold()
    return [row for row in data["schools"] if
        (not query or query in f'{row["name"]} {row["code"]} {row["town"]}'.casefold())
        and (not oblast or row["oblast"] == oblast)
        and (not municipality or row["municipality"] == municipality)]


@app.get("/uchilishta", response_class=HTMLResponse)
def school_list(request: Request, q: str = "", oblast: str = "", municipality: str = ""):
    data = snapshot()
    rows = selected(data, q, oblast, municipality)
    oblasts = sorted({r["oblast"] for r in data["schools"]}) if data else []
    municipalities = sorted({r["municipality"] for r in data["schools"] if not oblast or r["oblast"] == oblast}) if data else []
    return render(request, "list.html", data, nav="Училища", rows=rows, q=q, oblast=oblast,
                  municipality=municipality, oblasts=oblasts, municipalities=municipalities)


@app.get("/uchilishta/{code}", response_class=HTMLResponse)
def school_detail(request: Request, code: str):
    data = snapshot()
    school = next((row for row in data["schools"] if row["code"] == code), None) if data else None
    if school is None:
        raise HTTPException(404, "Няма такова училище")
    municipality = [r for r in data["schools"] if r["municipality"] == school["municipality"] and r["oblast"] == school["oblast"]]
    oblast = [r for r in data["schools"] if r["oblast"] == school["oblast"]]
    comparison = [("Училището", school["subjects"]["БЕЛ"]["score"], school["subjects"]["МАТ"]["score"]),
                  ("Общината", weighted(municipality, "БЕЛ"), weighted(municipality, "МАТ")),
                  ("Областта", weighted(oblast, "БЕЛ"), weighted(oblast, "МАТ")),
                  ("Страната, по училищния файл", weighted(data["schools"], "БЕЛ"), weighted(data["schools"], "МАТ"))]
    return render(request, "school.html", data, nav="Училища", school=school, comparison=comparison)


@app.get("/uchilishta.csv")
def school_csv(q: str = "", oblast: str = "", municipality: str = ""):
    data = snapshot()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Учебна година", "Код по НЕИСПУО", "Училище", "Област", "Община", "Населено място",
                     "БЕЛ явили се", "БЕЛ точки", "МАТ явили се", "МАТ точки", "Съпоставено с регистъра", "Ресурс на МОН"])
    for row in selected(data, q, oblast, municipality):
        bel, math = row["subjects"].get("БЕЛ", {}), row["subjects"].get("МАТ", {})
        writer.writerow([data["year"], row["code"], row["name"], row["oblast"], row["municipality"], row["town"],
                         bel.get("takers"), bel.get("score"), math.get("takers"), math.get("score"), row["matched"], data["exam_resource"]])
    return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="uchilishta.csv"'})


@app.get("/sources", response_class=HTMLResponse)
def sources(request: Request):
    return render(request, "sources.html", snapshot(), nav="Източници")


@app.get("/how", response_class=HTMLResponse)
def how(request: Request):
    return render(request, "how.html", snapshot())
