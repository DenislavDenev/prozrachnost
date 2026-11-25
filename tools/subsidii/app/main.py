import csv
import datetime as dt
import hashlib
import io
import json
import os
from decimal import Decimal
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from markupsafe import Markup

from ingest import config
from . import feedback, help as H, queries as Q

ROOT = Path(__file__).resolve().parent
ASSET_VERSION = hashlib.sha256(b"".join((ROOT / "static" / n).read_bytes() for n in ("app.css", "app.js", "karta.js", "state-help.js", "subsidii.js", "subsidii.css"))).hexdigest()[:12]
app = FastAPI(title="Субсидии и помощи", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.include_router(feedback.router("DenislavDenev/prozrachnost", Path(os.environ.get("SUBSIDII_FEEDBACK", str(config.DATA / "feedback"))), "Субсидии и помощи"))
t = Jinja2Templates(directory=ROOT / "templates")


@app.middleware("http")
async def one_snapshot(request: Request, call_next):
    """Every request reads one snapshot of the serving layer, and its answer says which publication that is: a cache in
    front of the site keeps an answer until the number moves, and it moves only when the published content does."""
    if request.url.path.startswith("/static"):
        return await call_next(request)
    with Q.snapshot() as c:
        token = Q.CONN.set(c)
        try:
            v = Q.version()
            response = await call_next(request)
        finally:
            Q.CONN.reset(token)
    if v:
        response.headers["X-Publication"] = str(v["publication_id"])
        response.headers["ETag"] = 'W/"pub-%s"' % v["publication_id"]
        response.headers["Last-Modified"] = format_datetime(v["published_at"].astimezone(dt.timezone.utc), usegmt=True)
    return response
MENU = [("Табло", "/"), ("Карта", "/karta"), ("Фондове и мерки", "/merki"), ("Получатели", "/poluchateli"), ("Общини", "/obshtini"),
        ("Сравнение", "/sravnenie"), ("Източници", "/sources")]
SOURCE_NAME = "ДФЗ, справка „Изплатени субсидии“"
SOURCE_URL = "https://seu.dfz.bg/seu/f?p=727:8110:::NO"


def number(v, d=0):
    return "няма данни" if v is None else f"{Decimal(str(v)):,.{d}f}".replace(",", " ").replace(".", ",")


def money(v):
    """Euro: from a billion in billions, from a million in millions (AGENTS.md 7); None is no data, not zero."""
    if v is None:
        return "няма данни"
    v = Decimal(str(v))
    if abs(v) >= 1_000_000_000:
        return number(v / 1_000_000_000, 2) + " млрд. €"
    if abs(v) >= 1_000_000:
        return number(v / 1_000_000, 2) + " млн. €"
    return number(v, 2 if abs(v) < 100000 else 0) + " €"


def full(v):
    return "няма данни" if v is None else number(v, 2) + " €"


def pct(v, d=1):
    return "няма данни" if v is None else number(Decimal(str(v)) * 100, d) + "%"


def signed(v, d=1):
    if v is None:
        return "няма данни"
    s = number(abs(Decimal(str(v)) * 100), d)
    return ("+" if v > 0 else "−" if v < 0 else "") + s + "%"


def date(v):
    if not v:
        return "няма данни"
    s = str(v)
    return s[8:10] + "." + s[5:7] + "." + s[:4]


def plural(n, one, many):
    return one if n == 1 else many


t.env.policies["json.dumps_kwargs"] = {"sort_keys": True, "default": lambda v: float(v) if isinstance(v, Decimal) else str(v)}
t.env.filters.update(num=number, money=money, full=full, pct=pct, signed=signed, date=date, plural=plural)


def render(request, name, **context):
    hub = os.environ.get("HUB_URL", "https://prozrachnost.denev.work")
    return t.TemplateResponse(request=request, name=name, context=dict(
        v=ASSET_VERSION, hub_url=hub, feedback_button=Markup(feedback.BUTTON), support_link=Markup(feedback.support_link(hub)), menu=MENU,
        fresh=Q.latest(), help_json=Markup(json.dumps(H.HELP, ensure_ascii=False).replace("</", "<\\/")), kind_names=Q.KINDS, fund_names=Q.FUNDS,
        oblasti=Q.oblasti(), refs=Q.refs(), source_name=SOURCE_NAME, source_url=SOURCE_URL, **context))


def serial(data):
    def default(v):
        if isinstance(v, Decimal):
            return float(v)
        return str(v)
    return JSONResponse(json.loads(json.dumps(data, default=default, ensure_ascii=False)))


def csv_response(rows, headers, name):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(headers)
    for r in rows:
        w.writerow(["" if r.get(h) is None else r.get(h) for h in headers])
    return Response(("﻿" + out.getvalue()).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def scope(request, fy=None, snap=None, oblast="", obshtina="", tip="all", fond=""):
    s = Q.resolve(fy, snap, oblast, obshtina, tip, fond)
    return s


def envelope(s, rows, **extra):
    """The JSON of an address: the same scope as the CSV, the source, the date and the licence note."""
    return dict(dataset="subsidii.paid", source=SOURCE_NAME, source_url=SOURCE_URL, licence=None,
                licence_note="На страницата на справката няма посочен лиценз; показва се с източника и не се раздава.",
                financial_year=s.fy, financial_year_label=s.fy_row["label_bg"], snapshot=str(s.snap), currency_original=s.currency,
                unit="EUR" if s.currency else None, filters=s.params(), **extra, rows=rows)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith(("/api/", "/export")) or exc.status_code not in (404, 400):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    r = render(request, "404.html", nav="", detail=exc.detail if isinstance(exc.detail, str) and exc.detail != "Not Found" else "Тази страница не съществува.")
    r.status_code = exc.status_code
    return r


# ---------- the shared parts ----------

@app.get("/healthz")
def health():
    v = Q.version()
    return {"ok": True, "publication": v and v["publication_id"]}


@app.get("/favicon.svg")
def favicon():
    return Response((ROOT / "static/favicon.svg").read_text(), media_type="image/svg+xml")


def fund_cards(s):
    rows, tot = Q.fund_rows(s)
    return rows, tot


# ---------- the dashboard ----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request, fy: str = None, snap: str = None, oblast: str = "", obshtina: str = "", tip: str = "all", fond: str = ""):
    s = scope(request, fy, snap, oblast, obshtina, tip, fond)
    if s is None:
        return render(request, "empty.html", nav="Табло")
    funds, tot = Q.fund_rows(s)
    return render(request, "home.html", nav="Табло", s=s, years=Q.years(), funds=funds, tot=tot, kinds_split=Q.by_kind(s), conc=Q.concentration(s),
                  series=Q.year_series(s), top=Q.top_recipients(s, 8), measures=Q.measures(s)[:8], params=s.params(), qs=urlencode(s.params()))


@app.get("/api/tablo.json")
def home_api(fy: str = None, snap: str = None, oblast: str = "", obshtina: str = "", tip: str = "all", fond: str = ""):
    s = scope(None, fy, snap, oblast, obshtina, tip, fond)
    if s is None:
        raise HTTPException(404, "Още няма данни")
    funds, tot = Q.fund_rows(s)
    conc = Q.concentration(s)
    return serial(envelope(s, [dict(fund=f["code"], name=f["name"], eur=f["eur"], original=f["original"]) for f in funds], recipients=tot["recipients"],
                           top1_share=conc["share"] if conc else None, per_year=[dict(fy=y["fy"], snapshot=y["snap"], total_eur=y["total"], funds={k: y[k] for k in Q.FUNDS}) for y in Q.year_series(s)]))


@app.get("/export-tablo.csv")
def home_csv(fy: str = None, snap: str = None, oblast: str = "", obshtina: str = "", tip: str = "all", fond: str = ""):
    s = scope(None, fy, snap, oblast, obshtina, tip, fond)
    if s is None:
        raise HTTPException(404, "Още няма данни")
    funds, tot = Q.fund_rows(s)
    rows = [dict(financial_year=s.fy, snapshot=str(s.snap), fund=f["code"], name=f["name"], eur=f["eur"], original=f["original"], currency=s.currency,
                 recipient_rows=tot["recipients"]) for f in funds]
    return csv_response(rows, ["financial_year", "snapshot", "fund", "name", "eur", "original", "currency", "recipient_rows"], f"subsidii-tablo-{s.fy}.csv")


# ---------- the map ----------

@app.get("/karta", response_class=HTMLResponse)
def karta(request: Request, l: str = "obshtini", fy: str = None, snap: str = None, fond: str = "total", per: str = "all"):
    d = Q.mapdata(l, fy, snap, fond, per)
    if d is None:
        return render(request, "empty.html", nav="Карта")
    d["definition"] = H.MAP_DEFINITION
    d["reading"] = reading(d)
    return render(request, "karta.html", nav="Карта", d=d, years=Q.years())


def reading(d):
    base = f"Избрана е {d['fy_label'].lower()}, нивото е {d['plural'].lower()}. "
    if d["per"] == "resident":
        base += (f"Сумата е разделена на жителите с постоянен адрес към {date(d['population_date'])} (ГРАО, чрез „Население“)"
                 + (", взети след края на годината, защото по-ранни няма" if d["population_late"] else "") + ". Без данни за жителите на цялата територия стойност няма. ")
    else:
        base += "Показваме общата сума в евро. "
    if d["currency"] != "BGN" and d["currency"] != "EUR":
        base += "Валутата на тази година още не е потвърдена, затова евро няма. "
    return base + "По-тъмното зелено е по-голяма стойност, сивото е липса на данни, не нула. Сумата е на получателите в територията, не на жителите."


@app.get("/api/karta.json")
def karta_api(l: str = "obshtini", fy: str = None, y: str = None, snap: str = None, fond: str = "total", per: str = "all"):
    d = Q.mapdata(l, fy, snap or y, fond, per)
    if d is None:
        raise HTTPException(404, "Още няма данни")
    d["definition"] = H.MAP_DEFINITION
    d["reading"] = reading(d)
    return serial(d)


@app.get("/export-karta.csv")
def karta_csv(level: str = "obshtini", fy: str = None, snap: str = None, fond: str = "total", per: str = "all"):
    d = Q.mapdata(level, fy, snap, fond, per)
    if d is None:
        raise HTTPException(404, "Още няма данни")
    rows = [dict(code=i["code"], name=i["name"], value=i["v"], unit=d["unit"], rank=i.get("rank"), recipient_rows=i["recipients"], financial_year=d["fy"], snapshot=d["y"],
                 fund=d["fond"], level=d["l"], population_date=d["population_date"] if d["per"] == "resident" else None) for i in d["items"]]
    return csv_response(rows, ["code", "name", "value", "unit", "rank", "recipient_rows", "financial_year", "snapshot", "fund", "level", "population_date"], f"subsidii-karta-{d['fy']}.csv")


# ---------- measures ----------

def measure_rows(s):
    return [dict(code=r["code"], measure=r["name"], objective=r["objective"], group=r["family_bg"], payments=r["payments"], efgz_eur=r["efgz_eur"], ezfrs_eur=r["ezfrs_eur"],
                 nb_eur=r["nb_eur"], total_eur=r["total_eur"], efgz_original=r["efgz"], ezfrs_original=r["ezfrs"], nb_original=r["nb"], total_original=r["total"],
                 financial_year=s.fy, snapshot=str(s.snap), currency=s.currency) for r in Q.measures(s)]


MEASURE_HEADERS = ["financial_year", "snapshot", "code", "measure", "objective", "group", "payments", "efgz_eur", "ezfrs_eur", "nb_eur", "total_eur",
                   "efgz_original", "ezfrs_original", "nb_original", "total_original", "currency"]


@app.get("/merki", response_class=HTMLResponse)
def merki(request: Request, fy: str = None, snap: str = None, oblast: str = "", obshtina: str = "", tip: str = "all", fond: str = ""):
    s = scope(request, fy, snap, oblast, obshtina, tip, fond)
    if s is None:
        return render(request, "empty.html", nav="Фондове и мерки")
    rows = Q.measures(s)
    funds, tot = Q.fund_rows(s)
    return render(request, "merki.html", nav="Фондове и мерки", s=s, years=Q.years(), rows=rows, funds=funds, tot=tot, qs=urlencode(s.params()), differs=Q.totals(s))


@app.get("/api/merki.json")
def merki_api(fy: str = None, snap: str = None, oblast: str = "", obshtina: str = "", tip: str = "all", fond: str = ""):
    s = scope(None, fy, snap, oblast, obshtina, tip, fond)
    if s is None:
        raise HTTPException(404, "Още няма данни")
    return serial(envelope(s, measure_rows(s)))


@app.get("/export-merki.csv")
def merki_csv(fy: str = None, snap: str = None, oblast: str = "", obshtina: str = "", tip: str = "all", fond: str = ""):
    s = scope(None, fy, snap, oblast, obshtina, tip, fond)
    if s is None:
        raise HTTPException(404, "Още няма данни")
    return csv_response(measure_rows(s), MEASURE_HEADERS, f"subsidii-merki-{s.fy}.csv")


# ---------- recipients ----------

def recipient_rows(s, data):
    """The rows of the list for an export: the name of a person is never in them (name_export)."""
    return [dict(financial_year=s.fy, snapshot=str(s.snap), recipient=r["name_export"], kind=r["kind"], org_id=r["org_id"] if r["kind"] == "legal" else None,
                 oblast=r["oblast"], municipality_id=r["municipality_id"], municipality=r["municipality"], efgz_eur=r["efgz_eur"], ezfrs_eur=r["ezfrs_eur"], nb_eur=r["nb_eur"],
                 total_eur=r["total_eur"], efgz_original=r["efgz"], ezfrs_original=r["ezfrs"], nb_original=r["nb"], total_original=r["total"], currency=s.currency)
            for r in data["rows"]]


RECIPIENT_HEADERS = ["financial_year", "snapshot", "recipient", "kind", "org_id", "oblast", "municipality_id", "municipality", "efgz_eur", "ezfrs_eur", "nb_eur", "total_eur",
                     "efgz_original", "ezfrs_original", "nb_original", "total_original", "currency"]


@app.get("/poluchateli", response_class=HTMLResponse)
def poluchateli(request: Request, fy: str = None, snap: str = None, oblast: str = "", obshtina: str = "", tip: str = "all", fond: str = "", q: str = "",
                sort: str = "total", direction: str = "desc", page: int = 1):
    s = scope(request, fy, snap, oblast, obshtina, tip, fond)
    if s is None:
        return render(request, "empty.html", nav="Получатели")
    data = Q.recipients(s, q, sort, direction, page)
    keep = s.params(q=q, sort=data["sort"], direction=data["direction"])
    return render(request, "poluchateli.html", nav="Получатели", s=s, years=Q.years(), d=data, qs=urlencode(keep), csv_qs=urlencode(s.params(q=q)),
                  link=lambda **kw: "/poluchateli?" + urlencode({**keep, **kw}))


@app.get("/api/poluchateli.json")
def poluchateli_api(fy: str = None, snap: str = None, oblast: str = "", obshtina: str = "", tip: str = "all", fond: str = "", q: str = "",
                    sort: str = "total", direction: str = "desc", page: int = 1, size: int = Q.PAGE):
    s = scope(None, fy, snap, oblast, obshtina, tip, fond)
    if s is None:
        raise HTTPException(404, "Още няма данни")
    d = Q.recipients(s, q, sort, direction, page, size=max(0, min(size, 500)) or Q.PAGE)
    return serial(envelope(s, recipient_rows(s, d), count=d["count"], page=d["page"], pages=d["pages"], search=q or None))


@app.get("/export-poluchateli.csv")
def poluchateli_csv(fy: str = None, snap: str = None, oblast: str = "", obshtina: str = "", tip: str = "all", fond: str = "", q: str = "",
                    sort: str = "total", direction: str = "desc"):
    s = scope(None, fy, snap, oblast, obshtina, tip, fond)
    if s is None:
        raise HTTPException(404, "Още няма данни")
    d = Q.recipients(s, q, sort, direction, size=0)
    return csv_response(recipient_rows(s, d), RECIPIENT_HEADERS, f"subsidii-poluchateli-{s.fy}.csv")


@app.get("/poluchateli/{org_id}", response_class=HTMLResponse)
def poluchatel(request: Request, org_id: str):
    o = Q.org(org_id)
    if not o:
        raise HTTPException(404, "Няма такава фирма")
    return render(request, "poluchatel.html", nav="Получатели", o=o)


def org_rows(o):
    out = []
    for y in o["years"]:
        for r in y["rows"]:
            out.append(dict(org_id=o["org_id"], recipient=o["name_bg"], financial_year=y["fy"], snapshot=str(y["snap"]), oblast=r["oblast"], municipality_id=r["municipality_id"],
                            municipality=r["municipality"], efgz_eur=r["efgz_eur"], ezfrs_eur=r["ezfrs_eur"], nb_eur=r["nb_eur"], total_eur=r["total_eur"],
                            efgz_original=r["efgz"], ezfrs_original=r["ezfrs"], nb_original=r["nb"], total_original=r["total"], currency=y["currency"]))
    return out


@app.get("/api/poluchateli/{org_id}.json")
def poluchatel_api(org_id: str):
    o = Q.org(org_id)
    if not o:
        raise HTTPException(404, "Няма такава фирма")
    return serial(dict(dataset="subsidii.paid", source=SOURCE_NAME, source_url=SOURCE_URL, licence=None, org_id=o["org_id"], name=o["name_bg"],
                       identity="по нормализирано име, не по ЕИК", rows=org_rows(o),
                       payments=[dict(financial_year=y["fy"], snapshot=str(y["snap"]), measures=[dict(code=p["code"], measure=p["name"], objective=p["objective"], payments=p["payments"],
                                                                                                   total_eur=p["total_eur"], total_original=p["total"]) for p in y["payments"]]) for y in o["years"]]))


@app.get("/export-poluchatel/{org_id}.csv")
def poluchatel_csv(org_id: str):
    o = Q.org(org_id)
    if not o:
        raise HTTPException(404, "Няма такава фирма")
    return csv_response(org_rows(o), ["org_id", "recipient", "financial_year", "snapshot", "oblast", "municipality_id", "municipality", "efgz_eur", "ezfrs_eur", "nb_eur", "total_eur",
                                      "efgz_original", "ezfrs_original", "nb_original", "total_original", "currency"], f"subsidii-poluchatel-{org_id}.csv")


@app.get("/api/tarsene.json")
def search_api(q: str = ""):
    return serial(dict(q=q, note="само юридически лица, по нормализирано име", rows=Q.search_orgs(q)))


# ---------- municipalities ----------

def municipality_rows(s, d):
    return [dict(municipality_id=r["id"], municipality=r["name"], oblast=r["oblast"], recipient_rows=r["recipients"], efgz_eur=r["efgz_eur"], ezfrs_eur=r["ezfrs_eur"], nb_eur=r["nb_eur"],
                 total_eur=r["total_eur"], efgz_original=r["efgz"], ezfrs_original=r["ezfrs"], nb_original=r["nb"], total_original=r["total"], currency=s.currency,
                 residents=r["persons"], per_resident_eur=r["per_resident"], residents_date=str(d["population_date"]) if d["population_date"] else None,
                 financial_year=s.fy, snapshot=str(s.snap)) for r in d["rows"]]


MUNICIPALITY_HEADERS = ["financial_year", "snapshot", "municipality_id", "municipality", "oblast", "recipient_rows", "efgz_eur", "ezfrs_eur", "nb_eur", "total_eur", "efgz_original",
                        "ezfrs_original", "nb_original", "total_original", "currency", "residents", "per_resident_eur", "residents_date"]


@app.get("/obshtini", response_class=HTMLResponse)
def obshtini(request: Request, fy: str = None, snap: str = None, oblast: str = "", obshtina: str = "", tip: str = "all", fond: str = ""):
    s = scope(request, fy, snap, oblast, obshtina, tip, fond)
    if s is None:
        return render(request, "empty.html", nav="Общини")
    return render(request, "obshtini.html", nav="Общини", s=s, years=Q.years(), d=Q.municipalities(s), qs=urlencode(s.params()))


@app.get("/api/obshtini.json")
def obshtini_api(fy: str = None, snap: str = None, oblast: str = "", obshtina: str = "", tip: str = "all", fond: str = ""):
    s = scope(None, fy, snap, oblast, obshtina, tip, fond)
    if s is None:
        raise HTTPException(404, "Още няма данни")
    d = Q.municipalities(s)
    return serial(envelope(s, municipality_rows(s, d), residents_date=d["population_date"]))


@app.get("/export-obshtini.csv")
def obshtini_csv(fy: str = None, snap: str = None, oblast: str = "", obshtina: str = "", tip: str = "all", fond: str = ""):
    s = scope(None, fy, snap, oblast, obshtina, tip, fond)
    if s is None:
        raise HTTPException(404, "Още няма данни")
    return csv_response(municipality_rows(s, Q.municipalities(s)), MUNICIPALITY_HEADERS, f"subsidii-obshtini-{s.fy}.csv")


@app.get("/obshtini/{mid}", response_class=HTMLResponse)
def obshtina_page(request: Request, mid: str, fy: str = None, snap: str = None):
    s = Q.resolve(fy, snap, "", mid, "all", "")
    m = Q.municipality(mid, s) if s else None
    if not m:
        raise HTTPException(404, "Няма такава община")
    return render(request, "obshtina.html", nav="Общини", s=s, m=m, top=Q.top_recipients(s, 10), measures=Q.measures(s)[:10], years=Q.years(),
                  qs=urlencode(s.params()), pop=Q.municipalities(s))


@app.get("/api/obshtini/{mid}.json")
def obshtina_api(mid: str, fy: str = None, snap: str = None):
    s = Q.resolve(fy, snap, "", mid, "all", "")
    m = Q.municipality(mid, s) if s else None
    if not m:
        raise HTTPException(404, "Няма такава община")
    return serial(dict(dataset="subsidii.paid", source=SOURCE_NAME, source_url=SOURCE_URL, licence=None, municipality_id=mid, name=m["m"]["name_bg"], oblast=m["m"]["oblast"],
                       years=[dict(financial_year=y["fy"], snapshot=str(y["snap"]), currency=y["currency"], recipient_rows=y["t"]["recipients"], total_eur=y["eur"]["total"],
                                   efgz_eur=y["eur"]["efgz"], ezfrs_eur=y["eur"]["ezfrs"], nb_eur=y["eur"]["nb"], total_original=y["t"]["total"]) for y in m["series"]]))


# ---------- any snapshot against any other ----------

@app.get("/sravnenie", response_class=HTMLResponse)
def sravnenie(request: Request, a_fy: str = None, a_snap: str = None, b_fy: str = None, b_snap: str = None, oblast: str = "", obshtina: str = "", tip: str = "all"):
    ys = Q.years()
    if not ys:
        return render(request, "empty.html", nav="Сравнение")
    a = Q.resolve(a_fy or (ys[1]["fy"] if len(ys) > 1 else ys[0]["fy"]), a_snap, oblast, obshtina, tip, "")
    b = Q.resolve(b_fy or ys[0]["fy"], b_snap, oblast, obshtina, tip, "")
    cmp = Q.compare(a, b)
    return render(request, "sravnenie.html", nav="Сравнение", years=ys, a=a, b=b, cmp=cmp, oblast=a.oblast, obshtina=a.obshtina, tip=a.kind,
                  qs=urlencode(dict(a_fy=a.fy, a_snap=a.snap, b_fy=b.fy, b_snap=b.snap, **({"oblast": a.oblast} if a.oblast else {}), **({"obshtina": a.obshtina} if a.obshtina else {}),
                                    **({"tip": a.kind} if a.kind != "all" else {}))))


def compare_rows(cmp):
    return [dict(fund=r["code"], name=r["name"], a_eur=r["a"], b_eur=r["b"], a_original=r["a_original"], b_original=r["b_original"], change=r["change"],
                 a_financial_year=cmp["a"].fy, a_snapshot=str(cmp["a"].snap), b_financial_year=cmp["b"].fy, b_snapshot=str(cmp["b"].snap),
                 same_scope=cmp["same_scope"]) for r in cmp["rows"]]


@app.get("/api/sravnenie.json")
def sravnenie_api(a_fy: str = None, a_snap: str = None, b_fy: str = None, b_snap: str = None, oblast: str = "", obshtina: str = "", tip: str = "all"):
    ys = Q.years()
    if not ys:
        raise HTTPException(404, "Още няма данни")
    a, b = Q.resolve(a_fy, a_snap, oblast, obshtina, tip, ""), Q.resolve(b_fy, b_snap, oblast, obshtina, tip, "")
    cmp = Q.compare(a, b)
    return serial(dict(dataset="subsidii.paid", source=SOURCE_NAME, source_url=SOURCE_URL, licence=None, same_scope=cmp["same_scope"], measures_only_in_a=cmp["only_a"],
                       measures_only_in_b=cmp["only_b"], same_currency=cmp["same_currency"], rows=compare_rows(cmp)))


@app.get("/export-sravnenie.csv")
def sravnenie_csv(a_fy: str = None, a_snap: str = None, b_fy: str = None, b_snap: str = None, oblast: str = "", obshtina: str = "", tip: str = "all"):
    a, b = Q.resolve(a_fy, a_snap, oblast, obshtina, tip, ""), Q.resolve(b_fy, b_snap, oblast, obshtina, tip, "")
    if a is None or b is None:
        raise HTTPException(404, "Още няма данни")
    return csv_response(compare_rows(Q.compare(a, b)), ["fund", "name", "a_financial_year", "a_snapshot", "a_eur", "a_original", "b_financial_year", "b_snapshot", "b_eur", "b_original",
                                                       "change", "same_scope"], "subsidii-sravnenie.csv")


# ---------- sources ----------

@app.get("/sources", response_class=HTMLResponse)
def sources(request: Request):
    problems = Q.problems()
    return render(request, "sources.html", nav="Източници", s=Q.sources(), problems=problems)


@app.get("/sources.json")
def sources_json():
    problems = Q.problems()
    s = Q.sources()
    ds = s["dataset"]
    return serial(dict(tool="subsidii", problems=problems, years=[dict(fy=y["fy"], label=y["label_bg"], currency=y["currency"], in_form=y["in_form"], gone_at=y["gone_at"], first_snapshot=y["first_snap"],
                                                                    last_snapshot=y["last_snap"]) for y in s["years"]],
                       snapshots=[dict(fy=r["fy"], day=r["day"], status=r["status"], rows=r["rows_file"], recipient_rows=r["holders"], payments=r["payments"], total=r["total_all"],
                                       differences_in_source=r["block_diffs"], sha256=r["sha256"]) for r in s["snapshots"]],
                       datasets=[dict(id=ds["id"], title=ds["title_bg"], channel=ds["channel"], why_not_higher=ds["why_not_higher"], licence=ds["licence"], licence_note=ds["licence_note"],
                                      distributed=ds["distributed"], cadence=ds["cadence"], personal_data=ds["personal_data_bg"], source_url=ds["source_url"],
                                      coverage_from=str(min((y["first_snap"] for y in s["years"] if y["first_snap"]), default="")) or None)],
                       residents=dict(date=s["population"]["day"], municipalities=s["population"]["municipalities"])))


@app.get("/how", response_class=HTMLResponse)
def how(request: Request):
    return render(request, "how.html", nav="Източници", help=H.HELP["columns"], ttl=2)
