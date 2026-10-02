"""Which files to load and how: the hold rule, a file that arrives late, a year that has left the source.

load.py loads one file; this module is the policy around it. It never downloads: the files come from the archive.
"""
import datetime as dt
import time

from . import archive, config, load, parse


def log(c, ref, field, old, new, cause):
    load.log(c, ref, field, old, new, cause)


def hold_rule(stats, prev):
    """A file with clearly fewer recipients, payments or money than the previous file of the same year waits for a second read."""
    if not prev:
        return None
    holders, payments, total = prev
    if holders and stats["holders"] < config.HOLD_ROWS * holders:
        return f"получателите паднаха от {holders} на {stats['holders']}"
    if payments and stats["payments"] < config.HOLD_ROWS * payments:
        return f"плащанията паднаха от {payments} на {stats['payments']}"
    if total and stats["total"]["total"] < config.HOLD_SUM * total:
        return f"общата сума падна от {total} на {stats['total']['total']}"
    return None


def mark(c, fy, day, status, sha, path, size, note):
    """A snapshot that is not built (held, invalid). A built one keeps its row."""
    load.ensure_year(c, fy)
    c.execute("""INSERT INTO silver.snapshot (fy, day, status, sha256, path, bytes, note) VALUES (%s, %s, %s, %s, %s, %s, %s)
                 ON CONFLICT (fy, day) DO UPDATE SET status = EXCLUDED.status, sha256 = EXCLUDED.sha256, path = EXCLUDED.path,
                 bytes = EXCLUDED.bytes, note = EXCLUDED.note, built_at = now() WHERE silver.snapshot.status <> 'built'""",
              (fy, day, status, sha, path, size, note))


