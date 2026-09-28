"""Парламент: server-rendered pages over live.*; the hall (static/zala.js) and the MP's strip read /api."""
import csv
import datetime as dt
import io
import os
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from ingest import checks, groups as gref
from ingest.config import SITE

from . import feedback

HERE = Path(__file__).parent
ROOT = HERE.parent
DSN = os.environ.get("PARLAMENT_DSN", "dbname=parlament")
HUB_URL = os.environ.get("HUB_URL", "http://localhost:8001")
ASSET_V = "2"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
app.add_middleware(GZipMiddleware, minimum_size=1000)
T = Jinja2Templates(directory=HERE / "templates")
app.include_router(feedback.router("DenislavDenev/prozrachnost", os.getenv("STATE_DIRECTORY", ROOT / ".data"), "Парламент"))

MONTHS = "януари февруари март април май юни юли август септември октомври ноември декември".split()
CODE = {"+": "за", "-": "против", "=": "въздържал се", "0": "не гласувал", "П": "регистриран", "О": "не е регистриран",
        "Р": "не е регистриран"}
LIST_LIMIT = 400   # rows of the list of votes on the page; the CSV has all


def q(sql, *args):
    with psycopg.connect(DSN) as c:
        return c.execute(sql, args).fetchall()


def one(sql, *args):
    r = q(sql, *args)
    return r[0] if r else None


# ---------- formatting ----------

def fnum(v, d=0):
    if v is None:
        return "няма данни"
    return f"{float(v):,.{d}f}".replace(",", " ").replace(".", ",").replace("-", "−")


def fpct(a, b, d=0):
    """a of b in %, "няма данни" when there is nothing to divide by."""
    return fnum(100 * a / b, d) + "%" if b else "няма данни"


def fdate(v):
    if not v:
        return "никога"
    if isinstance(v, dt.datetime):
        v = v.astimezone(dt.timezone(dt.timedelta(hours=3)))
    return v.strftime("%d.%m.%Y")


def flong(v):
    return f"{v.day} {MONTHS[v.month - 1]} {v.year}" if v else ""


def fname(s):
    """"АЙЛИН НУРИДИН ПЕХЛИВАНОВА" -> "Айлин Нуридин Пехливанова"."""
    return " ".join("-".join(p[:1] + p[1:].lower() for p in w.split("-")) for w in (s or "").split())


def fdistrict(s):
    """"23-СОФИЯ" -> "23 МИР София"."""
    if not s:
        return ""
    no, _, name = s.partition("-")
    return f"{no} МИР {fname(name)}" if no.isdigit() else fname(s)


def ordinal(n):
    """52 -> "52-ро", 51 -> "51-во", 47 -> "47-о", as the Assembly writes its own number."""
    return f"{n}-{'во' if n % 10 == 1 and n % 100 != 11 else 'ро' if n % 10 == 2 and n % 100 != 12 else 'о'}"


STATUS = {"ok": "наред", "held": "задържан до второ четене", "invalid": "невалиден отговор", "no-files": "без поименно гласуване",
          None: "не е четен"}

T.env.filters.update(num=fnum, date=fdate, long=flong, name=fname, district=fdistrict, status=lambda s: STATUS.get(s, s))
T.env.globals.update(gcolor=lambda code: gref.info(code)[1], asset_v=ASSET_V, hub_url=HUB_URL, feedback_button=Markup(feedback.BUTTON), pct=fpct, CODE=CODE,
                     support_link=Markup(feedback.support_link(HUB_URL)), ordinal=ordinal, SITE=SITE)


def page(request, name, nav, **ctx):
    r = one("SELECT max(last_ok) FROM ops.source_state WHERE ref LIKE 'month/%%'")
    return T.TemplateResponse(request, name, {"nav": nav, "fresh": r[0] if r else None, **ctx})


# ---------- data ----------

def assemblies():
    """[(no, first, last, sittings, votes)] of the assemblies with a roll call, the newest first."""
    return q("""SELECT s.assembly, min(s.date), max(s.date), count(DISTINCT s.id), count(*) FILTER (WHERE i.kind = 'vote')
                FROM live.sitting s JOIN live.item i ON i.sitting = s.id GROUP BY 1 ORDER BY 1 DESC""")


def pick_assembly(ns):
    """The asked assembly, else the newest; HTTPException(404) for one we do not have."""
    have = [a[0] for a in assemblies()]
    if ns is None:
        return have[0] if have else None
    if ns not in have:
        raise HTTPException(404)
    return ns


