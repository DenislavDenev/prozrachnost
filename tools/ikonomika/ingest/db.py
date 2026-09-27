import contextlib
import json
import os
import subprocess

import psycopg

from .config import DSN, ROOT


def connect(**kw):
    return psycopg.connect(DSN, **kw)


def code_sha():
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return os.environ.get("IKONOMIKA_CODE_SHA")


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


LOCKS = {"eurostat": 7_250_001, "bnb": 7_250_002}


class Busy(RuntimeError):
    """Another run of the same step holds the lock; reported as skipped."""


@contextlib.contextmanager
def job(step, inputs=None):
    """Record a run in ops.job_run under the step's advisory lock. Yields (conn, stats dict)."""
    conn = connect(autocommit=True)
    if not conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCKS[step],)).fetchone()[0]:
        conn.close()
        raise Busy(f"another '{step}' run is going")
    run_id = conn.execute("INSERT INTO ops.job_run(step, code_sha, inputs) VALUES (%s,%s,%s) RETURNING id",
                          (step, code_sha(), json.dumps(inputs or {}))).fetchone()[0]
    stats = {}
    try:
        yield conn, stats
        conn.execute("UPDATE ops.job_run SET status='ok', finished_at=now(), stats=%s WHERE id=%s",
                     (json.dumps(stats, default=str), run_id))
    except BaseException as e:
        conn.execute("UPDATE ops.job_run SET status='failed', finished_at=now(), stats=%s, error=%s WHERE id=%s",
                     (json.dumps(stats, default=str), repr(e)[:4000], run_id))
        raise
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCKS[step],))
        conn.close()


def log_change(conn, source, ref, field, old, new, cause):
    conn.execute("INSERT INTO ops.change_log (source, ref, field, old, new, cause) VALUES (%s,%s,%s,%s,%s,%s)",
                 (source, str(ref), field, None if old is None else str(old)[:2000],
                  None if new is None else str(new)[:2000], cause))