def confirmed(st, fy, sha, first_seen, now):
    """The second read: the archive read the source again at least 20 hours after the hold and the newest file of the
    year is still this one."""
    if not st:
        return False
    seen = st.get("seen", {}).get(f"fy{fy}", {})
    try:
        last = dt.datetime.fromisoformat(st["last_ok"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return False
    return seen.get("sha") == sha and last - first_seen >= dt.timedelta(hours=20)


def build_one(c, snap, raw, sha, path, st=None, now=None, archive_rows=None, snaps=None, idx=None, reader=None):
    """One file, with the rules. Returns (state, report): built | unchanged | held | invalid | rebuilt."""
    now = now or dt.datetime.now(dt.timezone.utc)
    fy, day = snap.fy, snap.day
    ref = f"{fy}/{day}"
    row = c.execute("SELECT status, sha256 FROM silver.snapshot WHERE fy = %s AND day = %s", (fy, day)).fetchone()
    if row and row[0] == "built" and row[1] == sha:
        return "unchanged", {"fy": fy, "day": str(day)}
    newest = load.newest_built(c, fy)
    if newest and (day < newest or (day == newest and row and row[0] == "built")) and snaps is not None:
        log(c, ref, "sha256", row[1] if row else None, sha, "rewritten")
        return "rebuilt", rebuild_year(c, fy, snaps, idx=idx, st=st, now=now, reader=reader)
    load.raw_file(c, fy, day, sha, path, len(raw))
    held = c.execute("SELECT sha256, first_seen FROM ops.held WHERE ref = %s", (ref,)).fetchone()
    ok_second = bool(held and held[0] == sha and confirmed(st, fy, sha, held[1], now))
    try:
        rep = load.apply(c, fy, day, raw, sha, path, decide=None if ok_second else hold_rule, archive_rows=archive_rows,
                         note="confirmed after a second read" if ok_second else None)
    except (parse.ShapeError, load.Mismatch) as e:
        mark(c, fy, day, "invalid", sha, path, len(raw), str(e))
        log(c, ref, None, row[1] if row else None, sha, "invalid")
        return "invalid", {"fy": fy, "day": str(day), "error": str(e)}
    except load.Held as h:
        if not held or held[0] != sha:
            c.execute("""INSERT INTO ops.held (ref, sha256, first_seen, reason) VALUES (%s, %s, %s, %s)
                         ON CONFLICT (ref) DO UPDATE SET sha256 = EXCLUDED.sha256, first_seen = EXCLUDED.first_seen, reason = EXCLUDED.reason""",
                      (ref, sha, now, h.reason))
            log(c, ref, None, None, sha, "held")
        mark(c, fy, day, "held", sha, path, len(raw), h.reason)
        return "held", {"fy": fy, "day": str(day), "note": h.reason}
    c.execute("DELETE FROM ops.held WHERE split_part(ref, '/', 1) = %s AND split_part(ref, '/', 2)::date <= %s", (str(fy), day))
    if ok_second:
        log(c, ref, None, None, sha, "confirmed")
    elif newest is None:
        log(c, ref, "rows", None, rep["rows"], "new-record")
    else:
        log(c, ref, "rows", c.execute("SELECT rows_file FROM silver.snapshot WHERE fy = %s AND day = %s", (fy, newest)).fetchone()[0], rep["rows"], "rewritten")
    return "built", rep


def rebuild_year(c, fy, snaps, idx=None, st=None, now=None, reader=None):
    """The year again from the archive, file by file in order, from empty tables. The bronze files are never changed, so
    the history comes back the same."""
    from . import gold
    with c.transaction():
        gold.drop_year(c, fy)
        for t in ("silver.block_diff", "silver.payment", "silver.holder", "silver.snapshot"):
            c.execute(f"DELETE FROM {t} WHERE fy = %s", (fy,))
    out = {"fy": fy, "files": 0}
    for snap in snaps.get(fy, []):
        raw, sha, path = reader(snap) if reader else archive.read(snap, idx)
        state, rep = build_one(c, snap, raw, sha, path, st=st, now=now, snaps=None)
        out["files"] += 1
        out.setdefault("states", []).append(state)
    return out


def sync_years(c, st, today=None):
    """Which years the source still offers (from the archive's last read). A year that has left stays, with the day it left."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    form = archive.years_in_form(st)
    out = {"gone": [], "new": []}
    if not form:
        return out
    for fy, in_form in c.execute("SELECT fy, in_form FROM silver.fiscal_year").fetchall():
        if in_form and fy not in form:
            run = (st.get("run_at") or str(today))[:10]
            c.execute("UPDATE silver.fiscal_year SET in_form = false, gone_at = %s WHERE fy = %s", (run, fy))
            log(c, fy, "in_form", "true", "false", "gone")
            out["gone"].append(fy)
        elif not in_form and fy in form:
            c.execute("UPDATE silver.fiscal_year SET in_form = true, gone_at = NULL WHERE fy = %s", (fy,))
    return out


def catch_up(c, st=None, now=None, root=None, limit_secs=None, only=None):
    """Every file of the archive that is not loaded yet, in order, year by year. Returns the report of the run."""
    st = st or archive.state(root)
    idx = archive.index_shas(root)
    snaps = archive.snapshots(root, idx)
    t0 = time.monotonic()
    rep = {"built": 0, "unchanged": 0, "held": 0, "invalid": 0, "rebuilt": 0, "files": [], "problems": [], "info": []}
    done = {(r[0], r[1]): (r[2], r[3]) for r in c.execute("SELECT fy, day, sha256, status FROM silver.snapshot")}
    for fy in sorted(snaps):
        if only and fy not in only:
            continue
        last_sha = st.get("seen", {}).get(f"fy{fy}", {}).get("sha")
        for snap in snaps[fy]:
            if limit_secs and time.monotonic() - t0 > limit_secs:
                rep["stopped"] = f"бюджетът от време свърши при {fy}/{snap.day}"
                return _finish(c, st, rep)
            want = idx.get(snap.rel)
            if want and done.get((fy, snap.day)) == (want[0], "built"):
                rep["unchanged"] += 1
                continue
            try:
                raw, sha, path = archive.read(snap, idx, root)
            except archive.ArchiveError as e:
                rep["problems"].append(f"{fy}/{snap.day}: {e}")
                continue
            newest_file = snap is snaps[fy][-1]
            arows = st.get("last", {}).get(str(fy), {}).get("rows") if (newest_file and last_sha == sha) else None
            state, r = build_one(c, snap, raw, sha, path, st=st, now=now, archive_rows=arows, snaps=snaps, idx=idx)
            rep[state] += 1
            rep["files"].append(dict(r, state=state))
            if state == "invalid":
                rep["problems"].append(f"{fy}/{snap.day}: невалиден файл: {r.get('error')}")
    return _finish(c, st, rep)


def _finish(c, st, rep):
    y = sync_years(c, st)
    rep["years_gone"] = y["gone"]
    load.load_units(c)
    if y["gone"]:
        new = sorted(archive.years_in_form(st))
        rep["info"].append(f"Субсидии: нова финансова година {new[-1] if new else '?'} в ДФЗ, {', '.join(map(str, y['gone']))} вече не се показва там (пазим я)")
    return rep
