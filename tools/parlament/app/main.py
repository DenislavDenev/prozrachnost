"""Парламент: server-rendered pages over live.*; the hall (static/zala.js) and the MP's strip read /api."""
import collections
import csv
import datetime as dt
import io
import os
import re
import time
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
LINE = {"+": "за", "-": "не подкрепя"}   # a group's line: for, or not for (against or abstained; ingest/stats.py)
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

STAGE = re.compile(r"\([^()]{2,200}\)")


def fpara(text):
    """A paragraph of the stenogram, escaped, with its stage directions ("(Шум и реплики.)") set apart."""
    return Markup(STAGE.sub(lambda m: f'<em class="stage">{m.group(0)}</em>', str(Markup.escape(text))))


T.env.filters.update(num=fnum, date=fdate, long=flong, name=fname, district=fdistrict, status=lambda s: STATUS.get(s, s), para=fpara)
T.env.globals.update(gcolor=lambda code: gref.info(code)[1], asset_v=ASSET_V, hub_url=HUB_URL, feedback_button=Markup(feedback.BUTTON), pct=fpct, CODE=CODE, LINE=LINE,
                     support_link=Markup(feedback.support_link(HUB_URL)), ordinal=ordinal, SITE=SITE)


_assemblies = {"at": 0.0, "rows": {}}


def assembly_rows():
    """{no: (name, API id, start, end)} of every assembly, read at most every 10 minutes."""
    if time.monotonic() - _assemblies["at"] > 600 or not _assemblies["rows"]:
        _assemblies["rows"] = {n: (name, api, a, b) for n, name, api, a, b in q('SELECT no, name, api_id, start, "end" FROM live.assembly')}
        _assemblies["at"] = time.monotonic()
    return _assemblies["rows"]


def aname(no, short=False):
    """52 -> "52-ро Народно събрание"; before 1991 the assembly's own name and years ("VII велико народно събрание, 1990-1991")."""
    if no is None:
        return "Народно събрание"
    if 36 <= no < 100:
        return f"{ordinal(no)} НС" if short else f"{ordinal(no)} Народно събрание"
    r = assembly_rows().get(no)
    if not r:
        return f"{no}"
    years = f"{r[2].year}" + (f"-{r[3].year}" if r[3] and r[3].year != r[2].year else "")
    return f"{r[0][:1].upper()}{r[0][1:]}, {years}"


def sten_url(sitting):
    """The sitting on parliament.bg: the page reads only the sitting's number (the assembly in the path is not used)."""
    return f"{SITE}/bg/plenaryst/ns/62/ID/{sitting}"


T.env.globals.update(aname=aname, sten_url=sten_url)


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


