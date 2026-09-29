"""The bills: every bill the Assembly lists by month since 2001 (archive-period/bg/L_Acts/<y>/<m>/1/0) and its answer
(bill/<id>): sponsors, committees, the steps with their dates and sittings, the promulgation. Then its votes in the
hall, found in the sitting of each hall step by the bill's short title.

- The vote's topic names the bill the short way: "Законопроект за изменение и допълнение на Закона за X" is voted as
  "ЗИД на Закона за X – второ гласуване ..." (checked on 07.2026). key() makes both the same; a vote belongs to the
  bill where the keys are equal. Several bills with one title in one sitting (they are often debated together) all
  get the vote: the page says so.
- A hall step without a sitting number (the first reading, often) is matched by its day.
- A bill read again with fewer steps than we hold waits for a second read (load.hold); nothing is deleted.
"""
import datetime as dt
import re

from . import db, http, parse
from .config import API
from .load import SOURCE, hold, months, save_raw, state

# the vote's topic writes the bill's title short
SHORT = [("законопроект за изменение и допълнение на ", "зид на "), ("законопроект за изменение на ", "зи на "),
         ("законопроект за допълнение на ", "зд на "), ("закон за изменение и допълнение на ", "зид на "),
         ("закон за изменение на ", "зи на "), ("закон за допълнение на ", "зд на "), ("законопроект за ", "закон за ")]
READING = {"първо": 1, "второ": 2}
REOPEN_AFTER = dt.timedelta(days=6)   # an open bill of the current assembly is read again weekly
REOPEN_MAX = 300


def key(title):
    """"Законопроект за изменение и допълнение на Закона за държавния дълг" and "ЗИД на Закона за държавния дълг –
    второ гласуване - параграф 3" -> "зид на закона за държавния дълг"."""
    t = " ".join((title or "").lower().replace("–", "-").replace("—", "-").replace("„", "").replace("“", "")
                 .replace("”", "").replace('"', "").split())
    t = re.split(r" - | -$|, № ?\d| № ?\d", t)[0].strip(" .,")
    for long, short in SHORT:
        if t.startswith(long):
            return short + t[len(long):]
    return t


def reading(topic):
    """The reading a vote's topic says: 1, 2, or None."""
    t = (topic or "").lower()
    return next((n for w, n in READING.items() if f"{w} гласуване" in t or f"{w} четене" in t), None)


def load(conn, stats_, first=None, get=None, today=None):
    """The bills of the months from `first` (default: the last two), then the open bills of the current assembly not read
    for a week; their votes linked. -> stats with `problems`."""
    get = get or http.get
    today = today or dt.date.today()
    if first is None:
        d = today.replace(day=1) - dt.timedelta(days=1)
        first = (d.year, d.month)
    out, problems, ids = {}, [], set()
    for y, m in months(first, (today.year, today.month)):
        ref = f"bills/{y}-{m:02d}"
        try:
            listed = parse.acts(get(f"{API}/archive-period/bg/L_Acts/{y}/{m}/1/0"))
        except (http.Gone, http.Failed, parse.ShapeError) as e:
            state(conn, ref, status="invalid", error=str(e)[:2000])
            problems.append(f"Парламент: законопроектите за {m:02d}.{y}: {e}")
            continue
        state(conn, ref, status="ok", error=None, last_ok="now", rows=len(listed))
        ids.update(i for i, _, _ in listed)
    current = conn.execute("SELECT max(no) FROM live.assembly WHERE api_id IS NOT NULL").fetchone()[0]
    ids.update(i for i, in conn.execute("""SELECT id FROM live.bill WHERE assembly = %s AND adopted IS NULL AND NOT withdrawn
                                          AND read_at < now() - %s ORDER BY read_at LIMIT %s""", (current, REOPEN_AFTER, REOPEN_MAX)))
    for bid in sorted(ids):
        try:
            got = bill(conn, bid, get)
        except (http.Gone, http.Failed, parse.ShapeError) as e:
            got = "invalid"
            state(conn, f"bill/{bid}", status="invalid", error=str(e)[:2000])
            problems.append(f"Парламент: законопроект {bid}: {e}")
        out[got] = out.get(got, 0) + 1
    linked = link(conn, sorted(ids))
    stats_.update(bills=out, linked=linked, problems=problems)
    return stats_


