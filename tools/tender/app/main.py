"""Тендер web app: server-rendered Jinja pages over schema `live`; JS only for charts and the network."""
import csv
import io
import json
import re
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote, urlencode
import datetime as dt
from zoneinfo import ZoneInfo

import markdown
import markdown.extensions.toc
import os
import secrets

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from . import feedback, queries as Q
from .fields import fields, ocds_fields

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
        "value_suspect": "съмнителна стойност", "annex_suspect": "съмнителен анекс"}


SOFIA = ZoneInfo("Europe/Sofia")


def fnum(v, d=0):
    if v is None:
        return "няма данни"
    s = f"{float(v):,.{d}f}".replace(",", " ").replace(".", ",")
    return s


def feur(v):
    return "няма данни" if v is None else fnum(v) + " €"


def fbig(v):
    if v is None:
        return "няма данни"
    v = float(v)
    if v >= 1e9:
        return fnum(v / 1e9, 1).rstrip("0").rstrip(",") + " млрд. €"
    if v >= 1e6:
        return fnum(v / 1e6, 1).rstrip("0").rstrip(",") + " млн. €"
    return feur(v)


def to_eur(v, currency):
    """A published amount in EUR: BGN at the fixed rate 1.95583; None for other currencies."""
    from ingest.normalize import num
    v = num(v) if isinstance(v, str) else v
    if v is None:
        return None
    c = (currency or "BGN").strip().upper()
    if c == "EUR":
        return float(v)
    if c in ("BGN", "ЛВ", "ЛВ."):
        return float(v) / 1.95583
    return None


def feurc(v, currency, d=2):
    """Amount in EUR; an amount in a third currency is shown as published, with its code."""
    e = to_eur(v, currency)
    if e is not None:
        return fnum(e, d) + " €"
    return "няма данни" if v in (None, "") else f"{v} {currency or ''}".strip()


def fdate(v):
    """dd.mm.yyyy; a time with a zone (the offers, ЦАИС ЕОП dates are UTC) is shown on the Sofia calendar day:
    an offer submitted at 00:30 local time is not dated the day before."""
    if isinstance(v, dt.datetime) and v.tzinfo:
        v = v.astimezone(SOFIA)
    return v.strftime("%d.%m.%Y") if v else "няма данни"


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


def fperiod(r):
    y = lambda d: str(d)[:4] if d else "?"
    if r.get("valid_to"):
        return f"{y(r['valid_from'])}–{y(r['valid_to'])}"
    return f"{y(r['valid_from'])}–?" if r.get("uncertain_after") else f"от {y(r['valid_from'])}"


# status of a procedure or lot (normalize.py): label shown in lists and pages
STATE = {"contracted": "с договор", "cancelled": "прекратена", "unawarded": "без възложен договор", "open": "в ход",
         "no_contract": "без публикуван договор"}
T.env.globals.update(STATE=STATE)
T.env.filters.update(period=fperiod, eurc=feurc, eur=feur, big=fbig, num=fnum, date=fdate, tc=tc, role=lambda r: ROLE.get(r, r),
                     form=lambda f: FORM.get(f or "", f or ""), flag=lambda f: FLAG.get(f, ""),
                     json=lambda o: json.dumps(o, default=jdefault, ensure_ascii=False).replace("</", "<\\/"),
                     q=lambda s: quote(s, safe=":"), noembed=lambda h: re.sub(r"(?<=[?&])embed=1(&|$)", "", h).rstrip("?&"))


# static assets carry their newest mtime as ?v=, so a deploy is never served from a stale browser cache
ASSET_V = str(int(max(p.stat().st_mtime for p in (HERE / "static").glob("*.*"))))
T.env.globals.update(fields=fields, ocds_fields=ocds_fields, v=ASSET_V, feedback_button=Markup(feedback.BUTTON),
                     support_link=Markup(feedback.support_link(os.getenv("HUB_URL", "http://192.168.1.68:8001"))))
app.include_router(feedback.router("DenislavDenev/tender", os.getenv("TENDER_DATA", "/opt/tender/data")))


def page(request, name, **ctx):
    if request.query_params.get("format") == "csv" and "L" in ctx and "rows" in ctx:
        return csv_response(ctx["rows"], name.removeprefix("list_").removeprefix("_").split(".")[0].replace("_table", "") + ".csv")
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
    return page(request, "company.html", c=c, nav="Фирми")


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
    return page(request, "buyer.html", b=b, nav="Възложители")


@app.get("/contracts/{cid}", response_class=HTMLResponse)
def contract(request: Request, cid: str):
    as_json = cid.endswith(".json")
    c = Q.contract(cid.removesuffix(".json"))
    if not c:
        raise HTTPException(404)
    if as_json:
        return JSONResponse(json.loads(json.dumps(c, default=jdefault)))
    if not (c["lot"] and c["lot"]["offers"]):
        Q.offers_state(c.get("unp_tender_id"))  # not read yet: to the front of the reader's queue
    return page(request, "contract.html", c=c, nav="Договори")


