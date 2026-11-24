"""Which files to load and how: a file that arrives late, a year that has left the source, the replay of a whole year.

load.py loads one file; this module is the policy around it. It never downloads: the files come from the archive of
Наблюдател, and each one is registered in ops.raw_file under its sha256 (Наблюдател has registered it already; the
same row is found). The time a file counts from is the time the archive read it: its line in index.jsonl, or the last
good read of the archive while that read still saw the same file. A held file is confirmed only by a later read.
"""
import datetime as dt
import time

from . import archive, config, load, parse, pipeline


def when(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def evidence(st, snap, sha, idx):
    """The moment the archive last saw this file as the newest of its year, else the moment it wrote it."""
    seen = (st or {}).get("seen", {}).get(f"fy{snap.fy}", {})
    if seen.get("sha") == sha and st.get("last_ok"):
        return when(st["last_ok"])
    return when(idx.get(snap.rel, ("", 0, ""))[2]) or pipeline.utcnow()


def mark(c, fy, day, status, sha, path, size, note, load_id=None):
    """A file that is not written (held, invalid). A built one keeps its row."""
    load.ensure_year(c, fy)
    c.execute("""INSERT INTO subsidii.snapshot (fy, day, status, sha256, path, bytes, note, load_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                 ON CONFLICT (fy, day) DO UPDATE SET status = EXCLUDED.status, sha256 = EXCLUDED.sha256, path = EXCLUDED.path,
                 bytes = EXCLUDED.bytes, note = EXCLUDED.note, built_at = now() WHERE subsidii.snapshot.status <> 'built'""",
              (fy, day, status, sha, path, size, note, load_id))


def same_as_current(c, fy, day, sha):
    """The file is the one in force for its year: it was written from it and that load is still current."""
    return c.execute("""SELECT 1 FROM subsidii.snapshot s JOIN ops.load l ON l.load_id = s.load_id AND l.is_current
                        WHERE s.fy = %s AND s.day = %s AND s.status = 'built' AND s.sha256 = %s""", (fy, day, sha)).fetchone() is not None


def build_one(c, r, snap, raw, sha, path, raw_file_id=None, now=None, archive_rows=None, snaps=None, idx=None, st=None, reader=None):
    """One file, with the rules. Returns (state, report): built | confirmed | unchanged | held | invalid | rebuilt.

    A file of a day before the newest one written for its year (it reached the archive late), or a second file of
    the day already written, reads the whole year again in order (with `snaps`: the files of the archive)."""
    fy, day = snap.fy, snap.day
    if same_as_current(c, fy, day, sha):
        rows = load.stored_summary(c, "fy = %s AND day = %s", (fy, day))

        def never(*_):
            raise RuntimeError("the same file was taken as new")
        done = pipeline.load(c, r, load.SOURCE, str(fy), rows, never, raw_file_id=raw_file_id, current=load.current, now=now, hold=load.HOLD,
                             hold_when=load.hold_rule)
        return done.status, {"fy": fy, "day": str(day)}
    newest = load.newest_built(c, fy)
    if snaps is not None and newest and (day < newest or (day == newest and c.execute(
            "SELECT 1 FROM subsidii.snapshot WHERE fy = %s AND day = %s AND status = 'built' AND sha256 <> %s", (fy, day, sha)).fetchone())):
        old = c.execute("SELECT sha256 FROM subsidii.snapshot WHERE fy = %s AND day = %s", (fy, day)).fetchone()
        pipeline.log_change(c, r, load.SOURCE, str(fy), f"{day}/sha256", old[0] if old else None, sha, "rewritten", now)
        return "rebuilt", replay_year(c, r, fy, snaps, idx or {}, st=st, reader=reader)
    try:
        done, rep = load.apply(c, r, fy, day, raw, sha, path, raw_file_id=raw_file_id, now=now, archive_rows=archive_rows)
    except (parse.ShapeError, load.Mismatch, pipeline.ShapeError) as e:
        pipeline.reject(c, r, load.SOURCE, str(fy), e, raw_file_id, now=now)
        mark(c, fy, day, "invalid", sha, path, len(raw), str(e))
        return "invalid", {"fy": fy, "day": str(day), "error": str(e)}
    if done.status == "held":
        mark(c, fy, day, "held", sha, path, len(raw), rep.get("note"))
        return "held", rep
    return {"loaded": "built", "confirmed": "confirmed", "unchanged": "unchanged"}[done.status], rep


def read(c, r, snap, idx, root=None):
    """(raw, sha, path, raw_file_id): the file checked against its name and the archive's index, and registered."""
    raw, sha, path = archive.read(snap, idx, root)
    file, _ = pipeline.register_raw(c, r, load.SOURCE, str(snap.fy), path, media_type="text/csv", fetched_at=when(idx.get(snap.rel, ("", 0, ""))[2]),
                                    expect_sha=sha)
    return raw, sha, path, file.raw_file_id


def clear_year(c, fy):
    """The landing rows and the aggregates of a year, before it is read again from the archive. The ledger is not
    touched: the loads stay as history, only none of them is current any more."""
    from . import gold
    with c.transaction():
        gold.drop_year(c, fy)
        for t in ("subsidii_src.block_diff", "subsidii_src.payment", "subsidii_src.holder", "subsidii.snapshot"):
            c.execute(f"DELETE FROM {t} WHERE fy = %s", (fy,))
        c.execute("UPDATE ops.load SET is_current = false WHERE tool = %s AND source = %s AND ref = %s AND is_current", (config.TOOL, load.SOURCE, str(fy)))
        c.execute("DELETE FROM ops.hold WHERE tool = %s AND source = %s AND ref = %s", (config.TOOL, load.SOURCE, str(fy)))


def replay_year(c, r, fy, snaps, idx, st=None, root=None, reader=None):
    """The year again from the archive, file by file in order, from empty tables, each file at the time the archive read
    it. The archive's files never change, so the history comes back the same."""
    clear_year(c, fy)
    out = {"fy": fy, "files": 0, "states": []}
    for snap in snaps.get(fy, []):
        raw, sha, path, rid = reader(snap) if reader else read(c, r, snap, idx, root)
        state, _ = build_one(c, r, snap, raw, sha, path, raw_file_id=rid, now=evidence(st, snap, sha, idx))
        out["files"] += 1
        out["states"].append(state)
    return out


def sync_years(c, r, st, today=None):
    """Which years the source still offers (from the archive's last read). A year that has left stays, with the day it left."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    form = archive.years_in_form(st)
    out = {"gone": [], "new": []}
    if not form:
        return out
    for fy, in_form in c.execute("SELECT fy, in_form FROM subsidii.source_year").fetchall():
        if in_form and fy not in form:
            run = (st.get("run_at") or str(today))[:10]
            c.execute("UPDATE subsidii.source_year SET in_form = false, gone_at = %s WHERE fy = %s", (run, fy))
            pipeline.log_change(c, r, load.SOURCE, str(fy), "in_form", "true", "false", "gone")
            out["gone"].append(fy)
        elif not in_form and fy in form:
            c.execute("UPDATE subsidii.source_year SET in_form = true, gone_at = NULL WHERE fy = %s", (fy,))
    return out


def catch_up(c, r, st=None, root=None, limit_secs=None, only=None):
    """Every file of the archive that is not loaded yet, in order, year by year; the newest file of each year is
    looked at on every run, so the ledger says when the archive last confirmed it. A file older than the newest one
    written for its year (it reached the archive late) reads the year again. Returns the report of the run."""
    st = st or archive.state(root)
    idx = archive.index_shas(root)
    snaps = archive.snapshots(root, idx)
    t0 = time.monotonic()
    rep = {"built": 0, "confirmed": 0, "unchanged": 0, "held": 0, "invalid": 0, "rebuilt": 0, "files": [], "problems": [], "info": []}
    done = {(x[0], x[1]): (x[2], x[3]) for x in c.execute("SELECT fy, day, sha256, status FROM subsidii.snapshot")}
    for fy in sorted(snaps):
        if only and fy not in only:
            continue
        last_sha = st.get("seen", {}).get(f"fy{fy}", {}).get("sha")
        for snap in snaps[fy]:
            if limit_secs and time.monotonic() - t0 > limit_secs:
                rep["stopped"] = f"бюджетът от време свърши при {fy}/{snap.day}"
                return _finish(c, r, st, rep)
            want = idx.get(snap.rel)
            newest_file = snap is snaps[fy][-1]
            if want and done.get((fy, snap.day)) == (want[0], "built") and not newest_file:
                rep["unchanged"] += 1
                continue
            try:
                raw, sha, path, rid = read(c, r, snap, idx, root)
            except (archive.ArchiveError, pipeline.CorruptRaw) as e:
                rep["problems"].append(f"{fy}/{snap.day}: {e}")
                continue
            arows = st.get("last", {}).get(str(fy), {}).get("rows") if (newest_file and last_sha == sha) else None
            state, x = build_one(c, r, snap, raw, sha, path, raw_file_id=rid, now=evidence(st, snap, sha, idx), archive_rows=arows,
                                 snaps=snaps, idx=idx, st=st, reader=lambda s: read(c, r, s, idx, root))
            rep[state] += 1
            if state == "rebuilt":
                rep["info"].append(f"Субсидии: ФГ {fy}: файл от {snap.day} стигна до архива след по-нов или замени записан; годината е прочетена наново")
                rep["files"].append(dict(x, state=state))
                break
            if state != "unchanged":
                rep["files"].append(dict(x, state=state))
            if state == "invalid":
                rep["problems"].append(f"{fy}/{snap.day}: невалиден файл: {x.get('error')}")
    return _finish(c, r, st, rep)


def _finish(c, r, st, rep):
    y = sync_years(c, r, st)
    rep["years_gone"] = y["gone"]
    load.load_units(c)
    if y["gone"]:
        new = sorted(archive.years_in_form(st))
        rep["info"].append(f"Субсидии: нова финансова година {new[-1] if new else '?'} в ДФЗ, {', '.join(map(str, y['gone']))} вече не се показва там (пазим я)")
    return rep
