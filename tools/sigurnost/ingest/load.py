"""The archive's answers into the landing zone, through the shared module (ingest/pipeline.py).

A set's list and each of its tables are read from the archive of Наблюдател, never from the portal. The archive file is
registered in place (`pipeline.register_raw`), the parser checks the shape, and `pipeline.load` writes the rows under a
load_id. The same content twice writes nothing (`unchanged`); the same file is not even read twice (`same`), unless the
parser changed (`reread`). An answer that is clearly smaller than the table read now (fewer rows, fewer columns, or no
table at all) is held and taken only when a read at least 20 hours later gives the same content (`confirmed`). An answer
that is not a table of a known kind is rejected (`pipeline.reject`): recorded, nothing replaced.

The rows given to `pipeline.load` are the parsed sheet: `_table` (what the table is, its columns) and one row per printed
row with its cells, numbers as text, so two reads compare exactly and every difference is logged row by row.
"""
import datetime as dt
import json
import re
from decimal import Decimal
from zoneinfo import ZoneInfo

from . import archive, config, pipeline, sheet, templates

SOURCE, LIST = "egov", "egov-list"
OK = ("loaded", "confirmed", "unchanged")


# ---------------------------------------------------------------- the lists of the sets

PORTAL_ZONE = ZoneInfo("Europe/Sofia")


def portal_time(v):
    """A time of data.egov.bg: the portal writes local time without a zone ("2025-10-30 11:40:56" is 09:40:56 UTC in
    winter), so the zone is said here, not left to the session of the database."""
    if not v:
        return None
    t = v if isinstance(v, dt.datetime) else dt.datetime.fromisoformat(str(v))
    return t if t.tzinfo else t.replace(tzinfo=PORTAL_ZONE)


MONTHS = ["януари", "февруари", "март", "април", "май", "юни", "юли", "август", "септември", "октомври", "ноември", "декември"]


def link_period(name):
    """"Бюлетин април 2024 г." is the month it is for; None for a name that does not say."""
    m = re.match(r"\s*Бюлетин\s+(\w+)\s+(\d{4})", name or "", re.I)
    if not m or m.group(1).lower() not in MONTHS:
        return None
    return dt.date(int(m.group(2)), MONTHS.index(m.group(1).lower()) + 1, 1)


def list_rows(data):
    """{resource_uri: row} of a set's list as the portal gave it."""
    if not isinstance(data, list) or any(not isinstance(e, dict) or not e.get("uri") for e in data):
        raise pipeline.ShapeError("Списъкът на набора не е списък от ресурси")
    return {e["uri"]: dict(name=e.get("name") or "", resource_type=e.get("type") or "", version=str(e.get("version") or ""),
                           updated_at=str(e.get("updated_at") or ""), resource_url=e.get("resource_url") or "") for e in data}


def write_list(conn, load_id, rows):
    with conn.cursor().copy("""COPY sigurnost_src.list_entry (load_id, resource_uri, name, resource_type, version, updated_at, resource_url)
                               FROM STDIN""") as cp:
        for uri, e in rows.items():
            cp.write_row((load_id, uri, e["name"], e["resource_type"], e["version"], e["updated_at"], e["resource_url"]))


def read_list(conn, load_id):
    return {u: dict(name=n, resource_type=t, version=v, updated_at=a, resource_url=url) for u, n, t, v, a, url in conn.execute(
        """SELECT resource_uri, name, resource_type, version, updated_at, resource_url FROM sigurnost_src.list_entry
           WHERE load_id = %s ORDER BY resource_uri""", (load_id,))}


def load_list(conn, r, set_uri, path, sha, read_at, now=None):
    """One list of a set from the archive file `path` (relative to the archive)."""
    file, raw = pipeline.register_raw(conn, r, LIST, set_uri, config.ARCHIVE / path, media_type="application/json",
                                      fetched_at=read_at, expect_sha=sha)
    try:
        rows = list_rows(json.loads(raw))
        return pipeline.load(conn, r, LIST, set_uri, rows, write_list, raw_file_id=file.raw_file_id, current=read_list, now=now,
                             hold=dt.timedelta(hours=config.HOLD_AFTER_H))
    except (ValueError, pipeline.ShapeError) as e:
        pipeline.reject(conn, r, LIST, set_uri, e, file.raw_file_id, now=now)
        return None