@app.get("/tenders/{unp}", response_class=HTMLResponse)
def tender(request: Request, unp: str):
    t = Q.tender(unp)
    if not t:
        raise HTTPException(404)
    lots = Q.tender_lots(t)
    read = Q.offers_state(t["tender_id"]) if not any(l["offers"] for l in lots) else None
    # until the offers are read: the counts the contracts and the award notices without award report (open data),
    # the largest per lot
    n_reported = sum(max((c["offers_count"] or 0 for c in l["contracts"]), default=0) for l in lots) +         sum(a["offers_count"] or 0 for a in t["awards"])
    return page(request, "tender.html", t=t, lots=lots, events=Q.tender_events(t, lots), read=read, n_reported=n_reported, nav="Поръчки")


@app.get("/network.json")
def network(focus: str, view: str = "all", at: str | None = None, depth: int = 2):
    if view not in ("all", "ownership", "management") or not re.fullmatch(r"[pcfl]:[\w:-]+", focus) or not 1 <= depth <= 12:
        raise HTTPException(400)
    if at and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", at):
        raise HTTPException(400)
    return JSONResponse(json.loads(json.dumps(Q.network(focus, view, at, depth=depth, max_nodes=3000 if depth >= 3 else 500),
                                              default=jdefault)))


NODE_ID = re.compile(r"[pcfl]:[\w:-]+")


def node_of(kind, ref):
    """Search hit -> network node id (companies only when they have a 9-digit ЕИК partida)."""
    if kind == "person":
        return "p:" + ref
    if kind == "company" and re.fullmatch(r"eik:\d{9}(\d{4})?", ref):
        return "c:" + ref[4:13]
    return None


def conn_args(n, lim, budget):
    ids = list(dict.fromkeys(x for x in n if NODE_ID.fullmatch(x)))
    lim = min(int(lim), 100000) if lim.isdigit() and int(lim) > 0 else 50
    budget = 60.0 if budget == "long" else 10.0
    return ids, lim, budget


@app.get("/connect")
def connect_old(request: Request):
    return RedirectResponse("/svarzanosti" + (f"?{request.url.query}" if request.url.query else ""), status_code=301)


@app.get("/svarzanosti", response_class=HTMLResponse)
def connect_page(request: Request, n: list[str] = Query([]), q: str = "", active: str = "", hubs: str = "", lim: str = "", budget: str = ""):
    """„Свързаности“: pick people or companies, see every shortest way the Trade Register links each pair."""
    ids, lim_n, secs = conn_args(n, lim, budget)
    chosen = [Q.node_info(x) or {"id": x, "name": x, "kind": ""} for x in ids]
    hits = Q.search(q, kinds=["person", "company"]) if q.strip() else []
    cands = [dict(r, node=node_of(r["kind"], r["ref"])) for r in hits]
    cands = [c for c in cands if c["node"] and c["node"] not in ids][:12]
    res = Q.connections(ids, active=bool(active), skip_hubs=bool(hubs), limit=lim_n, budget=secs) if len(ids) >= 2 else None
    return page(request, "connect.html", ids=ids, chosen=chosen, cands=cands, q=q, res=res, active=active, hubs=hubs,
                lim=lim_n, budget=budget, nav="Свързаности")


@app.get("/connect.json")
def connect_json(n: list[str] = Query([]), active: str = "", hubs: str = "", lim: str = "", budget: str = ""):
    ids, lim_n, secs = conn_args(n, lim, budget)
    if len(ids) < 2:
        raise HTTPException(400)
    return JSONResponse(json.loads(json.dumps(Q.connections(ids, active=bool(active), skip_hubs=bool(hubs), limit=lim_n, budget=secs), default=jdefault)))


@app.get("/find.json")
def find_json(q: str = "", kinds: str = "", net: str = ""):
    """Autocomplete for every search field (static/combo.js). kinds: any of buyer,company,person,tender;
    net=1 keeps only people and companies in the registry network (item.node). Without kinds: the network
    search of the graph pages (people and companies with a node)."""
    ks = [k for k in kinds.split(",") if k in ("buyer", "company", "person", "tender")]
    net = bool(net) or not ks
    out = []
    for r in Q.search(q, kinds=ks or ["person", "company"], limit=40 if net else 10) if q.strip() else []:
        node = node_of(r["kind"], r["ref"])
        if net and not node:
            continue
        out.append({"kind": r["kind"], "ref": r["ref"], "node": node, "sub": r["sub"], "href": link(r["kind"], r["ref"]),
                    "label": r["label"] if r["kind"] == "tender" else tc(r["label"])})
    return JSONResponse(out[:10])


@app.get("/lab/explore", response_class=HTMLResponse)
def lab_explore(request: Request, node: str = "p:01M3D2GEZYEKNZ4B6HSXW20CPK"):
    """Prototype of the links explorer built from the research (steps, expand, hubs, hierarchy, connect)."""
    if not NODE_ID.fullmatch(node):
        raise HTTPException(400)
    return page(request, "lab_explore.html", node=node, info=Q.node_info(node), nav="")


