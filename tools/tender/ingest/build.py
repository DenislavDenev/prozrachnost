"""Build steps of the 'build' lane: eop -> fx -> normalize -> derive -> publish.

The clean tables are rebuilt from the raw files and the landing zone (tender_src) in the build schema
tender_stage, then moved into schema tender in one transaction, only after every step of the cycle succeeded.
The pages read neither: they read the serving layer pub_tender, which the last step publishes when the rules hold.
"""
import datetime as dt
import json

from . import db, eop, fx, normalize, pipeline
from .config import RAW_EOP, ROOT, TOOL

# what a build is judged by; the rules of kind `publish` run inside the publication, those of kind `parity` belong
# to the move from the old database
RULE_KINDS = ["reconcile", "unique", "not_null", "fk", "unmatched", "drop"]

DERIVE = ROOT / "db" / "derive"


def step_eop(conn, stats, start=None, end=None):
    """Mirror new days (and refetch the last 3) and record them in tender_src.eop_file / tender_src.eop_day."""
    manifests = eop.sync(start or eop.EOP_FIRST_DAY, end)
    with conn.transaction():
        for m in manifests:
            conn.execute("INSERT INTO tender_src.eop_day(day, published, checked_at) VALUES (%s,%s,%s) "
                         "ON CONFLICT (day) DO UPDATE SET published=EXCLUDED.published, "
                         "checked_at=EXCLUDED.checked_at", (m["day"], m["published"], m["fetched_at"]))
            for kind, f in m["files"].items():
                conn.execute(
                    "INSERT INTO tender_src.eop_file(day, kind, key, sha256, size, fetched_at) "
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
    with conn.cursor().copy(f"COPY tender_stage.{table} ({', '.join(cols)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row([r.get(c) for c in cols])
    return len(rows)


def reset_stage(conn):
    """Empty the build schema. It is a fixed schema of the shared database (the tool's role cannot make schemas),
    so the objects inside it go, not the schema."""
    for (name,) in conn.execute("SELECT oid::regclass::text FROM pg_class WHERE relnamespace = 'tender_stage'::regnamespace AND relkind = 'v'").fetchall():
        conn.execute(f"DROP VIEW IF EXISTS {name} CASCADE")
    for (name,) in conn.execute("SELECT oid::regclass::text FROM pg_class WHERE relnamespace = 'tender_stage'::regnamespace AND relkind = 'r'").fetchall():
        conn.execute(f"DROP TABLE IF EXISTS {name} CASCADE")
    for (name,) in conn.execute("SELECT oid::regprocedure::text FROM pg_proc WHERE pronamespace = 'tender_stage'::regnamespace").fetchall():
        conn.execute(f"DROP FUNCTION IF EXISTS {name} CASCADE")


def start_stage(conn):
    """An empty build schema with the base tables and the name rules, in the caller's transaction."""
    reset_stage(conn)
    conn.execute((DERIVE / "schema.sql").read_text(encoding="utf-8"))
    conn.execute("SET search_path = public")


def step_normalize(conn, stats):
    """Rebuild the build schema from the raw files: base procurement tables."""
    res = normalize.normalize(_days_rows(), normalize.Fx(fx.load(conn)))
    with conn.transaction():
        start_stage(conn)
        for table in ("buyer", "tender", "lot", "contract", "contract_supplier", "amendment", "subcontract",
                      "source_record", "award", "notice", "annex_orphan"):
            stats[table] = _copy(conn, table, res[table])
    stats.update(res["_stats"])
    stats["rules"] = normalize.RULES_VERSION


def step_derive(conn, stats):
    """Run db/derive/NN_*.sql against the build schema (companies, networks, tags, search, what the pages show)."""
    from . import networks
    with conn.transaction():
        for f in sorted(DERIVE.glob("[0-9][0-9]_*.sql")):
            conn.execute(f.read_text(encoding="utf-8"))
            if f.name.startswith("20_"):  # edges exist: compute components in Python
                stats["components"] = networks.components(conn)
            stats.setdefault("sql", []).append(f.name)
        conn.execute("SET search_path = public")


def swap(conn):
    """The clean tables are derived whole, so a build replaces them whole: everything in schema tender leaves and
    everything in the build schema moves in, in the caller's transaction. A change of schema is a change in the
    catalogue, not a copy, and there is no second copy of the data once it is done."""
    for kind, flag in (("VIEW", "v"), ("TABLE", "r")):
        for (name,) in conn.execute("SELECT oid::regclass::text FROM pg_class WHERE relnamespace = 'tender'::regnamespace AND relkind = %s",
                                    (flag,)).fetchall():
            conn.execute(f"DROP {kind} IF EXISTS {name} CASCADE")
    for (name,) in conn.execute("SELECT oid::regprocedure::text FROM pg_proc WHERE pronamespace = 'tender'::regnamespace").fetchall():
        conn.execute(f"DROP FUNCTION IF EXISTS {name} CASCADE")
    moved = 0
    for kind, flag in (("TABLE", "r"), ("VIEW", "v")):
        for (name,) in conn.execute("SELECT oid::regclass::text FROM pg_class WHERE relnamespace = 'tender_stage'::regnamespace AND relkind = %s",
                                    (flag,)).fetchall():
            conn.execute(f"ALTER {kind} {name} SET SCHEMA tender")
            moved += 1
    for (name,) in conn.execute("SELECT oid::regprocedure::text FROM pg_proc WHERE pronamespace = 'tender_stage'::regnamespace").fetchall():
        conn.execute(f"ALTER FUNCTION {name} SET SCHEMA tender")
    return moved


def step_publish(conn, stats):
    """The build becomes the clean tables (schema tender), then the rules run, and only when they hold the serving
    layer (pub_tender, what the pages read) follows, as one publication with a version number."""
    if not conn.execute("SELECT to_regclass('tender_stage.search_item') IS NOT NULL").fetchone()[0]:
        raise RuntimeError("nothing is built: the build schema has no finished tables")
    with conn.transaction():
        stats["changes"] = record_changes(conn)
        stats["tables"] = swap(conn)
        last_day = conn.execute("SELECT max(day) FROM tender_src.eop_day WHERE published").fetchone()[0]
        tr_last = conn.execute("SELECT max(fetched_at) FROM tender_src.tr_deed WHERE status='ok'").fetchone()[0]
        conn.execute("INSERT INTO tender_src.build_state (id, at, eop_last_day, tr_last_read, rules) VALUES (1, now(), %s, %s, %s) "
                     "ON CONFLICT (id) DO UPDATE SET at=now(), eop_last_day=EXCLUDED.eop_last_day, "
                     "tr_last_read=EXCLUDED.tr_last_read, rules=EXCLUDED.rules",
                     (last_day, tr_last, normalize.RULES_VERSION))
    stats["published_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    serve(conn, stats)


def serve(conn, stats):
    """The rules, then the serving layer. A rule that fails, or a publication that is refused, fails the step:
    the pages keep the version they had."""
    r = db.RUNS.get(id(conn))
    if r is None:   # not inside a job (a test, a call by hand): the rules and the publication still need a run
        with pipeline.run(conn, TOOL, "serve") as r:
            db.RUNS[id(conn)] = r
            try:
                return serve(conn, stats)
            finally:
                db.RUNS.pop(id(conn), None)
    problems = pipeline.run_checks(conn, r, kinds=RULE_KINDS)
    if not problems:
        done = pipeline.publish(conn, r, ROOT)
        problems = done.problems
        stats["publication"] = done.publication_id
        stats["published_tables"] = len(done.changes)
    if problems:
        raise RuntimeError("; ".join(problems))


# fields as published (not what we derive from them): a difference between two builds is the source's change
CONTRACT_FIELDS = ("unp", "buyer_eik", "supplier_display", "subject", "contract_date", "effective_date", "value_initial",
                   "value_current", "currency", "estimated_value", "offers_count", "disqualified_offers_count", "is_framework")
TENDER_FIELDS = ("buyer_eik", "subject", "procedure_type", "estimated_value", "currency", "submission_deadline", "is_cancelled", "lots_count")


def record_changes(conn):
    """Before stage replaces live: every published field that changed, per contract and procedure, into
    ops.change_log. 'new-record' when a later publication brought it (a newer source day, an annex),
    'rewritten' when the same publication now says something else, 'removed' when a contract is gone.
    Only between builds of the same normalize rules: a rule change is ours, not the source's."""
    live = conn.execute("SELECT rules FROM tender_src.build_state WHERE id = 1").fetchone() if conn.execute(
        "SELECT to_regclass('tender.contract') IS NOT NULL").fetchone()[0] else None
    if not live or live[0] != normalize.RULES_VERSION:
        return {"skipped": f"rules {live[0] if live else None} -> {normalize.RULES_VERSION}"}
    out = {}
    r = db.RUNS.get(id(conn))
    run_id = str(r.run_id) if r else "NULL"
    for table, fields, extra in (("contract", CONTRACT_FIELDS, "OR s.annex_count IS DISTINCT FROM l.annex_count"),
                                 ("tender", TENDER_FIELDS, "")):
        key = "id" if table == "contract" else "unp"
        vals = ", ".join(f"('{f}', l.{f}::text, s.{f}::text)" for f in fields)
        out[table] = conn.execute(f"""INSERT INTO ops.change_log (tool, source, ref, field, old, new, cause, run_id)
            SELECT 'tender', 'eop-record', '{table}/' || s.{key}, f.name, f.o, f.n,
                   CASE WHEN s.source_day > l.source_day {extra} THEN 'new-record' ELSE 'rewritten' END, {run_id}
            FROM tender_stage.{table} s JOIN tender.{table} l USING ({key})
            CROSS JOIN LATERAL (VALUES {vals}) f(name, o, n)
            WHERE f.o IS DISTINCT FROM f.n""").rowcount
    out["removed"] = conn.execute("""INSERT INTO ops.change_log (tool, source, ref, field, old, new, cause, run_id)
        SELECT 'tender', 'eop-record', 'contract/' || l.id, NULL, concat_ws(' | ', l.unp, l.supplier_display, l.value_current, l.currency), NULL, 'removed', %s
        FROM tender.contract l WHERE NOT EXISTS (SELECT 1 FROM tender_stage.contract s WHERE s.id = l.id)""", (r.run_id if r else None,)).rowcount
    return out
