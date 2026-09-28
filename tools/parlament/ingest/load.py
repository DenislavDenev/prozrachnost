"""The import: the roster, then every sitting of the months asked for, each checked before it is written.

A sitting is written only when its roll call, counted by group, is the file by group for every item and group
(parse.check). Its raw files are kept by sha256; the same files again change nothing. New files with fewer items or
votes than we hold are `held`: they replace ours only when a read at least a day later gives the same files. A sitting
that leaves the list of its month is never deleted here.
"""
import datetime as dt
import hashlib
import urllib.parse

from . import db, http, parse, stats
from .config import API, RAW, SITE

SOURCE = "parliament"
CONFIRM_AFTER = dt.timedelta(days=1)


def save_raw(conn, ref, name, raw):
    """Keep an answer under raw/<date>/, unless it is the same as the last one of this ref."""
    sha = hashlib.sha256(raw).hexdigest()
    last = conn.execute("SELECT sha256 FROM ops.raw_file WHERE source = %s AND ref = %s ORDER BY id DESC LIMIT 1",
                        (SOURCE, ref)).fetchone()
    if not last or last[0] != sha:
        path = RAW / str(dt.date.today()) / f"{sha[:12]}-{name}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        conn.execute("INSERT INTO ops.raw_file (source, ref, path, sha256, bytes) VALUES (%s,%s,%s,%s,%s)",
                     (SOURCE, ref, str(path), sha, len(raw)))
    return sha


def state(conn, ref, **kw):
    """Upsert ops.source_state; `now` in a value means the current time."""
    cols = ["last_read", *kw]
    vals = ["now()", *("now()" if v == "now" else "%s" for v in kw.values())]
    conn.execute(f"""INSERT INTO ops.source_state (source, ref, {', '.join(cols)}) VALUES (%s, %s, {', '.join(vals)})
                     ON CONFLICT (source, ref) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in cols)}""",
                 (SOURCE, ref, *(v for v in kw.values() if v != "now")))


# ---------- the roster ----------

def roster(conn, stats_, get=None):
    """The current assembly's MPs: profile, group, constituency. Rows are added and updated, never removed (an MP
    who leaves stays in the assembly's history)."""
    get = get or http.get
    raw = get(f"{API}/coll-list-ns/bg")
    got = parse.roster(raw)
    with conn.transaction():
        save_raw(conn, "roster", "coll-list-ns.json", raw)
        old = {r[0]: r[1:] for r in conn.execute(
            "SELECT profile, name, grp_name, district, since FROM live.roster WHERE assembly = %s", (got["assembly"],))}
        for m in got["mps"]:
            new = (m["name"], m["group"], m["district"], m["since"])
            if old.get(m["profile"]) == new:
                continue
            if m["profile"] in old:
                for f, a, b in zip(("name", "grp_name", "district", "since"), old[m["profile"]], new):
                    if a != b:
                        db.log_change(conn, SOURCE, f"roster/{got['assembly']}/{m['profile']}", f, a, b, "rewritten")
            elif old:
                db.log_change(conn, SOURCE, f"roster/{got['assembly']}/{m['profile']}", None, None, m["name"], "new-record")
            conn.execute("""INSERT INTO live.roster VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (assembly, profile)
                            DO UPDATE SET name = EXCLUDED.name, grp_name = EXCLUDED.grp_name, district = EXCLUDED.district,
                            since = EXCLUDED.since""", (got["assembly"], m["profile"], *new))
        state(conn, "roster", status="ok", error=None, last_ok="now", rows=len(got["mps"]))
    stats_.update(assembly=got["assembly"], mps=len(got["mps"]))
    return stats_


# ---------- the sittings ----------

def months(first, last):
    y, m = first
    while (y, m) <= last:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def load(conn, stats_, first=None, get=None, today=None):
    """Every sitting of the months from `first` (year, month) to this one. Without `first`: from the month before the
    last sitting we hold, so a late file or a correction is read again. -> stats with `problems`."""
    get = get or http.get
    today = today or dt.date.today()
    if first is None:
        last = conn.execute("SELECT max(date) FROM live.sitting WHERE iv_sha IS NOT NULL").fetchone()[0]
        last = last or today
        first = (last.year - (last.month == 1), 12 if last.month == 1 else last.month - 1)
    out, problems, touched = {}, [], set()
    for y, m in months(first, (today.year, today.month)):
        ref = f"month/{y}-{m:02d}"
        try:
            listed = parse.sittings(get(f"{API}/archive-period/bg/Pl_StenV/{y}/{m}/0/0"))
        except (http.Gone, parse.ShapeError) as e:
            state(conn, ref, status="invalid", error=str(e)[:2000])
            problems.append(f"Парламент: списъкът на заседанията за {m:02d}.{y}: {e}")
            continue
        state(conn, ref, status="ok", error=None, last_ok="now", rows=len(listed))
        for sid, _ in listed:
            try:
                got, assembly = sitting(conn, sid, get)
            except (http.Gone, parse.ShapeError) as e:
                got, assembly = "invalid", None
                state(conn, f"sten/{sid}", status="invalid", error=str(e)[:2000])
            out[got] = out.get(got, 0) + 1
            if got == "stored":
                touched.add(assembly)
            if got == "invalid":
                err = conn.execute("SELECT error FROM ops.source_state WHERE source = %s AND ref = %s", (SOURCE, f"sten/{sid}")).fetchone()
                problems.append(f"Парламент: заседание {sid}: {err[0]}")
    if touched:
        stats.rebuild(conn, sorted(touched))
    stats_.update(sittings=out, rebuilt=sorted(touched), problems=problems)
    return stats_


