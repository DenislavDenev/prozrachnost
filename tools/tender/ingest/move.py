"""The move of Тендер from its own database into the shared one (10.11.2026): steps `replay` and `parity`.

replay   The state that cannot be read again from a raw file moves as it is: the register (partidas, roles, the
         persons and our numbers for them, the queue), the procedure pages of ЦАИС ЕОП, the payments, the editorial
         tables, the days and files of the open data, the rates. One snapshot of the old database, one transaction
         here. The old runs and the old change log go into the shared ledger. The clean tables are not copied: the
         build makes them here from the raw files and this state, which is the proof that they can be built.
parity   Every table of the old database against its table here: the number of rows and a sum over the content of
         every row, and the money of the contracts and the payments. Each comparison is a result of a rule of kind
         `parity` in ops.check_result. A difference stops the move.
"""
import psycopg
from psycopg import sql

from . import pipeline
from .config import OLD_DSN, TOOL

# the landing zone: (schema and table in the old database, table in tender_src)
STATE = [
    ("ops.eop_day", "eop_day"), ("ops.eop_file", "eop_file"), ("ops.fx_rate", "fx_rate"), ("ops.published", "build_state"),
    ("tr.queue", "tr_queue"), ("tr.deed", "tr_deed"), ("tr.role", "tr_role"), ("tr.person", "tr_person"),
    ("tr.name_search", "tr_name_search"), ("tr.change_day", "tr_change_day"),
    ("eopsvc.queue", "eopsvc_queue"), ("eopsvc.offer", "eopsvc_offer"), ("eopsvc.lot", "eopsvc_lot"),
    ("eopsvc.contract_lot", "eopsvc_contract_lot"), ("eopsvc.announcement", "eopsvc_announcement"),
    ("eopsvc.detail", "eopsvc_detail"), ("eopsvc.publication", "eopsvc_publication"), ("eopsvc.appeal", "eopsvc_appeal"),
    ("eopsvc.document", "eopsvc_document"), ("eopsvc.check_run", "eopsvc_check_run"),
    ("sebra.resource", "sebra_resource"), ("sebra.payment", "sebra_payment"),
    ("ed.tag", "ed_tag"), ("ed.tag_audit", "ed_tag_audit"), ("ed.public_figure", "ed_public_figure"), ("ed.article", "ed_article"),
]
# the clean tables the old build made (schema live) and the new one makes (schema tender)
BUILT = ["buyer", "tender", "lot", "contract", "contract_supplier", "amendment", "subcontract", "source_record", "award",
         "notice", "annex_orphan", "company", "company_stats", "buyer_stats", "tender_stats", "person", "edge", "node",
         "node_contract", "person_stats", "network_component", "network_component_stats", "tag_def", "tag", "search_item",
         "buyer_place", "form_type", "contract_lot", "offer_lot", "offer", "payment"]
# what is compared beside rows and content: the money, where a table has it
MONEY = [("contract", "amount_eur"), ("contract", "value_current"), ("payment", "amount_eur"), ("payment", "amount")]
NOT_COMPARED = {"build_state"}   # one row with the time of the last build: each database has its own
# The number of a network component is the order in which the build met it, so two builds of the same data may
# number the same components differently. They are compared by what is in them: a component is named by its
# smallest node, and its statistics are compared without the number.
BY_CONTENT = {
    "network_component": (["view", "mode", "node", "named"],
                          "(SELECT view, mode, node, min(node) OVER (PARTITION BY view, mode, component) AS named FROM {}.network_component) x"),
    "network_component_stats": (["view", "mode", "nodes", "persons", "companies", "contracts", "amount_eur"],
                                "(SELECT view, mode, nodes, persons, companies, contracts, amount_eur FROM {}.network_component_stats) x"),
}


def _columns(conn, schema, table):
    return [r[0] for r in conn.execute("""SELECT a.attname FROM pg_attribute a
        WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = '' ORDER BY a.attnum""",
                                       (f"{schema}.{table}",))]


def _old(old_dsn):
    old = psycopg.connect(old_dsn, autocommit=True)
    old.execute("SET default_transaction_read_only = on")
    old.execute("SET timezone = 'UTC'")
    return old


