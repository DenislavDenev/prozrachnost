"""Тендер web app: server-rendered Jinja pages over schema `live`; JS only for charts and the network."""
import csv
import io
import json
import re
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

import markdown
import markdown.extensions.toc
import os
import secrets

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import queries as Q

HERE = Path(__file__).parent
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
T = Jinja2Templates(directory=HERE / "templates")

ROLE = {"manager": "управител", "sole_owner": "едноличен собственик", "partner": "съдружник",
        "representative": "представител", "board_of_directors": "член на СД", "management_board": "член на УС",
        "supervisory_board": "член на НС", "procurator": "прокурист", "liquidator": "ликвидатор",
        "trader": "едноличен търговец", "chair": "председател", "governing_body": "член на орган",
        "controlling_board": "член на КС", "branch_manager": "управител на клон", "trustee": "синдик"}
FORM = {"OOD": "ООД", "EOOD": "ЕООД", "AD": "АД", "EAD": "ЕАД", "ET": "ЕТ", "K": "КД", "KD": "КД", "SD": "СД"}
FLAG = {"review": "≥ 10× прогнозната стойност", "value_low": "много ниска стойност",
        "value_suspect": "съмнителна стойност, сумира се прогнозната", "annex_suspect": "съмнителен анекс, сумира се първоначалната"}


def fnum(v, d=0):
    if v is None:
        return "—"
    s = f"{float(v):,.{d}f}".replace(",", " ").replace(".", ",")
    return s


def feur(v):
    return "—" if v is None else fnum(v) + " €"


def fbig(v):
    if v is None:
        return "—"
    v = float(v)
    if v >= 1e9:
        return fnum(v / 1e9, 1).rstrip("0").rstrip(",") + " млрд. €"
    if v >= 1e6:
        return fnum(v / 1e6, 1).rstrip("0").rstrip(",") + " млн. €"
    return feur(v)


def fdate(v):
    return v.strftime("%d.%m.%Y") if v else "—"


def tc(s):
    """Registry names come in upper case; show them in title case, keeping legal forms upper."""
    if not s:
        return ""
    s = re.sub(r"(^|[\s\-\"„(])(\w)", lambda m: m.group(1) + m.group(2).upper(), s.lower())
    return re.sub(r"(?<!\w)(Ад|Оод|Еоод|Еад|Ет|Дп|Дззд|Кд)(?!\w)", lambda m: m.group(0).upper(), s)


def jdefault(o):
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


T.env.filters.update(eur=feur, big=fbig, num=fnum, date=fdate, tc=tc, role=lambda r: ROLE.get(r, r),
                     form=lambda f: FORM.get(f or "", f or ""), flag=lambda f: FLAG.get(f, ""),
                     json=lambda o: json.dumps(o, default=jdefault, ensure_ascii=False).replace("</", "<\\/"),
                     q=lambda s: quote(s, safe=":"))


def page(request, name, **ctx):
    ctx.setdefault("fresh", Q.cached("fresh", Q.freshness, ttl=60))
    return T.TemplateResponse(request, name, ctx)


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    Q.one("SELECT 1 FROM live.contract LIMIT 1")
    return "ok"


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return page(request, "dash.html", d=Q.cached("dash", Q.dashboard), nav="Табло")


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = "", kind: str | None = None):
    res = Q.search(q, kind) if q else []
    hx = request.headers.get("HX-Request")
    if not hx and (len(res) == 1 or (res and res[0]["sim"] == 1 and q.strip().isdigit())):
        return RedirectResponse(link(res[0]["kind"], res[0]["ref"]), status_code=303)
    tmpl = "search_results.html" if hx else "search.html"
    return page(request, tmpl, q=q, kind=kind, res=res, link=link, nav="")


def link(kind, ref):
    return {"buyer": f"/buyers/{ref}", "company": f"/companies/{quote(ref, safe=':')}",
            "person": f"/persons/{ref}", "tender": f"/tenders/{quote(ref)}"}[kind]


