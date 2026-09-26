"""Build steps of the 'build' lane: eop -> fx -> normalize -> derive -> publish.

`stage` is rebuilt from the raw files and tr.*, then swapped in as `live` in one transaction,
only after every step of the cycle succeeded. `live` is what the web reads.
"""
import datetime as dt
import json

from . import db, eop, fx, normalize
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
            # a refetched recent day whose files changed (late additions), a file that is not JSON, a day gone
            for kind in m.get("changed", []):
                db.log_change(conn, "eop-file", f"{m['day']}/{kind}", None, None, (m["files"].get(kind) or {}).get("sha256"), "late")
            for kind in m.get("invalid", []):
                db.log_change(conn, "eop-file", f"{m['day']}/{kind}", None, None, "not valid JSON, kept our copy", "invalid")
    published = [m["day"] for m in manifests if m["published"]]
    stats.update(days=len(manifests), published=len(published), last_day=max(published, default=None),
                 changed=sum(len(m.get("changed", [])) for m in manifests), invalid=sum(len(m.get("invalid", [])) for m in manifests))


def step_eop_audit(conn, stats, full=False):
    """Compare what the source lists with what we hold: every day (full, weekly) or the last 90 days
    (daily). Days that differ are refetched (old copies kept in <day>/history) and logged."""
    held = sorted(p.name for p in RAW_EOP.iterdir() if p.is_dir())
    rep = eop.audit(held if full else held[-90:])
    with conn.transaction():
        for ch in rep["changed"]:
            for kind in ch["kinds"]:
                o, n = ch["old"].get(kind) or {}, ch["new"].get(kind) or {}
                db.log_change(conn, "eop-file", f"{ch['day']}/{kind}", None,
                              f"{o.get('sha256')} {o.get('size')} {o.get('modified')}", f"{n.get('sha256')} {n.get('size')} {n.get('modified')}",
                              "rewritten" if kind in ch["content"] else "listing")
        for day in rep["gone"]:
            db.log_change(conn, "eop-file", day, None, "published", "listing absent, kept our copy", "gone")
    stats.update(checked=rep["checked"], changed=len(rep["changed"]), content=sum(len(c["content"]) for c in rep["changed"]),
                 gone=len(rep["gone"]), baseline=rep["baseline"], days=[c["day"] for c in rep["changed"]][:20])


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
        for table in ("buyer", "tender", "lot", "contract", "contract_supplier", "amendment", "subcontract",
                      "source_record"):
            stats[table] = _copy(conn, table, res[table])
    stats.update(res["_stats"])
    stats["rules"] = normalize.RULES_VERSION


def step_derive(conn, stats):
    """Run db/derive/NN_*.sql against stage (companies, networks, tags, search)."""
    from . import networks
    with conn.transaction():
        # reference data kept in the repo (db/ref), loaded as stage tables before the derive SQL
        conn.execute("""CREATE TABLE stage.municipality (id text PRIMARY KEY, name_bg text NOT NULL, name_en text, drawn_as text NOT NULL,
                        oblast text NOT NULL, nuts3 text NOT NULL, nuts2 text NOT NULL, nuts1 text NOT NULL)""")
        with (DERIVE.parent / "ref" / "municipality.csv").open(encoding="utf-8") as fh,                 conn.cursor().copy("COPY stage.municipality FROM STDIN WITH (FORMAT csv, HEADER true)") as cp:
            cp.write(fh.read())
        for f in sorted(DERIVE.glob("[0-9][0-9]_*.sql")):
            conn.execute(f.read_text(encoding="utf-8"))
            if f.name.startswith("20_"):  # edges exist: compute components in Python
                stats["components"] = networks.components(conn)
            stats.setdefault("sql", []).append(f.name)