@app.get("/lab/tender/{unp}", response_class=HTMLResponse)
def lab_tender(request: Request, unp: str, v: str = "a"):
    """Three designs of the procedure page with the offers (A comparison, B timeline, C table)."""
    t = Q.tender(unp)
    if not t:
        raise HTTPException(404)
    return page(request, "lab_tender.html", t=t, lots=Q.tender_lots(t), v=v if v in ("a", "b", "c") else "a", nav="Поръчки")


@app.get("/lab/vazby", response_class=HTMLResponse)
def lab_vazby(request: Request, node: str = "c:201090465"):
    """Prototype after rejstrik.penize.cz „Vizualizace vztahů“: icon nodes, +/- expansion, time slider."""
    if not NODE_ID.fullmatch(node):
        raise HTTPException(400)
    return page(request, "lab_vazby.html", node=node, info=Q.node_info(node), nav="")


@app.get("/lab/links", response_class=HTMLResponse)
def lab_links(request: Request, node: str = "p:01M3D2GEZYEKNZ4B6HSXW20CPK"):
    """Prototypes of the links section (four variants side by side); not linked from the menu."""
    if not re.fullmatch(r"[pcfl]:[\w:-]+", node):
        raise HTTPException(400)
    return page(request, "lab_links.html", node=node, info=Q.node_info(node), nav="")


@app.get("/lab/top-buyers.json")
def lab_top_buyers(nodes: str):
    ids = [x for x in nodes.split(",") if re.fullmatch(r"c:\d{9}", x)][:1000]
    return JSONResponse(json.loads(json.dumps(Q.top_buyers(ids), default=jdefault)))


class Listing:
    """Sort, filter and page state of a list page, read from the query string. `cols` maps a column
    key to (SQL expression, default direction); only those keys can be sorted on.
    embed=1 renders only the table (profiles load it in place and re-sort it there); per= sets the page size."""
    PER = 50

    def __init__(self, request, cols, default):
        self.qp = {k: v for k, v in request.query_params.items() if v != ""}
        self.path = request.url.path
        self.embed = self.qp.get("embed") == "1"
        per = self.qp.get("per", "")
        self.PER = min(int(per), 200) if per.isdigit() and int(per) >= 5 else Listing.PER
        if self.qp.get("format") == "csv":  # the whole filtered list as CSV (page() answers with the file)
            self.PER, self.qp["offset"] = 100_000, "0"
        self.cols = cols
        self.sort = self.qp.get("sort") if self.qp.get("sort") in cols else default
        d = self.qp.get("dir")
        self.dir = d if d in ("asc", "desc") else cols[self.sort][1]
        off = self.qp.get("offset", "0")
        self.offset = int(off) if off.isdigit() else 0
        self.where, self.args, self.total = [], [], 0

    def get(self, key):
        return self.qp.get(key, "")

    def add(self, cond, *args):
        self.where.append(cond)
        self.args.extend(args)

    def fetch(self, base):
        """`base` is a SELECT with a {where} placeholder; returns one page of rows and sets total."""
        sql = base.replace("{where}", " AND ".join(self.where) or "true")
        order = f"{self.cols[self.sort][0]} {self.dir.upper()} NULLS LAST"
        rows = Q.rows(f"{sql} ORDER BY {order}, 1 LIMIT {self.PER} OFFSET %s", *self.args, self.offset)
        self.total = Q.one(f"SELECT count(*) n FROM ({sql}) x", *self.args)["n"]
        return rows

    def href(self, **kw):
        q = {**self.qp, **kw}
        return self.path + "?" + urlencode({k: v for k, v in q.items() if v not in (None, "")})

    def hide(self, col):
        return col in self.get("hide").split(",")

    def sort_href(self, key):
        d = ("asc" if self.dir == "desc" else "desc") if key == self.sort else self.cols[key][1]
        return self.href(sort=key, dir=d, offset=None)

    def aria(self, key):
        return ("ascending" if self.dir == "asc" else "descending") if key == self.sort else "none"

    def pages(self):
        prev = self.href(offset=(self.offset - self.PER) or None) if self.offset else None
        nxt = self.href(offset=self.offset + self.PER) if self.offset + self.PER < self.total else None
        return prev, nxt


def period(L, col):
    """from/to dates (YYYY-MM-DD) or a `days` preset, applied to the date expression `col`."""
    if L.get("days").isdigit():
        L.qp["from"] = (dt.date.today() - dt.timedelta(days=int(L.qp.pop("days")))).isoformat()
    for key, op in (("from", ">="), ("to", "<=")):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", L.get(key)):
            L.add(f"{col} {op} %s", L.get(key))
        else:
            L.qp.pop(key, None)


def contains(L, key, *exprs):
    v = L.get(key).strip()
    if v:
        L.add("(" + " OR ".join(f"{e} ILIKE %s" for e in exprs) + ")", *[f"%{v}%"] * len(exprs))


# the map's levels: municipality, oblast (NUTS 3), planning region (NUTS 2), macro-region (NUTS 1)
LEVEL = {"muni": "bp.municipality", "oblast": "m.nuts3", "region": "m.nuts2", "macro": "m.nuts1"}
AREA = re.compile(r"\d{9,13}|BG\d{1,3}")


