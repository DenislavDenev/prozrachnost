"""First school pages, backed by a reconciled MON publication."""

import csv
import io
import os
import re
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from ingest.config import DATA
from ingest.db import connect
from ingest.dzi import DZI_DATASET
from ingest.nvo import DATASETS as NVO_DATASETS
from ingest.sources import NVO7_DATASET, SCHOOLS_DATASET
from ingest.status import DATASETS as STATUS_DATASETS
from . import feedback, municipalities


ROOT = Path(__file__).resolve().parent
EXAM_URL = f"https://data.egov.bg/data/view/{NVO7_DATASET}"
REGISTER_URL = f"https://data.egov.bg/data/view/{SCHOOLS_DATASET}"
DZI_URL = f"https://data.egov.bg/data/view/{DZI_DATASET}"
NVO_URLS = {exam: f"https://data.egov.bg/data/view/{dataset}" for exam, dataset in NVO_DATASETS.items()}
STATUS_URLS = {kind: f"https://data.egov.bg/data/view/{dataset}" for kind, dataset in STATUS_DATASETS.items()}
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


SUBJECT_NAMES = {"БЕЛ": "Български език и литература", "МАТ": "Математика", "Мат": "Математика",
    "ЧО": "Човекът и обществото", "ЧП": "Човекът и природата",
    "ИСТ": "История и цивилизации", "Ист": "История и цивилизации", "ФА": "Физика и астрономия",
    "ГИ": "География и икономика", "ГЕО": "География и икономика", "ХООС": "Химия и опазване на околната среда",
    "БЗО": "Биология и здравно образование", "Фил": "Философия", "ФИЛ": "Философия",
    "АЕ": "Английски език", "РЕ": "Руски език", "НЕ": "Немски език", "ФЕ": "Френски език",
    "ИтЕ": "Италиански език", "ИсЕ": "Испански език", "ИИ": "Изобразително изкуство",
    "МУЗ": "Музика", "ИТ": "Информационни технологии", "Инф": "Информатика"}


def subject_name(value):
    stem = re.split(r"[\s(]", value, maxsplit=1)[0]
    suffix = value[len(stem):]
    return SUBJECT_NAMES.get(stem, stem) + (" " if suffix.startswith("(") else "") + suffix


templates.env.filters["subject_name"] = subject_name


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


def school_history(code, before_year):
    """Only published and code-reconciled NVO VII years can appear in a profile."""
    years = {}
    with connect() as conn:
        for year, resource, updated, subject, takers, score, scale, matched, verification in conn.execute("""
            SELECT p.school_year,p.exam_resource,p.exam_updated_at,e.subject,e.takers,e.score,e.scale,s.matched,p.verification
            FROM live.publication p JOIN live.school s ON s.school_year=p.school_year
            JOIN live.exam_result e ON e.school_year=s.school_year AND e.neispuo=s.neispuo
            WHERE s.neispuo=%s AND p.school_year<%s ORDER BY p.school_year DESC,e.subject
        """, (code, before_year)):
            item = years.setdefault(year, dict(year=year, resource=resource, updated=updated[:10], scale=scale,
                                               matched=matched, verification=verification, subjects={}))
            if item["scale"] != scale:
                raise ValueError(f"Mixed NVO VII scales in {year}")
            item["subjects"][subject] = dict(takers=takers, score=score)
    return list(years.values())


def source_history():
    with connect() as conn:
        return [dict(year=year, exam_resource=exam, exam_updated=exam_updated[:10],
                     register_resource=register, register_updated=register_updated[:10],
                     school_count=school_count, matched_count=matched_count, verification=verification)
                for year, exam, exam_updated, register, register_updated, school_count, matched_count, verification
                in conn.execute("""SELECT school_year,exam_resource,exam_updated_at,register_resource,
                    register_updated_at,school_count,matched_count,verification
                    FROM live.publication ORDER BY school_year DESC""")]


def dzi_sources():
    with connect() as conn:
        return [dict(resource=row[0], year=row[1], session=row[2], kind=row[3],
                     updated=row[4][:10], schools=row[5], verification=row[6],
                     anomalies=row[7]) for row in conn.execute("""SELECT resource,school_year,session,kind,
                    updated_at,school_count,verification,anomalies FROM live.dzi_publication
                    ORDER BY school_year DESC,session,kind""")]


