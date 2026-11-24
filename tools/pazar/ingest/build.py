"""Decides which days to build and how: the hold rule, rewritten days, a month rebuilt in order.

load.py builds one day; this module is the policy around it. It never downloads: the days come from the archive of
Наблюдател, whose files are in the shared register ops.raw_file. Every accepted file of a day is a load of the shared
module (source `kolkostruva`, ref = the day): its files go to pazar_src.file, its invalid rows to pazar_src.bad_row,
and the day is built in the same transaction.

A day whose file has far fewer chains or rows than the day before is held until the same file is read again at least
HOLD_AGAIN_H later (ops.hold; the shared module holds only a smaller second version of the same resource, and here the
comparison is with another day). A replay of the archive builds every day as it is, without holds.
"""
import datetime as dt
import zipfile
import io

from . import archive, config, load, parse, pipeline

SOURCE = "kolkostruva"


def d(s):
    return s if isinstance(s, dt.date) else dt.date.fromisoformat(s)


def log(c, r, ref, field, old, new, cause, at=None):
    pipeline.log_change(c, r, SOURCE, str(ref), field, old, new, cause, at)


def previous(c, day):
    """(chains, valid) of the last built or held day within 3 days before `day`; after a longer gap there is nothing to compare with."""
    return c.execute("""SELECT chains, valid FROM pazar.day WHERE status IN ('built', 'held') AND chains IS NOT NULL AND day < %s AND day >= %s
                        ORDER BY day DESC LIMIT 1""", (day, day - dt.timedelta(days=3))).fetchone()


def drop_is_big(c, day, parsed):
    prev = previous(c, day)
    if not prev:
        return None
    chains = sum(1 for f in parsed.files if f.eik and f.rows and not f.error)
    if chains < config.HOLD_CHAINS * prev[0]:
        return f"веригите паднаха от {prev[0]} на {chains}"
    if parsed.valid < config.HOLD_ROWS * prev[1]:
        return f"редовете паднаха от {prev[1]} на {parsed.valid}"
    return None


def mark_day(c, day, status, sha, path, size, note, chains=None, valid=None):
    """A day that is not built (held, invalid). A built day keeps its row."""
    c.execute("""INSERT INTO pazar.day (day, status, zip_sha256, zip_path, zip_bytes, note, chains, valid) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                 ON CONFLICT (day) DO UPDATE SET status = EXCLUDED.status, zip_sha256 = EXCLUDED.zip_sha256, zip_path = EXCLUDED.zip_path,
                 zip_bytes = EXCLUDED.zip_bytes, note = EXCLUDED.note, chains = EXCLUDED.chains, valid = EXCLUDED.valid, built_at = now()
                 WHERE pazar.day.status <> 'built'""", (day, status, sha, path, size, note, chains, valid))


def summary(raw, parsed):
    """{member: what was read from it}: the content of the day as the shared module compares it between two reads.
    CRC32 and size come from the ZIP, so a file that changed by one price is a different file."""
    info = {i.filename: i for i in zipfile.ZipFile(io.BytesIO(raw)).infolist() if not i.is_dir()}
    out = {}
    for f in parsed.files:
        i = info[f.member]
        out[f.member] = dict(chain_name=f.chain_name, eik=f.eik, eik_valid=bool(f.eik_valid), crc32=i.CRC, bytes=i.file_size,
                             delimiter=f.delimiter, bom=f.bom, encoding=f.encoding, truncated=f.truncated, records=f.records,
                             blank=f.blank, valid=len(f.rows), dups=f.dups, bad=len(f.bad), error=f.error, copy_of=f.copy_of)
    return out


FILE_COLUMNS = ("chain_name", "eik", "eik_valid", "crc32", "bytes", "delimiter", "bom", "encoding", "truncated", "records", "blank",
                "valid", "dups", "bad", "error", "copy_of")


def current_files(c, load_id):
    cols = ", ".join(FILE_COLUMNS)
    return {row[0]: dict(zip(FILE_COLUMNS, row[1:])) for row in
            c.execute(f"SELECT member, {cols} FROM pazar_src.file WHERE load_id = %s", (load_id,))}


def write_landing(c, load_id, files, parsed):
    with c.cursor() as cur:
        cur.executemany(f"INSERT INTO pazar_src.file (load_id, member, {', '.join(FILE_COLUMNS)}) VALUES (%s, %s{', %s' * len(FILE_COLUMNS)})",
                        [(load_id, m, *(v[k] for k in FILE_COLUMNS)) for m, v in files.items()])
        with cur.copy("COPY pazar_src.bad_row (load_id, member, line_no, reason, raw) FROM STDIN") as cp:
            for f in parsed.files:
                for line, reason, text in f.bad:
                    cp.write_row((load_id, f.member, line, reason, text))


