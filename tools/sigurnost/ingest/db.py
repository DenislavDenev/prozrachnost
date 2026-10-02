import psycopg

from . import config


def connect(**kw):
    return psycopg.connect(config.DSN, **kw)


def migrate(c):
    c.execute("CREATE TABLE IF NOT EXISTS public.schema_migrations (name text PRIMARY KEY)")
    c.commit()
    done = {r[0] for r in c.execute("SELECT name FROM public.schema_migrations")}
    for f in sorted((config.ROOT / "db/migrations").glob("*.sql")):
        if f.name in done:
            continue
        with c.transaction():
            c.execute(f.read_text(encoding="utf-8"))
            c.execute("INSERT INTO public.schema_migrations VALUES (%s)", (f.name,))
    c.commit()