def matura_snapshot(year, session="may", kind="mandatory"):
    if not re.fullmatch(r"20\d{2}/20\d{2}", year) or session not in {"may", "august"} or kind not in {"mandatory", "optional"}:
        return None
    with connect() as conn:
        row = conn.execute("""SELECT resource,updated_at,source_rows,school_count,verification,
            result_count,matched_count,unmatched,anomalies FROM live.dzi_publication
            WHERE school_year=%s AND session=%s AND kind=%s""", (year, session, kind)).fetchone()
        if not row:
            return None
        subjects = [dict(subject=r[0], schools=r[1], takers=r[2], mean=r[3],
                         hidden=r[4], missing_grade=r[5]) for r in conn.execute("""
            SELECT subject,count(*) FILTER (WHERE score IS NOT NULL),
              sum(takers) FILTER (WHERE takers>0 AND score IS NOT NULL),
              CASE WHEN count(*) FILTER (WHERE score IS NOT NULL AND takers IS NULL)>0 THEN NULL
                   ELSE round(sum(score*takers) FILTER (WHERE takers>0 AND score IS NOT NULL)
                         / nullif(sum(takers) FILTER (WHERE takers>0 AND score IS NOT NULL),0),2) END,
              count(*) FILTER (WHERE score IS NOT NULL AND takers IS NULL),
              count(*) FILTER (WHERE score IS NULL)
            FROM live.dzi_result WHERE resource=%s AND is_school
            GROUP BY subject ORDER BY sum(takers) FILTER (WHERE takers>0) DESC NULLS LAST,subject
        """, (row[0],))]
    return dict(year=year, session=session, kind=kind, resource=row[0], updated=row[1][:10],
                source_rows=row[2], schools=row[3], verification=row[4], results=row[5],
                matched=row[6], unmatched=row[7], anomalies=row[8], subjects=subjects)


def school_dzi(code):
    groups = {}
    with connect() as conn:
        for year, session, kind, resource, updated, verification, subject, takers, score, matched in conn.execute("""
            SELECT p.school_year,p.session,p.kind,p.resource,p.updated_at,p.verification,
                   r.subject,r.takers,r.score,r.matched
            FROM live.dzi_result r JOIN live.dzi_publication p ON p.resource=r.resource
            WHERE r.neispuo=%s AND r.is_school ORDER BY p.school_year DESC,p.session,p.kind,r.subject
        """, (code,)):
            item = groups.setdefault(resource, dict(year=year, session=session, kind=kind,
                resource=resource, updated=updated[:10], verification=verification, matched=matched, subjects=[]))
            item["subjects"].append(dict(subject=subject, takers=takers, score=score))
    return list(groups.values())


def nvo_sources():
    with connect() as conn:
        return [dict(resource=r[0], exam=r[1], year=r[2], updated=r[3][:10],
                     schools=r[4], subjects=r[5], verification=r[6], matched=r[7])
                for r in conn.execute("""SELECT resource,exam,school_year,updated_at,school_count,
                    subject_count,verification,matched_count FROM live.nvo_publication
                    ORDER BY school_year DESC,exam""")]


def nvo_snapshot(exam, year):
    if exam not in NVO_DATASETS or not re.fullmatch(r"20\d{2}/20\d{2}", year):
        return None
    with connect() as conn:
        row = conn.execute("""SELECT resource,updated_at,school_count,subject_count,result_count,
            verification,matched_count,unmatched FROM live.nvo_publication
            WHERE exam=%s AND school_year=%s""", (exam, year)).fetchone()
        if not row:
            return None
        subjects = [dict(subject=r[0], schools=r[1], takers=r[2], mean=r[3])
                    for r in conn.execute("""SELECT subject,count(*),
                        sum(takers) FILTER (WHERE takers>0 AND score IS NOT NULL),
                        round(sum(score*takers) FILTER (WHERE takers>0 AND score IS NOT NULL)
                            / nullif(sum(takers) FILTER (WHERE takers>0 AND score IS NOT NULL),0),2)
                        FROM live.nvo_result WHERE resource=%s GROUP BY subject
                        ORDER BY subject""", (row[0],))]
    return dict(exam=exam, year=year, resource=row[0], updated=row[1][:10],
                schools=row[2], subject_count=row[3], results=row[4], verification=row[5],
                matched=row[6], unmatched=row[7], subjects=subjects)