def group_names(assembly):
    """{code: full name} of the groups of an assembly (db/ref/groups.csv; the code itself for one not there)."""
    return {g: gref.info(g)[0] for g, in q("""SELECT DISTINCT g.grp FROM live.item_group g JOIN live.sitting s ON s.id = g.sitting
                                                 WHERE s.assembly = %s""", assembly)}


def seats_now(assembly):
    """[(group, MPs)] at the assembly's latest registration, in the Assembly's order."""
    r = one("""SELECT i.sitting, i.no FROM live.item i JOIN live.sitting s ON s.id = i.sitting
               WHERE s.assembly = %s AND i.kind = 'registration' ORDER BY i.at DESC LIMIT 1""", assembly)
    if not r:
        return []
    return q("""SELECT grp, listed FROM live.item_group WHERE sitting = %s AND no = %s ORDER BY listed DESC, grp""", *r)


def vote_rows(where="", args=(), limit=None):
    return q(f"""SELECT i.sitting, i.no, i.at, i.topic, i.yes, i.no_, i.abstain, i.voted, s.assembly,
                        (SELECT listed FROM live.item r WHERE r.sitting = i.sitting AND r.kind = 'registration' ORDER BY r.no LIMIT 1),
                        i.mismatch
                 FROM live.item i JOIN live.sitting s ON s.id = i.sitting
                 WHERE i.kind = 'vote' {where} ORDER BY i.at DESC, i.no DESC {f'LIMIT {int(limit)}' if limit else ''}""", *args)


def as_vote(r):
    sid, no, at, topic, y, n, a, voted, ns, listed, *mismatch = r
    return {"sitting": sid, "no": no, "at": at, "topic": topic or "без тема", "yes": y, "no_": n, "abstain": a, "voted": voted,
            "assembly": ns, "listed": listed or 240, "url": f"/glasuvane/{sid}/{no}", "mismatch": (mismatch or [None])[0]}


# ---------- pages ----------

@app.get("/favicon.svg")
def favicon():
    return FileResponse(HERE / "static" / "favicon.svg", media_type="image/svg+xml")


@app.get("/healthz")
def healthz():
    q("SELECT 1")
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    ns = pick_assembly(None)
    ctx = {"ns": ns}
    if ns:
        a = next(x for x in assemblies() if x[0] == ns)
        part = one("SELECT sum(voted), sum(votes) FROM live.mp_stat WHERE assembly = %s", ns)
        ctx.update(first=a[1], last=a[2], sittings=a[3], votes=a[4], part=part, seats=seats_now(ns), names=group_names(ns),
                   latest=[as_vote(r) for r in vote_rows("AND s.assembly = %s", (ns,), 12)],
                   close=[as_vote(r) for r in q("""
                       SELECT i.sitting, i.no, i.at, i.topic, i.yes, i.no_, i.abstain, i.voted, s.assembly, NULL
                       FROM live.item i JOIN live.sitting s ON s.id = i.sitting
                       WHERE i.kind = 'vote' AND s.assembly = %s AND i.voted >= 100
                       ORDER BY abs(i.yes - (i.voted - i.yes)), i.at DESC LIMIT 8""", ns)])
    return page(request, "home.html", "Табло", **ctx)


@app.get("/glasuvaniya", response_class=HTMLResponse)
def votes_page(request: Request, ns: int | None = None):
    q_ = request.query_params.get("q", "").strip()
    ns = pick_assembly(ns)
    where, args = "AND s.assembly = %s", [ns]
    if q_:
        where += " AND i.topic ILIKE %s"
        args.append(f"%{q_}%")
    rows = [as_vote(r) for r in vote_rows(where, args, LIST_LIMIT + 1)] if ns else []
    total = one(f"SELECT count(*) FROM live.item i JOIN live.sitting s ON s.id = i.sitting WHERE i.kind = 'vote' {where}", *args)[0] if ns else 0
    return page(request, "votes.html", "Гласувания", ns=ns, q=q_, rows=rows[:LIST_LIMIT], total=total, all=assemblies(),
                more=len(rows) > LIST_LIMIT)


def the_vote(sitting, no):
    r = vote_rows("AND i.sitting = %s AND i.no = %s", (sitting, no))
    if not r:
        raise HTTPException(404)
    return as_vote(r[0])