def sync_datasets(conn):
    """The sets we read (db/ref/datasets.csv, with their terms of use) and the list of each that is current now; the
    hyperlink resources of the lists as links. Returns {set_uri: [list rows] or None}."""
    current = {ref: lid for ref, lid in conn.execute(
        "SELECT ref, load_id FROM ops.load WHERE tool = %s AND source = %s AND is_current", (config.TOOL, LIST))}
    out = {}
    with conn.transaction():
        for d in config.datasets():
            name, shared = config.LICENCES[d["terms_of_use_id"]]
            lid = current.get(d["set_uri"])
            rows = read_list(conn, lid) if lid else None
            conn.execute(
                """INSERT INTO sigurnost.dataset (set_uri, title, kind, year, terms_of_use_id, licence, shared, licence_checked,
                                                  source_updated, listed, list_load_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,'2026-10-02',%s,%s,%s)
                   ON CONFLICT (set_uri) DO UPDATE SET title = EXCLUDED.title, kind = EXCLUDED.kind, year = EXCLUDED.year,
                     terms_of_use_id = EXCLUDED.terms_of_use_id, licence = EXCLUDED.licence, shared = EXCLUDED.shared,
                     licence_checked = EXCLUDED.licence_checked, source_updated = EXCLUDED.source_updated, listed = EXCLUDED.listed,
                     list_load_id = EXCLUDED.list_load_id""",
                (d["set_uri"], d["title"], d["kind"], int(d["year"]) if d["year"] else None, d["terms_of_use_id"], name, shared,
                 d["source_updated"] or None, len(rows) if rows is not None else None, lid))
            out[d["set_uri"]] = None if rows is None else [dict(e, uri=u) for u, e in rows.items()]
            for u, e in (rows or {}).items():
                if e["resource_type"] != "Хиперлинк" or not e["resource_url"]:
                    continue
                conn.execute("""INSERT INTO sigurnost.link (resource_uri, set_uri, name, url, period, source_updated_at, list_load_id)
                                VALUES (%s,%s,%s,%s,%s,%s,%s)
                                ON CONFLICT (resource_uri) DO UPDATE SET set_uri = EXCLUDED.set_uri, name = EXCLUDED.name, url = EXCLUDED.url,
                                  period = EXCLUDED.period, source_updated_at = EXCLUDED.source_updated_at, list_load_id = EXCLUDED.list_load_id""",
                             (u, d["set_uri"], e["name"], e["resource_url"], link_period(e["name"]), portal_time(e["updated_at"]), lid))
    return out


# ---------------------------------------------------------------- one table

def _note(sh, set_year):
    return (sh.note or "; ".join(sh.dropped + ["повторен ред: " + d for d in sh.dups])
            or (f"заглавието е за {sh.banner_year} г., наборът е за {set_year} г."
                if sh.banner_year and set_year and sh.banner_year != int(set_year) else None))


def table_rows(sh, fam, tid, set_year):
    """{key: row} of a parsed sheet for pipeline.load: `_table` says what the table is, then one row per printed row."""
    rows = {"_table": dict(kind=sh.kind, title=sh.title or None, family=fam, template=tid, title_year=sh.banner_year,
                           n_cols=sh.n_cols, n_blocks=len(sh.blocks), issues=sh.issues, note=_note(sh, set_year),
                           columns=[[n, label] for n, label in (sh.blocks[0].columns if sh.blocks else [])])}
    for bn, b in enumerate(sh.blocks, 1):
        for x in b.rows:
            rows[str(x.no)] = dict(block=bn, structure=b.structure, code=x.code, label=x.label, text=x.text, marker=x.marker,
                                   level=x.level, total=x.total, dup=x.dup,
                                   cells=[[col, text, None if value is None else str(value), issue] for col, text, value, issue in x.cells])
    return rows


def n_rows(rows):
    return len(rows) - 1 if rows["_table"]["kind"] == "table" else 0


