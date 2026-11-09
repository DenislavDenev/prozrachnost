"""The landing zone and the clean tables of Партии и пари, through the shared module (ingest/pipeline.py).

Every archived answer of ЕРИК that is not read yet becomes one load: `pipeline.register_raw` notes the archive file in
place, the parser checks the shape and the source's own counter and totals, `pipeline.load` writes the rows under a
load_id, holds a read that is smaller than the version shown now (`smaller`), and logs every difference. What is written is checked
against the source once more inside the same transaction: a difference rolls the load back and the answer is rejected.

The rows given to `pipeline.load` are plain text, numbers and booleans, so two reads compare exactly. The first row,
`_answer`, carries the counter and the totals of the source: an empty table that agrees with its own counter is an
answer like any other.
"""
import csv
import datetime as dt
import json
from decimal import Decimal

import psycopg

from . import archive, config, parse, pipeline

BUILD = config.ROOT / "db" / "build"
SOURCE = {"home": "home", "page_participants": "page-participants", "page_donation": "page-donation", "participants": "participants",
          "donors": "donors", "candidates": "candidates", "agencies": "agencies", "results": "results", "report": "report"}
KIND_BG = {"party": "Партия", "coalition": "Коалиция", "committee": "Инициативен комитет", "local_coalition": "Местна коалиция"}
OK = ("loaded", "confirmed")


def layouts():
    return json.loads((config.ROOT / "db" / "ref" / "report_layouts.json").read_text(encoding="utf-8"))


def ref_days():
    with (config.ROOT / "db" / "ref" / "election_day.csv").open(encoding="utf-8") as f:
        return {int(r["erik_id"]): dt.date.fromisoformat(r["election_day"]) for r in csv.DictReader(f)}


def num(v):
    """A sum as text with the digits of the source and no trailing zeros, so the same sum is the same text in every read."""
    if v is None:
        return None
    v = Decimal(v)
    return format(v.normalize(), "f") if v else "0"


def day(v):
    return None if v is None else str(v)


def ref_of(e):
    if e["table"] == "home":
        return "list"
    return f"{e['erik_id']}/{e['variant']}" if e["variant"] else str(e["erik_id"])


# ---------- the rows of an answer, as pipeline.load compares them ----------

def rows_of(e, raw):
    """{key: row} of one archived answer. Raises parse.ShapeError when the answer is not sound."""
    table = e["table"]
    if table == "home":
        return {str(eid): dict(title=title, position=pos) for pos, (eid, title) in enumerate(parse.home(raw), start=1)}
    if table == "page_participants":
        return {"page": parse.page_participants(raw)}
    if table == "page_donation":
        p = parse.page_donation(raw)
        return {"page": dict(threshold=num(p["threshold"]), shown=num(p["threshold_shown"]))}
    if table == "participants":
        t = parse.participants(raw, int(e["variant"]))
        out = {"_answer": dict(records_total=t["records_total"])}
        for r in t["rows"]:
            out[str(r["source_id"])] = {k: r[k] for k in ("reg_in", "kind", "name", "registry_number", "sub_election", "municipality", "settlement", "city_area")}
        return out
    if table == "donors":
        t = parse.donors(raw)
        out = {"_answer": dict(records_total=t["records_total"], total_money=num(t["total_money"]), total_in_kind=num(t["total_in_kind"]))}
        for r in t["rows"]:
            out[f"{r['row_key']}/{r['ordinal']}"] = dict(
                reg_in=r["reg_in"], source_id=r["source_id"], recipient_kind=r["recipient_kind"], recipient_name=r["recipient_name"],
                registry_number=r["registry_number"], label=r["label"], day=day(r["day"]), kind=r["kind"], stated_kind=r["stated_kind"],
                money=num(r["money"]), in_kind=num(r["in_kind"]), decl=r["decl"], donor_kind=r["donor_kind"], donor_name=r["donor_name"])
        return out
    if table == "candidates":
        t = parse.candidates(raw)
        out = {"_answer": dict(records_total=t["records_total"], total_money=num(t["total_money"]), total_in_kind=num(t["total_in_kind"]))}
        for r in t["rows"]:
            out[f"{r['row_key']}/{r['ordinal']}"] = dict(label=r["label"], role=r["role"], day=day(r["day"]), money=num(r["money"]), in_kind=num(r["in_kind"]),
                                                         decl=r["decl"])
        return out
    if table == "agencies":
        t = parse.agencies(raw)
        out = {"_answer": dict(records_total=t["records_total"])}
        for r in t["rows"]:
            out[f"{r['row_key']}/{r['ordinal']}"] = dict(label=r["label"], name=r["name"], eik=r["eik"], eik_valid=r["eik_valid"], kind=r["kind"], descr=r["descr"])
        return out
    if table == "results":
        t = parse.results(raw)
        return {"_answer": dict(records_total=t["records_total"], checked=t["checked"], compliant=t["compliant"], total_money=num(t["total_money"]),
                                total_in_kind=num(t["total_in_kind"]), total_sum=num(t["total_sum"]))}
    if table == "report":
        t = parse.report(raw, e["variant"], layouts())
        out = {"_answer": dict(records_total=t["records_total"], layout=t["kind"], columns=[[c["col"], c["code"], c["label"], c["section"], c["depth"], c["parent"]] for c in t["columns"]])}
        seen = {}
        for r in t["rows"]:
            seen[r["label"]] = k = seen.get(r["label"], 0) + 1
            out[f"{r['label']}#{k}"] = dict(cells=[c[1] for c in r["cells"]])
        return out
    raise KeyError(table)


