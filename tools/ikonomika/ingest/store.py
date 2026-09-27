"""Raw answers and the rule that nothing we hold is lost on one answer (STANDARD 1А.4-5).

apply() compares a parsed answer with what live.series holds for the same scope:
  - more or changed rows are written at once, each difference is a line in ops.change_log
    (a first read is not a change and is not logged);
  - an answer without a row we hold is `held`: nothing is written until a read at least a day later
    gives the same answer (same sha256), then it is `confirmed` and the rows are `removed`;
  - the same answer again changes nothing and logs nothing.
Everything of one answer is written in one transaction.
"""
import datetime as dt
import hashlib
import json
from decimal import Decimal

from . import db
from .config import RAW
from .jsonstat import ShapeError

CONFIRM_AFTER = dt.timedelta(days=1)


def dkey(dims):
    return json.dumps(dims, sort_keys=True, ensure_ascii=False)


def save_raw(conn, source, ref, name, raw):
    """Keep the answer under raw/<source>/<date>/, unless it is the same as the last one of this ref."""
    sha = hashlib.sha256(raw).hexdigest()
    last = conn.execute("SELECT sha256 FROM ops.raw_file WHERE source = %s AND ref = %s ORDER BY id DESC LIMIT 1",
                        (source, ref)).fetchone()
    if not last or last[0] != sha:
        path = RAW / source / str(dt.date.today()) / f"{sha[:12]}-{name}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        conn.execute("INSERT INTO ops.raw_file (source, ref, path, sha256, bytes) VALUES (%s,%s,%s,%s,%s)",
                     (source, ref, str(path), sha, len(raw)))
    return sha


def state(conn, source, ref, **kw):
    """Upsert ops.source_state; `now` in a value means the current time."""
    cols = ["last_read", *kw]
    vals = ["now()", *("now()" if v == "now" else "%s" for v in kw.values())]
    conn.execute(f"""INSERT INTO ops.source_state (source, ref, {', '.join(cols)}) VALUES (%s, %s, {', '.join(vals)})
                     ON CONFLICT (source, ref) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in cols)}""",
                 (source, ref, *(v for v in kw.values() if v != "now")))


def current(conn, indicator, where="", args=()):
    return {(dkey(d), g, t): (v, f) for d, g, t, v, f in conn.execute(
        f"SELECT dims, geo, time, value, flag FROM live.series WHERE indicator = %s {where}", (indicator, *args))}


def apply(conn, source, indicator, ref, sha, rows, labels=None, where="", args=()):
    """Write one parsed answer (rows of (dims, geo, time, value, flag)) for `indicator`, compared with what
    live.series holds under `where`. -> 'stored' | 'unchanged' | 'held'."""
    new = {}
    for dims, geo, time, value, flag in rows:
        k = (dkey(dims), geo, str(time))
        if k in new:
            raise ShapeError(f"the answer has {k} twice")
        new[k] = (None if value is None else Decimal(str(value)), flag)
    with conn.transaction():
        cur = current(conn, indicator, where, args)
        removed = cur.keys() - new.keys()
        held = conn.execute("SELECT sha256, first_at <= now() - %s FROM ops.held WHERE source = %s AND ref = %s",
                            (CONFIRM_AFTER, source, ref)).fetchone()
        if removed:
            if not held or held[0] != sha:
                conn.execute("""INSERT INTO ops.held (source, ref, sha256, rows) VALUES (%s,%s,%s,%s)
                                ON CONFLICT (source, ref) DO UPDATE SET sha256 = EXCLUDED.sha256, rows = EXCLUDED.rows, first_at = now()""",
                             (source, ref, sha, len(new)))
                db.log_change(conn, source, ref, "rows", len(cur), len(new), "held")
                state(conn, source, ref, status="held", error=f"{len(removed)} реда ги няма в новия отговор")
                return "held"
            if not held[1]:
                return "held"
            db.log_change(conn, source, ref, "rows", len(cur), len(new), "confirmed")
        if held:
            conn.execute("DELETE FROM ops.held WHERE source = %s AND ref = %s", (source, ref))
        put = [k for k in new if cur.get(k) != new[k]]
        if cur:
            for k in put:
                name = f"{indicator}/{k[0]}/{k[1]}/{k[2]}"
                if k not in cur:
                    db.log_change(conn, source, name, None, None, new[k][0], "new-record")
                else:
                    for i, field in enumerate(("value", "flag")):
                        if cur[k][i] != new[k][i]:
                            db.log_change(conn, source, name, field, cur[k][i], new[k][i], "rewritten")
            for k in removed:
                db.log_change(conn, source, f"{indicator}/{k[0]}/{k[1]}/{k[2]}", None, cur[k][0], None, "removed")
        if put:
            conn.execute("CREATE TEMP TABLE IF NOT EXISTS tmp_series (LIKE live.series) ON COMMIT DROP")
            with conn.cursor().copy("COPY tmp_series (indicator, dims, geo, time, value, flag) FROM STDIN") as cp:
                for k in put:
                    cp.write_row((indicator, *k, *new[k]))
            conn.execute("""INSERT INTO live.series SELECT * FROM tmp_series
                            ON CONFLICT (indicator, dims, geo, time) DO UPDATE SET value = EXCLUDED.value, flag = EXCLUDED.flag""")
        if removed:
            conn.execute("CREATE TEMP TABLE IF NOT EXISTS tmp_gone (dims jsonb, geo text, time text) ON COMMIT DROP")
            with conn.cursor().copy("COPY tmp_gone (dims, geo, time) FROM STDIN") as cp:
                for k in removed:
                    cp.write_row(k)
            conn.execute("""DELETE FROM live.series s USING tmp_gone g
                            WHERE s.indicator = %s AND s.dims = g.dims AND s.geo = g.geo AND s.time = g.time""", (indicator,))
        for dim, codes in (labels or {}).items():
            conn.cursor().executemany("""INSERT INTO live.dim_label VALUES (%s,%s,%s,%s)
                                         ON CONFLICT (indicator, dim, code) DO UPDATE SET label = EXCLUDED.label""",
                                      [(indicator, dim, c, lab) for c, lab in codes.items()])
        newer = cur and max(t for _, _, t in new) > max(t for _, _, t in cur) if new else False
        state(conn, source, ref, status="ok", error=None, last_ok="now", rows=len(new),
              **({"last_change": "now"} if put or removed else {}),
              **({"last_new_period": "now"} if newer or (not cur and new) else {}))
        return "stored" if put or removed else "unchanged"
