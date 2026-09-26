"""Тендер web app: server-rendered Jinja pages over schema `live`; JS only for charts and the network."""
import csv
import io
import json
import re
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote, urlencode
import datetime as dt

import markdown
import markdown.extensions.toc
import os
import secrets

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import queries as Q
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
    return "—" if v in (None, "") else f"{v} {currency or ''}".strip()


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


def fperiod(r):
    y = lambda d: str(d)[:4] if d else "?"
    if r.get("valid_to"):
        return f"{y(r['valid_from'])}–{y(r['valid_to'])}"
    return f"{y(r['valid_from'])}–?" if r.get("uncertain_after") else f"от {y(r['valid_from'])}"


T.env.filters.update(period=fperiod, eurc=feurc, eur=feur, big=fbig, num=fnum, date=fdate, tc=tc, role=lambda r: ROLE.get(r, r),
                     form=lambda f: FORM.get(f or "", f or ""), flag=lambda f: FLAG.get(f, ""),
                     json=lambda o: json.dumps(o, default=jdefault, ensure_ascii=False).replace("</", "<\\/"),
                     q=lambda s: quote(s, safe=":"), noembed=lambda h: re.sub(r"(?<=[?&])embed=1(&|$)", "", h).rstrip("?&"))


# static assets carry their newest mtime as ?v=, so a deploy is never served from a stale browser cache
ASSET_V = str(int(max(p.stat().st_mtime for p in (HERE / "static").glob("*.*"))))
T.env.globals.update(fields=fields, ocds_fields=ocds_fields, v=ASSET_V)


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
    return page(request, "contract.html", c=c, nav="Договори")


@app.get("/tenders/{unp}", response_class=HTMLResponse)
def tender(request: Request, unp: str):
    t = Q.tender(unp)
    if not t:
        raise HTTPException(404)
    return page(request, "tender.html", t=t, nav="Поръчки")


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


def map_filter(frm, to, scope):
    where, args = [], []
    for v, op in ((frm, ">="), (to, "<=")):
        if v:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                raise HTTPException(400)
            where.append(f"c.effective_date {op} %s")
            args.append(v)
    if scope == "municipal":
        where.append("bp.basis = 'municipality'")
    return " AND ".join(where) or "true", args


@app.get("/map", response_class=HTMLResponse)
def map_page(request: Request):
    return page(request, "map.html", nav="Карта")


@app.get("/map.json")
def map_data(frm: str = "", to: str = "", scope: str = "all"):
    """Contracts per municipality of the buyer (methodology 7), plus what could not be placed."""
    where, args = map_filter(frm, to, scope)
    munis = Q.rows(f"""SELECT bp.municipality id, count(*) n, round({Q.SUM}) eur, count(DISTINCT c.buyer_eik) buyers,
            {SINGLE} single_pct
        FROM live.contract c JOIN live.buyer_place bp ON bp.eik = c.buyer_eik WHERE {where} GROUP BY 1""", *args)
    cover = Q.one(f"""SELECT round({Q.SUM}) eur, round(sum(c.amount_eur) FILTER (WHERE NOT c.is_framework AND bp.eik IS NOT NULL)) placed
        FROM live.contract c LEFT JOIN live.buyer_place bp ON bp.eik = c.buyer_eik
        WHERE {where.replace("bp.basis = 'municipality'", "true")}""", *args)
    names = {r["id"]: r["name_bg"] for r in Q.cached("munis", lambda: Q.rows("SELECT id, name_bg FROM live.municipality"))}
    for m in munis:
        m["name"] = names.get(m["id"], m["id"])
    return JSONResponse(json.loads(json.dumps({"munis": munis, "cover": cover}, default=jdefault)))


@app.get("/map/{mid}.json")
def map_detail(mid: str, frm: str = "", to: str = "", scope: str = "all"):
    if not re.fullmatch(r"\d{9,13}", mid):
        raise HTTPException(400)
    where, args = map_filter(frm, to, scope)
    buyers = Q.rows(f"""SELECT c.buyer_eik eik, max(b.name) name, max(bp.basis) basis, count(*) n, round({Q.SUM}) eur,
            {SINGLE} single_pct
        FROM live.contract c JOIN live.buyer_place bp ON bp.eik = c.buyer_eik LEFT JOIN live.buyer b ON b.eik = c.buyer_eik
        WHERE bp.municipality = %s AND {where} GROUP BY 1 ORDER BY eur DESC NULLS LAST LIMIT 12""", mid, *args)
    suppliers = Q.rows(f"""SELECT s.party_key key, max(s.name) name, count(DISTINCT c.id) n, round({Q.SUM}) eur
        FROM live.contract c JOIN live.buyer_place bp ON bp.eik = c.buyer_eik JOIN live.contract_supplier s ON s.contract_id = c.id
        WHERE bp.municipality = %s AND {where} GROUP BY 1 ORDER BY eur DESC NULLS LAST LIMIT 8""", mid, *args)
    return JSONResponse(json.loads(json.dumps({"buyers": buyers, "suppliers": suppliers}, default=jdefault)))


@app.get("/buyers", response_class=HTMLResponse)
def buyers(request: Request):
    L = Listing(request, {"name": ("b.name", "asc"), "type": ("b.type", "asc"), "contracts": ("s.contracts", "desc"),
                          "suppliers": ("s.suppliers", "desc"), "eur": ("s.amount_eur", "desc")}, "eur")
    contains(L, "q", "b.name", "b.eik")
    if L.get("type"):
        L.add("b.type = %s", L.get("type"))
    rows = L.fetch("""SELECT b.eik, b.name, b.type, s.contracts, s.amount_eur, s.suppliers FROM live.buyer b
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
    rows = L.fetch("""SELECT c.key, c.eik, c.name, c.legal_form, c.seat, s.contracts, s.amount_eur, s.buyers, s.last_contract
                      FROM live.company c JOIN live.company_stats s USING (key) WHERE {where}""")
    return page(request, "list_companies.html", L=L, rows=rows, nav="Фирми")


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
    state = {"contracted": "s.contracts > 0", "cancelled": "t.is_cancelled",
             "open": "s.contracts IS NULL AND NOT coalesce(t.is_cancelled, false)"}.get(L.get("state"))
    if state:
        L.add(state)
    rows = L.fetch(f"""SELECT t.unp, {day} AS day, t.subject, t.buyer_eik, b.name buyer, t.procedure_type, t.estimated_eur,
                         t.is_cancelled, s.contracts, s.amount_eur, s.single_bid
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