def map_filter(request):
    """The contract filters of /contracts (period, procedure, sector, one offer, over estimate, clean),
    plus scope=municipal (only the municipality and its units)."""
    L = Listing(request, {"x": ("1", "asc")}, "x")
    contract_filters(L)
    if L.get("scope") == "municipal":
        L.add("bp.basis = 'municipality'")
    return " AND ".join(L.where) or "true", L.args


def area_cond(aid):
    if not AREA.fullmatch(aid):
        raise HTTPException(400)
    return ("bp.municipality = %s", [aid]) if aid[0].isdigit() else ("%s IN (m.nuts3, m.nuts2, m.nuts1)", [aid])


@app.get("/map", response_class=HTMLResponse)
def map_page(request: Request):
    return page(request, "map.html", **choices(), nav="Карта")


@app.get("/map.json")
def map_data(request: Request, level: str = "muni"):
    """Contracts per area of the buyer (methodology 7), plus what could not be placed."""
    where, args = map_filter(request)
    g = LEVEL.get(level, LEVEL["muni"])
    areas = Q.rows(f"""SELECT {g} id, count(*) n, round({Q.SUM}) eur, count(DISTINCT c.buyer_eik) buyers, {SINGLE} single_pct
        FROM live.contract c JOIN live.buyer_place bp ON bp.eik = c.buyer_eik JOIN live.municipality m ON m.id = bp.municipality
        WHERE {where} GROUP BY 1""", *args)
    cover = Q.one(f"""SELECT round({Q.SUM}) eur, round(sum(c.amount_eur) FILTER (WHERE NOT c.is_framework AND bp.eik IS NOT NULL)) placed
        FROM live.contract c LEFT JOIN live.buyer_place bp ON bp.eik = c.buyer_eik
        WHERE {where.replace("bp.basis = 'municipality'", "true")}""", *args)
    return JSONResponse(json.loads(json.dumps({"areas": areas, "cover": cover, "level": level}, default=jdefault)))


NUTS_NAME = {"BG31": "Северозападен район", "BG32": "Северен централен район", "BG33": "Североизточен район",
             "BG34": "Югоизточен район", "BG41": "Югозападен район", "BG42": "Южен централен район",
             "BG3": "Северна и Югоизточна България", "BG4": "Югозападна и Южна централна България"}


@app.get("/places/{aid}", response_class=HTMLResponse)
def place(request: Request, aid: str):
    """Profile of a municipality (by its ЕИК), an oblast, a planning region or a macro-region (NUTS code)."""
    cond, a0 = area_cond(aid)
    if aid[0].isdigit():
        m = Q.one("SELECT name_bg name, oblast, nuts3, nuts2 FROM live.municipality WHERE id = %s", aid)
        kind, name = "Община", m and m["name"]
    elif len(aid) == 5:
        m = Q.one("SELECT DISTINCT oblast name, nuts3, nuts2 FROM live.municipality WHERE nuts3 = %s", aid)
        kind, name = "Област", m and m["name"]
    else:
        m, kind, name = {"nuts2": aid[:4] if len(aid) == 4 else None}, "Район за планиране" if len(aid) == 4 else "Макрорайон", NUTS_NAME.get(aid)
    if not name:
        raise HTTPException(404)
    up = [(f"/places/{m['nuts3']}", "област " + m["oblast"])] if aid[0].isdigit() else []
    if m.get("nuts2") and len(aid) > 4:
        up.append((f"/places/{m['nuts2']}", NUTS_NAME[m["nuts2"]]))
    base = "FROM live.contract c JOIN live.buyer_place bp ON bp.eik = c.buyer_eik JOIN live.municipality m ON m.id = bp.municipality"
    stats = Q.one(f"SELECT count(*) n, round({Q.SUM}) eur, count(DISTINCT c.buyer_eik) buyers, {SINGLE} single_pct {base} WHERE {cond}", *a0)
    buyers = Q.rows(f"""SELECT c.buyer_eik eik, max(b.name) name, count(*) n, round({Q.SUM}) eur {base} LEFT JOIN live.buyer b ON b.eik = c.buyer_eik
        WHERE {cond} GROUP BY 1 ORDER BY eur DESC NULLS LAST LIMIT 8""", *a0)
    suppliers = Q.rows(f"""SELECT s.party_key key, max(s.name) name, count(DISTINCT c.id) n, round({Q.SUM}) eur
        {base} JOIN live.contract_supplier s ON s.contract_id = c.id WHERE {cond} GROUP BY 1 ORDER BY eur DESC NULLS LAST LIMIT 8""", *a0)
    by_year = Q.rows(f"""SELECT extract(year FROM c.effective_date)::int y, count(*) n, round({Q.SUM}) eur, {SINGLE} single_pct
        {base} WHERE {cond} AND {Q.YEARS} GROUP BY 1 ORDER BY 1""", *a0)
    param = "municipality" if aid[0].isdigit() else "area"
    return page(request, "place.html", aid=aid, place_name=name, kind=kind, up=up, s=stats, buyers=buyers, suppliers=suppliers,
                by_year=by_year, param=param, nav="Карта")


