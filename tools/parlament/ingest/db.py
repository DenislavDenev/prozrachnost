"""The connection and the ledger of a step. The shared module (ingest/pipeline.py, an unchanged copy of
tools/danni/pipeline.py) writes the runs, the raw files, the holds and the change log in schema ops; a step runs
inside job(), and save_raw(), log_change() and the holds of ingest/load.py write to its run."""
import contextlib
import contextvars
import os

from . import pipeline
from .config import DSN, HEAVY_LOCK, ROOT, TOOL

Busy = pipeline.Busy
_run = contextvars.ContextVar("parlament_run", default=None)


def connect():
    """An autocommit connection to the shared database; the code opens its own transactions."""
    return pipeline.connect(DSN)


def migrate(conn):
    """db/migrations/*.sql, each once, and the registers (db/datasets.toml, db/checks.toml)."""
    pipeline.require_core(conn, "0009")
    return pipeline.migrate(conn, TOOL, ROOT)


@contextlib.contextmanager
def job(step, inputs=None, conn=None):
    """One run of a step in ops.run, under its advisory lock (a second one at the same time is skipped: Busy).
    Yields (conn, stats); the Run is db.current()."""
    own = conn is None
    conn = conn or connect()
    try:
        with pipeline.run(conn, TOOL, step, params=inputs, sha=pipeline.code_sha(ROOT)) as r:
            token = _run.set(r)
            try:
                yield conn, r.stats
            finally:
                _run.reset(token)
    finally:
        if own:
            conn.close()


def current():
    r = _run.get()
    if r is None:
        raise RuntimeError("a write outside a step: run it inside db.job()")
    return r


def log_change(conn, source, ref, field, old, new, cause):
    pipeline.log_change(conn, current(), source, str(ref), field, old, new, cause)


@contextlib.contextmanager
def heavy():
    """The lock the heavy steps of every tool share on CT 118; nothing to wait for where the file is not there, or when
    the caller already holds it (PZ_HEAVY_LOCK set empty: a flock taken by the shell would block its own child here)."""
    if not HEAVY_LOCK or not os.path.exists(HEAVY_LOCK):
        yield
        return
    import fcntl
    fd = os.open(HEAVY_LOCK, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)