def cell(raw):
    if raw in (None, ""):
        return None
    v = Decimal(raw)
    return None if v == -1 else v


# ---------- writers: the rows into the landing zone, then the check against the source ----------

VERIFY = {
    "donors": """SELECT 'записани ' || x.n || ' реда, парични ' || x.m || ', непарични ' || x.v || '; източникът дава ' || a.records_total || ', ' || a.total_money || ', ' || a.total_in_kind
                 FROM partii_src.answer a CROSS JOIN LATERAL (SELECT count(*) AS n, coalesce(sum(money), 0) AS m, coalesce(sum(in_kind), 0) AS v
                                                              FROM partii_src.donation d WHERE d.load_id = a.load_id) x
                 WHERE a.load_id = %s AND (x.n <> a.records_total OR abs(x.m - a.total_money) > 0.000001 OR abs(x.v - a.total_in_kind) > 0.000001)""",
    "candidates": """SELECT 'записани ' || x.n || ' реда, парични ' || x.m || ', непарични ' || x.v || '; източникът дава ' || a.records_total || ', ' || a.total_money || ', ' || a.total_in_kind
                     FROM partii_src.answer a CROSS JOIN LATERAL (SELECT count(*) AS n, coalesce(sum(money), 0) AS m, coalesce(sum(in_kind), 0) AS v
                                                                  FROM partii_src.candidate_fund d WHERE d.load_id = a.load_id) x
                     WHERE a.load_id = %s AND (x.n <> a.records_total OR abs(x.m - a.total_money) > 0.000001 OR abs(x.v - a.total_in_kind) > 0.000001)""",
    "agencies": """SELECT 'записани ' || x.n || ' реда; източникът брои ' || a.records_total
                   FROM partii_src.answer a CROSS JOIN LATERAL (SELECT count(*) AS n FROM partii_src.agency_use d WHERE d.load_id = a.load_id) x
                   WHERE a.load_id = %s AND x.n <> a.records_total""",
    "participants": """SELECT 'записани ' || x.n || ' участници; източникът брои ' || a.records_total
                       FROM partii_src.answer a CROSS JOIN LATERAL (SELECT count(*) AS n FROM partii_src.participant_row d WHERE d.load_id = a.load_id) x
                       WHERE a.load_id = %s AND x.n <> a.records_total""",
    "report": """SELECT 'записани ' || x.n || ' участници и ' || x.cells || ' клетки; във файла са ' || a.records_total || ' участници по ' || x.cols || ' колони'
                 FROM partii_src.answer a CROSS JOIN LATERAL (
                   SELECT (SELECT count(*) FROM partii_src.report_row r WHERE r.load_id = a.load_id) AS n,
                          (SELECT count(*) FROM partii_src.report_cell r WHERE r.load_id = a.load_id) AS cells,
                          (SELECT count(*) FROM partii.report_column k WHERE k.layout = (ARRAY['party', 'coalition', 'committee', 'local_coalition'])[a.variant::int + 1]) AS cols) x
                 WHERE a.load_id = %s AND (x.n <> a.records_total OR x.cells <> x.n * x.cols)""",
}