@app.get("/map/{aid}.json")
def map_detail(request: Request, aid: str):
    cond, a0 = area_cond(aid)
    where, args = map_filter(request)
    base = "FROM live.contract c JOIN live.buyer_place bp ON bp.eik = c.buyer_eik JOIN live.municipality m ON m.id = bp.municipality"
    buyers = Q.rows(f"""SELECT c.buyer_eik eik, max(b.name) name, max(bp.basis) basis, count(*) n, round({Q.SUM}) eur, {SINGLE} single_pct
        {base} LEFT JOIN live.buyer b ON b.eik = c.buyer_eik
        WHERE {cond} AND {where} GROUP BY 1 ORDER BY eur DESC NULLS LAST LIMIT 12""", *a0, *args)
    suppliers = Q.rows(f"""SELECT s.party_key key, max(s.name) name, count(DISTINCT c.id) n, round({Q.SUM}) eur
        {base} JOIN live.contract_supplier s ON s.contract_id = c.id
        WHERE {cond} AND {where} GROUP BY 1 ORDER BY eur DESC NULLS LAST LIMIT 12""", *a0, *args)
    return JSONResponse(json.loads(json.dumps({"buyers": buyers, "suppliers": suppliers}, default=jdefault)))


@app.get("/buyers", response_class=HTMLResponse)
def buyers(request: Request):
    L = Listing(request, {"name": ("b.name", "asc"), "type": ("b.type", "asc"), "tenders": ("s.tenders", "desc"), "contracts": ("s.contracts", "desc"),
                          "suppliers": ("s.suppliers", "desc"), "eur": ("s.amount_eur", "desc")}, "eur")
    contains(L, "q", "b.name", "b.eik")
    if L.get("type"):
        L.add("b.type = %s", L.get("type"))
    rows = L.fetch("""SELECT b.eik, b.name, b.type, s.tenders, s.contracts, s.amount_eur, s.suppliers FROM live.buyer b
                      JOIN live.buyer_stats s USING (eik) WHERE {where}""")
    types = Q.cached("buyer_types", lambda: [r["type"] for r in Q.rows(
        "SELECT DISTINCT type FROM live.buyer WHERE type IS NOT NULL ORDER BY 1")])
    return page(request, "list_buyers.html", L=L, rows=rows, types=types, nav="Възложители")


@app.get("/companies", response_class=HTMLResponse)
def companies(request: Request):
    L = Listing(request, {"name": ("c.name", "asc"), "seat": ("c.seat", "asc"), "contracts": ("s.contracts", "desc"),
                          "buyers": ("s.buyers", "desc"), "eur": ("s.amount_eur", "desc"),
                          "last": ("s.last_contract", "desc")}, "eur")
    contains(L, "q", "c.name", "c.eik")
    contains(L, "seat", "c.seat")
    if L.get("tag"):
        L.add("c.key IN (SELECT entity_id FROM live.tag WHERE entity_type = 'company' AND code = %s)", L.get("tag"))
    rows = L.fetch("""SELECT c.key, c.eik, c.name, c.legal_form, c.seat, s.contracts, s.amount_eur, s.buyers, s.last_contract,
                        (SELECT t.reason FROM live.tag t WHERE t.entity_type = 'company' AND t.entity_id = c.key AND t.code = %s) reason
                      FROM live.company c JOIN live.company_stats s USING (key) WHERE {where}""".replace("%s", "'" + re.sub(r"[^a-z_]", "", L.get("tag")) + "'"))
    tags = Q.cached("tag_defs", lambda: Q.rows("SELECT d.code, d.label, (SELECT count(*) FROM live.tag t WHERE t.code = d.code) n FROM live.tag_def d ORDER BY 1"))
    return page(request, "list_companies.html", L=L, rows=rows, tags=tags, nav="Фирми")


@app.get("/flags", response_class=HTMLResponse)
def flags_index(request: Request):
    """Every automatic tag: what it measures, how many companies have it, a link to them."""
    defs = Q.rows("""SELECT d.*, (SELECT count(*) FROM live.tag t WHERE t.code = d.code) n FROM live.tag_def d
                     ORDER BY d.kind DESC, n DESC""")
    return page(request, "flags.html", defs=defs, nav="Фирми")


@app.get("/persons", response_class=HTMLResponse)
def persons(request: Request):
    L = Listing(request, {"name": ("p.name", "asc"), "companies": ("s.companies", "desc"), "active": ("s.active", "desc"),
                          "contracts": ("s.contracts", "desc"), "eur": ("s.amount_eur", "desc"),
                          "since": ("s.first_role", "asc")}, "eur")
    contains(L, "q", "p.name")
    if L.get("with") == "contracts":
        L.add("s.contracts > 0")
    rows = L.fetch("""SELECT p.id, p.name, s.companies, s.active, s.contracts, s.amount_eur, s.first_role
                      FROM live.person p JOIN live.person_stats s USING (id) WHERE {where}""")
    return page(request, "list_persons.html", L=L, rows=rows, nav="Лица")


