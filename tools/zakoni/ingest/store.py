"""Writing a parsed answer of one resource into the landing zone zakoni_src, through the shared module (pipeline.load).

A resource is a file of up to 300 MB that the source replaces whole. The shared module decides, as for every tool:
  - the same content as the current load writes nothing (`unchanged`);
  - an answer that lacks a record of the current load is `held` until a read at least a day later gives the same
    content (`confirmed`); nothing is replaced in between;
  - otherwise the answer is a new load and replaces the current one (`loaded`).
It decides on one hash per record (the record with its institutions, terms, relations, files ...), not on the rows
themselves: the rows of ПРИС are 330 MB in memory and the module would hold them twice more. The rows go straight from
the parser into the landing tables (COPY).

What this module adds to the shared one:
  - the field by field change log of each record against the load it replaces (ops.change_log, ref '<table>/<key>'):
    a new record, a changed field, a record that left; the children of a new record are not logged one by one. The first
    load of a resource is one line (the shared module writes it), not one per record;
  - when a record was first seen and last changed, carried from load to load (first_seen_at, changed_at);
  - the landing policy `latest`: the rows of the replaced load leave the landing zone in the same transaction. Its
    ledger line stays, and so does its file in the archive.
"""
import hashlib
import json

from . import pipeline
from .config import TOOL
from .schema import TABLES

SOURCE = "egov"
STATUS = {"loaded": "stored", "confirmed": "stored", "unchanged": "unchanged", "held": "held"}


def keystr(row, cols):
    return "/".join(str(row[c]) for c in cols)


def _dump(values):
    return json.dumps(values, default=str, ensure_ascii=False, separators=(",", ":")).encode()


def digests(parsed, tables):
    """{key: {'sha': hash}} for every parent record: its data columns and the rows of its children, in their order."""
    parent = tables[0]
    pkey, pdata = TABLES[parent]
    h = {}
    for row in parsed.tables.get(parent, []):
        h[keystr(row, pkey)] = hashlib.sha256(_dump([row[c] for c in pdata]))
    for t in tables[1:]:
        key, data = TABLES[t]
        for row in parsed.tables.get(t, []):
            h[keystr(row, pkey)].update(t.encode() + _dump([row[c] for c in (*key[len(pkey):], *data)]))
    return {k: {"sha": v.hexdigest()} for k, v in h.items()}


def current(parent):
    """The records of a load as digests() gives them: what the shared module compares a new answer with."""
    pkey = TABLES[parent][0]

    def read(conn, load_id):
        return {"/".join(str(x) for x in r[:-1]): {"sha": r[-1]} for r in conn.execute(
            f"SELECT {', '.join(pkey)}, record_sha FROM zakoni_src.{parent} WHERE load_id = %s", (load_id,))}
    return read


def previous(conn, ref, load_id):
    """The load this one replaces: the newest earlier accepted load of the resource (its rows are still in the landing zone)."""
    row = conn.execute("""SELECT load_id FROM ops.load WHERE tool = %s AND source = %s AND ref = %s AND load_id < %s
                          AND status IN ('loaded', 'confirmed') ORDER BY load_id DESC LIMIT 1""", (TOOL, SOURCE, ref, load_id)).fetchone()
    return row[0] if row else None


def writer(r, parsed, tables, ref, now):
    parent = tables[0]
    pkey = TABLES[parent][0]

    def write(conn, load_id, rows):
        prev = previous(conn, ref, load_id)
        seen = {}
        if prev:
            seen = {"/".join(str(x) for x in x[:-3]): x[-3:] for x in conn.execute(
                f"SELECT {', '.join(pkey)}, record_sha, first_seen_at, changed_at FROM zakoni_src.{parent} WHERE load_id = %s", (prev,))}
        for t in tables:
            key, data = TABLES[t]
            cols = [*key, *data]
            meta = ["record_sha", "first_seen_at", "changed_at"] if t == parent else []
            with conn.cursor().copy(f"COPY zakoni_src.{t} (load_id, {', '.join(cols + meta)}) FROM STDIN") as cp:
                for row in parsed.tables.get(t, []):
                    vals = [load_id, *(row[c] for c in cols)]
                    if meta:
                        k = keystr(row, key)
                        sha = rows[k]["sha"]
                        old = seen.get(k)
                        vals += [sha, old[1] if old else now, old[2] if old and old[0] == sha else now]
                    cp.write_row(vals)
        if prev:
            log_fields(conn, r, tables, load_id, prev, now)
            for t in tables:      # landing policy `latest`
                conn.execute(f"DELETE FROM zakoni_src.{t} WHERE load_id = %s", (prev,))
    return write


