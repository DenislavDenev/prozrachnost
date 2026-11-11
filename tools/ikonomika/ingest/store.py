"""Raw answers, loads and the clean tables, through the shared module (ingest/pipeline.py).

apply() loads one parsed answer of one resource (an indicator, a BNB window) into the landing zone
ikonomika_src as one batch (pipeline.load):
  - more or changed rows replace the current version at once, each difference is a line in ops.change_log
    (the first version of a resource is one line, not one per row);
  - an answer without a row we hold is `held`: nothing changes until a read at least a day later gives the same
    answer, then it is `confirmed`;
  - the same answer again changes nothing and logs nothing.
The landing zone keeps only the current version of each resource (policy `latest`): the rows of the replaced one
leave, its ledger line and raw file stay.

build() brings the clean tables ikonomika.series and ikonomika.observation up to the current loads
(db/build/observation.sql) and hands the BNB rates to the foundation (core.take_fx_rates()).
"""
import csv
import io
import json
from decimal import Decimal

from . import pipeline
from .config import RAW, ROOT, TOOL
from .jsonstat import ShapeError

STATUS = {"loaded": "stored", "confirmed": "stored", "unchanged": "unchanged", "held": "held"}


def dkey(dims):
    return json.dumps(dims, sort_keys=True, ensure_ascii=False)


def save_raw(conn, r, source, ref, name, raw, fetched_at=None):
    """The answer under its sha256 in RAW, registered once; every read is a line in ops.fetch."""
    return pipeline.save_raw(conn, r, source, ref, raw, RAW, name=name, fetched_at=fetched_at)


def state(conn, source, ref, at=None, **kw):
    """Upsert ikonomika.source_state; `now` in a value means the time of the read (`at`, else now())."""
    cols = ["last_read", *kw]
    vals = ["COALESCE(%s::timestamptz, now())", *("COALESCE(%s::timestamptz, now())" if v == "now" else "%s" for v in kw.values())]
    args = [at, *(at if v == "now" else v for v in kw.values())]
    conn.execute(f"""INSERT INTO ikonomika.source_state (source, ref, {', '.join(cols)}) VALUES (%s, %s, {', '.join(vals)})
                     ON CONFLICT (source, ref) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in cols)}""",
                 (source, ref, *args))


def keyed(rows, labels=None):
    """{key: row} for pipeline.load: one cell per (dims, geo, period), and the names of the categories."""
    out = {}
    for dims, geo, time, value, flag in rows:
        k = f"{dkey(dims)}/{geo}/{time}"
        if k in out:
            raise ShapeError(f"the answer has {k} twice")
        out[k] = {"dims": dims, "geo": geo, "period": str(time),
                  "value": None if value is None else Decimal(str(value)), "flag": flag}
    for dim, codes in (labels or {}).items():
        for code, label in codes.items():
            out[f"label/{dim}/{code}"] = {"dim": dim, "code": code, "label": label}
    return out


def writer(indicator):
    def write(conn, load_id, rows):
        with conn.cursor().copy("COPY ikonomika_src.observation (load_id, indicator, dims, geo, period, value, flag) FROM STDIN") as cp:
            for x in rows.values():
                if "period" in x:
                    cp.write_row((load_id, indicator, dkey(x["dims"]), x["geo"], x["period"], x["value"], x["flag"]))
        with conn.cursor().copy("COPY ikonomika_src.label (load_id, indicator, dim, code, label) FROM STDIN") as cp:
            for x in rows.values():
                if "label" in x:
                    cp.write_row((load_id, indicator, x["dim"], x["code"], x["label"]))
    return write


def current(conn, load_id):
    """The rows of a load, keyed as keyed() keys them."""
    out = {}
    for dims, geo, period, value, flag in conn.execute(
            "SELECT dims, geo, period, value, flag FROM ikonomika_src.observation WHERE load_id = %s", (load_id,)):
        out[f"{dkey(dims)}/{geo}/{period}"] = {"dims": dims, "geo": geo, "period": period, "value": value, "flag": flag}
    for dim, code, label in conn.execute("SELECT dim, code, label FROM ikonomika_src.label WHERE load_id = %s", (load_id,)):
        out[f"label/{dim}/{code}"] = {"dim": dim, "code": code, "label": label}
    return out


def accept_all(old, rows):
    """hold_when of the replay: the old database has already decided which answers it accepted."""
    return 0