def procedure_types():
    return [r["p"] for r in Q.rows("SELECT DISTINCT procedure_type p FROM live.contract WHERE procedure_type IS NOT NULL ORDER BY 1")]


SECTOR_NAMES = """SELECT DISTINCT ON (left(cpv, 2)) left(cpv, 2) d, cpv_description name FROM live.contract
                  WHERE cpv ~ '^[0-9]{2}000000' ORDER BY left(cpv, 2), cpv"""


def choices():
    return {"procedures": Q.cached("procs", procedure_types), "sectors": Q.cached("sectors", lambda: Q.rows(SECTOR_NAMES))}


def contract_filters(L, alias="c"):
    period(L, f"{alias}.effective_date")
    if L.get("procedure"):
        L.add(f"{alias}.procedure_type = %s", L.get("procedure"))
    if re.fullmatch(r"\d{2}", L.get("sector")):
        L.add(f"left({alias}.cpv, 2) = %s", L.get("sector"))
    if L.get("single"):
        L.add(f"{alias}.offers_count = 1")
    if L.get("over"):
        L.add(f"{alias}.estimate_ratio >= 2 AND {alias}.value_flag IN ('ok', 'review')")
    if L.get("clean"):
        L.add(f"NOT {alias}.is_framework AND {alias}.value_flag = 'ok'")


@app.get("/contracts", response_class=HTMLResponse)
def contracts(request: Request):
    L = Listing(request, {"date": ("c.effective_date", "desc"), "subject": ("c.subject", "asc"), "buyer": ("b.name", "asc"),
                          "supplier": ("c.supplier_display", "asc"), "offers": ("c.offers_count", "asc"),
                          "eur": ("c.amount_eur", "desc")}, "date")
    contains(L, "q", "c.subject", "c.unp")
    contains(L, "who", "b.name", "c.supplier_display", "c.buyer_eik")
    if L.get("buyer"):
        L.add("c.buyer_eik = %s", L.get("buyer"))
    if L.get("company"):
        L.add("c.id IN (SELECT contract_id FROM live.contract_supplier WHERE party_key = %s)", L.get("company"))
    if L.get("municipality"):
        L.add("c.buyer_eik IN (SELECT eik FROM live.buyer_place WHERE municipality = %s)", L.get("municipality"))
    if AREA.fullmatch(L.get("area")):  # oblast / region / macro-region (NUTS code) of the buyer
        L.add("""c.buyer_eik IN (SELECT bp.eik FROM live.buyer_place bp JOIN live.municipality m ON m.id = bp.municipality
                 WHERE %s IN (m.nuts3, m.nuts2, m.nuts1))""", L.get("area"))
    if L.get("person"):  # contracts of the companies where the person had a role on the contract date
        L.add("""c.id IN (SELECT k.contract_id FROM live.edge e JOIN live.node_contract k ON k.node = e.company
                 JOIN live.contract c2 ON c2.id = k.contract_id WHERE e.holder = 'p:' || %s
                 AND c2.effective_date >= e.valid_from AND (e.valid_to IS NULL OR c2.effective_date < e.valid_to))""", L.get("person"))
    contract_filters(L)
    rows = L.fetch("""SELECT c.id, c.unp, c.effective_date, c.subject, c.buyer_eik, b.name buyer, c.supplier_display supplier,
                        s0.party_key supplier_key, round(c.amount_eur) eur, c.offers_count, c.value_flag, c.is_framework, c.estimate_ratio,
                        c.annex_count, c.awarded_to_group, c.procedure_type
                      FROM live.contract c LEFT JOIN live.buyer b ON b.eik = c.buyer_eik
                      LEFT JOIN live.contract_supplier s0 ON s0.contract_id = c.id AND s0.position = 0 WHERE {where}""")
    return page(request, "_contracts_table.html" if L.embed else "list_contracts.html", L=L, rows=rows, **choices(), nav="Договори")


@app.get("/tenders", response_class=HTMLResponse)
def tenders(request: Request):
    day = "coalesce(t.published_at::date, s.first_contract)"
    L = Listing(request, {"date": (day, "desc"), "subject": ("t.subject", "asc"), "buyer": ("b.name", "asc"),
                          "procedure": ("t.procedure_type", "asc"), "contracts": ("s.contracts", "desc"),
                          "estimate": ("t.estimated_eur", "desc"), "eur": ("s.amount_eur", "desc")}, "date")
    contains(L, "q", "t.subject", "t.unp")
    contains(L, "who", "b.name", "t.buyer_eik")
    if L.get("buyer"):
        L.add("t.buyer_eik = %s", L.get("buyer"))
    period(L, day)
    if L.get("procedure"):
        L.add("t.procedure_type = %s", L.get("procedure"))
    if re.fullmatch(r"\d{2}", L.get("sector")):
        L.add("left(t.cpv, 2) = %s", L.get("sector"))
    if L.get("state") in STATE:
        L.add("t.state = %s", L.get("state"))
    rows = L.fetch(f"""SELECT t.unp, {day} AS day, t.subject, t.buyer_eik, b.name buyer, t.procedure_type, t.estimated_eur,
                         t.is_cancelled, t.state, s.contracts, s.amount_eur, s.single_bid
                       FROM live.tender t LEFT JOIN live.tender_stats s USING (unp) LEFT JOIN live.buyer b ON b.eik = t.buyer_eik
                       WHERE {{where}}""")
    return page(request, "_tenders_table.html" if L.embed else "list_tenders.html", L=L, rows=rows, **choices(), nav="Поръчки")


