import contextlib
import json
import os
import subprocess
from pathlib import Path

import psycopg

from .config import DSN

ROOT = Path(__file__).resolve().parent.parent


def connect(**kw):
    return psycopg.connect(DSN, **kw)


def code_sha():
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return os.environ.get("TENDER_CODE_SHA")


def migrate(conn):
    """Apply db/migrations/*.sql in order, each in its own transaction. Stops at the first failure."""
    conn.execute("CREATE TABLE IF NOT EXISTS public.schema_migrations "
                 "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
    conn.commit()
    done = {r[0] for r in conn.execute("SELECT name FROM public.schema_migrations")}
    applied = []
    for f in sorted((ROOT / "db" / "migrations").glob("*.sql")):
        if f.name in done:
            continue
        with conn.transaction():
            conn.execute(f.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO public.schema_migrations(name) VALUES (%s)", (f.name,))
        applied.append(f.name)
    return applied


# one step at a time per lane: 'build' (eop -> publish), 'tr' (register reads) and 'offers' (procedure pages) run independently
LOCKS = {"build": 7_070_701, "tr": 7_070_702, "enrich": 7_070_703, "seed": 7_070_704, "offers": 7_070_705, "offers-check": 7_070_706, "sebra": 7_070_707}


class Busy(RuntimeError):
    """Another step of the same lane holds the lock; the caller reports this as skipped."""


@contextlib.contextmanager
def job(step, lane="build", inputs=None, rule_versions=None):
    """Record a run in ops.job_run under the lane's advisory lock. Yields (conn, stats dict)."""
    lock = LOCKS[lane]
    conn = connect(autocommit=True)
    if not conn.execute("SELECT pg_try_advisory_lock(%s)", (lock,)).fetchone()[0]:
        conn.close()
        raise Busy(f"another '{lane}' step is running")
    run_id = conn.execute(
        "INSERT INTO ops.job_run(step, code_sha, inputs, rule_versions) VALUES (%s,%s,%s,%s) RETURNING id",
        (step, code_sha(), json.dumps(inputs or {}), json.dumps(rule_versions or {}))).fetchone()[0]
    stats = {}
    try:
        yield conn, stats
        conn.execute("UPDATE ops.job_run SET status='ok', finished_at=now(), stats=%s WHERE id=%s",
                     (json.dumps(stats, default=str), run_id))
    except BaseException as e:
        conn.execute("UPDATE ops.job_run SET status='failed', finished_at=now(), stats=%s, error=%s "
                     "WHERE id=%s", (json.dumps(stats, default=str), repr(e)[:4000], run_id))
        raise
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (lock,))
        conn.close()
