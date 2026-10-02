"""Writing a parsed answer into the silver tables (STANDARD 1А.4-6, 3А).

apply() puts the rows of one resource into the versioned tables:
  - a new record is a new row; a changed record closes its row (valid_to) and opens a new one: the old stays, and each
    difference, field by field, is a line in ops.change_log (the first read of a resource is not a change and is not logged);
  - an answer that lacks records we hold is `held`: nothing is written until a read at least a day later gives the same file;
    then it is `confirmed` and the missing records are closed (never deleted) and logged as `removed`;
  - the same answer again changes nothing and logs nothing.
Everything of one answer is written in one transaction.
"""
import datetime as dt
import hashlib
import json

from . import db
from .schema import PARENT, TABLES

CONFIRM_AFTER = dt.timedelta(days=1)
META = ("id", "valid_from", "valid_to", "row_sha", "raw_sha256")
SOURCE = "egov"


def row_sha(row, cols):
    return hashlib.sha1(json.dumps([row[c] for c in cols], default=str, ensure_ascii=False).encode()).hexdigest()


def _scope(table, origin):
    """Which current rows of a shared table belong to this resource (the two ПРИС resources share the tables)."""
    if origin is None:
        return "TRUE", ()
    if table == "pris_act":
        return "c.origin = %s", (origin,)
    return "c.pris_id IN (SELECT pris_id FROM silver.pris_act WHERE origin = %s)", (origin,)


def _stage(conn, table, rows):
    key, data = TABLES[table]
    cols = [*key, *data]
    conn.execute(f"CREATE TEMP TABLE n_{table} ON COMMIT DROP AS SELECT {', '.join(cols)}, row_sha FROM silver.{table} WITH NO DATA")
    with conn.cursor().copy(f"COPY n_{table} ({', '.join(cols)}, row_sha) FROM STDIN") as cp:
        for r in rows:
            cp.write_row([*(r[c] for c in cols), row_sha(r, data)])
    conn.execute(f"CREATE INDEX ON n_{table} ({', '.join(key)})")
    conn.execute(f"ANALYZE n_{table}")


def apply(conn, ref, sha, parsed, tables, origin=None, now=None):
    """Write one parsed answer. `tables` = the silver tables of the resource, the parent table first.
    -> 'stored' | 'unchanged' | 'held'."""
    now = now or dt.datetime.now(dt.timezone.utc)
    parent = tables[0]
    with conn.transaction():
        for t in tables:
            _stage(conn, t, parsed.tables.get(t, []))
        sc, sa = _scope(parent, origin)
        had = conn.execute(f"SELECT EXISTS (SELECT 1 FROM silver.{parent} c WHERE c.valid_to IS NULL AND {sc})", sa).fetchone()[0]
        pkey = TABLES[parent][0]
        eq = " AND ".join(f"c.{k} = n.{k}" for k in pkey)
        removed = conn.execute(f"""SELECT count(*) FROM silver.{parent} c WHERE c.valid_to IS NULL AND {sc}
                                   AND NOT EXISTS (SELECT 1 FROM n_{parent} n WHERE {eq})""", sa).fetchone()[0]
        held = conn.execute("SELECT sha256, first_at <= %s - %s FROM ops.held WHERE source = %s AND ref = %s",
                            (now, CONFIRM_AFTER, SOURCE, ref)).fetchone()
        n_new = len(parsed.tables.get(parent, []))
        if removed:
            if not held or held[0] != sha:
                conn.execute("""INSERT INTO ops.held (source, ref, sha256, rows, first_at) VALUES (%s,%s,%s,%s,%s)
                                ON CONFLICT (source, ref) DO UPDATE SET sha256 = EXCLUDED.sha256, rows = EXCLUDED.rows, first_at = EXCLUDED.first_at""",
                             (SOURCE, ref, sha, n_new, now))
                db.log_change(conn, SOURCE, ref, "rows", None, n_new, "held")
                db.state(conn, SOURCE, ref, status="held", error=f"{removed} записа от пазените ги няма в новия отговор")
                return "held"
            if not held[1]:
                return "held"
            db.log_change(conn, SOURCE, ref, "rows", None, n_new, "confirmed")
        if held:
            conn.execute("DELETE FROM ops.held WHERE source = %s AND ref = %s", (SOURCE, ref))
        # the children of a parent that is new in this answer are not logged one by one
        changes = 0
        for t in tables:
            changes += _write(conn, t, sha, origin, now, log=had, parent_new=None if t == parent else parent)
        return "stored" if changes else "unchanged"


def _write(conn, table, sha, origin, now, log, parent_new):
    key, data = TABLES[table]
    sc, sa = _scope(table, origin)
    eq = " AND ".join(f"c.{k} = n.{k}" for k in key)
    refx = f"concat_ws('/', '{table}', {', '.join(f'n.{k}' for k in key)})"
    refc = f"concat_ws('/', '{table}', {', '.join(f'c.{k}' for k in key)})"
    pk = [k for k in key if k != "ord"]
    skip_new_parents = ""
    if parent_new:
        pq = " AND ".join(f"p.{k} = n.{k}" for k in pk)
        skip_new_parents = f" AND EXISTS (SELECT 1 FROM silver.{parent_new} p WHERE {pq} AND p.valid_from < %s)"
    if log:
        # a new record (a child of a parent that is new in this answer is not logged one by one)
        conn.execute(f"""INSERT INTO ops.change_log (source, ref, field, old, new, cause)
                         SELECT %s, {refx}, NULL, NULL, NULL, 'new-record' FROM n_{table} n
                         WHERE NOT EXISTS (SELECT 1 FROM silver.{table} c WHERE c.valid_to IS NULL AND {eq}){skip_new_parents}""",
                     (SOURCE, *([now] if parent_new else [])))
        conn.execute(f"""INSERT INTO ops.change_log (source, ref, field, old, new, cause)
                         SELECT %s, {refc}, o.key, left(o.value, 2000), left(w.value, 2000), 'rewritten'
                         FROM silver.{table} c JOIN n_{table} n ON {eq} AND c.valid_to IS NULL AND c.row_sha <> n.row_sha,
                              LATERAL jsonb_each_text(to_jsonb(c)) o JOIN LATERAL jsonb_each_text(to_jsonb(n)) w ON w.key = o.key
                         WHERE o.value IS DISTINCT FROM w.value AND o.key <> ALL(%s)""", (SOURCE, list(META)))
        conn.execute(f"""INSERT INTO ops.change_log (source, ref, field, old, new, cause)
                         SELECT %s, {refc}, NULL, NULL, NULL, 'removed' FROM silver.{table} c
                         WHERE c.valid_to IS NULL AND {sc} AND NOT EXISTS (SELECT 1 FROM n_{table} n WHERE {eq})""", (SOURCE, *sa))
    closed = conn.execute(f"""UPDATE silver.{table} c SET valid_to = %s WHERE c.valid_to IS NULL AND {sc}
                              AND NOT EXISTS (SELECT 1 FROM n_{table} n WHERE {eq} AND n.row_sha = c.row_sha)""", (now, *sa)).rowcount
    cols = ", ".join([*key, *data])
    opened = conn.execute(f"""INSERT INTO silver.{table} ({cols}, valid_from, row_sha, raw_sha256)
                              SELECT {', '.join(f'n.{c}' for c in [*key, *data])}, %s, n.row_sha, %s FROM n_{table} n
                              WHERE NOT EXISTS (SELECT 1 FROM silver.{table} c WHERE c.valid_to IS NULL AND {eq})""", (now, sha)).rowcount
    return closed + opened