ROLLUP = {"name": ("name", "asc"), "n": ("n", "desc"), "eur": ("eur", "desc"), "single": ("single_pct", "desc")}
SINGLE = "round(100.0 * count(*) FILTER (WHERE c.offers_count = 1) / nullif(count(*) FILTER (WHERE c.offers_count IS NOT NULL), 0), 1)"


@app.get("/procedures", response_class=HTMLResponse)
def procedures(request: Request):
    L = Listing(request, ROLLUP, "n")
    period(L, "c.effective_date")
    L.add("c.procedure_type IS NOT NULL")
    rows = L.fetch(f"""SELECT * FROM (SELECT c.procedure_type k, c.procedure_type name, count(*) n, round({Q.SUM}) eur,
        {SINGLE} single_pct FROM live.contract c WHERE {{where}} GROUP BY 1) g""")
    return page(request, "list_rollup.html", L=L, rows=rows, title="Процедури", kind="procedure", nav="")


@app.get("/sectors", response_class=HTMLResponse)
def sectors(request: Request):
    L = Listing(request, ROLLUP, "eur")
    period(L, "c.effective_date")
    L.add("c.cpv ~ '^[0-9]{2}'")
    rows = L.fetch(f"""SELECT g.k, coalesce(sn.name, 'CPV ' || g.k) name, g.n, g.eur, g.single_pct FROM (
        SELECT left(c.cpv, 2) k, count(*) n, round({Q.SUM}) eur, {SINGLE} single_pct FROM live.contract c
        WHERE {{where}} GROUP BY 1) g LEFT JOIN ({SECTOR_NAMES}) sn ON sn.d = g.k""")
    return page(request, "list_rollup.html", L=L, rows=rows, title="Сектори", kind="sector", nav="")


# ---------- flows: one calendar year, drilled into one step at a time (contracts: sector -> buyer ->
# supplier; payments: first-level budget organisation -> organisation -> receiver) ----------

FLOW_DIMS = {
    "contracts": {"sector": "left(c.cpv, 2)", "buyer": "c.buyer_eik", "supplier": "s.party_key"},
    "payments": {"primary": "p.primary_org_code", "org": "p.organization",
                 "receiver": "CASE WHEN p.is_person THEN 'persons' WHEN p.company_key IS NOT NULL THEN p.company_key ELSE 'name:' || p.receiver_name END"},
}
FLOW_TOP = 12


def flow_base(mode, year, filters):
    if mode == "payments":
        where = ["p.settlement_date >= make_date(%s, 1, 1)", "p.settlement_date < make_date(%s + 1, 1, 1)", "p.amount_eur > 0"]
        args = [year, year]
        base = "FROM live.payment p"
        val = "p.amount_eur"
    else:
        # contracts once each (first supplier), no framework ceilings, no values the checks mark as errors
        where = ["c.effective_date >= make_date(%s, 1, 1)", "c.effective_date < make_date(%s + 1, 1, 1)", "NOT c.is_framework",
                 "c.value_flag = 'ok'", "c.amount_eur > 0"]
        args = [year, year]
        base = "FROM live.contract c JOIN live.contract_supplier s ON s.contract_id = c.id AND s.position = 0"
        val = "c.amount_eur"
    for k, v in filters.items():
        where.append(f"{FLOW_DIMS[mode][k]} = %s")
        args.append(v)
    return base, " AND ".join(where), args, val


@app.get("/flows", response_class=HTMLResponse)
def flows_page(request: Request):
    return page(request, "flows.html", year_now=dt.date.today().year, nav="Потоци")