def apply(conn, r, source, indicator, ref, file, rows, labels=None, now=None, replay=False):
    """Load one parsed answer (rows of (dims, geo, time, value, flag)) of the resource `ref` of `indicator`.
    -> 'stored' | 'unchanged' | 'held'."""
    new = keyed(rows, labels)
    before = conn.execute("""SELECT l.load_id, (SELECT max(period) FROM ikonomika_src.observation o WHERE o.load_id = l.load_id)
        FROM ops.load l WHERE l.tool = %s AND l.source = %s AND l.ref = %s AND l.is_current""", (TOOL, source, ref)).fetchone()
    if not any("period" in x for x in new.values()):
        # an answer without a number (a window of the archive before a currency existed): nothing to hold or replace
        if before and before[1] is not None:
            raise ShapeError("Празен отговор")
        state(conn, source, ref, at=now, status="ok", error=None, last_ok="now", rows=0)
        return "unchanged"
    done = pipeline.load(conn, r, source, ref, new, writer(indicator), raw_file_id=file.raw_file_id if file else None,
                         current=current, now=now, hold_when=accept_all if replay else None)
    n = sum("period" in x for x in new.values())
    if done.status == "held":
        reason = conn.execute("SELECT reason FROM ops.hold WHERE tool = %s AND source = %s AND ref = %s",
                              (TOOL, source, ref)).fetchone()
        state(conn, source, ref, at=now, status="held", error=reason[0] if reason else None)
        return "held"
    if done.previous:   # landing policy `latest`
        conn.execute("DELETE FROM ikonomika_src.observation WHERE load_id = %s", (done.previous,))
        conn.execute("DELETE FROM ikonomika_src.label WHERE load_id = %s", (done.previous,))
    newest = max(x["period"] for x in new.values() if "period" in x)
    changed = done.status != "unchanged"
    newer = changed and (before is None or before[1] is None or newest > before[1])
    state(conn, source, ref, at=now, status="ok", error=None, last_ok="now", rows=n,
          **({"last_change": "now"} if changed else {}), **({"last_new_period": "now"} if newer else {}))
    return STATUS[done.status]


def reject(conn, r, source, ref, cause, reason, file=None, now=None):
    """An answer that could not be read or is not valid: recorded, nothing replaced."""
    if cause == "invalid":
        pipeline.reject(conn, r, source, ref, reason, file.raw_file_id if file else None, now=now)
    elif cause == "gone":
        pipeline.log_change(conn, r, source, ref, "answer", None, str(reason)[:2000], "gone", now)
    state(conn, source, ref, at=now, status=cause, error=str(reason)[:2000])


def load_ref(conn, r):
    """db/ref/labels.csv, the Bulgarian names of the categories and the currencies, as a source of its own."""
    path = ROOT / "db" / "ref" / "labels.csv"
    raw = path.read_bytes()
    rows = {f"{x['dim']}/{x['code']}": dict(x) for x in csv.DictReader(io.StringIO(raw.decode("utf-8")))}
    file = save_raw(conn, r, "ref", "db/ref/labels.csv", "labels.csv", raw)

    def write(conn, load_id, rows):
        with conn.cursor().copy("COPY ikonomika_src.ref_label (load_id, dim, code, label) FROM STDIN") as cp:
            for x in rows.values():
                cp.write_row((load_id, x["dim"], x["code"], x["label"]))

    def read(conn, load_id):
        return {f"{d}/{c}": {"dim": d, "code": c, "label": lab} for d, c, lab in conn.execute(
            "SELECT dim, code, label FROM ikonomika_src.ref_label WHERE load_id = %s", (load_id,))}
    # our own file: what is in it is what we decided, a shorter one is not held
    done = pipeline.load(conn, r, "ref", "db/ref/labels.csv", rows, write, raw_file_id=file.raw_file_id, current=read,
                         hold_when=accept_all)
    if done.previous:
        conn.execute("DELETE FROM ikonomika_src.ref_label WHERE load_id = %s", (done.previous,))
    return done.status


BUILD_LOCK = 7_250_010


def build(conn, r):
    """The clean tables from the current loads, in one transaction; then the BNB rates into core.fx_rate."""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (BUILD_LOCK,))
        conn.execute((ROOT / "db" / "build" / "observation.sql").read_text(encoding="utf-8"))
        r.stats["facts"] = conn.execute("SELECT count(*) FROM ikonomika.observation WHERE superseded_at IS NULL").fetchone()[0]
    r.stats["fx_changed"] = conn.execute("SELECT core.take_fx_rates()").fetchone()[0]