def recheck(conn, stats_, get=None):
    """Read again every sitting that was refused or had no files (after the parser learns a new layout), then give
    every assembly its groups' one code and rebuild what the pages read."""
    get = get or http.get
    out, problems, touched = {}, [], set()
    for sid, in conn.execute("""SELECT substr(ref, 6)::int FROM ops.source_state WHERE source = %s AND ref LIKE 'sten/%%'
                                AND status IN ('invalid', 'no-files') ORDER BY 1""", (SOURCE,)).fetchall():
        try:
            got, assembly = sitting(conn, sid, get)
        except (http.Gone, parse.ShapeError) as e:
            got, assembly = "invalid", None
            state(conn, f"sten/{sid}", status="invalid", error=str(e)[:2000])
        out[got] = out.get(got, 0) + 1
        if got == "invalid":
            problems.append(f"Парламент: заседание {sid}: " + conn.execute(
                "SELECT error FROM ops.source_state WHERE source = %s AND ref = %s", (SOURCE, f"sten/{sid}")).fetchone()[0])
    everyone = [a for a, in conn.execute("SELECT DISTINCT assembly FROM live.sitting ORDER BY 1")]
    stats.rebuild(conn, everyone)
    stats_.update(sittings=out, rebuilt=everyone, problems=problems)
    return stats_


def sitting(conn, sid, get):
    """Read one sitting and its files. -> (outcome, assembly): outcome is stored | unchanged | no-files | held |
    invalid (the files do not add up; the reason is in ops.source_state).

    Every CSV of the sitting is read and told apart by its content (parse.kind), not its name; a file whose name is
    for another day is not this sitting's (12.2023: the roll call of 1.12 under 11.12). The same file twice is one
    file. When a kind has two different files, the first pair that adds up is taken."""
    ref = f"sten/{sid}"
    raw = get(f"{API}/pl-sten/{sid}")
    s = parse.sitting(raw)
    if s["id"] != sid:
        raise parse.ShapeError(f"asked for sitting {sid}, got {s['id']}")
    if s["assembly"] is None:
        raise parse.ShapeError(f"the heading names no assembly: {s['heading'][:120]!r}")
    conn.execute("""INSERT INTO live.sitting (id, date, assembly, heading) VALUES (%s,%s,%s,%s)
                    ON CONFLICT (id) DO UPDATE SET date = EXCLUDED.date, assembly = EXCLUDED.assembly,
                    heading = EXCLUDED.heading""", (sid, s["date"], s["assembly"], s["heading"]))
    found, why = {"gv": {}, "iv": {}}, []
    for path in sorted(s["files"], reverse=True):              # the newest upload first (the name starts with its time)
        if parse.file_date(path) not in (None, s["date"]):
            why.append(f"{path.rsplit('/', 1)[-1]} е за друг ден")
            continue
        body = get(SITE + urllib.parse.quote(path))
        try:
            k = parse.kind(body) if body.strip() else None
        except parse.ShapeError as e:
            k, reason = None, str(e)
        else:
            reason = "празен" if not body.strip() else "непознат вид файл"
        if k is None:
            why.append(f"{path.rsplit('/', 1)[-1]}: {reason}")
            continue
        found[k].setdefault(hashlib.sha256(body).hexdigest(), (path, body))
    if not (found["gv"] and found["iv"]):
        missing = " и ".join(x for x, k in (("по групи", "gv"), ("поименно", "iv")) if not found[k])
        state(conn, ref, status="no-files", error="; ".join([f"няма файл {missing} в CSV", *why])[:2000], last_ok="now", rows=0)
        return "no-files", s["assembly"]
    tried = []
    for gsha, (gpath, gv) in found["gv"].items():
        for isha, (ipath, iv) in found["iv"].items():
            if conn.execute("SELECT 1 FROM live.sitting WHERE id = %s AND gv_sha = %s AND iv_sha = %s", (sid, gsha, isha)).fetchone():
                state(conn, ref, status="ok", error=None, last_ok="now")
                return "unchanged", s["assembly"]
            items = parse.groups(gv)
            votes, aside = parse.split_shifted(items, parse.rollcall(iv))
            bad, notes = parse.check(items, votes, {mp: g for mp, (_, g) in aside.items()})
            tried.append(bad)
            if not bad:
                break
        if not tried[-1]:
            break
    if tried[-1]:
        state(conn, ref, status="invalid", error="; ".join(tried[0][:5])[:2000])
        return "invalid", s["assembly"]
    held_sha = f"{gsha}:{isha}"
    s["gv"], s["iv"] = gpath, ipath
    note = (f"Знаците на {', '.join(n for n, _ in aside.values())} в поименното гласуване на Народното събрание са "
            f"разместени; гласовете {'му' if len(aside) == 1 else 'им'} от този ден не са показани, а резултатите са от "
            f"гласуването по групи." if aside else None)
    with conn.transaction():
        save_raw(conn, f"{ref}/gv", s["gv"].rsplit("/", 1)[-1], gv)
        save_raw(conn, f"{ref}/iv", s["iv"].rsplit("/", 1)[-1], iv)
        n_items, n_votes = conn.execute("""SELECT (SELECT count(*) FROM live.item WHERE sitting = %s),
                                                  (SELECT count(*) FROM live.vote WHERE sitting = %s)""", (sid, sid)).fetchone()
        if len(items) < n_items or len(votes) < n_votes:
            held = conn.execute("SELECT sha256, first_at <= now() - %s FROM ops.held WHERE source = %s AND ref = %s",
                                (CONFIRM_AFTER, SOURCE, ref)).fetchone()
            if not held or held[0] != held_sha:
                conn.execute("""INSERT INTO ops.held (source, ref, sha256, rows) VALUES (%s,%s,%s,%s) ON CONFLICT (source, ref)
                                DO UPDATE SET sha256 = EXCLUDED.sha256, rows = EXCLUDED.rows, first_at = now()""",
                             (SOURCE, ref, held_sha, len(votes)))
                db.log_change(conn, SOURCE, ref, "votes", n_votes, len(votes), "held")
                state(conn, ref, status="held", error=f"новите файлове имат {len(items)} точки и {len(votes)} гласа, "
                                                       f"пазените {n_items} и {n_votes}")
                return "held", s["assembly"]
            if not held[1]:
                return "held", s["assembly"]
            db.log_change(conn, SOURCE, ref, "votes", n_votes, len(votes), "confirmed")
        conn.execute("DELETE FROM ops.held WHERE source = %s AND ref = %s", (SOURCE, ref))
        write(conn, sid, s["assembly"], items, votes, notes, logged=n_votes > 0)
        conn.execute("UPDATE live.sitting SET gv = %s, iv = %s, gv_sha = %s, iv_sha = %s, note = %s WHERE id = %s",
                     (gpath, ipath, gsha, isha, note, sid))
        state(conn, ref, status="ok", error=None, last_ok="now", last_change="now", rows=len(votes))
    return "stored", s["assembly"]


