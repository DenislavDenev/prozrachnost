"""Build steps of the 'build' lane: eop -> fx -> normalize -> derive -> publish.

`stage` is rebuilt from the raw files and tr.*, then swapped in as `live` in one transaction,
only after every step of the cycle succeeded. `live` is what the web reads.
"""
import datetime as dt
import json

from . import eop, fx, normalize
from .config import RAW_EOP
from .db import ROOT

DERIVE = ROOT / "db" / "derive"


def step_eop(conn, stats, start=None, end=None):
    """Mirror new days (and refetch the last 3) and record them in ops.eop_file / ops.eop_day."""
    manifests = eop.sync(start or eop.EOP_FIRST_DAY, end)
    with conn.transaction():
        for m in manifests:
            conn.execute("INSERT INTO ops.eop_day(day, published, checked_at) VALUES (%s,%s,%s) "
                         "ON CONFLICT (day) DO UPDATE SET published=EXCLUDED.published, "
                         "checked_at=EXCLUDED.checked_at", (m["day"], m["published"], m["fetched_at"]))
            for kind, f in m["files"].items():
                conn.execute(
                    "INSERT INTO ops.eop_file(day, kind, key, sha256, size, fetched_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (day, kind) DO UPDATE SET key=EXCLUDED.key, "
                    "sha256=EXCLUDED.sha256, size=EXCLUDED.size, fetched_at=EXCLUDED.fetched_at",
                    (m["day"], kind, f["key"], f["sha256"], f["size"], m["fetched_at"]))
    published = [m["day"] for m in manifests if m["published"]]
    stats.update(days=len(manifests), published=len(published), last_day=max(published, default=None))


def step_fx(conn, stats):
    stats["rates"] = fx.refresh(conn)


def _days_rows():
    for folder in sorted(p for p in RAW_EOP.iterdir() if p.is_dir()):
        for kind in eop.KINDS:
            yield folder.name, kind, eop.read_rows(folder.name, kind)


def _copy(conn, table, rows):
    if not rows:
        return 0
    cols = [c for c in rows[0] if not c.startswith("_")]
    with conn.cursor().copy(f"COPY stage.{table} ({', '.join(cols)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row([r.get(c) for c in cols])
    return len(rows)


def step_normalize(conn, stats):
    """Rebuild stage from the raw files: base procurement tables."""
    res = normalize.normalize(_days_rows(), normalize.Fx(fx.load(conn)))
    with conn.transaction():
        conn.execute((DERIVE / "schema.sql").read_text(encoding="utf-8"))
        conn.execute("SET search_path = public")
        for table in ("buyer", "tender", "lot", "contract", "contract_supplier", "amendment", "subcontract"):
            stats[table] = _copy(conn, table, res[table])
    stats.update(res["_stats"])
    stats["rules"] = normalize.RULES_VERSION


def step_derive(conn, stats):
    """Run db/derive/NN_*.sql against stage (companies, networks, tags, search)."""
    from . import networks
    with conn.transaction():
        for f in sorted(DERIVE.glob("[0-9][0-9]_*.sql")):
            conn.execute(f.read_text(encoding="utf-8"))
            if f.name.startswith("20_"):  # edges exist: compute components in Python
                stats["components"] = networks.components(conn)
            stats.setdefault("sql", []).append(f.name)


def step_publish(conn, stats):
    """Swap stage -> live atomically, keeping the previous live as `previous` for one cycle."""
    with conn.transaction():
        conn.execute("DROP SCHEMA IF EXISTS previous CASCADE")
        if conn.execute("SELECT 1 FROM pg_namespace WHERE nspname='live'").fetchone():
            conn.execute("ALTER SCHEMA live RENAME TO previous")
        conn.execute("ALTER SCHEMA stage RENAME TO live")
        conn.execute("GRANT USAGE ON SCHEMA live TO PUBLIC")
        conn.execute("CREATE TABLE IF NOT EXISTS ops.published (id int PRIMARY KEY, at timestamptz, "
                     "eop_last_day date, tr_last_read timestamptz)")
        last_day = conn.execute("SELECT max(day) FROM ops.eop_day WHERE published").fetchone()[0]
        tr_last = conn.execute("SELECT max(fetched_at) FROM tr.deed WHERE status='ok'").fetchone()[0]
        conn.execute("INSERT INTO ops.published VALUES (1, now(), %s, %s) ON CONFLICT (id) DO UPDATE "
                     "SET at=now(), eop_last_day=EXCLUDED.eop_last_day, tr_last_read=EXCLUDED.tr_last_read",
                     (last_day, tr_last))
    stats["published_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