def school_nvo(code):
    groups = {}
    with connect() as conn:
        for exam, year, resource, updated, verification, matched, subject, takers, score in conn.execute("""
            SELECT p.exam,p.school_year,p.resource,p.updated_at,p.verification,r.matched,
                   r.subject,r.takers,r.score FROM live.nvo_result r
            JOIN live.nvo_publication p ON p.resource=r.resource WHERE r.neispuo=%s
            ORDER BY p.exam,p.school_year DESC,r.subject""", (code,)):
            item = groups.setdefault(resource, dict(exam=exam, year=year, resource=resource,
                updated=updated[:10], verification=verification, matched=matched, subjects=[]))
            item["subjects"].append(dict(subject=subject, takers=takers, score=score))
    return list(groups.values())


def school_status(code):
    with connect() as conn:
        return [dict(kind=r[0], year=r[1], resource=r[2], updated=r[3][:10],
                     name=r[4], town=r[5], scope=r[6]) for r in conn.execute("""
            SELECT p.kind,p.school_year,p.resource,p.updated_at,r.name,r.town,r.scope
            FROM live.status_row r JOIN live.status_publication p ON p.resource=r.resource
            WHERE r.neispuo=%s AND r.is_school
            ORDER BY p.school_year DESC,p.kind,r.row_number""", (code,))]


def status_sources():
    with connect() as conn:
        return [dict(kind=r[0], year=r[1], resource=r[2], updated=r[3][:10],
                     rows=r[4], school_rows=r[5]) for r in conn.execute("""
            SELECT kind,school_year,resource,updated_at,row_count,school_rows
            FROM live.status_publication ORDER BY school_year DESC,kind""")]


def school_identity(code):
    with connect() as conn:
        row = conn.execute("""SELECT r.school,r.oblast,r.municipality,r.town,r.matched
            FROM live.nvo_result r JOIN live.nvo_publication p ON p.resource=r.resource
            WHERE r.neispuo=%s ORDER BY p.school_year DESC LIMIT 1""", (code,)).fetchone()
        if not row:
            row = conn.execute("""SELECT r.school,r.oblast,r.municipality,r.town,r.matched
                FROM live.dzi_result r JOIN live.dzi_publication p ON p.resource=r.resource
                WHERE r.neispuo=%s AND r.is_school ORDER BY p.school_year DESC LIMIT 1""",
                (code,)).fetchone()
    return dict(code=code, name=row[0], oblast=row[1], municipality=row[2], town=row[3],
                matched=row[4], subjects={}) if row else None


def nvo_schools(resource, subject="", q="", page=1, limit=50, sort="name"):
    q = q.strip()[:120]
    order = {"name": "lower(school),neispuo,subject",
             "takers": "takers DESC NULLS LAST,lower(school),neispuo,subject",
             "score": "CASE WHEN takers>=5 THEN score END DESC NULLS LAST,lower(school),neispuo,subject"}.get(sort)
    if order is None:
        raise HTTPException(400, "Непозната подредба")
    where = """resource=%s AND (%s='' OR subject=%s) AND
        (%s='' OR school ILIKE '%%'||%s||'%%' OR neispuo=%s OR town ILIKE '%%'||%s||'%%')"""
    params = [resource, subject, subject, q, q, q, q]
    with connect() as conn:
        count = conn.execute("SELECT count(*) FROM live.nvo_result WHERE " + where, params).fetchone()[0]
        rows = [dict(code=r[0], school=r[1], town=r[2], subject=r[3], takers=r[4],
                     score=r[5], matched=r[6]) for r in conn.execute("""
            SELECT neispuo,school,town,subject,takers,score,matched FROM live.nvo_result
            WHERE """ + where + " ORDER BY " + order + " LIMIT %s OFFSET %s",
            params + [limit, (page-1)*limit])]
    return count, rows


