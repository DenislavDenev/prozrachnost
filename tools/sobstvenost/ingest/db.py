import contextlib
import psycopg
from .config import DSN, ROOT

def connect(**kw): return psycopg.connect(DSN,**kw)
def migrate(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS public.schema_migrations(name text PRIMARY KEY, applied_at timestamptz DEFAULT now())')
    conn.commit()
    for f in sorted((ROOT/'db/migrations').glob('*.sql')):
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE name=%s',(f.name,)).fetchone():
            with conn.transaction():
                conn.execute(f.read_text(encoding='utf-8'))
                conn.execute('INSERT INTO schema_migrations(name) VALUES (%s)',(f.name,))

@contextlib.contextmanager
def job(step):
    with connect(autocommit=True) as conn:
        key=8006000
        if not conn.execute('SELECT pg_try_advisory_lock(%s)',(key,)).fetchone()[0]: raise RuntimeError('another ownership import is running')
        id=conn.execute('INSERT INTO ops.job_run(step) VALUES (%s) RETURNING id',(step,)).fetchone()[0]
        try:
            yield conn
            conn.execute("UPDATE ops.job_run SET status='ok',finished_at=now() WHERE id=%s",(id,))
        except BaseException as e:
            conn.execute("UPDATE ops.job_run SET status='failed',finished_at=now(),error=%s WHERE id=%s",(str(e)[:4000],id))
            raise
        finally: conn.execute('SELECT pg_advisory_unlock(%s)',(key,))