def writer(e):
    """write(conn, load_id, rows) for the answer `e`."""
    table, eid, variant = e["table"], e["erik_id"], e["variant"]

    def answer(conn, load_id, a):
        conn.execute("""INSERT INTO partii_src.answer (load_id, erik_id, source, variant, records_total, total_money, total_in_kind, total_sum, checked, compliant)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                     (load_id, eid, table, variant, a["records_total"], a.get("total_money"), a.get("total_in_kind"), a.get("total_sum"), a.get("checked"), a.get("compliant")))

    def write(conn, load_id, rows):
        data = [(k, r) for k, r in rows.items() if k != "_answer"]
        cur = conn.cursor()
        if table == "home":
            cur.executemany("INSERT INTO partii_src.election_row (load_id, erik_id, title, list_position) VALUES (%s, %s, %s, %s)",
                            [(load_id, int(k), r["title"], r["position"]) for k, r in data])
            return
        if table == "page_participants":
            p = rows["page"]
            conn.execute("INSERT INTO partii_src.page_fact (load_id, erik_id, listed_cik, listed_rik, listed_oik) VALUES (%s, %s, %s, %s, %s)", (load_id, eid, p["cik"], p["rik"], p["oik"]))
            return
        if table == "page_donation":
            p = rows["page"]
            conn.execute("INSERT INTO partii_src.page_fact (load_id, erik_id, threshold_eur, threshold_shown) VALUES (%s, %s, %s, %s)", (load_id, eid, p["threshold"], p["shown"]))
            return
        answer(conn, load_id, rows["_answer"])
        if table == "participants":
            cur.executemany("""INSERT INTO partii_src.participant_row (load_id, source_id, erik_id, reg_in, kind, name, registry_number, sub_election, municipality, settlement, city_area)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            [(load_id, int(k), eid, r["reg_in"], r["kind"], r["name"], r["registry_number"], r["sub_election"], r["municipality"], r["settlement"], r["city_area"]) for k, r in data])
        elif table == "donors":
            cur.executemany("""INSERT INTO partii_src.donation (load_id, row_no, erik_id, row_key, ordinal, reg_in, source_id, recipient_kind, recipient_name, registry_number,
                                 recipient_label, donation_day, kind, stated_kind, money, in_kind, decl, donor_kind, donor_name)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            [(load_id, i, eid, *k.rsplit("/", 1), r["reg_in"], r["source_id"], r["recipient_kind"], r["recipient_name"], r["registry_number"], r["label"], r["day"],
                              r["kind"], r["stated_kind"], r["money"], r["in_kind"], r["decl"], r["donor_kind"], r["donor_name"]) for i, (k, r) in enumerate(data, start=1)])
        elif table == "candidates":
            cur.executemany("""INSERT INTO partii_src.candidate_fund (load_id, row_no, erik_id, row_key, ordinal, participant_label, role, fund_day, money, in_kind, decl)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            [(load_id, i, eid, *k.rsplit("/", 1), r["label"], r["role"], r["day"], r["money"], r["in_kind"], r["decl"]) for i, (k, r) in enumerate(data, start=1)])
        elif table == "agencies":
            cur.executemany("""INSERT INTO partii_src.agency_use (load_id, row_no, erik_id, row_key, ordinal, participant_label, agency_name, agency_eik, is_eik_valid, agency_kind, descr)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            [(load_id, i, eid, *k.rsplit("/", 1), r["label"], r["name"], r["eik"], r["eik_valid"], r["kind"], r["descr"]) for i, (k, r) in enumerate(data, start=1)])
        elif table == "report":
            a = rows["_answer"]
            for col, code, label, section, depth, parent in a["columns"]:
                conn.execute("""INSERT INTO partii.report_column (layout, col, code, label, section, depth, parent_code) VALUES (%s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (layout, col) DO UPDATE SET code = EXCLUDED.code, label = EXCLUDED.label, section = EXCLUDED.section,
                                  depth = EXCLUDED.depth, parent_code = EXCLUDED.parent_code""", (a["layout"], col, code, label, section, depth, parent))
            cells = []
            for i, (k, r) in enumerate(data, start=1):
                label, no = k.rsplit("#", 1)
                conn.execute("INSERT INTO partii_src.report_row (load_id, row_no, erik_id, layout, participant_label, label_no, filled) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                             (load_id, i, eid, a["layout"], label, int(no), sum(1 for x in r["cells"] if cell(x) is not None)))
                cells += [(load_id, i, col, x, cell(x)) for col, x in enumerate(r["cells"], start=1)]
            cur.executemany("INSERT INTO partii_src.report_cell (load_id, row_no, col, amount_raw, amount) VALUES (%s, %s, %s, %s, %s)", cells)
        if table in VERIFY:      # what is written is the source's own counter and totals, or nothing is written
            bad = conn.execute(VERIFY[table], (load_id,)).fetchone()
            if bad:
                raise parse.Mismatch(bad[0])
    return write


def current(e):
    """current(conn, load_id): the rows of an earlier load in the shape of rows_of, so pipeline.load can compare the two."""
    table = e["table"]

    def read(conn, load_id):
        q = lambda sql: conn.execute(sql, (load_id,)).fetchall()
        if table == "home":
            return {str(i): dict(title=t, position=p) for i, t, p in q("SELECT erik_id, title, list_position FROM partii_src.election_row WHERE load_id = %s")}
        if table == "page_participants":
            return {"page": dict(zip(("cik", "rik", "oik"), q("SELECT listed_cik, listed_rik, listed_oik FROM partii_src.page_fact WHERE load_id = %s")[0]))}
        if table == "page_donation":
            a, b = q("SELECT threshold_eur, threshold_shown FROM partii_src.page_fact WHERE load_id = %s")[0]
            return {"page": dict(threshold=num(a), shown=num(b))}
        n, m, v, s, checked, compliant = q("SELECT records_total, total_money, total_in_kind, total_sum, checked, compliant FROM partii_src.answer WHERE load_id = %s")[0]
        if table == "results":
            return {"_answer": dict(records_total=n, checked=checked, compliant=compliant, total_money=num(m), total_in_kind=num(v), total_sum=num(s))}
        if table == "participants":
            out = {"_answer": dict(records_total=n)}
            for r in q("SELECT source_id, reg_in, kind, name, registry_number, sub_election, municipality, settlement, city_area FROM partii_src.participant_row WHERE load_id = %s"):
                out[str(r[0])] = dict(zip(("reg_in", "kind", "name", "registry_number", "sub_election", "municipality", "settlement", "city_area"), r[1:]))
            return out
        if table == "donors":
            out = {"_answer": dict(records_total=n, total_money=num(m), total_in_kind=num(v))}
            for r in q("""SELECT row_key, ordinal, reg_in, source_id, recipient_kind, recipient_name, registry_number, recipient_label, donation_day, kind, stated_kind, money,
                                 in_kind, decl, donor_kind, donor_name FROM partii_src.donation WHERE load_id = %s ORDER BY row_no"""):
                out[f"{r[0]}/{r[1]}"] = dict(reg_in=r[2], source_id=r[3], recipient_kind=r[4], recipient_name=r[5], registry_number=r[6], label=r[7], day=day(r[8]), kind=r[9],
                                             stated_kind=r[10], money=num(r[11]), in_kind=num(r[12]), decl=r[13], donor_kind=r[14], donor_name=r[15])
            return out
        if table == "candidates":
            out = {"_answer": dict(records_total=n, total_money=num(m), total_in_kind=num(v))}
            for r in q("SELECT row_key, ordinal, participant_label, role, fund_day, money, in_kind, decl FROM partii_src.candidate_fund WHERE load_id = %s ORDER BY row_no"):
                out[f"{r[0]}/{r[1]}"] = dict(label=r[2], role=r[3], day=day(r[4]), money=num(r[5]), in_kind=num(r[6]), decl=r[7])
            return out
        if table == "agencies":
            out = {"_answer": dict(records_total=n)}
            for r in q("SELECT row_key, ordinal, participant_label, agency_name, agency_eik, is_eik_valid, agency_kind, descr FROM partii_src.agency_use WHERE load_id = %s ORDER BY row_no"):
                out[f"{r[0]}/{r[1]}"] = dict(label=r[2], name=r[3], eik=r[4], eik_valid=r[5], kind=r[6], descr=r[7])
            return out
        if table == "report":
            layout = q("SELECT layout FROM partii_src.report_row WHERE load_id = %s LIMIT 1")
            layout = layout[0][0] if layout else parse.REPORT_KINDS[e["variant"]]
            cols = [list(r) for r in conn.execute("SELECT col, code, label, section, depth, parent_code FROM partii.report_column WHERE layout = %s ORDER BY col", (layout,))]
            out = {"_answer": dict(records_total=n, layout=layout, columns=cols)}
            for label, no, cells in q("""SELECT r.participant_label, r.label_no, array_agg(c.amount_raw ORDER BY c.col) FROM partii_src.report_row r
                                         JOIN partii_src.report_cell c USING (load_id, row_no) WHERE r.load_id = %s GROUP BY r.row_no, r.participant_label, r.label_no ORDER BY r.row_no"""):
                out[f"{label}#{no}"] = dict(cells=cells)
            return out
        raise KeyError(table)
    return read


# ---------- the run over the archive ----------

def read_since(e, pos, st):
    """Did the last good run of the archive read the address of this answer? It reads the first elections of the list every
    day and all of them on Sunday; `last` in its state says how many the last run took."""
    if e["table"] == "home" or pos.get(e["erik_id"], 0) <= config.RECENT:
        return True
    return (st.get("last") or {}).get("elections", 0) >= len(pos) > 0


def smaller(old, rows):
    """hold_when for the tables of the register: it grows during a campaign and corrects rows that have no number of their
    own (the key of a row holds its sum and its day), so a corrected row is a key that left and a key that came. A read
    is held only when it is smaller: fewer rows, or a lower total of the source. What left is still logged."""
    fewer = max(len(old) - len(rows), 0)
    if fewer:
        return fewer
    a, b = old.get("_answer") or {}, rows.get("_answer") or {}
    for k in ("total_money", "total_in_kind"):
        if a.get(k) is not None and b.get(k) is not None and Decimal(b[k]) < Decimal(a[k]) - config.SUM_TOLERANCE:
            return 1
    return 0


GROWING = ("donors", "candidates", "agencies")      # the other answers are lists whose rows keep their keys: any missing key holds


def positions(conn):
    return dict(conn.execute("""SELECT r.erik_id, r.list_position FROM partii_src.election_row r JOIN ops.load l USING (load_id)
                                WHERE l.tool = 'partii' AND l.source = 'home' AND l.is_current"""))


def take(conn, r, e, raw, raw_file_id, now, pos):
    """One answer through pipeline.load. Returns its status; a ShapeError is recorded as a rejected load and raised on."""
    source, ref = SOURCE[e["table"]], ref_of(e)
    try:
        rows = rows_of(e, raw)
        return pipeline.load(conn, r, source, ref, rows, writer(e), raw_file_id=raw_file_id, current=current(e), now=now, hold=dt.timedelta(hours=config.CONFIRM_H),
                             hold_when=smaller if e["table"] in GROWING else None).status
    except (parse.ShapeError, pipeline.ShapeError) as x:
        pipeline.reject(conn, r, source, ref, x, raw_file_id, now=now)
        raise


def read_archive(conn, r, root=None, st=None, limit=None):
    """Every archived answer that is not read yet, oldest first, then the second look at what is held."""
    root = root or config.ARCHIVE
    st = st or archive.state(root)
    rep = dict(read=0, loaded=0, confirmed=0, unchanged=0, held=0, rejected=0, problems=[])
    done = {(s, f, t) for s, f, t in conn.execute("SELECT source, ref, fetched_at FROM ops.fetch WHERE tool = 'partii'")}
    entries = archive.entries(root)
    pos = positions(conn)
    latest = {}
    for e in entries:
        source, ref = SOURCE[e["table"]], ref_of(e)
        latest[(source, ref)] = e
        if (source, ref, e["at"]) in done:
            continue
        if limit and rep["read"] >= limit:
            rep["stopped"] = f"спря след {limit} отговора; останалите са при следващото пускане"
            break
        if e["table"] != "home" and not pos:
            rep["problems"].append(f"няма приет списък с избори, а има отговор в архива: {e['file']}")
            continue
        try:
            raw = archive.read(e, root)
        except archive.ArchiveError as x:
            rep["problems"].append(str(x))
            continue
        file, _ = pipeline.register_raw(conn, r, source, ref, root / e["file"], fetched_at=e["at"])
        rep["read"] += 1
        try:
            rep[take(conn, r, e, raw, file.raw_file_id, e["at"], pos)] += 1
        except (parse.ShapeError, pipeline.ShapeError) as x:
            rep["rejected"] += 1
            rep["problems"].append(f"{e['file']}: невалиден отговор: {x}")
        if e["table"] == "home":
            pos = positions(conn)
    # the archive keeps an answer only when it changes, so "the same answer at a later read" is: the archive has read the
    # address again (last_ok, and for an election outside the daily ones a run that took all of them) and this is still
    # its newest answer for it. The list of elections is looked at
    # again on every run, which is also what says the archive is alive.
    last_ok = archive.when(st["last_ok"]) if st.get("last_ok") else None
    again = {("home", "list")} | {(s, f) for s, f in conn.execute("SELECT source, ref FROM ops.hold WHERE tool = 'partii'")}
    for key in sorted(again):
        e = latest.get(key)
        if not e or not last_ok or last_ok <= e["at"] or st["seen"].get(e["key"], {}).get("sha") != e["sha256"] or not read_since(e, pos, st):
            continue
        seen = conn.execute("SELECT max(loaded_at) FROM ops.load WHERE tool = 'partii' AND source = %s AND ref = %s", key).fetchone()[0]
        if seen and seen >= last_ok:
            continue
        try:
            raw = archive.read(e, root)
            fid = conn.execute("SELECT raw_file_id FROM ops.fetch WHERE tool = 'partii' AND source = %s AND ref = %s ORDER BY fetched_at DESC, fetch_id DESC LIMIT 1", key).fetchone()
            status = take(conn, r, e, raw, fid and fid[0], last_ok, pos)
            if key != ("home", "list") or status != "unchanged":
                rep[status] += 1
        except (archive.ArchiveError, parse.ShapeError, pipeline.ShapeError) as x:
            rep["problems"].append(f"{e['file']}: {x}")
    return rep


# ---------- the clean tables ----------

def build_elections(conn, rep):
    """partii.election from the list of the home page shown now. An election that left the list keeps its row and gets gone_at."""
    cur = conn.execute("SELECT load_id, loaded_at FROM ops.load WHERE tool = 'partii' AND source = 'home' AND is_current").fetchone()
    if not cur:
        return
    load_id, at = cur
    seen = conn.execute("SELECT max(loaded_at) FROM ops.load WHERE tool = 'partii' AND source = 'home' AND status IN ('loaded', 'confirmed', 'unchanged')").fetchone()[0]
    days = ref_days()
    known = dict(conn.execute("SELECT erik_id, gone_at FROM partii.election"))
    listed = conn.execute("SELECT erik_id, title, list_position FROM partii_src.election_row WHERE load_id = %s ORDER BY list_position", (load_id,)).fetchall()
    for eid, title, pos in listed:
        kind, d = parse.title_info(title)
        src = "title" if d else "ref" if eid in days else None
        first = conn.execute("""SELECT min(l.loaded_at) FROM partii_src.election_row r JOIN ops.load l USING (load_id) WHERE r.erik_id = %s""", (eid,)).fetchone()[0]
        conn.execute("""INSERT INTO partii.election (erik_id, title, kind, election_day, day_source, list_position, first_seen_at, last_seen_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (erik_id) DO UPDATE SET title = EXCLUDED.title, kind = EXCLUDED.kind, election_day = EXCLUDED.election_day,
                          day_source = EXCLUDED.day_source, list_position = EXCLUDED.list_position, last_seen_at = EXCLUDED.last_seen_at, gone_at = NULL""",
                     (eid, title, kind, d or days.get(eid), src, pos, first, seen))
    here = {eid for eid, _, _ in listed}
    for eid, gone in known.items():
        if eid not in here and gone is None:
            conn.execute("UPDATE partii.election SET gone_at = %s WHERE erik_id = %s", (at, eid))
            title = conn.execute("SELECT title FROM partii.election WHERE erik_id = %s", (eid,)).fetchone()[0]
            rep.setdefault("notices", []).append(f"ЕРИК: изборът „{title}“ ({eid}) вече не е в регистъра (пазим го от {at:%d.%m.%Y})")


def report_currency(conn):
    """The currency of the reports after an election: leva before 01.01.2026, euro from then on. The rule is tested on the
    data: where a participant has donations both in its report (line 2.1) and in the register (which is in euro), the two
    are compared at the fixed rate and one to one. More matches against the rule than for it leave the currency unknown."""
    for eid, d in conn.execute("SELECT erik_id, election_day FROM partii.election ORDER BY erik_id").fetchall():
        pairs = conn.execute("""SELECT x.amount, d.money FROM partii.as_of('infinity') s JOIN partii.report_row r USING (load_id)
                                  JOIN partii.report_column k ON k.layout = r.layout AND k.code = 'income.2.1'
                                  JOIN partii.report_cell x ON x.load_id = r.load_id AND x.row_no = r.row_no AND x.col = k.col
                                  JOIN (SELECT d.participant_id, sum(d.money) AS money FROM partii.as_of('infinity') s2 JOIN partii.donation d USING (load_id)
                                        WHERE s2.tbl = 'donors' AND s2.erik_id = %s GROUP BY 1) d ON d.participant_id = r.participant_id
                                WHERE s.tbl = 'report' AND s.erik_id = %s AND x.amount > 0 AND d.money > 0""", (eid, eid)).fetchall()
        bgn = sum(1 for a, m in pairs if abs(a / config.BGN_PER_EUR - m) <= Decimal("0.02"))
        eur = sum(1 for a, m in pairs if abs(a - m) <= Decimal("0.02"))
        rule = None if d is None else "BGN" if d < config.EURO_FROM else "EUR"
        evidence = f"от {len(pairs)} участници с дарения и в отчета, и в регистъра: {bgn} съвпадат при лева по 1,95583, {eur} при евро"
        if rule and ((rule == "BGN" and eur > bgn) or (rule == "EUR" and bgn > eur)):
            rule = None      # the rule ops.check_rule report_currency tells
        conn.execute("UPDATE partii.election SET report_currency = %s, report_currency_evidence = %s WHERE erik_id = %s", (rule, evidence, eid))


def build(conn, rep=None):
    """The clean tables from the landing zone, in one transaction. The readings themselves are never rebuilt."""
    rep = {} if rep is None else rep
    with conn.transaction():
        build_elections(conn, rep)
        with psycopg.ClientCursor(conn) as cur:
            cur.execute((BUILD / "10_objects.sql").read_text(encoding="utf-8"))
        report_currency(conn)
        with psycopg.ClientCursor(conn) as cur:
            for name in ("20_money.sql", "30_observations.sql"):
                cur.execute((BUILD / name).read_text(encoding="utf-8"), dict(recent=config.RECENT))
        rep["clean"] = {t: conn.execute(f"SELECT count(*) FROM partii.{t}").fetchone()[0] for t in ("election", "participant", "agency", "participant_money", "observation", "unmatched")}
    return rep