def accept(c, r, day, raw, sha, path, raw_file_id, parsed, build, note=None, replace=False, now=None):
    """The day's file as a load of the shared module, and, when `build`, the day built in the same transaction.
    Returns (status of the load, report of the build or None)."""
    files = summary(raw, parsed)
    out = {}

    def write(conn, load_id, rows):
        write_landing(conn, load_id, rows, parsed)
        if build:
            out["report"] = load.build_day(conn, r, day, parsed, sha, path, len(raw), load_id, note=note, replace=replace)
        else:
            conn.execute("UPDATE pazar.day SET load_id = %s WHERE day = %s", (load_id, day))

    # a rewritten file replaces the day at once, as the build always did: only a smaller file of another day is held
    done = pipeline.load(c, r, SOURCE, str(day), files, write, raw_file_id=raw_file_id, current=current_files, now=now,
                         hold_when=lambda old, rows: 0)
    return done, out.get("report")


def build_one(c, r, day, raw, sha, path, raw_file_id, now=None, replay=False, parsed=None):
    """One day, with the rules. Returns (state, report): built | unchanged | held | invalid | rebuilt."""
    now = now or dt.datetime.now(dt.timezone.utc)
    size = len(raw)
    row = c.execute("SELECT status, zip_sha256 FROM pazar.day WHERE day = %s", (day,)).fetchone()
    if row and row[0] == "built" and row[1] == sha:
        return "unchanged", {"day": str(day)}
    try:
        parsed = parsed or parse.parse_day(raw)
    except parse.ShapeError as e:
        with c.transaction():
            mark_day(c, day, "invalid", sha, path, size, str(e))
            pipeline.reject(c, r, SOURCE, str(day), e, raw_file_id, now=now)
        return "invalid", {"day": str(day), "error": str(e)}
    rewritten = bool(row and row[0] == "built")
    confirmed = False
    if not replay:
        why = drop_is_big(c, day, parsed)
        if why and not rewritten:
            held = c.execute("SELECT content_sha256, first_at FROM ops.hold WHERE tool = %s AND source = %s AND ref = %s",
                             (config.TOOL, SOURCE, str(day))).fetchone()
            if held and held[0] == sha and now - held[1] >= dt.timedelta(hours=config.HOLD_AGAIN_H):
                confirmed = True
            else:
                with c.transaction():
                    if not held or held[0] != sha:
                        c.execute("""INSERT INTO ops.hold (tool, source, ref, content_sha256, rows, reason, first_at) VALUES (%s, %s, %s, %s, %s, %s, %s)
                                     ON CONFLICT (tool, source, ref) DO UPDATE SET content_sha256 = EXCLUDED.content_sha256, rows = EXCLUDED.rows,
                                       reason = EXCLUDED.reason, first_at = EXCLUDED.first_at""",
                                  (config.TOOL, SOURCE, str(day), sha, parsed.valid, why, now))
                        log(c, r, day, None, None, sha, "held", now)
                    mark_day(c, day, "held", sha, path, size, why, sum(1 for f in parsed.files if f.eik and f.rows and not f.error), parsed.valid)
                r.count("held")
                return "held", {"day": str(day), "note": why}
    nxt = (day.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    later = c.execute("SELECT 1 FROM pazar.day WHERE status = 'built' AND day > %s AND day < %s", (day, nxt)).fetchone()   # in its month
    if confirmed:                  # the month rebuild leaves out the days still held: this one is not held any more
        with c.transaction():
            c.execute("DELETE FROM pazar.day WHERE day = %s AND status = 'held'", (day,))
            c.execute("DELETE FROM ops.hold WHERE tool = %s AND source = %s AND ref = %s", (config.TOOL, SOURCE, str(day)))
            log(c, r, day, None, None, sha, "confirmed", now)
    if rewritten or later:
        if rewritten:
            log(c, r, day, "sha256", row[1], sha, "rewritten", now)
        accept(c, r, day, raw, sha, path, raw_file_id, parsed, build=False, now=now)
        return "rebuilt", rebuild_month(c, r, day.replace(day=1), parsed={day: parsed})
    note = "confirmed after a second read" if confirmed else None
    _, report = accept(c, r, day, raw, sha, path, raw_file_id, parsed, build=True, note=note, replace=bool(row), now=now)
    load.analyze(c, day)
    return "built", report


def rebuild_month(c, r, ms, parsed=None):
    """The month again, day by day in order, from an empty partition: every day of the month whose file was accepted
    (it has a current load), read again from its registered file. A held day (not confirmed) or an invalid one has no
    accepted file and stays out. `parsed` saves the parsing of a day already parsed."""
    ym = ms.strftime("%Y%m")
    nxt = (ms + dt.timedelta(days=32)).replace(day=1)
    days = [(dt.date.fromisoformat(ref), *rest) for ref, *rest in c.execute(
        """SELECT l.ref, l.load_id, l.raw_file_id, f.sha256, f.path FROM ops.load l JOIN ops.raw_file f USING (raw_file_id)
           WHERE l.tool = %s AND l.source = %s AND l.is_current AND l.ref >= %s AND l.ref < %s ORDER BY l.ref""",
        (config.TOOL, SOURCE, str(ms), str(nxt))).fetchall()]
    with c.transaction():
        c.execute(f"DROP TABLE IF EXISTS pazar.price_span_{ym}")
        c.execute("DELETE FROM pazar.chain_day WHERE day >= %s AND day < %s", (ms, nxt))
        c.execute("DELETE FROM pazar.day WHERE day = ANY(%s)", ([x[0] for x in days],))
        c.execute("SELECT pazar.ensure_month(%s)", (ms,))
    out = {"month": ym, "days": 0, "secs": 0.0}
    for day, load_id, raw_file_id, sha, path in days:
        raw = pipeline.read_raw(c, raw_file_id)
        p = (parsed or {}).get(day) or parse.parse_day(raw)
        report = load.build_day(c, r, day, p, sha, path, len(raw), load_id, note="month rebuilt", replace=True)
        out["days"] += 1
        out["secs"] += report["secs"]
    load.analyze(c, ms)
    r.count("months_rebuilt")
    return out


def catch_up(c, r, first=None, last=None, now=None, st=None, limit_secs=None, replay=False):
    """Every archived day in [first, last] that is not built from its newest file, in order. A built day is not read
    again. The next file is read and parsed in a second thread while the database works on the current one."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    st = st or archive.state()
    t0 = time.monotonic()
    rep = {"built": 0, "unchanged": 0, "held": 0, "invalid": 0, "rebuilt": 0, "days": [], "problems": []}
    built = {r_[0]: r_[1] for r_ in c.execute("SELECT day, zip_sha256 FROM pazar.day WHERE status = 'built'")}
    todo = []
    for k, (sha, _) in sorted(archive.days(st).items()):
        if (first and d(k) < d(first)) or (last and d(k) > d(last)):
            continue
        if built.get(d(k)) == sha:
            rep["unchanged"] += 1
            continue
        todo.append(k)

    def load_day(k):
        raw, sha, path = archive.read(k, st)
        try:
            return raw, sha, path, parse.parse_day(raw)
        except parse.ShapeError:            # build_one parses again, marks the day invalid and says why
            return raw, sha, path, None

    with ThreadPoolExecutor(1) as pool:
        ahead = pool.submit(load_day, todo[0]) if todo else None
        for i, k in enumerate(todo):
            day = d(k)
            if limit_secs and time.monotonic() - t0 > limit_secs:
                rep["stopped"] = "бюджетът от време свърши при " + k
                break
            try:
                raw, sha, path, parsed = ahead.result()
            except archive.ArchiveError as e:
                rep["problems"].append(f"{k}: {e}")
                ahead = pool.submit(load_day, todo[i + 1]) if i + 1 < len(todo) else None
                continue
            ahead = pool.submit(load_day, todo[i + 1]) if i + 1 < len(todo) else None
            raw_file_id = archive.raw_file(c, r, day, sha, path)
            try:
                state, rp = build_one(c, r, day, raw, sha, path, raw_file_id, now=now, replay=replay, parsed=parsed)
            except load.Mismatch as e:
                with c.transaction():
                    mark_day(c, day, "invalid", sha, path, len(raw), str(e))
                    pipeline.reject(c, r, SOURCE, str(day), e, raw_file_id, now=now)
                state, rp = "invalid", {"day": k, "error": str(e)}
            rep[state] += 1
            rep.setdefault("changed_from", k)
            rep["changed_from"] = min(rep["changed_from"], k)
            rep["days"].append(rp if state in ("held", "invalid") else {kk: rp[kk] for kk in ("day", "valid", "bad", "chains", "secs") if kk in rp} | {"state": state, "day": k})
            if state == "invalid":
                rep["problems"].append(f"{k}: невалиден ден: {rp.get('error')}")
            if state == "rebuilt":
                rep["changed_from"] = min(rep["changed_from"], k[:8] + "01")
    return rep