@app.get("/glasuvane/{sitting}/{no}", response_class=HTMLResponse)
def vote_page(request: Request, sitting: int, no: int):
    v = the_vote(sitting, no)
    s = one("SELECT date, heading, iv, gv, note FROM live.sitting WHERE id = %s", sitting)
    groups = q("""SELECT g.grp, g.yes, g.no_, g.abstain, g.voted, l.line, l.voters, l.with_line,
                         (SELECT count(*) FROM live.vote x WHERE x.sitting = g.sitting AND x.item = g.no AND x.grp = g.grp)
                  FROM live.item_group g LEFT JOIN live.line l ON l.sitting = g.sitting AND l.item = g.no AND l.grp = g.grp
                  WHERE g.sitting = %s AND g.no = %s ORDER BY g.voted DESC, g.grp""", sitting, no)
    against = q("""SELECT v.mp, m.name, v.grp, v.code, l.line FROM live.vote v
                   JOIN live.line l ON l.sitting = v.sitting AND l.item = v.item AND l.grp = v.grp
                   JOIN live.sitting s ON s.id = v.sitting JOIN live.mp m ON m.assembly = s.assembly AND m.no = v.mp
                   WHERE v.sitting = %s AND v.item = %s AND l.line IS NOT NULL AND v.code IN ('+', '-', '=') AND v.code <> l.line
                   ORDER BY v.grp, m.name""", sitting, no)
    nav = q("""SELECT no FROM live.item WHERE sitting = %s AND kind = 'vote' AND no IN (%s, %s) ORDER BY no""", sitting, no - 1, no + 1)
    same_day = q("SELECT no, topic FROM live.item WHERE sitting = %s AND kind = 'vote' ORDER BY no", sitting)
    seats = q("""SELECT v.mp, m.name, v.grp, v.code FROM live.vote v JOIN live.sitting s ON s.id = v.sitting
                 JOIN live.mp m ON m.assembly = s.assembly AND m.no = v.mp
                 WHERE v.sitting = %s AND v.item = %s ORDER BY v.grp, m.name""", sitting, no)
    rank = {g: i for i, (g, _) in enumerate(seats_at(sitting))}
    seats.sort(key=lambda r: (rank.get(r[2], len(rank)), r[2]))   # the groups in the Assembly's order, as in the hall
    groups.sort(key=lambda r: (rank.get(r[0], len(rank)), r[0]))
    by_group = {}                                                   # each group's MPs: for, abstain, against, did not vote
    for mp, name, g, code in sorted(seats, key=lambda r: ("+=-0".index(r[3]), r[1])):
        by_group.setdefault(g, []).append((mp, name, code))
    return page(request, "vote.html", "Гласувания", v=v, s=s, groups=groups, against=against, names=group_names(v["assembly"]), seats=seats, by_group=by_group,
                prev=next((n for n, in nav if n < no), None), next=next((n for n, in nav if n > no), None), same_day=same_day)


@app.get("/api/glasuvane/{sitting}/{no}.json")
def vote_api(sitting: int, no: int):
    """The hall: every MP of the vote with the group and the vote, and the groups in the Assembly's order."""
    v = the_vote(sitting, no)
    seats = q("""SELECT v.mp, m.name, v.grp, v.code FROM live.vote v JOIN live.sitting s ON s.id = v.sitting
                 JOIN live.mp m ON m.assembly = s.assembly AND m.no = v.mp
                 WHERE v.sitting = %s AND v.item = %s ORDER BY v.grp, m.name""", sitting, no)
    order = [g for g, _ in seats_at(sitting)]
    order = order + sorted({s[2] for s in seats} - set(order))
    return JSONResponse({"assembly": v["assembly"],
                         "groups": [{"grp": g, "name": gref.info(g)[0], "color": gref.info(g)[1]} for g in order],
                         "totals": {"yes": v["yes"], "no": v["no_"], "abstain": v["abstain"], "voted": v["voted"]},
                         "seats": [{"mp": mp, "name": fname(n), "grp": g, "code": c} for mp, n, g, c in seats]},
                        headers={"Cache-Control": "public, max-age=3600"})


def seats_at(sitting):
    return q("""SELECT grp, listed FROM live.item_group WHERE sitting = %s AND no =
                (SELECT min(no) FROM live.item WHERE sitting = %s AND kind = 'registration') ORDER BY listed DESC, grp""", sitting, sitting)


