"""Decides which days to build and how: the hold rule, rewritten days, a month rebuilt in order.

load.py builds one day; this module is the policy around it. It never downloads: the days come from the archive.
"""
import datetime as dt
import json

from . import archive, config, load, parse


def d(s):
    return s if isinstance(s, dt.date) else dt.date.fromisoformat(s)


def log(c, ref, field, old, new, cause):
    c.execute("INSERT INTO ops.change_log (source, ref, field, old, new, cause) VALUES ('kolkostruva', %s, %s, %s, %s, %s)",
              (str(ref), field, old, new, cause))


def raw_file(c, day, sha, path, size):
    c.execute("INSERT INTO ops.raw_file (source, ref, path, sha256, bytes) VALUES ('kolkostruva', %s, %s, %s, %s) ON CONFLICT DO NOTHING",
              (str(day), path, sha, size))


def previous(c, day):
    """(chains, valid) of the last built day before `day`."""
    return c.execute("SELECT chains, valid FROM silver.day WHERE status IN ('built', 'held') AND chains IS NOT NULL AND day < %s ORDER BY day DESC LIMIT 1", (day,)).fetchone()


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
    c.execute("""INSERT INTO silver.day (day, status, zip_sha256, zip_path, zip_bytes, note, chains, valid) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                 ON CONFLICT (day) DO UPDATE SET status = EXCLUDED.status, zip_sha256 = EXCLUDED.zip_sha256, zip_path = EXCLUDED.zip_path,
                 zip_bytes = EXCLUDED.zip_bytes, note = EXCLUDED.note, chains = EXCLUDED.chains, valid = EXCLUDED.valid, built_at = now()
                 WHERE silver.day.status <> 'built'""", (day, status, sha, path, size, note, chains, valid))


def build_one(c, day, raw, sha, path, now=None, month_replay=False, st=None, source=None):
    """One day, with the rules. Returns (state, report): built | unchanged | held | invalid | rebuilt."""
    now = now or dt.datetime.now(dt.timezone.utc)
    size = len(raw)
    row = c.execute("SELECT status, zip_sha256 FROM silver.day WHERE day = %s", (day,)).fetchone()
    if row and row[0] == "built" and row[1] == sha:
        return "unchanged", {"day": str(day)}
    raw_file(c, day, sha, path, size)
    try:
        parsed = parse.parse_day(raw)
    except parse.ShapeError as e:
        mark_day(c, day, "invalid", sha, path, size, str(e))
        log(c, day, None, row[1] if row else None, sha, "invalid")
        return "invalid", {"day": str(day), "error": str(e)}
    rewritten = bool(row and row[0] == "built")
    confirmed = False
    if not month_replay:
        why = drop_is_big(c, day, parsed)
        if why and not rewritten:
            held = c.execute("SELECT sha256, first_seen FROM ops.held WHERE ref = %s", (str(day),)).fetchone()
            if held and held[0] == sha and now - held[1] >= dt.timedelta(hours=20):
                confirmed = True
            else:
                if not held or held[0] != sha:
                    c.execute("""INSERT INTO ops.held (ref, sha256, first_seen, reason) VALUES (%s, %s, %s, %s)
                                 ON CONFLICT (ref) DO UPDATE SET sha256 = EXCLUDED.sha256, first_seen = EXCLUDED.first_seen, reason = EXCLUDED.reason""",
                              (str(day), sha, now, why))
                    log(c, day, None, None, sha, "held")
                mark_day(c, day, "held", sha, path, size, why, sum(1 for f in parsed.files if f.eik and f.rows and not f.error), parsed.valid)
                return "held", {"day": str(day), "note": why}
    later = c.execute("SELECT 1 FROM silver.day WHERE status = 'built' AND day > %s AND day >= %s", (day, day.replace(day=1))).fetchone()
    if rewritten or later:
        if rewritten:
            log(c, day, "sha256", row[1], sha, "rewritten")
        report = rebuild_month(c, day.replace(day=1), st=st, source=source or {day: (raw, sha, path)})
        return "rebuilt", report
    report = load.build_day(c, day, parsed, sha, path, size, note="confirmed after a second read" if confirmed else None, replace=bool(row))
    if confirmed:
        log(c, day, None, None, sha, "confirmed")
    c.execute("DELETE FROM ops.held WHERE ref = %s", (str(day),))
    return "built", report


def rebuild_month(c, ms, st=None, source=None):
    """The month again, day by day in order, from an empty partition. The files come from the archive (`st` is its
    state) or, in tests, from `source`, {day: (raw, sha, path)}. A day that is held and has not been confirmed stays out."""
    if source is None:
        st = st or archive.state()
        source = {d(k): None for k in archive.days(st)}
    ym = ms.strftime("%Y%m")
    nxt = (ms + dt.timedelta(days=32)).replace(day=1)
    held = {r[0] for r in c.execute("SELECT day FROM silver.day WHERE status = 'held' AND day >= %s AND day < %s", (ms, nxt))}
    with c.transaction():
        c.execute(f"DROP TABLE IF EXISTS silver.price_span_{ym}")
        for t in ("silver.chain_day", "silver.bad_row"):
            c.execute(f"DELETE FROM {t} WHERE day >= %s AND day < %s", (ms, nxt))
        c.execute("DELETE FROM silver.day WHERE status = 'built' AND day >= %s AND day < %s", (ms, nxt))
        c.execute("SELECT silver.ensure_month(%s)", (ms,))
    out = {"month": ym, "days": 0, "secs": 0.0}
    for day in sorted(k for k in source if ms <= k < nxt):
        if day in held:
            continue
        raw, sha, path = source[day] or archive.read(str(day), st)
        parsed = parse.parse_day(raw)
        report = load.build_day(c, day, parsed, sha, path, len(raw), note="month rebuilt", replace=True)
        out["days"] += 1
        out["secs"] += report["secs"]
    return out


def catch_up(c, first=None, last=None, now=None, st=None, limit_secs=None):
    """Every archived day in [first, last] that is not built, in order. Returns the report of the run."""
    import time
    st = st or archive.state()
    t0 = time.monotonic()
    rep = {"built": 0, "unchanged": 0, "held": 0, "invalid": 0, "rebuilt": 0, "days": [], "problems": []}
    for k in sorted(archive.days(st)):
        day = d(k)
        if (first and day < d(first)) or (last and day > d(last)):
            continue
        if limit_secs and time.monotonic() - t0 > limit_secs:
            rep["stopped"] = "бюджетът от време свърши при " + k
            break
        try:
            raw, sha, path = archive.read(k, st)
        except archive.ArchiveError as e:
            rep["problems"].append(f"{k}: {e}")
            continue
        try:
            state, r = build_one(c, day, raw, sha, path, now=now, st=st)
        except load.Mismatch as e:
            mark_day(c, day, "invalid", sha, path, len(raw), str(e))
            log(c, day, None, None, sha, "invalid")
            state, r = "invalid", {"day": k, "error": str(e)}
        rep[state] += 1
        if state != "unchanged":
            rep["days"].append(r if state in ("held", "invalid") else {kk: r[kk] for kk in ("day", "valid", "bad", "chains", "secs") if kk in r} | ({"state": state}))
        if state == "invalid":
            rep["problems"].append(f"{k}: невалиден ден: {r.get('error')}")
    return rep