def replay(conn, r, old_dsn=OLD_DSN, again=False, old_ops="ops"):
    """Copy the state of the old database into tender_src and its ledger into ops. Refuses a landing zone that
    already holds something, unless told to start over (`again`): the second copy would undo what was read since."""
    filled = conn.execute("SELECT (SELECT count(*) FROM tender_src.tr_deed) + (SELECT count(*) FROM tender_src.eop_day)").fetchone()[0]
    if filled and not again:
        raise RuntimeError("the landing zone is not empty: replay copies into an empty one (--again starts over)")
    counts = {}
    with _old(old_dsn) as old, old.transaction(), conn.transaction():
        old.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        conn.execute("TRUNCATE " + ", ".join(f"tender_src.{new}" for _, new in STATE))
        for source, new in STATE:
            schema, table = source.split(".")
            schema = old_ops if schema == "ops" else schema   # the old ledger had the name the shared one has
            cols = _columns(conn, "tender_src", new)
            names = sql.SQL(", ").join(map(sql.Identifier, cols))
            n = 0
            with old.cursor().copy(sql.SQL("COPY (SELECT {} FROM {}.{}) TO STDOUT (FORMAT BINARY)").format(
                    names, sql.Identifier(schema), sql.Identifier(table))) as out, \
                    conn.cursor().copy(sql.SQL("COPY tender_src.{} ({}) FROM STDIN (FORMAT BINARY)").format(
                        sql.Identifier(new), names)) as into:
                for data in out:
                    into.write(data)
            n = conn.execute(sql.SQL("SELECT count(*) FROM tender_src.{}").format(sql.Identifier(new))).fetchone()[0]
            counts[new] = n
        for table in ("ed_tag", "ed_tag_audit", "ed_article"):   # the numbers go on from where the old ones stopped
            conn.execute(sql.SQL("SELECT setval(pg_get_serial_sequence({}, 'id'), GREATEST((SELECT max(id) FROM tender_src.{}), 1))").format(
                sql.Literal(f"tender_src.{table}"), sql.Identifier(table)))
        # the old ledger, once: the runs (the D-1 chain asks which day is done) and the change log
        if not conn.execute("SELECT 1 FROM ops.run WHERE tool = %s AND params ? 'old_id' LIMIT 1", (TOOL,)).fetchone():
            runs = old.execute(sql.SQL("""SELECT id, step, started_at, finished_at, status, code_sha, inputs, stats, error
                                          FROM {}.job_run ORDER BY id""").format(sql.Identifier(old_ops))).fetchall()
            with conn.cursor() as cur:
                cur.executemany("""INSERT INTO ops.run (tool, step, started_at, finished_at, status, code_sha, params, stats, error)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                                [(TOOL, step, started, finished, "failed" if status == "running" else status, sha,
                                  psycopg.types.json.Jsonb({**(inputs or {}), "old_id": old_id}),
                                  psycopg.types.json.Jsonb(stats or {}),
                                  error if status != "running" else "still marked as running when the database was left")
                                 for old_id, step, started, finished, status, sha, inputs, stats, error in runs])
            counts["runs"] = len(runs)
            log = old.execute(sql.SQL("SELECT detected_at, source, ref, field, old, new, cause FROM {}.change_log ORDER BY id").format(
                sql.Identifier(old_ops))).fetchall()
            with conn.cursor() as cur:
                cur.executemany("""INSERT INTO ops.change_log (detected_at, tool, source, ref, field, old, new, cause)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", [(at, TOOL, *rest) for at, *rest in log])
            counts["changes"] = len(log)
    r.stats.update(counts)
    return []


def _measure(conn, schema, table, cols):
    """(rows, a sum over the content of every row) of a table: the same rows give the same two numbers whatever
    their order. Every column is read as text, so the two sides need not agree on a type's name."""
    relation = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))
    if table in BY_CONTENT:
        cols, text = BY_CONTENT[table]
        relation = sql.SQL(text).format(sql.Identifier(schema))
    row = sql.SQL("ROW({})::text").format(sql.SQL(", ").join(sql.SQL("{}::text").format(sql.Identifier(c)) for c in cols))
    return conn.execute(sql.SQL("SELECT count(*), COALESCE(sum(('x' || substr(md5({}), 1, 15))::bit(60)::bigint), 0) FROM {}").format(
        row, relation)).fetchone()