@app.get("/flows.json")
def flows_json(request: Request, year: int = 0, mode: str = "contracts"):
    """One step of the drill-down: the path chosen so far (filters in the order of the dimensions), then the
    next dimension split into its largest FLOW_TOP parts and "Други". Every part is also in `all` (up to
    500), so the page can list and open each of them; the money behind any node or link is in the lists."""
    mode = mode if mode in FLOW_DIMS else "contracts"
    year = year or dt.date.today().year - 1
    dims = FLOW_DIMS[mode]
    filters = {k: request.query_params[k] for k in dims if request.query_params.get(k)}
    nxt = next((k for k in dims if k not in filters), None)
    base, where, args, val = flow_base(mode, year, filters)
    total = Q.one(f"SELECT round(sum({val})) v, count(*) n {base} WHERE {where}", *args)
    parts = Q.rows(f"""SELECT {dims[nxt]} k, round(sum({val})) v, count(*) n, count(*) OVER () parts {base}
        WHERE {where} AND {dims[nxt]} IS NOT NULL GROUP BY 1 ORDER BY v DESC NULLS LAST LIMIT 500""", *args) if nxt else []
    names = flow_names(mode, {nxt: [r["k"] for r in parts]}) if nxt else {}
    shown = sum(float(r["v"] or 0) for r in parts[:FLOW_TOP])
    path_names = flow_names(mode, {k: [v] for k, v in filters.items()})
    # the whole of each step of the path (the year, the sector, the buyer in it), for the labels
    keys = list(filters)
    sums = [float(Q.one(f"SELECT round(sum({v2})) v {b2} WHERE {w2}", *a2)["v"] or 0)
            for b2, w2, a2, v2 in (flow_base(mode, year, {k: filters[k] for k in keys[:i]}) for i in range(len(keys)))] + [float(total["v"] or 0)]
    return JSONResponse(json.loads(json.dumps({
        "mode": mode, "year": year, "next": nxt, "total": total,
        "whole": sums[0],
        "path": [{"dim": k, "key": v, "label": path_names.get(f"{k}|{v}", v), "v": sums[i + 1]} for i, (k, v) in enumerate(filters.items())],
        "all": [{"key": r["k"], "label": names.get(f"{nxt}|{r['k']}", r["k"]), "v": float(r["v"] or 0), "n": r["n"]} for r in parts],
        "parts": parts[0]["parts"] if parts else 0, "top": FLOW_TOP,
        "rest": max(0.0, float(total["v"] or 0) - shown), "other_label": names.get(f"{nxt}|~") if nxt else None,
    }, default=jdefault)))


def flow_names(mode, keys):
    out = {}
    for dim, ks in keys.items():
        ks = [k for k in ks if k and k != "~"]
        other = {"sector": "Други сектори", "buyer": "Други възложители", "supplier": "Други изпълнители",
                 "primary": "Други първостепенни", "org": "Други разпоредители", "receiver": "Други получатели"}[dim]
        out[f"{dim}|~"] = other
        if dim == "sector":
            sn = {r["d"]: r["name"] for r in Q.cached("sectors", lambda: Q.rows(SECTOR_NAMES))}
            out.update({f"sector|{k}": sn.get(k, "CPV " + k) for k in ks})
        elif dim == "buyer":
            out.update({f"buyer|{r['eik']}": tc(r["name"]) for r in Q.rows("SELECT eik, name FROM live.buyer WHERE eik = ANY(%s)", ks)})
        elif dim == "supplier":
            out.update({f"supplier|{r['key']}": tc(r["name"]) for r in Q.rows("SELECT key, name FROM live.company WHERE key = ANY(%s)", ks)})
            out.update({f"supplier|{k}": tc(k[5:]) for k in ks if k.startswith("name:")})
        elif dim == "primary":
            out.update({f"primary|{r['k']}": r["n"] for r in Q.rows(
                "SELECT DISTINCT ON (primary_org_code) primary_org_code k, primary_organization n FROM live.payment WHERE primary_org_code = ANY(%s)", ks)})
        elif dim == "org":
            out.update({f"org|{k}": k for k in ks})
        else:
            out.update({f"receiver|{r['key']}": tc(r["name"]) for r in Q.rows("SELECT key, name FROM live.company WHERE key = ANY(%s)", ks)})
            out.update({f"receiver|{k}": k[5:] for k in ks if k.startswith("name:")})
            out["receiver|persons"] = "Физически лица"
    return out


@app.get("/payments", response_class=HTMLResponse)
def payments(request: Request):
    """Budget payments (СЕБРА) with filters; embed=1 for the flows page and profiles."""
    L = Listing(request, {"date": ("p.settlement_date", "desc"), "receiver": ("p.receiver_name", "asc"), "org": ("p.organization", "asc"),
                          "eur": ("p.amount_eur", "desc")}, "date")
    period(L, "p.settlement_date")
    if L.get("year").isdigit():
        L.add("p.settlement_date >= make_date(%s, 1, 1) AND p.settlement_date < make_date(%s + 1, 1, 1)", int(L.get("year")), int(L.get("year")))
    for key, col in (("primary", "p.primary_org_code"), ("org", "p.organization"), ("company", "p.company_key"), ("buyer", "p.buyer_eik")):
        if L.get(key):
            L.add(f"{col} = %s", L.get(key))
    r = L.get("receiver")
    if r == "persons":
        L.add("p.is_person")
    elif r.startswith("name:"):
        L.add("p.receiver_name = %s AND p.company_key IS NULL", r[5:])
    elif r:
        L.add("p.company_key = %s", r)
    contains(L, "q", "p.receiver_name", "p.reason", "p.organization")
    rows = L.fetch("""SELECT p.settlement_date, p.receiver_name, p.is_person, p.company_key, p.amount_eur, p.reason, p.pay_code,
                        p.organization, p.primary_organization, p.buyer_eik FROM live.payment p WHERE {where}""")
    return page(request, "_payments_table.html" if L.embed else "list_payments.html", L=L, rows=rows, nav="Потоци")


@app.get("/kak-raboti", response_class=HTMLResponse)
def how(request: Request):
    md = (HERE.parent / "docs" / "how.md").read_text(encoding="utf-8")
    return page(request, "prose.html", title="Как работи", html=md_html(md), nav="")


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
