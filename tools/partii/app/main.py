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

from ingest import config
from . import feedback, help as H, queries as Q

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Партии и пари", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.include_router(feedback.router("DenislavDenev/prozrachnost", Path(os.environ.get("PARTII_FEEDBACK", str(config.DATA / "feedback"))), "Партии и пари"))
t = Jinja2Templates(directory=ROOT / "templates")
MENU = [("Табло", "/"), ("Избори", "/izbori"), ("Участници", "/uchastnici"), ("Агенции", "/agencii"), ("Източници", "/sources")]
TENDER = os.environ.get("TENDER_URL", "https://tender.denev.work")
# the version in the address of every static file: the newest of them, so a deploy that changes one is not served from the browser cache
STATIC_V = str(int(max(p.stat().st_mtime for p in (ROOT / "static").glob("*.*"))))


@app.middleware("http")
async def publication(request, call_next):
    """Every answer says which publication of the serving layer it shows, so a cache can keep it until the next one."""
    response = await call_next(request)
    if request.url.path.startswith("/static"):
        return response
    try:
        v = Q.version()
    except Exception:
        v = None
    if v:
        response.headers["X-Publication"] = str(v["publication_id"])
        response.headers["ETag"] = f'W/"pub-{v["publication_id"]}"'
        response.headers["Last-Modified"] = v["published_at"].astimezone(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    return response


def number(v, d=2):
    return "няма данни" if v is None else f"{Decimal(str(v)):,.{d}f}".replace(",", " ").replace(".", ",")


def eur(v, d=2):
    """Money for the text and the tables: from 1 000 million up it is written in billions (AGENTS.md 7)."""
    if v is None:
        return "няма данни"
    v = Decimal(str(v))
    if abs(v) >= 1_000_000_000:
        return number(v / 1_000_000_000, 2) + " млрд. €"
    return number(v, d) + " €"


def money(v, currency):
    return "няма данни" if v is None else number(v) + (" лв." if currency == "BGN" else " €" if currency == "EUR" else "")


def date(v):
    if not v:
        return "няма данни"
    s = str(v)
    return s[8:10] + "." + s[5:7] + "." + s[:4]


def plural(n, one, many):
    return one if n == 1 else many


t.env.filters.update(num=number, eur=eur, money=money, date=date, plural=plural)
t.env.globals.update(KIND=Q.KIND_BG, REG=Q.REG_BG, ELECTION=Q.ELECTION_BG, DECL=Q.DECL_BG, ERIK=Q.ERIK, TENDER=TENDER)


def render(request, name, **context):
    hub = os.environ.get("HUB_URL", "https://prozrachnost.denev.work")
    helps = dict(H.HELP, rows=context.pop("helps", []))      # the "?" of the rows of a report are made for the page
    return t.TemplateResponse(request=request, name=name, context=dict(
        v=STATIC_V, hub_url=hub, feedback_button=Markup(feedback.BUTTON), support_link=Markup(feedback.support_link(hub)), menu=MENU,
        fresh=Q.latest(), help_json=Markup(json.dumps(helps, ensure_ascii=False).replace("</", "<\\/")), **context))


def plain(v):
    """A value for CSV and JSON: a sum keeps the digits of the source, a date is ISO."""
    if isinstance(v, Decimal):
        return format(v.normalize(), "f") if v else "0"
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


def export(kind, rows, headers, name, meta=None):
    """The same rows as CSV or as JSON: one function, so the two never differ in scope."""
    rows = [{h: plain(r.get(h)) for h in headers} for r in rows]
    if kind == "json":
        return JSONResponse(dict(meta or {}, columns=headers, rows=rows))
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(headers)
    for r in rows:
        w.writerow(["" if r[h] is None else r[h] for h in headers])
    return Response(("﻿" + out.getvalue()).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


def meta(e=None, as_of=None, **more):
    """What every export says about itself: the source, the unit, the scope and the moment."""
    out = dict(source="ЕРИК, Сметна палата", source_url=Q.ERIK + ("/Reports?electionId=%d" % e["erik_id"] if e else "/"), unit="EUR",
               election=e and dict(erik_id=e["erik_id"], title=e["title"], election_day=plain(e["election_day"])),
               as_of=as_of or "последното четене", read_at=plain(e and e.get("donations_read")))
    out.update(more)
    return out


def kind_of(ext):
    if ext not in ("csv", "json"):
        raise HTTPException(404)
    return ext


def the_election(e, d=None):
    eid = e or Q.default_election()
    el = Q.election(eid, d) if eid else None
    if not el:
        raise HTTPException(404, "Няма такъв избор")
    return el


def parse_key(key):
    try:
        eid, reg, sid = (int(x) for x in key.split("-"))
    except ValueError:
        raise HTTPException(404, "Няма такъв участник") from None
    return eid, reg, sid


def chart(series, unit):
    return dict(unit=unit, series=[dict(s, points=[[str(p[0]), None if p[1] is None else float(p[1])] for p in s["points"]]) for s in series])


def bars(rows, value, top=15):
    """Rows for the horizontal bars: the width is the share of the largest value."""
    rows = [r for r in rows if r[value]][:top]
    most = max((r[value] for r in rows), default=0)
    return [dict(r, w=round(float(r[value] / most) * 100, 2) if most else 0) for r in rows]


@app.get("/healthz")
def health():
    v = Q.version()
    return {"ok": True, "publication": v and v["publication_id"]}


@app.get("/favicon.svg")
def favicon():
    return Response((ROOT / "static/favicon.svg").read_text(), media_type="image/svg+xml")


# ---------- the dashboard ----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request, e: int = None, d: str = None):
    if not Q.default_election():
        return render(request, "empty.html", nav="Табло")
    el = the_election(e, d)
    rec = Q.recipients(el["erik_id"], d)
    pts, undated = Q.by_day(el["erik_id"], d)
    return render(request, "home.html", nav="Табло", el=el, d=d, elections=Q.elections(d), moments=Q.moments(el["erik_id"]), rec=rec, top=bars(rec, "money"),
                  undated=undated, days=len(pts), check=Q.income_check(el["erik_id"], d))


@app.get("/api/dni.json")
def days_api(e: int = None, d: str = None, p: str = None):
    el = the_election(e, d)
    pts, _ = Q.by_day(el["erik_id"], d, parse_key(p)[1:] if p else None)
    money = in_kind = Decimal(0)
    a, b = [], []
    for day, m, v in pts:
        money, in_kind = money + m, in_kind + v
        a.append((day, money))
        b.append((day, in_kind))
    series = [dict(name="Парични дарения, натрупано, €", geo="BG", points=a)]
    if in_kind:
        series.append(dict(name="Непарични дарения, натрупано, €", geo="X", points=b, color="#121417"))
    return JSONResponse(chart(series, "€"))


ELECTION_COLS = ["erik_id", "title", "kind", "election_day", "donations", "money_eur", "in_kind_eur", "recipients", "candidate_rows", "candidate_money_eur",
                 "candidate_in_kind_eur", "agency_rows", "participants", "threshold_eur", "donations_read", "gone_at"]


@app.get("/export-izbori.{ext}")
def elections_export(ext: str, d: str = None):
    rows = [dict(r, money_eur=r["money"], in_kind_eur=r["in_kind"], candidate_money_eur=r["candidate_money"], candidate_in_kind_eur=r["candidate_in_kind"],
                 threshold_eur=r["threshold_shown"]) for r in Q.elections(d)]
    return export(kind_of(ext), rows, ELECTION_COLS, "partii-izbori", meta(as_of=d))


# ---------- an election ----------

@app.get("/izbori", response_class=HTMLResponse)
def elections_page(request: Request, d: str = None):
    return render(request, "elections.html", nav="Избори", elections=Q.elections(d), d=d)


@app.get("/izbori/{eid}", response_class=HTMLResponse)
def election_page(request: Request, eid: int, d: str = None, a: str = None, b: str = None):
    el = the_election(eid, d)
    moments = Q.moments(eid)
    days = [str(m["applied_at"])[:10] for m in moments]
    cmp_rows = None
    if len(days) > 1:
        a, b = a if a in days else days[0], b if b in days else days[-1]
        cmp_rows = Q.compare(eid, a, b)
    rec = Q.recipients(eid, d)
    return render(request, "election.html", nav="Избори", el=el, d=d, moments=moments, days=days, rec=rec, top=bars(rec, "money"),
                  donations=Q.donations(eid, d), funds=Q.candidate_funds(eid, d), agencies=Q.agency_rows(eid, d), reports=Q.reports(eid, d),
                  check=Q.income_check(eid, d), a=a, b=b, cmp=cmp_rows)


RECIPIENT_COLS = ["erik_id", "participant", "name", "kind", "donations", "money_eur", "in_kind_eur", "over_threshold", "over_threshold_no_declaration", "first_day", "last_day"]
DONATION_COLS = ["id", "erik_id", "participant", "recipient", "recipient_kind", "donation_day", "kind", "money_eur", "in_kind_eur", "declaration", "over_threshold", "donor"]
FUND_COLS = ["erik_id", "participant", "participant_label", "role", "rows", "money_eur", "in_kind_eur", "over_threshold_no_declaration"]
AGENCY_COLS = ["erik_id", "election", "participant", "participant_label", "agency_name", "agency_eik", "eik_valid", "agency_kind", "description", "rows"]
REPORT_COLS = ["erik_id", "layout", "participant", "participant_label", "currency", "income", "expense", "in_kind", "total", "income_eur", "expense_eur", "in_kind_eur",
               "total_eur", "filled_cells"]
LINE_COLS = ["erik_id", "layout", "participant_label", "col", "code", "label", "section", "depth", "parent", "amount_raw", "amount", "currency", "amount_eur"]
COMPARE_COLS = ["erik_id", "participant", "name", "kind", "a", "b", "donations_a", "money_a_eur", "in_kind_a_eur", "donations_b", "money_b_eur", "in_kind_b_eur",
                "money_diff_eur", "in_kind_diff_eur"]


def donor_text(r):
    return r["donor_name"] if r["donor_kind"] == "legal" else "физическо лице (едноличен търговец)" if r["donor_kind"] == "sole_trader" else "физическо лице"


def donation_rows(eid, d=None, p=None):
    return [dict(r, id=f"{r['erik_id']}-{r['row_key']}-{r['ordinal']}", participant=Q.pkey(r), money_eur=r["money"], in_kind_eur=r["in_kind"],
                 declaration=r["decl"], donor=donor_text(r)) for r in Q.donations(eid, d, parse_key(p)[1:] if p else None)]


@app.get("/export-dareniya.{ext}")
def donations_export(ext: str, e: int = None, d: str = None, p: str = None):
    el = the_election(e, d)
    return export(kind_of(ext), donation_rows(el["erik_id"], d, p), DONATION_COLS, f"partii-dareniya-{el['erik_id']}", meta(el, d, participant=p,
                  note="Дарителите са физически лица и не се назовават. Парични и непарични са отделни колони."))


@app.get("/export-poluchateli.{ext}")
def recipients_export(ext: str, e: int = None, d: str = None):
    el = the_election(e, d)
    rows = [dict(r, participant=Q.pkey(r), money_eur=r["money"], in_kind_eur=r["in_kind"], over_threshold_no_declaration=r["over_none"]) for r in Q.recipients(el["erik_id"], d)]
    return export(kind_of(ext), rows, RECIPIENT_COLS, f"partii-poluchateli-{el['erik_id']}", meta(el, d))


@app.get("/export-kandidati.{ext}")
def funds_export(ext: str, e: int = None, d: str = None):
    el = the_election(e, d)
    rows = [dict(r, participant=Q.pkey(r) if r["source_id"] is not None else None, rows=r["n"], money_eur=r["money"], in_kind_eur=r["in_kind"],
                 over_threshold_no_declaration=r["over_none"]) for r in Q.candidate_funds(el["erik_id"], d)]
    return export(kind_of(ext), rows, FUND_COLS, f"partii-kandidati-{el['erik_id']}", meta(el, d))


def agency_out(rows):
    return [dict(r, election=r["title"], participant=Q.pkey(r) if r["source_id"] is not None else None, description=r["descr"], rows=r["n"]) for r in rows]


@app.get("/export-agencii.{ext}")
def agencies_export(ext: str, e: int = None, d: str = None, eik: str = None):
    el = the_election(e, d) if e else None
    return export(kind_of(ext), agency_out(Q.agency_rows(e, d, eik=eik)), AGENCY_COLS, "partii-agencii" + (f"-{e}" if e else "") + (f"-{eik}" if eik else ""),
                  meta(el, d, unit=None, agency_eik=eik))


def report_out(rows):
    return [dict(r, participant=Q.pkey(r) if r["source_id"] is not None else None, filled_cells=r["filled"]) for r in rows]


@app.get("/export-otcheti.{ext}")
def reports_export(ext: str, e: int = None, d: str = None):
    el = the_election(e, d)
    return export(kind_of(ext), [dict(r, erik_id=el["erik_id"]) for r in report_out(Q.reports(el["erik_id"], d))], REPORT_COLS, f"partii-otcheti-{el['erik_id']}",
                  meta(el, d, unit="както е подаден отчетът (currency), и EUR", currency_evidence=el["report_currency_evidence"]))


@app.get("/export-otchet.{ext}")
def report_export(ext: str, e: int, l: str, n: int, d: str = None):
    el = the_election(e, d)
    rows = [dict(r, erik_id=e, layout=l) for r in Q.report_lines(e, l, n=n, as_of=d)]
    if not rows:
        raise HTTPException(404, "Няма такъв отчет")
    return export(kind_of(ext), rows, LINE_COLS, f"partii-otchet-{e}-{l}-{n}", meta(el, d, unit="както е подаден отчетът (currency), и EUR"))


@app.get("/export-sravnenie.{ext}")
def compare_export(ext: str, e: int, a: str, b: str):
    el = the_election(e)
    rows = [dict(r, erik_id=e, participant=r["key"], a=a, b=b, money_a_eur=r["money_a"], in_kind_a_eur=r["in_kind_a"], money_b_eur=r["money_b"],
                 in_kind_b_eur=r["in_kind_b"], money_diff_eur=r["money_diff"], in_kind_diff_eur=r["in_kind_diff"]) for r in Q.compare(e, a, b)]
    return export(kind_of(ext), rows, COMPARE_COLS, f"partii-sravnenie-{e}", meta(el, None, a=a, b=b))


@app.get("/izbori/{eid}/otchet/{layout}/{n}", response_class=HTMLResponse)
def report_page(request: Request, eid: int, layout: str, n: int, d: str = None):
    el = the_election(eid, d)
    lines = Q.report_lines(eid, layout, n=n, as_of=d)
    if not lines:
        raise HTTPException(404, "Няма такъв отчет")
    return render(request, "report.html", nav="Избори", el=el, d=d, layout=layout, n=n, lines=lines, label=lines[0]["participant_label"],
                  helps=H.report_help(lines))


# ---------- participants ----------

PARTICIPANT_COLS = ["erik_id", "election", "election_day", "participant", "name", "kind", "registered_in", "municipality", "settlement", "donations", "donated_money_eur",
                    "donated_in_kind_eur", "over_threshold_no_declaration", "candidate_rows", "candidate_money_eur", "candidate_in_kind_eur", "agencies",
                    "report_income", "report_expense", "report_currency", "report_income_eur", "report_expense_eur"]


def participant_out(rows):
    return [dict(r, election=r["title"], participant=Q.pkey(r), registered_in=Q.REG_BG[r["reg_in"]], donated_money_eur=r["donated_money"],
                 donated_in_kind_eur=r["donated_in_kind"], over_threshold_no_declaration=r["over_threshold_no_decl"], candidate_money_eur=r["candidate_money"],
                 candidate_in_kind_eur=r["candidate_in_kind"]) for r in rows]


@app.get("/uchastnici", response_class=HTMLResponse)
def participants_page(request: Request, q: str = "", e: int = None):
    rows = Q.participants(q.strip() or None, e, limit=500)
    return render(request, "participants.html", nav="Участници", rows=rows, q=q, e=e, elections=Q.elections())


@app.get("/export-uchastnici.{ext}")
def participants_export(ext: str, q: str = "", e: int = None):
    return export(kind_of(ext), participant_out(Q.participants(q.strip() or None, e, limit=100000)), PARTICIPANT_COLS, "partii-uchastnici", meta(search=q or None, erik_id=e))


@app.get("/uchastnici/{key}", response_class=HTMLResponse)
def participant_page(request: Request, key: str):
    eid, reg, sid = parse_key(key)
    p = Q.participant(eid, reg, sid)
    if not p:
        raise HTTPException(404, "Няма такъв участник")
    report = [r for r in Q.reports(eid) if r["source_id"] == sid and r["reg_in"] == reg]
    lines = Q.report_lines(eid, report[0]["layout"], n=report[0]["n"]) if len(report) == 1 else []
    pts, undated = Q.by_day(eid, None, (reg, sid))
    return render(request, "participant.html", nav="Участници", p=p, key=key, donations=Q.donations(eid, None, (reg, sid)), funds=Q.candidate_funds(eid, None, p["participant_id"]),
                  agencies=Q.agency_rows(eid, None, p["participant_id"]), report=report[0] if len(report) == 1 else None, lines=lines, helps=H.report_help(lines),
                  same=Q.same_name(p), days=len(pts), undated=undated)


# ---------- agencies ----------

@app.get("/agencii", response_class=HTMLResponse)
def agencies_page(request: Request, q: str = ""):
    return render(request, "agencies.html", nav="Агенции", rows=Q.agencies(q), q=q, unmatched=Q.unmatched("agency"))


@app.get("/agencii/{eik}", response_class=HTMLResponse)
def agency_page(request: Request, eik: str):
    a = Q.agency(eik)
    if not a:
        raise HTTPException(404, "Няма такава агенция")
    return render(request, "agency.html", nav="Агенции", a=a, rows=Q.agency_rows(eik=eik))


# ---------- sources ----------

@app.get("/sources", response_class=HTMLResponse)
def sources(request: Request):
    return render(request, "sources.html", nav="Източници", s=Q.sources(), problems=Q.problems())


@app.get("/sources.json")
def sources_json():
    problems = Q.problems()
    s = Q.sources()
    return JSONResponse(json.loads(json.dumps(dict(
        tool="partii", problems=problems, latest=Q.latest(),
        datasets=s["datasets"], indicators=s["indicators"],
        tables=s["tables"], answers=s["statuses"], unmatched=s["unmatched"], checks=s["checks"],
        elections=[dict(erik_id=e["erik_id"], title=e["title"], election_day=e["election_day"], gone_at=e["gone_at"], donations=e["records_total"],
                        money_eur=e["total_money"], in_kind_eur=e["total_in_kind"], read_at=e["read_at"], reads=e["reads"], raw_sha256=e["sha256"],
                        report_currency=e["report_currency"]) for e in s["elections"]],
        exports=["/export-izbori.{csv,json}", "/export-poluchateli.{csv,json}?e=", "/export-dareniya.{csv,json}?e=&d=&p=", "/export-kandidati.{csv,json}?e=",
                 "/export-agencii.{csv,json}?e=&eik=", "/export-otcheti.{csv,json}?e=", "/export-otchet.{csv,json}?e=&l=&n=", "/export-sravnenie.{csv,json}?e=&a=&b=",
                 "/export-uchastnici.{csv,json}?q=&e="]), default=plain)))


@app.get("/how", response_class=HTMLResponse)
def how(request: Request):
    return render(request, "how.html", nav="Източници")