@app.get("/api/zala/{assembly}.json")
def hall_api(assembly: int):
    """The hall of the assembly by group, at its latest registration."""
    pick_assembly(assembly)
    return JSONResponse({"groups": [{"grp": g, "n": n, "name": gref.info(g)[0], "color": gref.info(g)[1]} for g, n in seats_now(assembly)]},
                        headers={"Cache-Control": "public, max-age=3600"})


@app.get("/deputati", response_class=HTMLResponse)
def mps_page(request: Request, ns: int | None = None):
    ns = pick_assembly(ns)
    rows = q("""SELECT mp, name, grp, votes, voted, with_line, against_line, regs, present, first, last, district
                FROM live.mp_stat WHERE assembly = %s ORDER BY name""", ns) if ns else []
    return page(request, "mps.html", "Депутати", ns=ns, rows=rows, all=assemblies(), names=group_names(ns) if ns else {},
                current=ns == pick_assembly(None))


@app.get("/deputati/{ns}/{mp}", response_class=HTMLResponse)
def mp_page(request: Request, ns: int, mp: int):
    s = one("""SELECT mp, name, grp, votes, voted, yes, no_, abstain, regs, present, with_line, against_line, first, last, district, profile
               FROM live.mp_stat WHERE assembly = %s AND mp = %s""", ns, mp)
    if not s:
        raise HTTPException(404)
    groups = q("""SELECT v.grp, min(x.date), max(x.date) FROM live.vote v JOIN live.sitting x ON x.id = v.sitting
                  WHERE x.assembly = %s AND v.mp = %s GROUP BY v.grp ORDER BY 2""", ns, mp)
    against = q("""SELECT i.sitting, i.no, i.at, i.topic, v.code, l.line FROM live.vote v
                   JOIN live.sitting x ON x.id = v.sitting JOIN live.item i ON i.sitting = v.sitting AND i.no = v.item
                   JOIN live.line l ON l.sitting = v.sitting AND l.item = v.item AND l.grp = v.grp
                   WHERE x.assembly = %s AND v.mp = %s AND l.line IS NOT NULL AND v.code IN ('+', '-', '=') AND v.code <> l.line
                   ORDER BY i.at DESC""", ns, mp)
    avg = one("SELECT sum(voted)::float / nullif(sum(votes), 0), sum(against_line)::float / nullif(sum(with_line + against_line), 0) FROM live.mp_stat WHERE assembly = %s", ns)
    return page(request, "mp.html", "Депутати", ns=ns, s=s, groups=groups, against=against, avg=avg, names=group_names(ns))


@app.get("/api/deputat/{ns}/{mp}.json")
def mp_api(ns: int, mp: int):
    """Every vote of the MP in time: [sitting, item, date, code, the group's line], for the strip on the page."""
    rows = q("""SELECT v.sitting, v.item, x.date, v.code, l.line, i.topic FROM live.vote v
                JOIN live.sitting x ON x.id = v.sitting JOIN live.item i ON i.sitting = v.sitting AND i.no = v.item
                LEFT JOIN live.line l ON l.sitting = v.sitting AND l.item = v.item AND l.grp = v.grp
                WHERE x.assembly = %s AND v.mp = %s AND i.kind = 'vote' ORDER BY i.at, v.item""", ns, mp)
    if not rows:
        raise HTTPException(404)
    return JSONResponse({"votes": [[s, i, d.isoformat(), c, l, t or "без тема"] for s, i, d, c, l, t in rows]},
                        headers={"Cache-Control": "public, max-age=3600"})


@app.get("/grupi", response_class=HTMLResponse)
def groups_page(request: Request, ns: int | None = None):
    ns = pick_assembly(ns)
    unity, pairs, order = [], [], []
    if ns:
        unity = q("""SELECT l.grp, sum(l.with_line), sum(l.voters), count(*) FROM live.line l JOIN live.sitting s ON s.id = l.sitting
                     WHERE s.assembly = %s AND l.line IS NOT NULL GROUP BY 1 HAVING count(*) >= 10 ORDER BY 2::float / sum(l.voters) DESC""", ns)
        order = [g for g, *_ in unity]
        pairs = q("""SELECT a.grp, b.grp, count(*), count(*) FILTER (WHERE a.line = b.line)
                     FROM live.line a JOIN live.line b ON b.sitting = a.sitting AND b.item = a.item AND a.grp < b.grp
                     JOIN live.sitting s ON s.id = a.sitting
                     WHERE s.assembly = %s AND a.line IS NOT NULL AND b.line IS NOT NULL GROUP BY 1, 2""", ns)
    return page(request, "groups.html", "Групи", ns=ns, unity=unity, pairs=pairs, order=order, all=assemblies(),
                names=group_names(ns) if ns else {})


