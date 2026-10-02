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
from . import feedback, help as H, queries as Q

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Пазар", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.include_router(feedback.router("DenislavDenev/prozrachnost", Path(os.environ.get("PAZAR_FEEDBACK", str(config.DATA / "feedback"))), "Пазар"))
t = Jinja2Templates(directory=ROOT / "templates")
MENU = [("Табло", "/"), ("Карта", "/karta"), ("Продукти", "/produkti"), ("Категории", "/kategorii"), ("Вериги", "/verigi"),
        ("Моята кошница", "/koshnica"), ("Промоции", "/promocii"), ("Горива", "/goriva"), ("Проверка", "/proverka"), ("Източници", "/sources")]


def number(v, d=2):
    return "няма данни" if v is None else f"{Decimal(str(v)):,.{d}f}".replace(",", " ").replace(".", ",")


def eur(v, d=2):
    return "няма данни" if v is None else number(v, d) + " €"


def signed(v, d=1):
    if v is None:
        return "няма данни"
    s = number(abs(v), d)
    return ("+" if round(v, d) > 0 else "−" if round(v, d) < 0 else "") + s + "%"      # -0,04 shown to one decimal is 0,0%, not −0,0%


def date(v):
    if not v:
        return "няма данни"
    s = str(v)
    return s[8:10] + "." + s[5:7] + "." + s[:4]


def plural(n, one, many):
    return one if n == 1 else many


t.env.filters.update(num=number, eur=eur, signed=signed, date=date, plural=plural)


def render(request, name, **context):
    hub = os.environ.get("HUB_URL", "https://prozrachnost.denev.work")
    latest = Q.latest()
    return t.TemplateResponse(request=request, name=name, context=dict(
        v="1", hub_url=hub, feedback_button=Markup(feedback.BUTTON), support_link=Markup(feedback.support_link(hub)), menu=MENU,
        fresh=latest, help_json=Markup(json.dumps(H.HELP, ensure_ascii=False).replace("</", "<\\/")), **context))


def serial(data):
    return JSONResponse(json.loads(json.dumps(data, default=lambda v: float(v) if isinstance(v, Decimal) else str(v))))


