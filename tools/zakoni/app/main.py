"""Закони и решения: the pages. Gold only; every list has the same scope as its CSV and JSON (the addresses end in .csv and .json)."""
import datetime as dt
import json
import os
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from ingest import db as _db
from ingest.config import DATA, ROOT as TOOLROOT
from ingest import checks as _checks

from . import feedback
from . import queries as Q

ROOT = Path(__file__).resolve().parent
VERSION = "2"
HUB_URL = os.environ.get("HUB_URL", "https://prozrachnost.denev.work")
MONTHS = ["януари", "февруари", "март", "април", "май", "юни", "юли", "август", "септември", "октомври", "ноември", "декември"]
LICENCE = ("CC-BY (условия 2 на data.egov.bg, набор „Справки от Портала за обществени консултации“ на Администрацията на "
           "Министерския съвет), проверено на 02.10.2026")

app = FastAPI(title="Закони и решения", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.include_router(feedback.router("DenislavDenev/prozrachnost", os.getenv("ZAKONI_FEEDBACK", str(DATA / "feedback")), "Закони и решения"))
T = Jinja2Templates(directory=ROOT / "templates")


def bgdate(v):
    return "няма данни" if not v else f"{v:%d.%m.%Y}"


def num(v, digits=0):
    if v is None:
        return "няма данни"
    return f"{Decimal(str(v)):,.{digits}f}".replace(",", " ").replace(".", ",")


T.env.filters.update(date=bgdate, num=num)
T.env.policies["json.dumps_kwargs"] = {"default": lambda v: float(v) if isinstance(v, Decimal) else str(v), "sort_keys": False}
T.env.globals.update(MONTHS=MONTHS)


def help_data():
    zna = "https://justice.government.bg/home/normdoc/2127837184"
    return {"rows": [], "columns": {
        "short": dict(
            title="Кратък срок",
            text=("Срокът за предложения и становища по проект на нормативен акт е не по-кратък от 30 дни. В изключителни случаи, с изрично "
                  "посочени причини в мотивите или в доклада, може да е друг, но не под 14 дни (чл. 26, ал. 4 от Закона за нормативните актове, "
                  "в сила от 04.11.2016 г.). Тук „кратък срок“ е индикатор, не обвинение: броят дни от откриването до приключването, както ги "
                  "дава порталът, е под 30. Мотивът се показва, когато източникът го дава."),
            example="Условен пример: проект, открит на 1 март и приключващ на 15 март, има срок 14 дни и е с кратък срок; ако мотивът е посочен, се вижда до него.",
            source=zna),
        "days": dict(title="Срок в дни", text="Дните от датата на откриване до датата на приключване на консултацията, както са на портала.",
                     example="Условен пример: от 15.09 до 15.10 са 30 дни.", source=Q.SOURCE_URL),
        "reason": dict(title="Мотив за кратък срок", text="Текстът, който вносителят е записал на портала. Източникът понякога има само тире или точка: тогава мотив не е посочен.",
                       example="Условен пример: „Прилагането на новите размери от 1 януари налага съкратен срок.“", source=Q.SOURCE_URL),
        "kind": dict(title="Вид консултация", text="Централно: акт на министър, на Министерския съвет или закон. Общинско: акт на общински съвет или кмет. Областно: акт на областен управител.",
                     example="Условен пример: наредба на общински съвет е общинска консултация.", source=Q.SOURCE_URL),
        "act_type": dict(title="Вид акт на МС", text="Постановление: общ или поименен акт на Министерския съвет, обнародван в Държавен вестник. Решение: за приемане на стратегия, одобряване на законопроект, разпределяне на средства. Разпореждане: за конкретна поименна работа. Протоколно решение: решение, записано само в протокола на заседанието.",
                        example="Условен пример: „Решение за одобряване на законопроект“ е внасянето му в парламента.", source=Q.SOURCE_URL),
    }}


def render(request, name, nav, status_code=200, **ctx):
    b = Q.build()
    base = dict(
        nav=nav, v=VERSION, hub_url=HUB_URL, feedback_button=Markup(feedback.BUTTON), support_link=Markup(feedback.support_link(HUB_URL)),
        built=b["built_at"] if b else None, archive_read=Q.archive_read(), help_json=Markup(json.dumps(help_data(), ensure_ascii=False).replace("</", "<\\/")),
        source_url=Q.SOURCE_URL, licence=LICENCE, request=request, one_year=dt.timedelta(days=365), date_rule=Q.RULE_FROM)
    return T.TemplateResponse(request=request, name=name, context={**base, **ctx}, status_code=status_code)


def serial(data):
    return JSONResponse(json.loads(json.dumps(data, default=lambda v: float(v) if isinstance(v, Decimal) else str(v))))


def csv_response(data, columns, name):
    return Response(Q.csv_bytes(data, columns), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


def api(data, **meta):
    """One envelope for every JSON address: the licence, the source and the build go with the data."""
    b = Q.build()
    return serial(dict(licence=LICENCE, source=Q.SOURCE_URL, build=b["build_id"] if b else None, built_at=b["built_at"] if b else None, **meta, data=data))


def today():
    return dt.date.today()


@app.exception_handler(404)
async def not_found(request, exc):
    return render(request, "404.html", "", status_code=404)


@app.get("/healthz")
def health():
    with _db.connect() as c:
        c.execute("SELECT 1 FROM gold.build LIMIT 1")
    return {"ok": True}


@app.get("/favicon.svg")
def favicon():
    return Response((ROOT / "static" / "favicon.svg").read_text(), media_type="image/svg+xml")


# ------------------------------------------------------------------------------------------------ the dashboard
def week_start(day):
    return day - dt.timedelta(days=day.weekday())


@app.get("/", response_class=HTMLResponse)
def home(request: Request, na: str = ""):
    on = Q.d(na, today())                              # as_of: the page as it stood on a day
    monday = week_start(on)
    week = Q.one("""SELECT count(*) AS n, count(*) FILTER (WHERE act_type = 'Постановления') AS p, count(*) FILTER (WHERE act_type = 'Решения') AS r,
                           count(*) FILTER (WHERE act_type = 'Разпореждания') AS z FROM gold.act
                    WHERE accepted BETWEEN %s AND %s AND act_type IN ('Постановления', 'Решения', 'Разпореждания')""", (monday, on))
    newest = Q.one("SELECT max(accepted) AS d FROM gold.act WHERE accepted <= %s", (on,))["d"]
    cnt = Q.open_count(on)
    weeks = Q.acts_by_week(on - dt.timedelta(days=7 * 52), on)
    return render(request, "home.html", "Табло", on=on, monday=monday, week=week, newest=newest, cnt=cnt, open=Q.open_now(on),
                  weeks=weeks, is_today=(on == today()))


@app.get("/api/sedmici.json")
def weeks_api(frm: str = "", to: str = ""):
    to_d = Q.d(to, today())
    return api(Q.acts_by_week(Q.d(frm, to_d - dt.timedelta(days=7 * 52)), to_d), kind="weeks")


# ------------------------------------------------------------------------------------------------ the map
MEASURES = {"n": ("Общински консултации", "консултации", "Колко проекта на актове на общински съвет и кмет са били на обществено обсъждане през годината."),
            "short": ("Общински консултации със срок под 30 дни", "консултации", "Колко от тях са имали срок по-кратък от 30 дни; индикатор, не обвинение."),
            "share": ("Дял на общинските консултации със срок под 30 дни", "% от консултациите", "Каква част от консултациите на общината са със срок под 30 дни. Общини без консултации са без данни.")}
LEVELS = [("obshtini", "Общини"), ("oblasti", "Области")]
SINGLE = {"obshtini": "Община", "oblasti": "Област"}


def map_payload(m, l, y):
    if m not in MEASURES or l not in dict(LEVELS):
        raise HTTPException(400, "Невалиден показател или ниво")
    years = [str(x) for x in Q.consultation_filters()["years"]]
    if not y or y not in years:
        # the latest year in which at least 80% of the municipalities appear (AGENTS.md 7)
        good = Q.rows("""SELECT extract(year FROM opened)::int AS y, count(DISTINCT municipality_id) AS n FROM gold.consultation
                         WHERE municipality_id IS NOT NULL GROUP BY 1 ORDER BY 1 DESC""")
        total = Q.one("SELECT count(*) AS n FROM ref.municipality")["n"]
        y = str(next((r["y"] for r in good if r["n"] >= 0.8 * total), years[0] if years else ""))
    items = Q.map_data(m, l, y) if y else []
    name, unit, definition = MEASURES[m]
    allm = Q.one("""SELECT count(*) AS n, count(*) FILTER (WHERE short_term_applies) AS short FROM gold.consultation
                    WHERE municipality_id IS NOT NULL AND extract(year FROM opened) = %s""", (int(y or 0),))
    bg = allm["n"] if m == "n" else allm["short"] if m == "short" else (round(100 * allm["short"] / allm["n"], 1) if allm["n"] else None)
    for it in items:
        it["href"] = f"/konsultacii?municipality={it['code']}&year={y}" if l == "obshtini" else None
    return dict(definition=definition + " Общините са по института, който е внесъл проекта; консултациите на държавните институции не са на картата.",
                m=m, l=l, o="bg", nuts=4 if l == "obshtini" else 3, title=name, plural=dict(LEVELS)[l], single=SINGLE[l], unit=unit, digits=1 if m == "share" else 0,
                items=items, y=y, years=years, levels=LEVELS, bg=bg, base=None, updated=None, countries={}, dataset="АМС · " + y,
                url=Q.SOURCE_URL, csv=f"/karta.csv?m={m}&l={l}&y={y}", denominator="current", population_date=None)


@app.get("/karta", response_class=HTMLResponse)
def karta(request: Request, m: str = "n", l: str = "obshtini", y: str = ""):
    return render(request, "karta.html", "Карта", d=map_payload(m, l, y), measures={k: (None, None, v[0]) for k, v in MEASURES.items()})


@app.get("/api/karta.json")
def karta_api(m: str = "n", l: str = "obshtini", y: str = "", o: str = "bg", denominator: str = "current"):
    return serial(map_payload(m, l, y))


@app.get("/karta.csv")
def karta_csv(m: str = "n", l: str = "obshtini", y: str = ""):
    p = map_payload(m, l, y)
    rows = [dict(code=i["code"], name=i["name"], year=p["y"], consultations=i["n"], short_term=i["short"], value=i["v"], unit=p["unit"]) for i in p["items"]]
    return csv_response(rows, ["code", "name", "year", "consultations", "short_term", "value", "unit"], "karta")


# ------------------------------------------------------------------------------------------------ acts of the Council of Ministers
ACT_CSV = ["pris_id", "doc_num", "accepted", "act_type", "about", "importer", "legal_reason", "protocol", "gazette_number", "gazette_year", "confidential", "active", "consultation_reg_num", "origin"]


def act_filters(q="", type="", importer="", year="", frm="", to="", gazette=""):
    return dict(q=q.strip(), type=type, importer=importer, year=year if year.isdigit() else "", **{"from": Q.d(frm), "to": Q.d(to)}, gazette=gazette)


@app.get("/aktove", response_class=HTMLResponse)
def acts(request: Request, q: str = "", type: str = "", importer: str = "", year: str = "", frm: str = "", to: str = "", gazette: str = ""):
    f = act_filters(q, type, importer, year, frm, to, gazette)
    data, total = Q.acts(f)
    if year.isdigit():
        w_from, w_to = dt.date(int(year), 1, 1), dt.date(int(year), 12, 31)
    else:
        w_to = f["to"] or today()
        w_from = f["from"] or w_to - dt.timedelta(days=7 * 52)
    wk = Q.acts_by_week(w_from, w_to)
    return render(request, "aktove.html", "Актове на МС", rows=data, total=total, f=f, types=Q.ACT_TYPES, importers=Q.importers(), years=Q.act_years(), weeks=wk,
                  query=request.url.query)


@app.get("/aktove.csv")
def acts_csv(q: str = "", type: str = "", importer: str = "", year: str = "", frm: str = "", to: str = "", gazette: str = ""):
    data, _ = Q.acts(act_filters(q, type, importer, year, frm, to, gazette), limit=0)
    return csv_response(data, ACT_CSV, "aktove")


@app.get("/aktove.json")
def acts_json(q: str = "", type: str = "", importer: str = "", year: str = "", frm: str = "", to: str = "", gazette: str = ""):
    data, total = Q.acts(act_filters(q, type, importer, year, frm, to, gazette), limit=0)
    return api([{k: r[k] for k in ACT_CSV} for r in data], total=total)


@app.get("/aktove/{pris_id}.json")
def act_json(pris_id: int):
    a = Q.act(pris_id)
    if not a:
        raise HTTPException(404)
    return api(a)


@app.get("/aktove/{pris_id}", response_class=HTMLResponse)
def act_page(request: Request, pris_id: int):
    a = Q.act(pris_id)
    if not a:
        raise HTTPException(404)
    return render(request, "akt.html", "Актове на МС", a=a)


# ------------------------------------------------------------------------------------------------ consultations
CONS_CSV = ["reg_num", "name", "level", "act_type", "opened", "closes", "days", "short_term_applies", "short_term_reason", "reason_given", "policy_area",
            "institution_id", "institution_name", "municipality_id", "municipality", "oblast", "comment_count", "act_pris_id"]


def cons_filters(q="", level="", act_type="", area="", year="", frm="", to="", na="", short="", oblast="", municipality="", institution=""):
    return dict(q=q.strip(), level=level, act_type=act_type, area=area, year=year if year.isdigit() else "", short=short, oblast=oblast,
                municipality=municipality if municipality.isdigit() else "", institution=institution if institution.isdigit() else "",
                **{"from": Q.d(frm), "to": Q.d(to), "open_on": Q.d(na)})


@app.get("/konsultacii", response_class=HTMLResponse)
def consultations(request: Request, q: str = "", level: str = "", act_type: str = "", area: str = "", year: str = "", frm: str = "", to: str = "", na: str = "",
                  short: str = "", oblast: str = "", municipality: str = "", institution: str = ""):
    f = cons_filters(q, level, act_type, area, year, frm, to, na, short, oblast, municipality, institution)
    data, total = Q.consultations(f)
    shorts = sum(1 for r in data if r["short_term_applies"])
    return render(request, "konsultacii.html", "Консултации", rows=data, total=total, f=f, flt=Q.consultation_filters(), shorts=shorts, query=request.url.query)


@app.get("/konsultacii.csv")
def consultations_csv(q: str = "", level: str = "", act_type: str = "", area: str = "", year: str = "", frm: str = "", to: str = "", na: str = "",
                      short: str = "", oblast: str = "", municipality: str = "", institution: str = ""):
    data, _ = Q.consultations(cons_filters(q, level, act_type, area, year, frm, to, na, short, oblast, municipality, institution), limit=0)
    return csv_response(data, CONS_CSV, "konsultacii")


@app.get("/konsultacii.json")
def consultations_json(q: str = "", level: str = "", act_type: str = "", area: str = "", year: str = "", frm: str = "", to: str = "", na: str = "",
                       short: str = "", oblast: str = "", municipality: str = "", institution: str = ""):
    data, total = Q.consultations(cons_filters(q, level, act_type, area, year, frm, to, na, short, oblast, municipality, institution), limit=0)
    return api([{k: r[k] for k in CONS_CSV} for r in data], total=total)


@app.get("/konsultacii/{reg}.json")
def consultation_json(reg: str):
    c = Q.consultation(reg)
    if not c:
        raise HTTPException(404)
    return api(c)


@app.get("/konsultacii/{reg}", response_class=HTMLResponse)
def consultation_page(request: Request, reg: str):
    c = Q.consultation(reg)
    if not c:
        raise HTTPException(404)
    return render(request, "konsultacia.html", "Консултации", c=c)


# ------------------------------------------------------------------------------------------------ strategic documents
STRAT_CSV = ["doc_key", "name", "level", "policy_area", "doc_type", "authority", "accepted", "expires", "act_pris_id", "files"]


def strat_filters(q="", level="", type="", year="", na=""):
    return dict(q=q.strip(), level=level, type=type, year=year if year.isdigit() else "", valid_on=Q.d(na))


@app.get("/strategii", response_class=HTMLResponse)
def strategies(request: Request, q: str = "", level: str = "", type: str = "", year: str = "", na: str = ""):
    f = strat_filters(q, level, type, year, na)
    data, total = Q.strategies(f)
    return render(request, "strategii.html", "Стратегии", rows=data, total=total, f=f, flt=Q.strategy_filters(), query=request.url.query)


@app.get("/strategii.csv")
def strategies_csv(q: str = "", level: str = "", type: str = "", year: str = "", na: str = ""):
    data, _ = Q.strategies(strat_filters(q, level, type, year, na), limit=0)
    return csv_response(data, STRAT_CSV, "strategii")


@app.get("/strategii.json")
def strategies_json(q: str = "", level: str = "", type: str = "", year: str = "", na: str = ""):
    data, total = Q.strategies(strat_filters(q, level, type, year, na), limit=0)
    return api([{k: r[k] for k in STRAT_CSV} for r in data], total=total)


# ------------------------------------------------------------------------------------------------ search, comparison
@app.get("/tarsene", response_class=HTMLResponse)
def search(request: Request, q: str = ""):
    return render(request, "tarsene.html", "Търсене", q=q, r=Q.search(q))


@app.get("/tarsene.json")
def search_json(q: str = ""):
    return api(Q.search(q, 100))


def periods(a, b):
    """'2025' or '2025-03' or '2025-03-01:2025-03-31' -> (from, to)."""
    def one(s, default):
        s = (s or "").strip()
        try:
            if ":" in s:
                x, y = s.split(":")
                return dt.date.fromisoformat(x), dt.date.fromisoformat(y)
            if len(s) == 4:
                return dt.date(int(s), 1, 1), dt.date(int(s), 12, 31)
            if len(s) == 7:
                y, m = int(s[:4]), int(s[5:])
                end = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
                return dt.date(y, m, 1), end
        except ValueError:
            pass
        return default
    y = today().year
    return one(a, (dt.date(y - 1, 1, 1), dt.date(y - 1, 12, 31))), one(b, (dt.date(y - 2, 1, 1), dt.date(y - 2, 12, 31)))


@app.get("/sravnenie", response_class=HTMLResponse)
def compare(request: Request, a: str = "", b: str = ""):
    (af, at), (bf, bt) = periods(a, b)
    return render(request, "sravnenie.html", "Сравнение", c=Q.compare(af, at, bf, bt), a=a or str(af.year), b=b or str(bf.year))


@app.get("/sravnenie.json")
def compare_json(a: str = "", b: str = ""):
    (af, at), (bf, bt) = periods(a, b)
    return api(Q.compare(af, at, bf, bt))


@app.get("/sravnenie.csv")
def compare_csv(a: str = "", b: str = ""):
    (af, at), (bf, bt) = periods(a, b)
    c = Q.compare(af, at, bf, bt)
    rows = []
    for k, p in (("a", c["a"]), ("b", c["b"])):
        rows.append(dict(period=k, from_=p["frm"], to=p["to"], acts=p["acts_total"], consultations=p["consultations"]["n"], short_term=p["consultations"]["short"],
                         median_days=p["consultations"]["median_days"], comments=p["consultations"]["comments"]))
    return csv_response([{**r, "from": r.pop("from_")} for r in rows], ["period", "from", "to", "acts", "consultations", "short_term", "median_days", "comments"], "sravnenie")


# ------------------------------------------------------------------------------------------------ sources
def source_names():
    from ingest.load import RESOURCES
    return {r.id: r for r in RESOURCES}


@app.get("/sources", response_class=HTMLResponse)
def sources(request: Request):
    with _db.connect(autocommit=True) as c:
        problems = _checks.freshness(c)
    return render(request, "sources.html", "Източници", rows=Q.sources(), names=source_names(), recon=Q.reconciliations(), problems=problems, build=Q.build())


@app.get("/sources.json")
def sources_json():
    names = source_names()
    data = [dict(resource=r["ref"], name=names[r["ref"]].name if r["ref"] in names else None, role=r["role"], status=r["status"], error=r["error"], rows=r["rows"],
                 sha256=r["sha256"], version=r["version"], last_read=r["last_read"], last_ok=r["last_ok"], last_change=r["last_change"],
                 url=f"{Q.SOURCE_URL}", licence=LICENCE) for r in Q.sources()]
    return api(data, reconciliations=Q.reconciliations(), archive_read=Q.archive_read())


@app.get("/how", response_class=HTMLResponse)
def how(request: Request):
    return render(request, "how.html", "Източници")