def step_publish(conn, stats):
    """Swap stage -> live atomically, keeping the previous live as `previous` for one cycle."""
    with conn.transaction():
        stats["changes"] = record_changes(conn)
        conn.execute("DROP SCHEMA IF EXISTS previous CASCADE")
        if conn.execute("SELECT 1 FROM pg_namespace WHERE nspname='live'").fetchone():
            conn.execute("ALTER SCHEMA live RENAME TO previous")
        conn.execute("ALTER SCHEMA stage RENAME TO live")
        conn.execute("GRANT USAGE ON SCHEMA live TO PUBLIC")
        conn.execute("CREATE TABLE IF NOT EXISTS ops.published (id int PRIMARY KEY, at timestamptz, "
                     "eop_last_day date, tr_last_read timestamptz)")
        last_day = conn.execute("SELECT max(day) FROM ops.eop_day WHERE published").fetchone()[0]
        tr_last = conn.execute("SELECT max(fetched_at) FROM tr.deed WHERE status='ok'").fetchone()[0]
        conn.execute("INSERT INTO ops.published (id, at, eop_last_day, tr_last_read, rules) VALUES (1, now(), %s, %s, %s) "
                     "ON CONFLICT (id) DO UPDATE SET at=now(), eop_last_day=EXCLUDED.eop_last_day, "
                     "tr_last_read=EXCLUDED.tr_last_read, rules=EXCLUDED.rules",
                     (last_day, tr_last, normalize.RULES_VERSION))
    stats["published_at"] = dt.datetime.now(dt.timezone.utc).isoformat()


# fields as published (not what we derive from them): a difference between two builds is the source's change
CONTRACT_FIELDS = ("unp", "buyer_eik", "supplier_display", "subject", "contract_date", "effective_date", "value_initial",
                   "value_current", "currency", "estimated_value", "offers_count", "disqualified_offers_count", "is_framework")
TENDER_FIELDS = ("buyer_eik", "subject", "procedure_type", "estimated_value", "currency", "submission_deadline", "is_cancelled", "lots_count")


def record_changes(conn):
    """Before stage replaces live: every published field that changed, per contract and procedure, into
    ops.change_log. 'new-record' when a later publication brought it (a newer source day, an annex),
    'rewritten' when the same publication now says something else, 'removed' when a contract is gone.
    Only between builds of the same normalize rules: a rule change is ours, not the source's."""
    live = conn.execute("SELECT rules FROM ops.published WHERE id = 1").fetchone() if conn.execute(
        "SELECT to_regclass('live.contract') IS NOT NULL AND to_regclass('ops.published') IS NOT NULL").fetchone()[0] else None
    if not live or live[0] != normalize.RULES_VERSION:
        return {"skipped": f"rules {live[0] if live else None} -> {normalize.RULES_VERSION}"}
    out = {}
    for table, fields, extra in (("contract", CONTRACT_FIELDS, "OR s.annex_count IS DISTINCT FROM l.annex_count"),
                                 ("tender", TENDER_FIELDS, "")):
        key = "id" if table == "contract" else "unp"
        vals = ", ".join(f"('{f}', l.{f}::text, s.{f}::text)" for f in fields)
        out[table] = conn.execute(f"""INSERT INTO ops.change_log (source, ref, field, old, new, cause)
            SELECT 'eop-record', '{table}/' || s.{key}, f.name, f.o, f.n,
                   CASE WHEN s.source_day > l.source_day {extra} THEN 'new-record' ELSE 'rewritten' END
            FROM stage.{table} s JOIN live.{table} l USING ({key})
            CROSS JOIN LATERAL (VALUES {vals}) f(name, o, n)
            WHERE f.o IS DISTINCT FROM f.n""").rowcount
    out["removed"] = conn.execute("""INSERT INTO ops.change_log (source, ref, field, old, new, cause)
        SELECT 'eop-record', 'contract/' || l.id, NULL, concat_ws(' | ', l.unp, l.supplier_display, l.value_current, l.currency), NULL, 'removed'
        FROM live.contract l WHERE NOT EXISTS (SELECT 1 FROM stage.contract s WHERE s.id = l.id)""").rowcount
    return out