def csv_response(rows, headers, name="pazar.csv"):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(headers)
    for r in rows:
        w.writerow(["" if r.get(h) is None else r.get(h) for h in headers])
    return Response(("﻿" + out.getvalue()).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def chart(series, unit):
    """The JSON of a line chart (static/app.js): points are [day, value]; a value of None is a break in the line."""
    return dict(unit=unit, series=[dict(s, points=[[str(p[0]), p[1]] for p in s["points"]]) for s in series])


# ---------- the basket of a page: one definition for the number, the chart, the table and the CSV ----------

def scope(k=None, c=None, d=None):
    basket = Q.resolve(k, c)
    day = Q.day_or_latest(d)
    return basket, day


@app.get("/healthz")
def health():
    with db.connect() as c:
        c.execute("SELECT 1")
    return {"ok": True}


@app.get("/favicon.svg")
def favicon():
    return Response((ROOT / "static/favicon.svg").read_text(), media_type="image/svg+xml")


@app.get("/", response_class=HTMLResponse)
def home(request: Request, k: str = None, c: str = None, d: str = None):
    basket, day = scope(k, c, d)
    if not day:
        return render(request, "empty.html", nav="Табло")
    summary = Q.day_summary(day)
    now = Q.cost(day, basket)
    ch = Q.change(basket, day, 30)
    up, down = Q.movers(day, basket, 30)
    return render(request, "home.html", nav="Табло", basket=basket, baskets=Q.baskets(), day=day, days=Q.built_days(), summary=summary, now=now,
                  ch=ch, up=up, down=down, chains=Q.chain_table(day, basket), coverage=Q.coverage())


@app.get("/api/index.json")
def index_api(k: str = None, c: str = None, first: str = None, last: str = None, chain: str = None):
    basket = Q.resolve(k, c)
    scope_, key = ("chain", chain) if chain else ("country", Q.NATIONAL)
    pts = Q.index_series(basket, first, last, scope_, key)
    return serial(chart([dict(name="Индекс на цените, 100 = първия ден", geo="BG", points=[(str(d), round(v, 2)) for d, v in pts])], "индекс"))


@app.get("/api/cost.json")
def cost_api(k: str = None, c: str = None, first: str = None, last: str = None):
    basket = Q.resolve(k, c)
    return serial(chart([dict(name="Сума от медианните цени на кошницата, €", geo="BG", points=[(str(d), round(v, 2)) for d, v in Q.cost_series(basket, first, last)])], "€"))


@app.get("/api/coverage.json")
def coverage_api():
    pts = Q.coverage()
    return serial(chart([dict(name="Вериги, подали файл", geo="BG", points=[(str(d), n) for d, n, _ in pts])], "вериги"))


@app.get("/export-index.csv")
def index_csv(k: str = None, c: str = None, first: str = None, last: str = None):
    basket = Q.resolve(k, c)
    idx, cost = dict(Q.index_series(basket, first, last)), dict(Q.cost_series(basket, first, last))
    days = sorted(set(idx) | set(cost))
    rows = [dict(day=str(d), index=None if d not in idx else round(idx[d], 4), cost_eur=cost.get(d), basket=basket.label, categories=",".join(map(str, basket.cats))) for d in days]
    return csv_response(rows, ["day", "index", "cost_eur", "basket", "categories"], "pazar-indeks.csv")


# ---------- the map ----------

@app.get("/karta", response_class=HTMLResponse)
def karta(request: Request, k: str = None, c: str = None, d: str = None):
    basket, day = scope(k, c, d)
    if not day:
        return render(request, "empty.html", nav="Карта")
    return render(request, "karta.html", nav="Карта", basket=basket, baskets=Q.baskets(), day=day, days=Q.built_days(), d=mapdata(basket, day))


def mapdata(basket, day):
    items = Q.municipality_levels(day, basket)
    shown = [i for i in items if i["v"] is not None]
    return dict(m="level", l="obshtini", y=str(day), o="bg", nuts=4, title="Ценово равнище на кошницата", plural="Общини", single="Община",
                unit="% спрямо средното за страната", digits=1, dataset="Колко струва (КЗП)", url="/sources", csv=f"/export-karta.csv?{basket.param}&d={day}",
                levels=[["obshtini", "Общини"]], years=[str(x) for x in reversed(Q.built_days())], countries={}, bg=0, base=None, denominator="current",
                definition=H.MAP_DEFINITION, basket=basket.label, shown=len(shown), total=len(items), param=basket.param,
                items=[dict(code=i["code"], name=i["name"], v=None if i["v"] is None else round(i["v"], 2), rank=i.get("rank"), why=i["why"],
                            stores=i["stores"], chains=i["chains"], cats=i["cats"]) for i in items])


@app.get("/api/karta.json")
def map_api(k: str = None, c: str = None, y: str = None):
    basket, day = scope(k, c, y)
    return serial(mapdata(basket, day))


@app.get("/export-karta.csv")
def map_csv(k: str = None, c: str = None, d: str = None):
    basket, day = scope(k, c, d)
    rows = [dict(municipality_id=i["code"], municipality=i["name"], oblast=i["oblast"], level_pct=None if i["v"] is None else round(i["v"], 2),
                 stores=i["stores"], chains=i["chains"], categories_with_price=i["cats"], note=i["why"], day=str(day), basket=basket.label, unit="% спрямо средното за страната")
            for i in Q.municipality_levels(day, basket)]
    return csv_response(rows, ["municipality_id", "municipality", "oblast", "level_pct", "stores", "chains", "categories_with_price", "note", "day", "basket", "unit"], "pazar-karta.csv")


# ---------- products ----------

@app.get("/produkti", response_class=HTMLResponse)
def products(request: Request, q: str = "", chain: str = "", category: int = None):
    found, more = Q.search_products(q, chain or None, category) if q.strip() or chain or category else ([], False)
    return render(request, "products.html", nav="Продукти", q=q, chain=chain, category=category, found=found, more=more, chains=Q.chains_list(Q.latest()) if Q.latest() else [],
                  cats=Q.categories())


@app.get("/produkti/{eik}/{code}", response_class=HTMLResponse)
def product_page(request: Request, eik: str, code: str, d: str = None):
    p = Q.product(eik, code)
    if not p:
        raise HTTPException(404, "Няма такъв продукт")
    day = Q.day_or_latest(d)
    return render(request, "product.html", nav="Продукти", p=p, day=day, stores=Q.product_stores(p["id"], day), days=Q.built_days())


@app.get("/api/product/{eik}/{code}.json")
def product_api(eik: str, code: str, first: str = None):
    p = Q.product(eik, code)
    if not p:
        raise HTTPException(404, "Няма такъв продукт")
    ser = Q.product_series(p["id"], first)
    pts = lambda i: [(str(r[0]), r[i]) for r in ser]
    return serial(chart([dict(name="Най-ниска цена в обект", geo="BG", points=pts(1), color="#9aa1aa"), dict(name="Медиана", geo="BG", points=pts(2)),
                         dict(name="Най-висока цена в обект", geo="BG", points=pts(3), color="#121417")], "€"))


@app.get("/export-product/{eik}/{code}.csv")
def product_csv(eik: str, code: str):
    p = Q.product(eik, code)
    if not p:
        raise HTTPException(404, "Няма такъв продукт")
    rows = [dict(day=str(r[0]), min_eur=r[1], median_eur=r[2], max_eur=r[3], stores=r[4], chain_eik=eik, code=code, name=p["name"]) for r in Q.product_series(p["id"])]
    return csv_response(rows, ["day", "min_eur", "median_eur", "max_eur", "stores", "chain_eik", "code", "name"], f"pazar-produkt-{eik}-{code}.csv")


# ---------- categories ----------

@app.get("/kategorii", response_class=HTMLResponse)
def categories(request: Request, d: str = None):
    day = Q.day_or_latest(d)
    return render(request, "categories.html", nav="Категории", day=day, rows=Q.category_table(day) if day else [], days=Q.built_days())


@app.get("/kategorii/{code}", response_class=HTMLResponse)
def category_page(request: Request, code: int, d: str = None):
    cat = Q.category(code)
    if not cat:
        raise HTTPException(404, "Няма такава категория")
    day = Q.day_or_latest(d)
    return render(request, "category.html", nav="Категории", cat=cat, day=day, chains=Q.category_by(day, code, "chain"), oblasti=Q.category_by(day, code, "oblast"),
                  days=Q.built_days())


@app.get("/api/category/{code}.json")
def category_api(code: int, chain: str = None):
    scope_, key = ("chain", chain) if chain else ("country", Q.NATIONAL)
    ser = Q.category_series(code, scope=scope_, key=key)
    pts = lambda i: [(str(r[0]), r[i]) for r in ser]
    return serial(chart([dict(name="Най-ниска цена", geo="BG", points=pts(1), color="#9aa1aa"), dict(name="Медиана", geo="BG", points=pts(2)),
                         dict(name="Най-висока цена", geo="BG", points=pts(3), color="#121417")], "€"))


@app.get("/export-category/{code}.csv")
def category_csv(code: int, d: str = None):
    day = Q.day_or_latest(d)
    rows = []
    for scope_ in ("chain", "oblast"):
        for r in Q.category_by(day, code, scope_):
            rows.append(dict(day=str(day), category=code, scope=scope_, key=r["key"], name=r["name"], median_eur=r["median"], min_eur=r["min"], max_eur=r["max"], prices=r["prices"], stores=r["stores"]))
    return csv_response(rows, ["day", "category", "scope", "key", "name", "median_eur", "min_eur", "max_eur", "prices", "stores"], f"pazar-kategoria-{code}.csv")


@app.get("/export-kategorii.csv")
def categories_csv(d: str = None):
    day = Q.day_or_latest(d)
    rows = [dict(r, day=str(day), group_bg=r["group"], name_bg=r["name"]) for r in Q.category_table(day)]
    return csv_response(rows, ["day", "code", "name_bg", "group_bg", "median", "min", "max", "prices", "stores", "chains", "promo_share"], "pazar-kategorii.csv")


# ---------- chains ----------

@app.get("/verigi", response_class=HTMLResponse)
def chains(request: Request, d: str = None):
    day = Q.day_or_latest(d)
    return render(request, "chains.html", nav="Вериги", day=day, rows=Q.chains_list(day) if day else [], cov=Q.coverage())


@app.get("/verigi/{eik}", response_class=HTMLResponse)
def chain_page(request: Request, eik: str, d: str = None):
    ch = Q.chain(eik)
    if not ch:
        raise HTTPException(404, "Няма такава верига")
    day = Q.day_or_latest(d)
    days = Q.chain_days(eik)
    return render(request, "chain.html", nav="Вериги", ch=ch, day=day, days=days, stores=Q.chain_stores(eik, day), summary=Q.chain_summary(eik, day),
                  missed=[x for x in days if not x["filed"]])


@app.get("/export-verigi.csv")
def chains_csv(d: str = None):
    day = Q.day_or_latest(d)
    rows = [dict(r, first=str(r["first"]), last=str(r["last"]), day=str(day)) for r in Q.chains_list(day)]
    return csv_response(rows, ["day", "eik", "name", "national", "stores", "days", "missed", "of", "copies", "truncated", "first", "last"], "pazar-verigi.csv")


@app.get("/export-verigi/{eik}.csv")
def chain_csv(eik: str):
    if not Q.chain(eik):
        raise HTTPException(404, "Няма такава верига")
    rows = [dict(r, day=str(r["day"])) for r in Q.chain_days(eik)]
    return csv_response(rows, ["day", "filed", "present", "valid", "bad", "stores", "copy_of", "truncated", "currency", "error"], f"pazar-verigi-{eik}.csv")


# ---------- the visitor's own basket ----------

@app.get("/koshnica", response_class=HTMLResponse)
def own_basket(request: Request, c: str = None, d: str = None):
    day = Q.day_or_latest(d)
    basket = Q.resolve(None, c) if c else None
    res = None
    if basket and day:
        res = dict(cost=Q.cost(day, basket), chains=Q.chain_table(day, basket), change=Q.change(basket, day, 30), top=Q.municipality_levels(day, basket))
    return render(request, "own.html", nav="Моята кошница", day=day, basket=basket, res=res, cats=Q.categories(), groups=Q.groups(), days=Q.built_days())


@app.get("/export-koshnica.csv")
def own_csv(c: str = None, k: str = None, d: str = None):
    basket, day = scope(k, c, d)
    t_ = Q.chain_table(day, basket)
    rows = [dict(day=str(day), basket=basket.label, categories=",".join(map(str, basket.cats)), chain_eik=r["eik"], chain=r["name"], cost_eur=r["value"],
                 vs_country_pct=r["vs_country"], categories_with_price=r["n_cats"], common_categories=t_["common"]) for r in t_["rows"]]
    return csv_response(rows, ["day", "basket", "categories", "chain_eik", "chain", "cost_eur", "vs_country_pct", "categories_with_price", "common_categories"], "pazar-koshnica.csv")


# ---------- promotions, fuel, verification, sources ----------

@app.get("/promocii", response_class=HTMLResponse)
def promotions(request: Request, d: str = None):
    day = Q.day_or_latest(d)
    return render(request, "promotions.html", nav="Промоции", day=day, rows=Q.promotions(day) if day else [], days=Q.built_days())


@app.get("/goriva", response_class=HTMLResponse)
def fuel(request: Request):
    return render(request, "fuel.html", nav="Горива", rows=Q.fuel())


@app.get("/api/fuel.json")
def fuel_api(geo: str = "BG", with_tax: int = 1):
    rows = [r for r in Q.fuel() if r[1] == geo]
    names = {"euro95": "Бензин А95", "diesel": "Дизел", "LPG": "Газ (LPG)"}
    series = []
    for fuel_ in ("euro95", "diesel", "LPG"):
        series.append(dict(name=names[fuel_], geo=geo, points=[(str(r[0]), r[3] if with_tax else r[4]) for r in rows if r[2] == fuel_]))
    return serial(chart(series, "€/л"))


@app.get("/export-goriva.csv")
def fuel_csv():
    rows = [dict(week=str(r[0]), geo=r[1], fuel=r[2], with_tax_eur_l=r[3], wo_tax_eur_l=r[4]) for r in Q.fuel()]
    return csv_response(rows, ["week", "geo", "fuel", "with_tax_eur_l", "wo_tax_eur_l"], "pazar-goriva.csv")


@app.get("/proverka", response_class=HTMLResponse)
def verification(request: Request):
    protocols = []
    for p in sorted((config.ROOT / "docs" / "verification").glob("*.csv"), reverse=True):
        rows = list(csv.DictReader(p.open(encoding="utf-8")))
        note = (p.with_suffix(".txt")).read_text(encoding="utf-8") if p.with_suffix(".txt").exists() else ""
        protocols.append(dict(name=p.stem, rows=rows, note=note))
    return render(request, "verification.html", nav="Проверка", ov=Q.verification_overview(), protocols=protocols, cov=Q.coverage(), flags=Q.FLAG_TEXT)


@app.get("/sources", response_class=HTMLResponse)
def sources(request: Request):
    with db.connect() as c:
        problems = checks.freshness(c)
    return render(request, "sources.html", nav="Източници", s=Q.sources(), problems=problems)


@app.get("/sources.json")
def sources_json():
    with db.connect() as c:
        problems = checks.freshness(c)
    s = Q.sources()
    return serial(dict(tool="pazar", problems=problems, days=[dict(status=r[0], n=r[1], first=r[2], last=r[3]) for r in s["days"]],
                       datasets=[dict(id="pazar.prices", title="Колко струва: цени на дребно по обект и ден", channel="file", licence=None, licence_note="не е посочен; чака писмен отговор на КЗП",
                                      distributed=False, coverage_from=config.FIRST_DAY, latest=str(Q.latest())),
                                 dict(id="pazar.fuel", title="Седмичен бюлетин на петрола на ЕК", channel="file", licence="CC BY 4.0 (правна бележка на Комисията)", distributed=True)]))


@app.get("/how", response_class=HTMLResponse)
def how(request: Request):
    return render(request, "how.html", nav="Източници", help=H.HELP)
