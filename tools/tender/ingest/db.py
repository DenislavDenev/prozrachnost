"""The connection, the lanes and the run ledger of Тендер in the shared database.

The runs, the change log, the rules and the migrations are the shared module (ingest/pipeline.py, schema ops).
What stays here is the lane: several steps of Тендер exclude each other (eop, fx, normalize, derive and publish
are one lane, the register reads another), which the per-step lock of the shared module does not express.
"""
import contextlib

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
         "sebra": 7_070_707, "daily": 7_070_708, "offers-queue": 7_070_709}


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


def log_change(conn, source, ref, field, old, new, cause):
    """One line of ops.change_log: what a source changed after we first read it."""
    r = RUNS.get(id(conn))
    conn.execute("INSERT INTO ops.change_log (tool, source, ref, field, old, new, cause, run_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                 (TOOL, source, str(ref), field, None if old is None else str(old)[:2000], None if new is None else str(new)[:2000],
                  cause, r.run_id if r else None))