def matura_schools(resource, subject="", q="", page=1, limit=50, sort="name"):
    """School results for one publication; score order excludes small groups."""
    q = q.strip()[:120]
    order = {"name": "lower(school),row_number,subject",
             "takers": "takers DESC NULLS LAST,lower(school),row_number,subject",
             "score": "CASE WHEN takers>=5 THEN score END DESC NULLS LAST,lower(school),row_number,subject"}.get(sort)
    if order is None:
        raise HTTPException(400, "Непозната подредба")
    params = [resource, subject, subject, q, q, q, q]
    where = """resource=%s AND is_school AND (%s='' OR subject=%s)
        AND (%s='' OR school ILIKE '%%'||%s||'%%' OR neispuo=%s OR town ILIKE '%%'||%s||'%%')"""
    with connect() as conn:
        count = conn.execute("SELECT count(*) FROM live.dzi_result WHERE " + where, params).fetchone()[0]
        rows = [dict(code=r[0], school=r[1], town=r[2], subject=r[3], takers=r[4],
                     score=r[5], matched=r[6]) for r in conn.execute("""
            SELECT neispuo,school,town,subject,takers,score,matched
            FROM live.dzi_result WHERE """ + where + """
            ORDER BY """ + order + " LIMIT %s OFFSET %s",
            params + [limit, (page-1)*limit])]
    return count, rows


def render(request, page, data=None, **context):
    hub = os.environ.get("HUB_URL", "/")
    return templates.TemplateResponse(request=request, name=page, context=dict(
        hub_url=hub, nav=context.pop("nav", ""), d=data,
        feedback_button=Markup(feedback.BUTTON),
        support_link=Markup(feedback.support_link(hub)),
        exam_url=EXAM_URL, register_url=REGISTER_URL, dzi_url=DZI_URL,
        nvo_urls=NVO_URLS, status_urls=STATUS_URLS, **context))


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
    places, unmapped = municipalities.scores(data)
    sources = nvo_sources()
    years = {exam: max((item["year"] for item in sources if item["exam"] == exam), default=None)
             for exam in NVO_DATASETS}
    return render(request, "home.html", data, nav="Табло", scores=scores,
                  places=places, unmapped=unmapped, nvo_years=years)


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
        school = school_identity(code)
        if school is None:
            raise HTTPException(404, "Няма такова училище")
        comparison = []
        history = school_history(code, "9999/9999")
    else:
        municipality = [r for r in data["schools"] if r["municipality"] == school["municipality"] and r["oblast"] == school["oblast"]]
        oblast = [r for r in data["schools"] if r["oblast"] == school["oblast"]]
        comparison = [("Училището", school["subjects"]["БЕЛ"]["score"], school["subjects"]["МАТ"]["score"]),
                      ("Общината", weighted(municipality, "БЕЛ"), weighted(municipality, "МАТ")),
                      ("Областта", weighted(oblast, "БЕЛ"), weighted(oblast, "МАТ")),
                      ("Страната, по училищния файл", weighted(data["schools"], "БЕЛ"), weighted(data["schools"], "МАТ"))]
        history = school_history(code, data["year"])
    return render(request, "school.html", data, nav="Училища", school=school, comparison=comparison,
                  history=history, matura=school_dzi(code), nvo=school_nvo(code),
                  statuses=school_status(code))


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
    return render(request, "sources.html", snapshot(), nav="Източници", publications=source_history(),
                  dzi_publications=dzi_sources(), nvo_publications=nvo_sources(),
                  status_publications=status_sources())


@app.get("/matura", response_class=HTMLResponse)
def matura_latest():
    years = sorted({item["year"] for item in dzi_sources()}, reverse=True)
    return RedirectResponse("/matura/" + years[0].replace("/", "-") if years else "/matura/none", status_code=307)


@app.get("/matura/{year}", response_class=HTMLResponse)
def matura_year(request: Request, year: str, session: str = "may", kind: str = "mandatory"):
    publications = dzi_sources()
    years = sorted({item["year"] for item in publications}, reverse=True)
    academic = year.replace("-", "/")
    if academic not in years and years:
        raise HTTPException(404, "Няма публикувани матури за тази година")
    data = matura_snapshot(academic, session, kind) if academic in years else None
    available = [item for item in publications if item["year"] == academic]
    return render(request, "matura.html", snapshot(), nav="Матури", exam=data,
                  years=years, available=available, selected_year=academic,
                  selected_session=session, selected_kind=kind)


def selected_matura(year, session, kind, subject):
    academic = year.replace("-", "/")
    exam = matura_snapshot(academic, session, kind)
    if not exam or (subject and subject not in {item["subject"] for item in exam["subjects"]}):
        raise HTTPException(404, "Няма публикувани резултати за този избор")
    return exam


