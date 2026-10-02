"""The archive's answers into silver, with the hold rule and the log of changes.

A resource is read from the archive (never from the portal). A version of a table is (resource, sha256): the same answer
twice changes nothing, a new answer is a new version and the old one stays. An answer that is clearly smaller than the one
we hold (a table that lost rows or columns, or turned empty) is not taken: it is *held* with its sha256, and taken only
when a second read at least 20 hours later gives the same sha256 (`confirmed`). A shape error is stored as an invalid
version (no rows) and the previous version stays current.
"""
import datetime as dt
import json
import re

from . import archive, config, sheet, templates

HOLD_AFTER = dt.timedelta(hours=20)
SOURCE = "egov"


def sync_datasets(c, st, now=None):
    """The sets we read, with their terms of use (db/ref/datasets.csv) and the list the archive holds for each."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out = {}
    for d in config.datasets():
        name, shared = config.LICENCES[d["terms_of_use_id"]]
        lst = archive.resources(d["set_uri"], st)
        listed, sha, path = (len(lst[0]), lst[1], lst[2]) if lst else (None, None, None)
        c.execute(
            """INSERT INTO silver.dataset (set_uri, title, kind, year, terms_of_use_id, licence, shared, licence_checked, source_updated,
                                           listed, list_sha256, list_path, list_read_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,'2026-10-02',%s,%s,%s,%s,%s)
               ON CONFLICT (set_uri) DO UPDATE SET title = EXCLUDED.title, kind = EXCLUDED.kind, year = EXCLUDED.year,
                 terms_of_use_id = EXCLUDED.terms_of_use_id, licence = EXCLUDED.licence, shared = EXCLUDED.shared,
                 source_updated = EXCLUDED.source_updated, listed = EXCLUDED.listed, list_sha256 = EXCLUDED.list_sha256,
                 list_path = EXCLUDED.list_path, list_read_at = EXCLUDED.list_read_at""",
            (d["set_uri"], d["title"], d["kind"], int(d["year"]) if d["year"] else None, d["terms_of_use_id"], name, shared,
             d["source_updated"] or None, listed, sha, path, now if lst else None))
        out[d["set_uri"]] = lst[0] if lst else None
        if lst:
            sync_links(c, d["set_uri"], lst[0], sha)
    return out


MONTHS = ["януари", "февруари", "март", "април", "май", "юни", "юли", "август", "септември", "октомври", "ноември", "декември"]


def link_period(name):
    """"Бюлетин април 2024 г." is the month it is for; None for a name that does not say."""
    m = re.match(r"\s*Бюлетин\s+(\w+)\s+(\d{4})", name or "", re.I)
    if not m or m.group(1).lower() not in MONTHS:
        return None
    return dt.date(int(m.group(2)), MONTHS.index(m.group(1).lower()) + 1, 1)


def sync_links(c, set_uri, resources, list_sha):
    """The resources of type "Хиперлинк" (a link to a file on the Ministry's site) are recorded as links and not opened."""
    n = 0
    for r in resources:
        if r.get("type") != "Хиперлинк" or not r.get("resource_url"):
            continue
        c.execute("""INSERT INTO silver.link (resource_uri, set_uri, name, url, period, source_updated_at, list_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s)
                     ON CONFLICT (resource_uri) DO UPDATE SET name = EXCLUDED.name, url = EXCLUDED.url, period = EXCLUDED.period,
                       source_updated_at = EXCLUDED.source_updated_at, list_sha256 = EXCLUDED.list_sha256""",
                  (r["uri"], set_uri, r.get("name") or "", r["resource_url"], link_period(r.get("name")), r.get("updated_at") or None, list_sha))
        n += 1
    return n


def _log(c, ref, field, old, new, cause):
    c.execute("INSERT INTO ops.change_log (source, ref, field, old, new, cause) VALUES (%s,%s,%s,%s,%s,%s)",
              (SOURCE, ref, field, old, new, cause))


def _write(c, uri, sha, sh, fam, tid):
    with c.cursor() as cur:
        with cur.copy("COPY silver.col (resource_uri, sha256, col_no, label) FROM STDIN") as cp:
            for col_no, label in (sh.blocks[0].columns if sh.blocks else []):
                cp.write_row((uri, sha, col_no, label))
        with cur.copy("COPY silver.row (resource_uri, sha256, row_no, block_no, structure, code, label, text, marker, level, is_total) FROM STDIN") as cp:
            for bn, b in enumerate(sh.blocks, 1):
                for r in b.rows:
                    cp.write_row((uri, sha, r.no, bn, b.structure, r.code, r.label, r.text, r.marker, r.level, r.total))
        with cur.copy("COPY silver.cell (resource_uri, sha256, row_no, col_no, text, value, issue) FROM STDIN") as cp:
            for b in sh.blocks:
                for r in b.rows:
                    for col_no, text, value, issue in r.cells:
                        cp.write_row((uri, sha, r.no, col_no, text, value, issue))


def _smaller(prev, sh):
    """Why the new answer must not replace the held one, or None."""
    if prev is None or prev["kind"] != "table":
        return None
    if sh.kind != "table":
        return "таблицата стана празна"
    rows = sum(len(b.rows) for b in sh.blocks)
    if rows < config.HOLD_ROWS * prev["n_rows"]:
        return f"редовете паднаха от {prev['n_rows']} на {rows}"
    if len(sh.blocks[0].columns) + 2 < prev["n_cols"]:
        return f"колоните паднаха от {prev['n_cols']} на {len(sh.blocks[0].columns) + 2}"
    return None


def store(c, ds, res, got, now=None):
    """One resource. `got` is archive.read(...) = (bytes, sha256, file, read_at). Returns one of
    new, rewritten, same, held, confirmed, invalid, empty. `c` is in autocommit mode."""
    now = now or dt.datetime.now(dt.timezone.utc)
    uri = res["uri"]
    raw, sha, rel, read_at = got
    if c.execute("SELECT 1 FROM silver.resource WHERE resource_uri = %s AND sha256 = %s AND status IN ('built', 'invalid')", (uri, sha)).fetchone():
        return "same"
    c.execute("INSERT INTO ops.raw_file (source, ref, path, sha256, bytes) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
              (SOURCE, uri, rel, sha, len(raw)))
    meta = dict(uri=uri, sha=sha, set_uri=ds["set_uri"], name=res.get("name") or "", version=str(res.get("version") or ""),
                updated=res.get("updated_at") or None, path=rel, bytes=len(raw), read_at=read_at)
    prev = c.execute("SELECT sha256, kind, n_rows, n_cols FROM silver.resource WHERE resource_uri = %s AND is_current", (uri,)).fetchone()
    prev = dict(zip(("sha", "kind", "n_rows", "n_cols"), prev)) if prev else None
    try:
        sh = sheet.parse(raw)
        fam = templates.family_of(sh)
        tid = templates.template_id(sh, fam) if fam else None
    except (sheet.ShapeError, templates.TemplateError) as e:
        with c.transaction():
            c.execute("""INSERT INTO silver.resource (resource_uri, sha256, set_uri, name, version, source_updated_at, path, bytes, read_at, kind,
                         note, status) VALUES (%(uri)s,%(sha)s,%(set_uri)s,%(name)s,%(version)s,%(updated)s,%(path)s,%(bytes)s,%(read_at)s,'invalid',%(note)s,'invalid')
                         ON CONFLICT (resource_uri, sha256) DO UPDATE SET note = EXCLUDED.note, status = 'invalid'""",
                      dict(meta, note=str(e)[:500]))
            _log(c, uri, "sha256", prev and prev["sha"], sha, "invalid")
        return "invalid"
    why = _smaller(prev, sh)
    result = "new" if prev is None else "rewritten"
    if why:
        held = c.execute("SELECT sha256, first_seen FROM ops.held WHERE ref = %s", (uri,)).fetchone()
        if held and held[0] == sha and now - held[1] >= HOLD_AFTER:
            _log(c, uri, "sha256", prev["sha"], sha, "confirmed")
            c.execute("DELETE FROM ops.held WHERE ref = %s", (uri,))
            result = "confirmed"
        else:
            if not held or held[0] != sha:
                c.execute("""INSERT INTO ops.held (ref, sha256, first_seen, reason) VALUES (%s,%s,%s,%s)
                             ON CONFLICT (ref) DO UPDATE SET sha256 = EXCLUDED.sha256, first_seen = EXCLUDED.first_seen, reason = EXCLUDED.reason""",
                          (uri, sha, now, why))
                _log(c, uri, "sha256", prev["sha"], sha, "held")
            c.execute("""INSERT INTO silver.resource (resource_uri, sha256, set_uri, name, version, source_updated_at, path, bytes, read_at, kind,
                         note, status) VALUES (%(uri)s,%(sha)s,%(set_uri)s,%(name)s,%(version)s,%(updated)s,%(path)s,%(bytes)s,%(read_at)s,%(kind)s,%(note)s,'held')
                         ON CONFLICT (resource_uri, sha256) DO NOTHING""", dict(meta, kind=sh.kind, note=why))
            return "held"
    elif prev is not None:
        c.execute("DELETE FROM ops.held WHERE ref = %s", (uri,))
    n_rows = sum(len(b.rows) for b in sh.blocks)
    with c.transaction():
        c.execute("UPDATE silver.resource SET is_current = false WHERE resource_uri = %s AND is_current", (uri,))
        c.execute("""INSERT INTO silver.resource (resource_uri, sha256, set_uri, name, version, source_updated_at, path, bytes, read_at, kind,
                     title, family, template, n_rows, n_cols, n_blocks, issues, note, status, is_current)
                     VALUES (%(uri)s,%(sha)s,%(set_uri)s,%(name)s,%(version)s,%(updated)s,%(path)s,%(bytes)s,%(read_at)s,%(kind)s,
                             %(title)s,%(fam)s,%(tid)s,%(n_rows)s,%(n_cols)s,%(n_blocks)s,%(issues)s,%(note)s,'built',true)
                     ON CONFLICT (resource_uri, sha256) DO UPDATE SET kind = EXCLUDED.kind, title = EXCLUDED.title, family = EXCLUDED.family,
                       template = EXCLUDED.template, n_rows = EXCLUDED.n_rows, n_cols = EXCLUDED.n_cols, n_blocks = EXCLUDED.n_blocks,
                       issues = EXCLUDED.issues, note = EXCLUDED.note, status = 'built', is_current = true""",
                  dict(meta, kind=sh.kind, title=sh.title or None, fam=fam, tid=tid, n_rows=n_rows if sh.kind == "table" else 0, n_cols=sh.n_cols,
                       n_blocks=len(sh.blocks), issues=sh.issues, note=sh.note or None))
        if sh.kind == "table":
            _write(c, uri, sha, sh, fam, tid)
        if result == "new":
            _log(c, uri, "sha256", None, sha, "new-record")
        elif result == "rewritten":
            _log(c, uri, "sha256", prev["sha"], sha, "rewritten")
            if prev["n_rows"] != n_rows:
                _log(c, uri, "n_rows", str(prev["n_rows"]), str(n_rows), "rewritten")
    if sh.kind != "table":
        return "empty" if result == "new" else result
    return result


def ingest(c, st, now=None, sets=None):
    """Every resource of every set the archive holds a list for. Returns a report."""
    now = now or dt.datetime.now(dt.timezone.utc)
    lists = sync_datasets(c, st, now)
    rep = {"sets": 0, "resources": 0, "outcomes": {}, "unread": [], "not_in_archive": [], "problems": []}
    for d in config.datasets():
        lst = lists.get(d["set_uri"])
        if sets and d["set_uri"] not in sets:
            continue
        if lst is None:
            rep["not_in_archive"].append(d["set_uri"])
            continue
        rep["sets"] += 1
        known = {r[0] for r in c.execute("SELECT resource_uri FROM silver.resource WHERE set_uri = %s AND is_current", (d["set_uri"],))}
        for res in lst:
            if res.get("type") == "Хиперлинк":
                rep["links"] = rep.get("links", 0) + 1       # a link to a file on mvr.bg: recorded by sync_links, never opened
                known.discard(res["uri"])
                continue
            got = archive.read(res["uri"], st)
            if got is None:
                rep["unread"].append(res["uri"])
                continue
            out = store(c, d, res, got, now)
            rep["resources"] += 1
            rep["outcomes"][out] = rep["outcomes"].get(out, 0) + 1
            known.discard(res["uri"])
        for gone in known:
            _log(c, gone, "listed", "1", "0", "removed")
    return rep


def silver_blocks(c, uri, sha):
    """The blocks of a stored table, as the parser gives them, for the checks and the gold build."""
    cols = [(r[0], r[1]) for r in c.execute("SELECT col_no, label FROM silver.col WHERE resource_uri = %s AND sha256 = %s ORDER BY col_no", (uri, sha))]
    cells = {}
    for rn, cn, text, value, issue in c.execute("SELECT row_no, col_no, text, value, issue FROM silver.cell WHERE resource_uri = %s AND sha256 = %s ORDER BY row_no, col_no", (uri, sha)):
        cells.setdefault(rn, []).append((cn, text, value, issue))
    blocks, cur, key = [], None, None
    for rn, bn, structure, code, label, text, marker, level, total in c.execute(
            """SELECT row_no, block_no, structure, code, label, text, marker, level, is_total FROM silver.row
               WHERE resource_uri = %s AND sha256 = %s ORDER BY row_no""", (uri, sha)):
        if bn != key:
            cur = sheet.Block(structure, "", cols, [], [], rn)
            blocks.append(cur)
            key = bn
        cur.rows.append(sheet.Row(rn, code, label, text, marker, level, cells.get(rn, []), total))
    return blocks