@app.get("/api/grupi/{ns}/edinstvo.json")
def unity_api(ns: int):
    """Each group's unity by month: of its MPs who voted, the share with the group's line."""
    pick_assembly(ns)
    rows = q("""SELECT l.grp, to_char(s.date, 'YYYY-MM'), sum(l.with_line), sum(l.voters) FROM live.line l
                JOIN live.sitting s ON s.id = l.sitting WHERE s.assembly = %s AND l.line IS NOT NULL
                GROUP BY 1, 2 HAVING sum(l.voters) > 0 ORDER BY 1, 2""", ns)
    series = {}
    for g, m, w, n in rows:
        series.setdefault(g, []).append([m, round(100 * w / n, 1)])
    return JSONResponse({"series": [{"name": g, "points": p} for g, p in series.items() if len(p) >= 2]})


@app.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request):
    reads = q("""SELECT ref, last_read, last_ok, status, error, rows FROM ops.source_state
                 WHERE ref NOT LIKE 'sten/%%' ORDER BY ref DESC LIMIT 14""")
    bad = q("""SELECT st.ref, s.date, st.status, st.error FROM ops.source_state st JOIN live.sitting s ON 'sten/' || s.id = st.ref
               WHERE st.status NOT IN ('ok', 'no-files') ORDER BY s.date DESC""")
    counts = one("""SELECT count(*), count(*) FILTER (WHERE iv_sha IS NOT NULL), min(date) FILTER (WHERE iv_sha IS NOT NULL)
                    FROM live.sitting""")
    return page(request, "sources.html", "Източници", reads=reads, bad=bad, counts=counts,
                problems=freshness_problems())


def freshness_problems():
    with psycopg.connect(DSN) as c:
        return checks.freshness(c)


@app.get("/how", response_class=HTMLResponse)
def how(request: Request):
    return page(request, "how.html", "Как работи")


# ---------- CSV ----------

def csv_response(name, head, rows):
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf)
    w.writerow(head)
    w.writerows(rows)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/csv/glasuvaniya-{ns}.csv")
def votes_csv(ns: int):
    pick_assembly(ns)
    rows = vote_rows("AND s.assembly = %s", (ns,))
    return csv_response(f"glasuvaniya-{ns}.csv", ["заседание", "точка", "време", "тема", "за", "против", "въздържали се", "гласували"],
                        [(r[0], r[1], r[2].isoformat(sep=" "), r[3], r[4], r[5], r[6], r[7]) for r in rows])


@app.get("/csv/glasuvane/{sitting}/{no}.csv")
def vote_csv(sitting: int, no: int):
    the_vote(sitting, no)
    rows = q("""SELECT m.name, v.grp, v.code FROM live.vote v JOIN live.sitting s ON s.id = v.sitting
                JOIN live.mp m ON m.assembly = s.assembly AND m.no = v.mp WHERE v.sitting = %s AND v.item = %s ORDER BY v.grp, m.name""",
             sitting, no)
    return csv_response(f"glasuvane-{sitting}-{no}.csv", ["депутат", "група", "код", "вот"], [(fname(n), g, c, CODE[c]) for n, g, c in rows])


@app.get("/csv/deputati-{ns}.csv")
def mps_csv(ns: int):
    pick_assembly(ns)
    rows = q("""SELECT name, grp, district, votes, voted, yes, no_, abstain, with_line, against_line, regs, present, first, last
                FROM live.mp_stat WHERE assembly = %s ORDER BY name""", ns)
    return csv_response(f"deputati-{ns}.csv", ["депутат", "група", "избирателен район", "гласувания", "гласувал", "за", "против",
                                              "въздържал се", "с групата", "срещу групата", "регистрации", "регистриран", "от", "до"],
                        [(fname(r[0]), *r[1:]) for r in rows])


@app.exception_handler(404)
def not_found(request: Request, exc):
    return T.TemplateResponse(request, "404.html", {"nav": None, "fresh": None}, status_code=404)