def bill(conn, bid, get):
    """Read one bill. -> stored | unchanged | held."""
    ref = f"bill/{bid}"
    raw = get(f"{API}/bill/{bid}")
    b = parse.bill(raw)
    if b["id"] != bid:
        raise parse.ShapeError(f"asked for bill {bid}, got {b['id']}")
    with conn.transaction():
        sha = save_raw(conn, ref, f"bill-{bid}.json", raw)
        old = conn.execute("SELECT sha, (SELECT count(*) FROM live.bill_step WHERE bill = %s) FROM live.bill WHERE id = %s", (bid, bid)).fetchone()
        if old and old[0] == sha:
            conn.execute("UPDATE live.bill SET read_at = now() WHERE id = %s", (bid,))
            state(conn, ref, status="ok", error=None, last_ok="now")
            return "unchanged"
        if old and len(b["steps"]) < old[1] and hold(conn, ref, sha, len(b["steps"]), old[1], len(b["steps"])):
            state(conn, ref, status="held", error=f"новият отговор има {len(b['steps'])} етапа, пазеният {old[1]}")
            return "held"
        if old:
            db.log_change(conn, SOURCE, ref, "steps", old[1], len(b["steps"]), "rewritten")
        conn.execute("""INSERT INTO live.bill (id, sign, date, title, final_title, assembly, session, withdrawn, adopted, dv_issue, dv_year,
                                               government, sha, read_at)
                        VALUES (%(id)s, %(sign)s, %(date)s, %(title)s, %(final)s, %(assembly)s, %(session)s, %(withdrawn)s, %(adopted)s,
                                %(dv_issue)s, %(dv_year)s, %(government)s, %(sha)s, now())
                        ON CONFLICT (id) DO UPDATE SET sign = EXCLUDED.sign, date = EXCLUDED.date, title = EXCLUDED.title,
                          final_title = EXCLUDED.final_title, assembly = EXCLUDED.assembly, session = EXCLUDED.session,
                          withdrawn = EXCLUDED.withdrawn, adopted = EXCLUDED.adopted, dv_issue = EXCLUDED.dv_issue,
                          dv_year = EXCLUDED.dv_year, government = EXCLUDED.government, sha = EXCLUDED.sha, read_at = now()""",
                     {**b, "sha": sha})
        for table in ("bill_sponsor", "bill_committee", "bill_step"):
            conn.execute(f"DELETE FROM live.{table} WHERE bill = %s", (bid,))
        for i, (pid, name) in enumerate(b["sponsors"]):
            conn.execute("INSERT INTO live.bill_sponsor VALUES (%s,%s,%s,%s)", (bid, i, pid, name))
        for cid, name, role in b["committees"]:
            conn.execute("INSERT INTO live.bill_committee VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (bid, cid, name, role))
        for s in b["steps"]:
            conn.execute("INSERT INTO live.bill_step VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                         (bid, s["id"], s["date"], s["sitting"], s["committee"], s["committee_name"], s["what"], s["stage"]))
        state(conn, ref, status="ok", error=None, last_ok="now", last_change="now", rows=len(b["steps"]))
    return "stored"


def link(conn, ids=None):
    """live.bill_item: the votes of each hall step's sitting (by its number, else its day) whose topic has the bill's
    key; the reading is the step's, else the topic's. -> the number of links."""
    where, args = ("WHERE b.id = ANY(%s)", [ids]) if ids is not None else ("", [])
    steps = conn.execute(f"""SELECT DISTINCT b.id, b.title, st.stage, s.id FROM live.bill b
                             JOIN live.bill_step st ON st.bill = b.id AND st.stage LIKE 'зала%%'
                             JOIN live.sitting s ON s.id = st.sitting OR (st.sitting IS NULL AND s.date = st.date)
                             {where}""", args).fetchall()
    items = {}
    n = 0
    with conn.transaction():
        if ids is not None:
            conn.execute("DELETE FROM live.bill_item WHERE bill = ANY(%s)", (ids,))
        else:
            conn.execute("DELETE FROM live.bill_item")
        for bid, title, stage, sid in steps:
            if sid not in items:
                items[sid] = conn.execute("SELECT no, topic FROM live.item WHERE sitting = %s AND kind = 'vote'", (sid,)).fetchall()
            k = key(title)
            step_reading = next((r for w, r in READING.items() if w in (stage or "")), None)
            for no, topic in items[sid]:
                if key(topic) == k:
                    n += conn.execute("INSERT INTO live.bill_item VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                                      (bid, sid, no, reading(topic) or step_reading)).rowcount
    return n