@app.get("/matura/{year}/uchilishta", response_class=HTMLResponse)
def matura_school_list(request: Request, year: str, session: str = "may", kind: str = "mandatory",
                       subject: str = "", q: str = "", page: int = 1, sort: str = "name"):
    exam = selected_matura(year, session, kind, subject)
    page = max(1, page)
    count, rows = matura_schools(exam["resource"], subject, q, page, sort=sort)
    return render(request, "matura_schools.html", snapshot(), nav="Матури", exam=exam,
                  rows=rows, count=count, subject=subject, q=q.strip()[:120], page_number=page, sort=sort)


@app.get("/matura/{year}/uchilishta.csv")
def matura_school_csv(year: str, session: str = "may", kind: str = "mandatory",
                      subject: str = "", q: str = "", sort: str = "name"):
    exam = selected_matura(year, session, kind, subject)
    _, rows = matura_schools(exam["resource"], subject, q, limit=100000, sort=sort)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Учебна година", "Сесия", "Вид", "Код по НЕИСПУО", "Училище", "Населено място",
                     "Предмет и вариант", "Явили се", "Средна оценка", "Сверено с регистъра", "Ресурс на МОН"])
    for row in rows:
        writer.writerow([exam["year"], exam["session"], exam["kind"], row["code"], row["school"],
                         row["town"], row["subject"], row["takers"], row["score"], row["matched"], exam["resource"]])
    return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="matura-uchilishta.csv"'})


def nvo_exam(slug):
    exam = {"4": "nvo4", "10": "nvo10"}.get(slug)
    if exam is None:
        raise HTTPException(404, "Няма такъв изпит")
    return exam


@app.get("/nvo", response_class=HTMLResponse)
def nvo_latest():
    sources = nvo_sources()
    years = sorted((item["year"] for item in sources if item["exam"] == "nvo4"), reverse=True)
    return RedirectResponse("/nvo/4/" + years[0].replace("/", "-") if years else "/nvo/4/none", status_code=307)


@app.get("/nvo/{grade}/{year}", response_class=HTMLResponse)
def nvo_year(request: Request, grade: str, year: str):
    exam = nvo_exam(grade)
    sources = nvo_sources()
    years = sorted({item["year"] for item in sources if item["exam"] == exam}, reverse=True)
    academic = year.replace("-", "/")
    if years and academic not in years:
        raise HTTPException(404, "Няма публикувано НВО за тази година")
    data = nvo_snapshot(exam, academic) if academic in years else None
    return render(request, "nvo.html", snapshot(), nav="НВО IV и X", exam=data,
                  grade=grade, years=years, selected_year=academic)


def selected_nvo(grade, year, subject):
    exam = nvo_snapshot(nvo_exam(grade), year.replace("-", "/"))
    if not exam or (subject and subject not in {item["subject"] for item in exam["subjects"]}):
        raise HTTPException(404, "Няма публикувани резултати за този избор")
    return exam


@app.get("/nvo/{grade}/{year}/uchilishta", response_class=HTMLResponse)
def nvo_school_list(request: Request, grade: str, year: str, subject: str = "", q: str = "",
                    page: int = 1, sort: str = "name"):
    exam = selected_nvo(grade, year, subject)
    page = max(1, page)
    count, rows = nvo_schools(exam["resource"], subject, q, page, sort=sort)
    return render(request, "nvo_schools.html", snapshot(), nav="НВО IV и X", exam=exam,
                  grade=grade, rows=rows, count=count, subject=subject, q=q.strip()[:120],
                  page_number=page, sort=sort)


@app.get("/nvo/{grade}/{year}/uchilishta.csv")
def nvo_school_csv(grade: str, year: str, subject: str = "", q: str = "", sort: str = "name"):
    exam = selected_nvo(grade, year, subject)
    _, rows = nvo_schools(exam["resource"], subject, q, limit=100000, sort=sort)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Изпит", "Учебна година", "Код по НЕИСПУО", "Училище", "Населено място",
                     "Предмет", "Явили се", "Среден резултат в точки", "Сверено с регистъра", "Ресурс на МОН"])
    for row in rows:
        writer.writerow(["НВО " + grade, exam["year"], row["code"], row["school"], row["town"],
                         row["subject"], row["takers"], row["score"], row["matched"], exam["resource"]])
    return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="nvo-uchilishta.csv"'})


@app.get("/how", response_class=HTMLResponse)
def how(request: Request):
    return render(request, "how.html", snapshot())