def log_fields(conn, r, tables, new, old, now):
    """Each difference between two loads of a resource, table by table, as the old silver layer logged it."""
    parent = tables[0]
    pkey = TABLES[parent][0]
    args = dict(now=now, tool=TOOL, source=SOURCE, run=r.run_id, new=new, old=old)
    for t in tables:
        key, data = TABLES[t]
        eq = " AND ".join(f"o.{k} = n.{k}" for k in key)
        peq = " AND ".join(f"p.{k} = n.{k}" for k in pkey)
        ref_n = f"concat_ws('/', '{t}', {', '.join(f'n.{k}' for k in key)})"
        ref_o = f"concat_ws('/', '{t}', {', '.join(f'o.{k}' for k in key)})"
        head = "INSERT INTO ops.change_log (detected_at, tool, source, ref, field, old, new, cause, run_id) "
        # a new record; the children of a record that is new itself are not logged one by one
        child = "" if t == parent else f" AND EXISTS (SELECT 1 FROM zakoni_src.{parent} p WHERE p.load_id = %(old)s AND {peq})"
        conn.execute(head + f"""SELECT %(now)s, %(tool)s, %(source)s, {ref_n}, NULL, NULL, NULL, 'new-record', %(run)s
            FROM zakoni_src.{t} n WHERE n.load_id = %(new)s
            AND NOT EXISTS (SELECT 1 FROM zakoni_src.{t} o WHERE o.load_id = %(old)s AND {eq}){child}""", args)
        conn.execute(head + f"""SELECT %(now)s, %(tool)s, %(source)s, {ref_o}, x.key, left(x.value, 2000), left(y.value, 2000), 'rewritten', %(run)s
            FROM zakoni_src.{t} o JOIN zakoni_src.{t} n ON n.load_id = %(new)s AND {eq}
            CROSS JOIN LATERAL jsonb_each_text(jsonb_build_object({', '.join(f"'{c}', o.{c}" for c in data)})) x
            JOIN LATERAL jsonb_each_text(jsonb_build_object({', '.join(f"'{c}', n.{c}" for c in data)})) y ON y.key = x.key
            WHERE o.load_id = %(old)s AND ROW({', '.join(f'o.{c}' for c in data)}) IS DISTINCT FROM ROW({', '.join(f'n.{c}' for c in data)})
              AND x.value IS DISTINCT FROM y.value""", args)
        conn.execute(head + f"""SELECT %(now)s, %(tool)s, %(source)s, {ref_o}, NULL, NULL, NULL, 'removed', %(run)s
            FROM zakoni_src.{t} o WHERE o.load_id = %(old)s
            AND NOT EXISTS (SELECT 1 FROM zakoni_src.{t} n WHERE n.load_id = %(new)s AND {eq})""", args)


def apply(conn, r, ref, parsed, tables, raw_file_id, now, hold_when=None):
    """Load one parsed answer. -> the shared module's Loaded (status loaded | confirmed | unchanged | held)."""
    rows = digests(parsed, tables)
    return pipeline.load(conn, r, SOURCE, ref, rows, writer(r, parsed, tables, ref, now), raw_file_id=raw_file_id,
                         current=current(tables[0]), now=now, hold_when=hold_when)


def same_file(conn, r, ref, parent, raw_file_id, now):
    """The answer is the very file of the current load: its content is the current content, nothing is parsed again.
    A line in the ledger says the source was read (`unchanged`). -> Loaded, or None when there is no such load."""
    cur = conn.execute("""SELECT load_id, content_sha256 FROM ops.load WHERE tool = %s AND source = %s AND ref = %s
                          AND is_current AND raw_file_id = %s""", (TOOL, SOURCE, ref, raw_file_id)).fetchone()
    if not cur:
        return None
    rows = current(parent)(conn, cur[0])
    if pipeline.content_hash(rows) != cur[1]:     # the records are hashed another way since (a new version of the code)
        return None
    return pipeline.load(conn, r, SOURCE, ref, rows, lambda *a: None, raw_file_id=raw_file_id, now=now)


def held_count(conn, ref, parent):
    """How many records of the resource the landing zone holds now (the current load)."""
    return conn.execute(f"""SELECT count(*) FROM zakoni_src.{parent} WHERE load_id =
        (SELECT load_id FROM ops.load WHERE tool = %s AND source = %s AND ref = %s AND is_current)""", (TOOL, SOURCE, ref)).fetchone()[0]


def state(conn, ref, at=None, **kw):
    """Upsert zakoni.source_state; a value 'now' means the time of the read (`at`)."""
    cols = ["last_read", *kw]
    vals = ["COALESCE(%s::timestamptz, now())", *("COALESCE(%s::timestamptz, now())" if v == "now" else "%s" for v in kw.values())]
    args = [at, *(at if v == "now" else v for v in kw.values())]
    conn.execute(f"""INSERT INTO zakoni.source_state (ref, {', '.join(cols)}) VALUES (%s, {', '.join(vals)})
                     ON CONFLICT (ref) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in cols)}""", (ref, *args))
