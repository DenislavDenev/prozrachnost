"""The move from the database of its own (`ikonomika`, until 11.11.2026) into the shared one.

replay  every answer the old database stored in raw/, in the order it was read, through the same parsers and the
        same checks, with the time it was read. The old database decided which answers it accepted (it held and
        confirmed some), so a replayed answer is not held again. Nothing is copied from the old tables, except the
        state of the reads (when each indicator was last read, its new period, the source's update date): that state
        has no raw file, because the old database kept a file only when an answer differed from the one before.
parity  the old database against the serving layer of the shared one: for every indicator and period the number
        of cells, their sum and a hash of every cell with its flag; the names of the categories; the state of the
        reads. Each comparison is a result of a rule of kind `parity` in ops.check_result.
"""
import datetime as dt
import json
import re

import psycopg

from . import bnb, ecb, eurostat, http, jsonstat, mf, pipeline, store
from .config import OLD_DSN, TOOL
from .jsonstat import ShapeError

HICP = ("hicp_i15", "hicp_rch_m", "hicp_rch_a")   # checked together (checks.hicp_rates)
SAME_RUN = dt.timedelta(minutes=30)


def quarter_end(a):
    nxt = dt.date(a.year + 1, 1, 1) if a.month >= 10 else dt.date(a.year, 3 * ((a.month - 1) // 3) + 4, 1)
    return nxt - dt.timedelta(days=1)


def ecb_rows(raw, ind):
    """The rows of `ind` from its stored answer: the answers of its series joined one after another, replayed in the
    order ecb.read() asks for them."""
    pieces = [p for p in re.split(rb"(?m)^(?=KEY,)", raw) if p.strip()]
    flow_of = {flow: i for i, flow, *_ in ecb.SERIES}

    def get(url):
        flow = url.split("/data/")[1].split("/")[0]
        if flow_of[flow] != ind:
            raise http.Gone(f"not replayed: {url}")
        if not pieces:
            raise ShapeError(f"the stored answer of {ind} has fewer series than are asked for")
        return pieces.pop(0)
    rows, _raws, failed, problems = ecb.read(get)
    if ind in failed:
        raise ShapeError("; ".join(p for p in problems if "not replayed" not in p))
    if pieces:
        raise ShapeError(f"the stored answer of {ind} has {len(pieces)} series more than are asked for")
    bad = ecb.check(rows.get(ind, []))
    if bad:
        raise ShapeError("; ".join(bad[:5]))
    return rows[ind]


def mf_rows(raw, vintages):
    """The rows and the names of the forecasts from the stored answer (the forecasts joined one after another). The
    list of forecasts was not stored; their dates and short names are those the old database kept, in order."""
    text, docs, at, dec = raw.decode("utf-8"), [], 0, json.JSONDecoder()
    while True:
        while at < len(text) and text[at].isspace():
            at += 1
        if at >= len(text):
            break
        doc, at = dec.raw_decode(text, at)
        docs.append(json.dumps(doc, ensure_ascii=False).encode())
    if len(docs) > len(vintages):
        raise ShapeError(f"{len(docs)} forecasts in the answer, {len(vintages)} known")
    lst = {"success": True, "total_records": len(docs),
           "resources": [{"uri": str(i), "created_at": f"{v}T00:00:00", "name": name} for i, (v, name) in enumerate(vintages[:len(docs)])]}

    def call(method, body):
        return json.dumps(lst).encode() if method == "listResources" else docs[int(body["resource_uri"])]
    rows, _raws, got, problems, _unknown = mf.read(call, pause=0)
    if problems:
        raise ShapeError("; ".join(problems[:5]))
    return rows, got


def replay(conn, r, old_dsn=OLD_DSN, old_ops="ops"):
    """old_ops: the schema of the old ledger (a test keeps it apart from the shared ops)."""
    with psycopg.connect(old_dsn, autocommit=True) as old:
        old.execute("SET default_transaction_read_only = on")
        files = old.execute(f"SELECT source, ref, path, sha256, fetched_at FROM {old_ops}.raw_file ORDER BY fetched_at, id").fetchall()
        vintages = old.execute("""SELECT code, label FROM live.dim_label WHERE indicator = 'mf_forecast' AND dim = 'vintage'
                                  ORDER BY code""").fetchall()
        states = old.execute(f"""SELECT source, ref, last_read, last_ok, last_change, last_new_period, status, error, rows,
                                 updated, label FROM {old_ops}.source_state""").fetchall()
        log = old.execute(f"""SELECT detected_at, source, ref, field, old, new, cause FROM {old_ops}.change_log
                             WHERE cause IN ('held', 'confirmed') ORDER BY id""").fetchall()
    inds = {i["id"]: i for i in eurostat.indicators()}
    parsed = {}   # (ref, path) -> the parsed Eurostat answer, for the HICP check across three answers

    def eurostat_answer(ref, path, raw=None):
        if (ref, path) not in parsed:
            if raw is None:
                with open(path, "rb") as f:
                    raw = f.read()
            parsed[(ref, path)] = jsonstat.parse(raw, eurostat.pinned(inds[ref]))
        return parsed[(ref, path)]

    problems, counts = [], {}
    for source, ref, path, sha, at in files:
        file, raw = pipeline.register_raw(conn, r, source, ref, path, fetched_at=at, expect_sha=sha)
        try:
            if source == "eurostat":
                if ref not in inds:
                    raise ShapeError(f"{ref} is no longer an indicator")
                answer = eurostat_answer(ref, path, raw)
                group = {ref: answer}
                if ref in HICP:   # the other two of the same run: their newest answer read by then
                    for other in HICP:
                        near = [(a, p) for s, x, p, _, a in files if s == "eurostat" and x == other and a <= at + SAME_RUN]
                        if other != ref and near:
                            group[other] = eurostat_answer(other, max(near)[1])
                bad = eurostat.check(group, {ref})
                if bad:
                    raise ShapeError(bad[ref])
                res = store.apply(conn, r, "eurostat", ref, ref, file, answer["rows"], answer["labels"], now=at, replay=True)
            elif source == "bnb":
                if ref == "today":   # the day's CSV is only checked against the archive
                    continue
                a, codes = ref.split("/")
                a = dt.date.fromisoformat(a)
                ind, rows = bnb.window_rows(raw, ref, codes.split("-"), a, quarter_end(a))
                res = store.apply(conn, r, "bnb", ind, ref, file, [(d, "BG", str(day), v, None) for _, d, day, v in rows],
                                  now=at, replay=True)
            elif source == "ecb":
                res = store.apply(conn, r, "ecb", ref, ref, file, ecb_rows(raw, ref), ecb.labels(ref), now=at, replay=True)
            elif source == "mf":
                rows, names = mf_rows(raw, vintages)
                res = store.apply(conn, r, "mf", ref, ref, file, rows, mf.labels(rows, names), now=at, replay=True)
            else:
                raise ShapeError(f"unknown source {source}")
        except (ShapeError, pipeline.ShapeError) as e:
            store.reject(conn, r, source, ref, "invalid", e, file, now=at)
            problems.append(f"{source} {ref} {at:%Y-%m-%d %H:%M}: {str(e)[:300]}")
            res = "invalid"
        counts[f"{source} {res}"] = counts.get(f"{source} {res}", 0) + 1
    # the state of the reads as the old database knew it (it has no raw file: an unchanged answer was not kept)
    with conn.transaction():
        for row in states:
            conn.execute("""INSERT INTO ikonomika.source_state (source, ref, last_read, last_ok, last_change, last_new_period,
                  status, error, rows, updated, label) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (source, ref) DO UPDATE SET last_read = EXCLUDED.last_read, last_ok = EXCLUDED.last_ok,
                  last_change = EXCLUDED.last_change, last_new_period = EXCLUDED.last_new_period, status = EXCLUDED.status,
                  error = EXCLUDED.error, rows = EXCLUDED.rows, updated = EXCLUDED.updated, label = EXCLUDED.label""", row)
    # the holds of the old log: a replayed answer is not held again, so they would be lost
    if not conn.execute("SELECT 1 FROM ops.change_log WHERE tool = %s AND cause IN ('held', 'confirmed') LIMIT 1", (TOOL,)).fetchone():
        for detected_at, source, ref, field, old_value, new_value, cause in log:
            pipeline.log_change(conn, r, source, ref, field, old_value, new_value, cause, detected_at)
    r.stats.update(files=len(files), answers=counts, states=len(states), holds=len(log))
    return problems


CELLS = """SELECT indicator, time, count(*), COALESCE(sum(value), 0), count(value),
  md5(string_agg(dims::text || '|' || geo || '|' || COALESCE(trim_scale(value)::text, '') || '|' || COALESCE(flag, ''),
                 E'\\n' ORDER BY dims::text, geo))
FROM {table} GROUP BY 1, 2"""
STATE = "SELECT source, ref, last_ok, last_new_period, status, rows, updated FROM {table}"


def parity(conn, r, old_dsn=OLD_DSN, old_ops="ops"):
    with psycopg.connect(old_dsn, autocommit=True) as old:
        old.execute("SET default_transaction_read_only = on")
        old_cells = {(i, t): x for i, t, *x in old.execute(CELLS.format(table="live.series"))}
        old_labels = set(old.execute("SELECT indicator, dim, code, label FROM live.dim_label"))
        old_state = set(old.execute(STATE.format(table=f"{old_ops}.source_state")))
    new_cells = {(i, t): x for i, t, *x in conn.execute(CELLS.format(table="pub_ikonomika.series"))}
    new_labels = set(conn.execute("SELECT indicator, dim, code, label FROM pub_ikonomika.dim_label"))
    new_state = set(conn.execute(STATE.format(table="ikonomika.source_state")))
    differ = sorted(k for k in old_cells.keys() | new_cells.keys() if old_cells.get(k) != new_cells.get(k))
    detail = "; ".join(f"{i} {t}: старо {old_cells.get((i, t), ['-'])[:2]}, ново {new_cells.get((i, t), ['-'])[:2]}"
                       for i, t in differ[:20])
    labels = old_labels ^ new_labels
    state = old_state ^ new_state
    pipeline.record_check(conn, r, "parity_rows", sum(x[0] for x in old_cells.values()), sum(x[0] for x in new_cells.values()))
    pipeline.record_check(conn, r, "parity_values", sum(x[1] for x in old_cells.values()), sum(x[1] for x in new_cells.values()))
    pipeline.record_check(conn, r, "parity_cells_differ", 0, len(differ), detail or None)
    pipeline.record_check(conn, r, "parity_labels", 0, len(labels), "; ".join(map(str, sorted(labels)[:20])) or None)
    pipeline.record_check(conn, r, "parity_state", 0, len(state), "; ".join(map(str, sorted(state, key=str)[:20])) or None)
    r.stats.update(old_rows=sum(x[0] for x in old_cells.values()), new_rows=sum(x[0] for x in new_cells.values()),
                   groups=len(old_cells), cells_differ=len(differ), labels_differ=len(labels), state_differ=len(state),
                   old_sum=sum(x[1] for x in old_cells.values()), new_sum=sum(x[1] for x in new_cells.values()))