def smaller(old, rows):
    """hold_when: how many rows count as missing in the new answer against the table read now; 0 takes it. A table that
    turned empty, lost more than a tenth of its rows, or lost columns is held until a second read says the same."""
    before, after = old.get("_table"), rows["_table"]
    if not before or before["kind"] != "table":
        return 0
    have = n_rows(old)
    if after["kind"] != "table":
        return have or 1
    if n_rows(rows) < config.HOLD_ROWS * have:
        return have - n_rows(rows)
    if len(after["columns"]) + 2 < before["n_cols"]:
        return have
    return 0


def writer(meta):
    """write(conn, load_id, rows) for pipeline.load: the table, its columns, rows and cells."""
    def write(conn, load_id, rows):
        t = rows["_table"]
        conn.execute("""INSERT INTO sigurnost_src.resource (load_id, resource_uri, set_uri, raw_sha256, name, version, source_updated_at,
                          read_at, kind, title, family, template, title_year, n_rows, n_cols, n_blocks, issues, note)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                     (load_id, meta["uri"], meta["set_uri"], meta["sha"], meta["name"], meta["version"], meta["updated"], meta["read_at"],
                      t["kind"], t["title"], t["family"], t["template"], t["title_year"], n_rows(rows), t["n_cols"], t["n_blocks"],
                      t["issues"], t["note"]))
        cur = conn.cursor()
        with cur.copy("COPY sigurnost_src.col (load_id, col_no, label) FROM STDIN") as cp:
            for n, label in t["columns"]:
                cp.write_row((load_id, n, label))
        with cur.copy("""COPY sigurnost_src.row (load_id, row_no, block_no, structure, code, label, text, marker, level, is_total, dup)
                         FROM STDIN""") as cp:
            for k, x in rows.items():
                if k != "_table":
                    cp.write_row((load_id, int(k), x["block"], x["structure"], x["code"], x["label"], x["text"], x["marker"],
                                  x["level"], x["total"], x["dup"]))
        with cur.copy("COPY sigurnost_src.cell (load_id, row_no, col_no, text, value, issue) FROM STDIN") as cp:
            for k, x in rows.items():
                if k != "_table":
                    for col, text, value, issue in x["cells"]:
                        cp.write_row((load_id, int(k), col, text, None if value is None else Decimal(value), issue))
    return write


def read_table(conn, load_id):
    """The rows of a stored table, exactly as table_rows gave them."""
    t = conn.execute("""SELECT kind, title, family, template, title_year, n_cols, n_blocks, issues, note FROM sigurnost_src.resource
                        WHERE load_id = %s""", (load_id,)).fetchone()
    cols = [[n, label] for n, label in conn.execute("SELECT col_no, label FROM sigurnost_src.col WHERE load_id = %s ORDER BY col_no", (load_id,))]
    rows = {"_table": dict(zip(("kind", "title", "family", "template", "title_year", "n_cols", "n_blocks", "issues", "note"), t), columns=cols)}
    cells = {}
    for rn, col, text, value, issue in conn.execute(
            "SELECT row_no, col_no, text, value, issue FROM sigurnost_src.cell WHERE load_id = %s ORDER BY row_no, col_no", (load_id,)):
        cells.setdefault(rn, []).append([col, text, None if value is None else str(value), issue])
    for rn, bn, structure, code, label, text, marker, level, total, dup in conn.execute(
            """SELECT row_no, block_no, structure, code, label, text, marker, level, is_total, dup FROM sigurnost_src.row
               WHERE load_id = %s ORDER BY row_no""", (load_id,)):
        rows[str(rn)] = dict(block=bn, structure=structure, code=code, label=label, text=text, marker=marker, level=level,
                             total=total, dup=dup, cells=cells.get(rn, []))
    return rows


def last_file(conn, uri):
    """(sha256 of the file, status) of the last load of a resource, or None."""
    return conn.execute("""SELECT f.sha256, l.status FROM ops.load l JOIN ops.raw_file f USING (raw_file_id)
                           WHERE l.tool = %s AND l.source = %s AND l.ref = %s ORDER BY l.load_id DESC LIMIT 1""",
                        (config.TOOL, SOURCE, uri)).fetchone()


def store(conn, r, ds, res, got, now=None, reread=False):
    """One resource. `got` is archive.read(...) = (bytes, sha256, file, read_at). Returns one of
    new, empty, rewritten, unchanged, same, held, confirmed, invalid."""
    uri = res["uri"]
    raw, sha, rel, read_at = got
    last = last_file(conn, uri)
    if last and last[0] == sha and last[1] != "held" and not reread:
        return "same"
    file, raw = pipeline.register_raw(conn, r, SOURCE, uri, config.ARCHIVE / rel, media_type="application/json",
                                      fetched_at=read_at, expect_sha=sha)
    try:
        sh = sheet.parse(raw, flat=ds["kind"] == "crime-old")
        fam = templates.family_of(sh, ds["kind"])
        tid = templates.template_id(sh, fam) if fam else None
    except (sheet.ShapeError, templates.TemplateError) as e:
        pipeline.reject(conn, r, SOURCE, uri, str(e)[:500], file.raw_file_id, now=now)
        return "invalid"
    rows = table_rows(sh, fam, tid, ds["year"])
    meta = dict(uri=uri, set_uri=ds["set_uri"], sha=sha, name=res.get("name") or "", version=str(res.get("version") or ""),
                updated=portal_time(res.get("updated_at")), read_at=read_at)
    done = pipeline.load(conn, r, SOURCE, uri, rows, writer(meta), raw_file_id=file.raw_file_id, current=read_table, now=now,
                         hold=dt.timedelta(hours=config.HOLD_AFTER_H), hold_when=smaller)
    if done.status == "loaded":
        if done.previous is not None:
            return "rewritten"
        return "new" if sh.kind == "table" else "empty"
    return done.status


def ingest(conn, r, st, now=None, sets=None, reread=False):
    """The list of every set the archive holds, then every table of it. Returns a report."""
    rep = {"sets": 0, "resources": 0, "outcomes": {}, "unread": [], "not_in_archive": [], "problems": []}
    for d in config.datasets():
        if sets and d["set_uri"] not in sets:
            continue
        ent = st["seen"].get(f"{d['set_uri']}/list")
        if ent is None:
            continue
        load_list(conn, r, d["set_uri"], ent["file"], ent["sha"], ent["at"], now)
    lists = sync_datasets(conn)
    for d in config.datasets():
        lst = lists.get(d["set_uri"])
        if sets and d["set_uri"] not in sets:
            continue
        if lst is None:
            rep["not_in_archive"].append(d["set_uri"])
            continue
        rep["sets"] += 1
        for res in lst:
            if res["resource_type"] == "Хиперлинк":
                rep["links"] = rep.get("links", 0) + 1       # a link to a file on mvr.bg: kept as a link, never opened
                continue
            got = archive.read(res["uri"], st)
            if got is None:
                rep["unread"].append(res["uri"])
                continue
            out = store(conn, r, d, dict(res, updated_at=res["updated_at"] or None), got, now, reread)
            rep["resources"] += 1
            rep["outcomes"][out] = rep["outcomes"].get(out, 0) + 1
    return rep


def blocks_of(conn, load_id):
    """The blocks of a stored table, as the parser gives them, for the checks and the clean tables. Repeated rows are out."""
    cols = [(n, label) for n, label in conn.execute("SELECT col_no, label FROM sigurnost_src.col WHERE load_id = %s ORDER BY col_no", (load_id,))]
    cells = {}
    for rn, cn, text, value, issue in conn.execute(
            "SELECT row_no, col_no, text, value, issue FROM sigurnost_src.cell WHERE load_id = %s ORDER BY row_no, col_no", (load_id,)):
        cells.setdefault(rn, []).append((cn, text, value, issue))
    blocks, cur, key = [], None, None
    for rn, bn, structure, code, label, text, marker, level, total in conn.execute(
            """SELECT row_no, block_no, structure, code, label, text, marker, level, is_total FROM sigurnost_src.row
               WHERE load_id = %s AND NOT dup ORDER BY row_no""", (load_id,)):
        if bn != key:
            cur = sheet.Block(structure, "", cols, [], [], rn)
            blocks.append(cur)
            key = bn
        cur.rows.append(sheet.Row(rn, code, label, text, marker, level, cells.get(rn, []), total))
    return blocks