@app.get("/companies/{key:path}", response_class=HTMLResponse)
def company(request: Request, key: str):
    if key.endswith("/contracts.csv"):
        return csv_response(Q.contracts_of(key=key.removesuffix("/contracts.csv"), limit=100000), "contracts.csv")
    if re.fullmatch(r"\d{9}(\d{4})?", key):
        key = "eik:" + key
    c = Q.company(key)
    if not c:
        raise HTTPException(404)
    return page(request, "company.html", c=c, contracts=Q.contracts_of(key=key, limit=12), nav="Фирми")


@app.get("/persons/{pid}", response_class=HTMLResponse)
def person(request: Request, pid: str):
    p = Q.person(pid)
    if not p:
        raise HTTPException(404)
    return page(request, "person.html", p=p, nav="Лица")


@app.get("/buyers/{eik}", response_class=HTMLResponse)
def buyer(request: Request, eik: str, offset: int = 0):
    if eik.endswith(".csv"):
        return csv_response(Q.contracts_of(buyer=eik.removesuffix(".csv"), limit=100000), "contracts.csv")
    b = Q.buyer(eik)
    if not b:
        raise HTTPException(404)
    return page(request, "buyer.html", b=b, contracts=Q.contracts_of(buyer=eik, limit=12), nav="Възложители")


@app.get("/contracts/{cid}", response_class=HTMLResponse)
def contract(request: Request, cid: str):
    as_json = cid.endswith(".json")
    c = Q.contract(cid.removesuffix(".json"))
    if not c:
        raise HTTPException(404)
    if as_json:
        return JSONResponse(json.loads(json.dumps(c, default=jdefault)))
    return page(request, "contract.html", c=c, nav="Поръчки")


@app.get("/tenders/{unp}", response_class=HTMLResponse)
def tender(request: Request, unp: str):
    t = Q.tender(unp)
    if not t:
        raise HTTPException(404)
    return page(request, "tender.html", t=t, nav="Поръчки")


@app.get("/network.json")
def network(focus: str, view: str = "all", at: str | None = None):
    if view not in ("all", "ownership", "management") or not re.fullmatch(r"[pcfl]:[\w:-]+", focus):
        raise HTTPException(400)
    if at and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", at):
        raise HTTPException(400)
    return JSONResponse(json.loads(json.dumps(Q.network(focus, view, at), default=jdefault)))


@app.get("/buyers", response_class=HTMLResponse)
def buyers(request: Request, offset: int = 0):
    rows = Q.rows("""SELECT b.eik, b.name, b.type, s.contracts, s.amount_eur, s.suppliers FROM live.buyer b
                     JOIN live.buyer_stats s USING (eik) ORDER BY s.amount_eur DESC NULLS LAST LIMIT 50 OFFSET %s""", offset)
    return page(request, "list.html", title="Възложители", kind="buyer", rows=rows, offset=offset, nav="Възложители")


@app.get("/companies", response_class=HTMLResponse)
def companies(request: Request, offset: int = 0):
    rows = Q.rows("""SELECT c.key, c.eik, c.name, c.seat, s.contracts, s.amount_eur, s.buyers FROM live.company c
                     JOIN live.company_stats s USING (key) ORDER BY s.amount_eur DESC NULLS LAST LIMIT 50 OFFSET %s""", offset)
    return page(request, "list.html", title="Фирми", kind="company", rows=rows, offset=offset, nav="Фирми")


@app.get("/methodology", response_class=HTMLResponse)
def methodology(request: Request):
    md = (HERE.parent / "docs" / "methodology.md").read_text(encoding="utf-8")
    return page(request, "prose.html", title="Методология", html=md_html(md), nav="Методология")


@app.get("/legal", response_class=HTMLResponse)
def legal(request: Request):
    md = (HERE.parent / "docs" / "legal.md").read_text(encoding="utf-8")
    return page(request, "prose.html", title="Лични данни и източници", html=md_html(md), nav="")