def seats_now(assembly, day=None):
    """[(group, MPs)] at the assembly's latest registration (on or before `day`), in the Assembly's order."""
    r = one("""SELECT i.sitting, i.no FROM live.item i JOIN live.sitting s ON s.id = i.sitting
               WHERE s.assembly = %s AND i.kind = 'registration' AND s.date <= coalesce(%s, s.date)
               ORDER BY i.at DESC LIMIT 1""", assembly, day)
    if not r:
        return []
    return q("""SELECT grp, listed FROM live.item_group WHERE sitting = %s AND no = %s AND listed > 0 ORDER BY listed DESC, grp""", *r)


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
        ctx.update(hist=one("""SELECT count(*), min(date), count(steno_sha), count(iv_sha), min(date) FILTER (WHERE iv_sha IS NOT NULL)
                               FROM live.sitting"""),
                   first=a[1], last=a[2], sittings=a[3], votes=a[4], part=part, seats=seats_now(ns), names=group_names(ns),
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
                   WHERE v.sitting = %s AND v.item = %s AND l.line IS NOT NULL AND v.code IN ('+', '-', '=') AND (v.code = '+') <> (l.line = '+')
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
def hall_api(assembly: int, date: dt.date | None = None):
    """The hall of the assembly by group, at its latest registration (on or before `date`)."""
    pick_assembly(assembly)
    return JSONResponse({"date": date.isoformat() if date else None,
                         "groups": [{"grp": g, "n": n, "name": gref.info(g)[0], "color": gref.info(g)[1]} for g, n in seats_now(assembly, date)]},
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
                   WHERE x.assembly = %s AND v.mp = %s AND l.line IS NOT NULL AND v.code IN ('+', '-', '=') AND (v.code = '+') <> (l.line = '+')
                   ORDER BY i.at DESC""", ns, mp)
    avg = one("SELECT sum(voted)::float / nullif(sum(votes), 0), sum(against_line)::float / nullif(sum(with_line + against_line), 0) FROM live.mp_stat WHERE assembly = %s", ns)
    person = one("SELECT p.person FROM live.mp m JOIN live.profile p ON p.id = m.profile WHERE m.assembly = %s AND m.no = %s", ns, mp)
    return page(request, "mp.html", "Депутати", ns=ns, s=s, groups=groups, against=against, avg=avg, names=group_names(ns),
                person=person[0] if person else None)


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
def groups_page(request: Request, ns: int | None = None, date: dt.date | None = None):
    ns = pick_assembly(ns)
    span = one("SELECT min(date), max(date) FROM live.sitting WHERE assembly = %s AND iv_sha IS NOT NULL", ns) if ns else None
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
                names=group_names(ns) if ns else {}, day=date.isoformat() if date else None, span=span)


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
    return JSONResponse({"series": [{"name": g, "color": gref.info(g)[1], "points": p} for g, p in series.items() if len(p) >= 2]})


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


# ---------- the sittings, the stenograms, the people ----------

ROLE_WORDS = {"председател": "председател", "председателят": "председател", "заместник-председател": "зам.-председател",
              "заместник председател": "зам.-председател", "докладчик": "докладчик", "реплика": "от залата"}


def speech_rows(where, *args):
    """[(sitting, no, role, name, note, grp, profile, person, text)] of the stenogram."""
    return q(f"""SELECT sp.sitting, sp.no, sp.role, sp.name, sp.note, sp.grp, sp.profile, p.person, sp.text
                 FROM live.speech sp LEFT JOIN live.profile p ON p.id = sp.profile WHERE {where} ORDER BY sp.sitting, sp.no""", *args)


def as_speech(r):
    sid, no, role, name, note, grp, profile, person, text = r
    code = grp if grp and gref.info(grp)[1] != gref.GREY else None
    return {"sitting": sid, "no": no, "role": role, "role_s": ROLE_WORDS.get(role or "", role), "name": name, "note": note, "grp": grp,
            "code": code, "person": person, "paras": [x for x in (text or "").split("\n") if x.strip()], "text": text}


def years():
    return q("SELECT extract(year FROM date)::int, count(*) FROM live.sitting GROUP BY 1 ORDER BY 1 DESC")


@app.get("/zasedaniya", response_class=HTMLResponse)
def sittings_page(request: Request, g: int | None = None, ns: int | None = None):
    """Every sitting of a year (or of an assembly): its votes, speeches, video, the stenogram as text or scan."""
    ys = years()
    if not ys:
        if ns is not None:
            raise HTTPException(404)
        return page(request, "sittings.html", "Заседания", rows=[], years=[], year=None, ns=None)
    if ns is not None:
        where, args, year = "s.assembly = %s", [ns], None
    else:
        year = g or ys[0][0]
        if year not in {y for y, _ in ys}:
            raise HTTPException(404)
        where, args = "s.date >= make_date(%s, 1, 1) AND s.date < make_date(%s + 1, 1, 1)", [year, year]
    rows = q(f"""SELECT s.id, s.date, s.assembly,
                        (SELECT count(*) FROM live.item i WHERE i.sitting = s.id AND i.kind = 'vote'),
                        (SELECT count(*) FROM live.speech sp WHERE sp.sitting = s.id AND sp.no > 0),
                        coalesce(array_length(s.video, 1), 0), s.pdf, s.steno_sha IS NOT NULL
                 FROM live.sitting s WHERE {where} ORDER BY s.date DESC, s.id DESC""", *args)
    if ns is not None and not rows:
        raise HTTPException(404)
    return page(request, "sittings.html", "Заседания", rows=rows, years=ys, year=year, ns=ns)


def the_sitting(sid):
    s = one("""SELECT id, date, assembly, heading, video, pdf, steno_sha, iv, gv, note FROM live.sitting WHERE id = %s""", sid)
    if not s:
        raise HTTPException(404)
    return dict(zip(("id", "date", "assembly", "heading", "video", "pdf", "steno", "iv", "gv", "note"), s))


def speakers_of(speeches):
    """The people who spoke, the most words first: [(name, person, group, speeches, words)]; the chair and the hall apart."""
    by = collections.OrderedDict()
    for x in speeches:
        if not x["name"] or x["role"] in ("председател", "председателят", "заместник-председател", "заместник председател", "реплика"):
            continue
        k = (x["name"], x["person"])
        n, w, g = by.get(k, (0, 0, None))
        by[k] = (n + 1, w + len(x["text"].split()), g or x["grp"])
    return sorted(((name, person, g, n, w) for (name, person), (n, w, g) in by.items()), key=lambda r: -r[4])


@app.get("/zasedanie/{sid}", response_class=HTMLResponse)
def sitting_page(request: Request, sid: int):
    s = the_sitting(sid)
    votes = [as_vote(r) for r in reversed(vote_rows("AND i.sitting = %s", (sid,)))]
    speeches = [as_speech(r) for r in speech_rows("sp.sitting = %s", sid)]
    prev = one("SELECT id, date FROM live.sitting WHERE (date, id) < (%s, %s) ORDER BY date DESC, id DESC LIMIT 1", s["date"], sid)
    after = one("SELECT id, date FROM live.sitting WHERE (date, id) > (%s, %s) ORDER BY date, id LIMIT 1", s["date"], sid)
    return page(request, "sitting.html", "Заседания", s=s, votes=votes, speeches=speeches, speakers=speakers_of(speeches),
                prev=prev, next=after)


@app.get("/zasedanie/{sid}/stenograma", response_class=HTMLResponse)
def steno_page(request: Request, sid: int):
    """The stenogram alone, to read (it opens in a new tab from the sitting)."""
    s = the_sitting(sid)
    speeches = [as_speech(r) for r in speech_rows("sp.sitting = %s", sid)]
    if not speeches:
        raise HTTPException(404)
    return page(request, "steno.html", "Заседания", s=s, speeches=speeches)


@app.get("/izkazvane/{sid}/{no}", response_class=HTMLResponse)
def speech_page(request: Request, sid: int, no: int):
    """One speech with its own address, the one before and after it."""
    s = the_sitting(sid)
    rows = [as_speech(r) for r in speech_rows("sp.sitting = %s AND sp.no BETWEEN %s AND %s", sid, no - 1, no + 1)]
    this = next((x for x in rows if x["no"] == no), None)
    if not this:
        raise HTTPException(404)
    return page(request, "speech.html", "Заседания", s=s, x=this, before=next((x for x in rows if x["no"] == no - 1), None),
                after=next((x for x in rows if x["no"] == no + 1), None))


WORDS = re.compile(r"[0-9A-Za-zА-Яа-яЁёЪъЬьЮюЯяІі]+")
SEARCH_LIMIT = 100


def tsquery(text):
    """"бюджет здраве" -> "бюджет:* & здраве:*": every word, as a beginning (the index is 'simple': no stemming)."""
    return " & ".join(f"{w.lower()}:*" for w in WORDS.findall(text or "")[:8])


@app.get("/tarsene", response_class=HTMLResponse)
def search_page(request: Request, ns: int | None = None, chovek: int | None = None):
    text = (request.query_params.get("q") or "").strip()
    tq = tsquery(text)
    rows, who = [], None
    if chovek:
        who = one("SELECT name FROM live.profile WHERE person = %s ORDER BY assembly DESC LIMIT 1", chovek)
    if tq or chovek:
        where, args = [], []
        if tq:
            where.append("sp.tsv @@ to_tsquery('simple', %s)")
            args.append(tq)
        if ns:
            where.append("s.assembly = %s")
            args.append(ns)
        if chovek:
            where.append("sp.profile IN (SELECT id FROM live.profile WHERE person = %s)")
            args.append(chovek)
        head = ("ts_headline('simple', sp.text, to_tsquery('simple', %s), 'MaxFragments=2, MinWords=6, MaxWords=26, "
                "StartSel=\x01, StopSel=\x02, FragmentDelimiter= … ')") if tq else "left(sp.text, 300)"
        rows = q(f"""SELECT sp.sitting, sp.no, s.date, s.assembly, sp.name, sp.grp, p.person, {head}
                     FROM live.speech sp JOIN live.sitting s ON s.id = sp.sitting LEFT JOIN live.profile p ON p.id = sp.profile
                     WHERE {' AND '.join(where)} ORDER BY s.date DESC, sp.sitting DESC, sp.no LIMIT {SEARCH_LIMIT + 1}""",
                 *(([tq] if tq else []) + args))
    found = [(sid, no, d, a, name, grp, person,
              Markup(str(Markup.escape(snip)).replace("\x01", "<mark>").replace("\x02", "</mark>")))
             for sid, no, d, a, name, grp, person, snip in rows[:SEARCH_LIMIT]]
    return page(request, "search.html", "Търсене", text=text, rows=found, more=len(rows) > SEARCH_LIMIT, ns=ns, chovek=chovek,
                who=who[0] if who else None, all=q("SELECT DISTINCT s.assembly FROM live.sitting s WHERE s.steno_sha IS NOT NULL ORDER BY 1 DESC"))


@app.get("/chovek/{person}", response_class=HTMLResponse)
def person_page(request: Request, person: int):
    """Everything about an MP across the assemblies: where, in which group and role, how they voted, what they said,
    their absences and penalties. Only the public role."""
    profs = q("""SELECT p.id, p.assembly, p.name, p.district, p.list, p.profession, p.languages, a.start, a."end"
                 FROM live.profile p JOIN live.assembly a ON a.no = p.assembly WHERE p.person = %s ORDER BY p.assembly""", person)
    if not profs:
        raise HTTPException(404)
    ids = [r[0] for r in profs]
    ms = q("""SELECT m.profile, m.body_name, m.body_kind, m.role, m.since, m.until, b.grp
              FROM live.membership m LEFT JOIN live.body b ON b.id = m.body WHERE m.profile = ANY(%s)
              ORDER BY m.since NULLS LAST, m.body_kind""", ids)
    votes = {r[0]: r for r in q("""SELECT st.assembly, st.mp, st.votes, st.voted, st.with_line, st.against_line, st.grp
                                   FROM live.mp_stat st WHERE st.profile = ANY(%s)""", ids)}
    said = {a: (n, w) for a, n, w in q("""SELECT s.assembly, count(*), sum(array_length(string_to_array(sp.text, ' '), 1))
                                         FROM live.speech sp JOIN live.sitting s ON s.id = sp.sitting
                                         WHERE sp.profile = ANY(%s) GROUP BY 1""", ids)}
    latest = q("""SELECT sp.sitting, sp.no, s.date, s.assembly, sp.grp, left(sp.text, 260) FROM live.speech sp
                  JOIN live.sitting s ON s.id = sp.sitting WHERE sp.profile = ANY(%s) ORDER BY s.date DESC, sp.no DESC LIMIT 12""", ids)
    absences = q("SELECT date, body_name, kind FROM live.absence WHERE profile = ANY(%s) ORDER BY date DESC", ids)
    penalties = q("SELECT date, kind, note, by_name, what FROM live.penalty WHERE profile = ANY(%s) ORDER BY date DESC", ids)
    # the timeline: per assembly, its span and the groups inside it, as shares of the span
    lines = []
    for pid, a, name, district, lst, prof, lang, start, end in profs:
        end = end or dt.date.today()
        span = max(1, (end - start).days)
        bars = []
        for p_, body, kind, role, since, until, grp in ms:
            if p_ != pid or kind != 2 or not since:
                continue
            f, t = max(since, start), min(until or end, end)
            bars.append({"left": 100 * (f - start).days / span, "width": max(0.6, 100 * (t - f).days / span), "name": body,
                         "grp": grp, "color": gref.info(grp)[1] if grp else gref.GREY, "since": since, "until": until})
        lines.append({"profile": pid, "assembly": a, "start": start, "end": end, "district": district, "list": lst, "bars": bars,
                      "roles": [m for m in ms if m[0] == pid and m[2] in (1, 2) and m[3] and not m[3].startswith("член")],
                      "bodies": [m for m in ms if m[0] == pid and m[2] not in (1, 2)], "votes": votes.get(a), "said": said.get(a)})
    return page(request, "person.html", "Депутати", person=person, pname=profs[-1][2], profs=profs, lines=lines, latest=latest,
                absences=absences, penalties=penalties, profession=profs[-1][5], languages=profs[-1][6])


@app.get("/grupa/{ns}/{code}", response_class=HTMLResponse)
def group_page(request: Request, ns: int, code: str):
    """A parliamentary group in an assembly: its size in time, who was in it and when, its unity, the groups it votes
    with, its leaders, how much its MPs spoke."""
    pick_assembly(ns)
    span = one("""SELECT min(s.date), max(s.date) FROM live.item_group g JOIN live.sitting s ON s.id = g.sitting
                  WHERE s.assembly = %s AND g.grp = %s""", ns, code)
    if not span or not span[0]:
        raise HTTPException(404)
    members = q("""SELECT v.mp, m.name, p.person, min(s.date), max(s.date), count(*) FILTER (WHERE i.kind = 'vote')
                   FROM live.vote v JOIN live.sitting s ON s.id = v.sitting JOIN live.item i ON i.sitting = v.sitting AND i.no = v.item
                   JOIN live.mp m ON m.assembly = s.assembly AND m.no = v.mp LEFT JOIN live.profile p ON p.id = m.profile
                   WHERE s.assembly = %s AND v.grp = %s GROUP BY 1, 2, 3 ORDER BY 4, 2""", ns, code)
    unity = one("""SELECT sum(l.with_line), sum(l.voters), count(*) FROM live.line l JOIN live.sitting s ON s.id = l.sitting
                   WHERE s.assembly = %s AND l.grp = %s AND l.line IS NOT NULL""", ns, code)
    pairs = q("""SELECT b.grp, count(*), count(*) FILTER (WHERE a.line = b.line)
                 FROM live.line a JOIN live.line b ON b.sitting = a.sitting AND b.item = a.item AND b.grp <> a.grp
                 JOIN live.sitting s ON s.id = a.sitting
                 WHERE s.assembly = %s AND a.grp = %s AND a.line IS NOT NULL AND b.line IS NOT NULL
                 GROUP BY 1 HAVING count(*) >= 10 ORDER BY 3::float / count(*) DESC""", ns, code)
    leaders = q("""SELECT DISTINCT p.person, p.name, m.role, m.since, m.until FROM live.body b
                   JOIN live.membership m ON m.body = b.id JOIN live.profile p ON p.id = m.profile
                   WHERE b.assembly = %s AND b.grp = %s AND m.role IS NOT NULL AND m.role NOT LIKE 'член%%'
                   ORDER BY m.since, p.name""", ns, code)
    first, last = span
    return page(request, "group.html", "Групи", ns=ns, code=code, gname=gref.info(code)[0], color=gref.info(code)[1],
                first=first, last=last, members=members, unity=unity, pairs=pairs, leaders=leaders, names=group_names(ns),
                joined=[m for m in members if m[3] > first], left=[m for m in members if m[4] < last])


@app.get("/api/grupa/{ns}/{code}.json")
def group_api(ns: int, code: str):
    """The group's MPs on the list at the first registration of every sitting."""
    pick_assembly(ns)
    rows = q("""SELECT s.date, g.listed FROM live.item_group g JOIN live.sitting s ON s.id = g.sitting
                WHERE s.assembly = %s AND g.grp = %s AND g.listed IS NOT NULL
                  AND g.no = (SELECT min(i.no) FROM live.item i WHERE i.sitting = g.sitting AND i.kind = 'registration')
                ORDER BY s.date""", ns, code)
    return JSONResponse({"series": [{"name": gref.info(code)[0], "color": gref.info(code)[1],
                                     "points": [[d.isoformat(), n] for d, n in rows]}]},
                        headers={"Cache-Control": "public, max-age=3600"})


@app.get("/sabraniya", response_class=HTMLResponse)
def assemblies_page(request: Request):
    """Every assembly since 1879 and what we hold of it."""
    rows = q("""SELECT a.no, a.start, a."end", count(s.id), count(s.steno_sha), count(s.pdf), count(s.iv_sha),
                       (SELECT count(*) FROM live.profile p WHERE p.assembly = a.no)
                FROM live.assembly a LEFT JOIN live.sitting s ON s.assembly = a.no GROUP BY 1, 2, 3 ORDER BY a.start DESC""")
    return page(request, "assemblies.html", "Заседания", rows=rows)


@app.exception_handler(404)
def not_found(request: Request, exc):
    return T.TemplateResponse(request, "404.html", {"nav": None, "fresh": None}, status_code=404)