def write(conn, sid, assembly, items, votes, notes, logged):
    """Replace the sitting's items and votes; when we held it before, every changed vote is a line in the log.
    `notes` {item: text}: where the roll call differs a little from the file by group (parse.check)."""
    old = {}
    if logged:
        old = {(i, mp): c for i, mp, c in conn.execute("SELECT item, mp, code FROM live.vote WHERE sitting = %s", (sid,))}
        new = {(item, no): code for no, _, _, item, code in votes}
        for k in sorted(old.keys() | new.keys()):
            if old.get(k) != new.get(k):
                db.log_change(conn, SOURCE, f"sten/{sid}/{k[0]}/{k[1]}", "code", old.get(k), new.get(k),
                              "rewritten" if k in old and k in new else "new-record" if k in new else "removed")
    conn.execute("DELETE FROM live.item WHERE sitting = %s", (sid,))
    with conn.cursor().copy("COPY live.item (sitting, no, kind, at, topic, yes, no_, abstain, voted, present, listed, mismatch) FROM STDIN") as cp:
        for no, it in sorted(items.items()):
            counts = (*it["total"], None, None) if it["kind"] == "vote" else (None, None, None, None, *it["total"])
            cp.write_row((sid, no, it["kind"], it["at"], it["topic"], *counts, notes.get(no)))
    with conn.cursor().copy("COPY live.item_group (sitting, no, grp, yes, no_, abstain, voted, present, listed) FROM STDIN") as cp:
        for no, it in sorted(items.items()):
            for g, c in it["groups"].items():
                c = c or (None,) * (4 if it["kind"] == "vote" else 2)   # an empty row in the file by group: no numbers
                cp.write_row((sid, no, g, *((*c, None, None) if it["kind"] == "vote" else (None, None, None, None, *c))))
    names = {no: name for no, name, *_ in votes}
    was = dict(conn.execute("SELECT no, name FROM live.mp WHERE assembly = %s AND no = ANY(%s)", (assembly, list(names))))
    for no, name in names.items():
        if was.get(no) == name:
            continue
        if no in was:
            db.log_change(conn, SOURCE, f"mp/{assembly}/{no}", "name", was[no], name, "rewritten")
        conn.execute("""INSERT INTO live.mp VALUES (%s,%s,%s) ON CONFLICT (assembly, no) DO UPDATE SET name = EXCLUDED.name""",
                     (assembly, no, name))
    with conn.cursor().copy("COPY live.vote (sitting, item, mp, grp, code) FROM STDIN") as cp:
        for no, _, g, item, code in votes:
            cp.write_row((sid, item, no, g, code))
