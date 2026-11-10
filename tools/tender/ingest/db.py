"""The connection, the lanes and the run ledger of Тендер in the shared database.

The runs, the change log, the rules and the migrations are the shared module (ingest/pipeline.py, schema ops).
What stays here is the lane: several steps of Тендер exclude each other (eop, fx, normalize, derive and publish
are one lane, the register reads another), which the per-step lock of the shared module does not express.
"""
import contextlib
import datetime as dt
import gzip

import psycopg

from . import pipeline
from .config import DSN, ROOT, TOOL


def connect(**kw):
    return psycopg.connect(DSN, **kw)


def code_sha():
    return pipeline.code_sha(ROOT)


def migrate(conn):
    """The migrations of db/migrations and the registers (datasets, sources, rules), through the shared module."""
    conn.autocommit = True
    pipeline.require_core(conn, "0010")
    return pipeline.migrate(conn, TOOL, ROOT)


# one step at a time per lane: 'build' (eop -> publish), 'tr' (register reads) and 'offers' (procedure pages) run independently
# 'daily' is the D-1 chain (ingest/daily.py) that runs the others in order; 'offers-queue' adds to the
# offers queue while a long read holds 'offers'
LOCKS = {"build": 7_070_701, "tr": 7_070_702, "enrich": 7_070_703, "seed": 7_070_704, "offers": 7_070_705, "offers-check": 7_070_706,
         "sebra": 7_070_707, "daily": 7_070_708, "offers-queue": 7_070_709, "tr-changes": 7_070_710}


class Busy(RuntimeError):
    """Another step of the same lane holds the lock; the caller reports this as skipped."""


@contextlib.contextmanager
def job(step, lane="build", inputs=None, rule_versions=None):
    """One step under the lane's advisory lock, recorded in ops.run. Yields (conn, stats dict).
    `conn.run` is the run of the shared module: log_change() and the rules need it."""
    lock = LOCKS[lane]
    conn = connect(autocommit=True)
    if not conn.execute("SELECT pg_try_advisory_lock(%s)", (lock,)).fetchone()[0]:
        conn.close()
        raise Busy(f"another '{lane}' step is running")
    try:
        try:
            with pipeline.run(conn, TOOL, step, params={**(inputs or {}), **({"rules": rule_versions} if rule_versions else {})},
                              sha=code_sha()) as r:
                RUNS[id(conn)] = r
                yield conn, r.stats
        except pipeline.Busy as e:   # the same step in another lane's hands
            raise Busy(str(e)) from e
    finally:
        RUNS.pop(id(conn), None)
        conn.execute("SELECT pg_advisory_unlock(%s)", (lock,))
        conn.close()


RUNS = {}   # the run of each open job connection
HOLD = dt.timedelta(hours=24)   # how long a smaller answer waits for a second read that agrees


def log_change(conn, source, ref, field, old, new, cause):
    """One line of ops.change_log: what a source changed after we first read it."""
    r = RUNS.get(id(conn))
    conn.execute("INSERT INTO ops.change_log (tool, source, ref, field, old, new, cause, run_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                 (TOOL, source, str(ref), field, None if old is None else str(old)[:2000], None if new is None else str(new)[:2000],
                  cause, r.run_id if r else None))


# ---------------------------------------------------------------- landing: one answer of a source, through the shared module

def run_of(conn):
    """The run of a connection. A call outside a job (a test, a call by hand) still needs one for the ledger."""
    r = RUNS.get(id(conn))
    if r is None or r.conn is not conn:
        run_id = conn.execute("INSERT INTO ops.run (tool, step, status, finished_at) VALUES (%s, 'manual', 'ok', now()) RETURNING run_id",
                              (TOOL,)).fetchone()[0]
        r = RUNS[id(conn)] = pipeline.Run(conn, TOOL, "manual", run_id)
    return r


def keep_raw(conn, source, ref, raw, folder, name, media_type=None, compress=True, fetched_at=None):
    """The answer as it came, in the register of raw files (ops.raw_file, ops.fetch). Stored gzipped with a fixed
    header, so the same answer is the same file and is stored once."""
    body = gzip.compress(raw, mtime=0) if compress else raw
    return pipeline.save_raw(conn, run_of(conn), source, str(ref), body, folder, name=name + (".gz" if compress else ""),
                             media_type=media_type, fetched_at=fetched_at)


def raw_of(conn, source, ref):
    """The bytes of the answer behind the current load of a resource (ungzipped), or None when there is none."""
    row = conn.execute("""SELECT l.raw_file_id, f.path FROM ops.load l JOIN ops.raw_file f USING (raw_file_id)
        WHERE l.tool = %s AND l.source = %s AND l.ref = %s AND l.is_current""", (TOOL, source, str(ref))).fetchone()
    if not row:
        return None
    raw = pipeline.read_raw(conn, row[0])
    return gzip.decompress(raw) if row[1].endswith(".gz") else raw


def land(conn, source, ref, rows, write, file=None, **kw):
    """One answer into the landing zone as one load (pipeline.load): the same content writes nothing, an answer with
    fewer rows than the current one is held until a second read at least a day later says the same."""
    kw.setdefault("hold", HOLD)
    return pipeline.load(conn, run_of(conn), source, str(ref), rows, write,
                         raw_file_id=file.raw_file_id if file else None, **kw)


def forget(conn, source, ref):
    """What we held of a resource is no longer ours to protect (a partida confirmed absent, or found unrelated): its
    load stops being the current one, so the next answer is taken as a first read. The ledger keeps the line."""
    conn.execute("UPDATE ops.load SET is_current = false WHERE tool = %s AND source = %s AND ref = %s AND is_current",
                 (TOOL, source, str(ref)))
    conn.execute("DELETE FROM ops.hold WHERE tool = %s AND source = %s AND ref = %s", (TOOL, source, str(ref)))


def reject(conn, source, ref, reason, file=None):
    pipeline.reject(conn, run_of(conn), source, str(ref), reason, file.raw_file_id if file else None)