def parity(conn, r, old_dsn=OLD_DSN, old_ops="ops"):
    """The old database against the shared one, table by table. Returns nothing: the results are in
    ops.check_result, and the step reports the rules that failed."""
    conn.execute("SET timezone = 'UTC'")
    detail = {}
    with _old(old_dsn) as old:
        pairs = [(old_ops if o.split(".")[0] == "ops" else o.split(".")[0], o.split(".")[1], "tender_src", n)
                 for o, n in STATE if n not in NOT_COMPARED]
        pairs += [("live", t, "tender", t) for t in BUILT]
        for old_schema, old_table, schema, table in pairs:
            # the columns of the old table, in its order: the new one has them all (and, for some, a key to core more)
            cols = _columns(old, old_schema, old_table)
            missing = set(cols) - set(_columns(conn, schema, table))
            if missing:
                raise RuntimeError(f"{schema}.{table} lacks {sorted(missing)} of {old_schema}.{old_table}")
            was, now = _measure(old, old_schema, old_table, cols), _measure(conn, schema, table, cols)
            pipeline.record_check(conn, r, f"parity_{table}_rows", was[0], now[0])
            pipeline.record_check(conn, r, f"parity_{table}_content", was[1], now[1])
            detail[table] = {"rows": [was[0], now[0]], "same": was == now}
        for table, column in MONEY:
            q = sql.SQL("SELECT COALESCE(sum({}), 0) FROM {}.{}")
            was = old.execute(q.format(sql.Identifier(column), sql.Identifier("live"), sql.Identifier(table))).fetchone()[0]
            now = conn.execute(q.format(sql.Identifier(column), sql.Identifier("tender"), sql.Identifier(table))).fetchone()[0]
            pipeline.record_check(conn, r, f"parity_{table}_{column}_sum", was, now)
            detail[f"{table}.{column}"] = [str(was), str(now)]
        # the municipalities: the old copy of municipality.csv against the shared dimension
        was = old.execute("SELECT id, name_bg, name_en, drawn_as, oblast, nuts3, nuts2, nuts1 FROM live.municipality ORDER BY id").fetchall()
        now = conn.execute("""SELECT m.eik, m.name_bg, m.name_en, m.drawn_as_eik, o.label_bg, o.nuts3_code, n3.parent_code, n2.parent_code
            FROM core.municipality m JOIN core.oblast o USING (oblast_code) JOIN core.nuts n3 ON n3.nuts_code = o.nuts3_code
            JOIN core.nuts n2 ON n2.nuts_code = n3.parent_code WHERE m.gone_at IS NULL ORDER BY m.eik""").fetchall()
        pipeline.record_check(conn, r, "parity_municipality_rows", len(was), len(now))
        pipeline.record_check(conn, r, "parity_municipality_differ", 0, len(set(was) ^ set(now)))
        # the ledger
        runs = old.execute(sql.SQL("SELECT count(*) FROM {}.job_run").format(sql.Identifier(old_ops))).fetchone()[0]
        pipeline.record_check(conn, r, "parity_runs", runs,
                              conn.execute("SELECT count(*) FROM ops.run WHERE tool = %s AND params ? 'old_id'", (TOOL,)).fetchone()[0])
        changes, last = old.execute(sql.SQL("SELECT count(*), max(detected_at) FROM {}.change_log").format(sql.Identifier(old_ops))).fetchone()
        pipeline.record_check(conn, r, "parity_changes", changes, conn.execute(
            "SELECT count(*) FROM ops.change_log WHERE tool = %s AND run_id IS NULL AND detected_at <= %s", (TOOL, last)).fetchone()[0])
    r.stats["tables"] = len(detail)
    r.stats["differ"] = sorted(t for t, d in detail.items() if isinstance(d, dict) and not d["same"])
    r.stats["detail"] = detail


def parity_rules():
    """The names of the rules parity() records, for db/checks.toml (tests compare the two)."""
    names = []
    for _, new in STATE:
        if new not in NOT_COMPARED:
            names += [f"parity_{new}_rows", f"parity_{new}_content"]
    for t in BUILT:
        names += [f"parity_{t}_rows", f"parity_{t}_content"]
    names += [f"parity_{t}_{c}_sum" for t, c in MONEY]
    return names + ["parity_municipality_rows", "parity_municipality_differ", "parity_runs", "parity_changes"]