@app.get("/sources", response_class=HTMLResponse)
def sources(request: Request):
    s = {
        "eop": Q.one("SELECT min(day) first, max(day) FILTER (WHERE published) last, count(*) FILTER (WHERE published) days FROM ops.eop_day"),
        "tr": Q.one("""SELECT count(*) FILTER (WHERE status='done') done, count(*) FILTER (WHERE status='pending') pending,
                        count(*) FILTER (WHERE status='failed') failed, max(done_at) last FROM tr.queue"""),
        "deeds": Q.one("SELECT count(*) FILTER (WHERE status='ok') ok, count(*) FILTER (WHERE status='absent') absent FROM tr.deed"),
        "names": Q.one("""SELECT count(*) FILTER (WHERE status='done') done, count(*) FILTER (WHERE status='ambiguous') ambiguous,
                          (SELECT count(DISTINCT name_key) FROM tr.person) total FROM tr.name_search"""),
        "runs": Q.rows("""SELECT DISTINCT ON (step) step, status, started_at, finished_at, error FROM ops.job_run
                          ORDER BY step, started_at DESC"""),
    }
    return page(request, "sources.html", s=s, nav="")


def md_html(md):
    return markdown.markdown(md, extensions=["tables", "toc"],
                             extension_configs={"toc": {"slugify": markdown.extensions.toc.slugify_unicode}})


def csv_response(rows, name):
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return StreamingResponse(iter(["﻿" + buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


basic = HTTPBasic()


def admin(creds: HTTPBasicCredentials = Depends(basic)):
    """HTTP Basic for the review queue; disabled unless TENDER_ADMIN_PASSWORD is set (see deploy/README)."""
    pw = os.environ.get("TENDER_ADMIN_PASSWORD")
    if not pw or not (secrets.compare_digest(creds.username, "admin") and secrets.compare_digest(creds.password, pw)):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
    return creds.username


@app.get("/admin/review", response_class=HTMLResponse)
def review(request: Request, who: str = Depends(admin)):
    figures = Q.rows("""SELECT f.*, p.name person_name,
        (SELECT count(DISTINCT e.company) FROM live.edge e WHERE e.holder = 'p:' || f.person_id) companies
        FROM ed.public_figure f JOIN tr.person p ON p.id = f.person_id WHERE f.status = 'candidate'
        ORDER BY f.label LIMIT 100""")
    arts = Q.rows("""SELECT a.*, coalesce(p.name, c.name) entity_name FROM ed.article a
        LEFT JOIN tr.person p ON a.entity_type = 'person' AND p.id = a.entity_id
        LEFT JOIN live.company c ON a.entity_type = 'company' AND c.key = a.entity_id
        WHERE a.status = 'candidate' ORDER BY a.published_at DESC NULLS LAST LIMIT 100""")
    return page(request, "review.html", figures=figures, arts=arts, nav="")


@app.post("/admin/review/{kind}", response_class=HTMLResponse)
def review_decide(kind: str, key: str = Form(...), decision: str = Form(...), who: str = Depends(admin)):
    if decision not in ("confirmed", "rejected") or kind not in ("figure", "article"):
        raise HTTPException(400)
    with Q.connect() as conn:
        if kind == "figure":
            pid, qid = key.split("|", 1)
            conn.execute("UPDATE ed.public_figure SET status=%s, reviewed_by=%s, reviewed_at=now() WHERE person_id=%s AND wikidata_qid=%s",
                         (decision, who, pid, qid))
        else:
            conn.execute("UPDATE ed.article SET status=%s, reviewed_by=%s, reviewed_at=now() WHERE id=%s", (decision, who, int(key)))
    return HTMLResponse(f'<span class="mut">{"потвърдено" if decision == "confirmed" else "отхвърлено"}</span>')


@app.exception_handler(404)
def not_found(request: Request, exc):
    return T.TemplateResponse(request, "404.html", {"fresh": {}, "nav": ""}, status_code=404)
