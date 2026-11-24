"""What the pages, the CSV and the JSON read: the serving layer only (pub_sigurnost.* and pub.*). Not the clean tables,
not the landing zone, not the ledger: the web role has no right to them, and the serving layer can be hosted without them.

Every function takes the year (or years) as a parameter: "the latest" is only the newest year published. The same
function feeds a page, its CSV and its JSON, so they have one scope."""
import re
from decimal import Decimal

import psycopg

from ingest import checks as C, config

AB = ["reg", "reg_unknown", "per100k", "solved", "clearance"]          # shown for the structures and the types of a country
BLOCK = ["reg", "per100k", "solved", "clearance", "persons", "persons_women", "persons_minors", "persons_foreigners"]
ROWS = {"BG": "types"}                                                    # the country's own table; an oblast's rows are in the table by structure


def q(sql, *args):
    with psycopg.connect(config.DSN, autocommit=True) as c:
        c.execute("SET TIME ZONE 'UTC'")
        cur = c.execute(sql, args)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def version():
    """The publication the pages are showing: its number and when it was made; None before the first one."""
    r = q("SELECT publication_id, published_at FROM pub.publication WHERE tool = 'sigurnost' ORDER BY publication_id DESC LIMIT 1")
    return r[0] if r else None


def health():
    return q("SELECT 1 AS ok")[0]["ok"] == 1


def state():
    """(problems, info) of freshness at the end of the last step."""
    rows = q("SELECT kind, text FROM pub_sigurnost.state_note ORDER BY kind, n")
    return [r["text"] for r in rows if r["kind"] == "problem"], [r["text"] for r in rows if r["kind"] == "info"]


def years():
    return [r["year"] for r in q("SELECT DISTINCT year FROM pub_sigurnost.source_table WHERE family = 'structures' ORDER BY year DESC")]


def latest():
    y = years()
    return y[0] if y else None


def year_or_latest(y):
    ys = years()
    try:
        y = int(y)
    except (TypeError, ValueError):
        return ys[0] if ys else None
    return y if y in ys else (ys[0] if ys else None)


def structures():
    return q("SELECT code, name, kind, sort FROM pub_sigurnost.structure ORDER BY sort")


def structure(code):
    r = q("SELECT code, name, kind, sort FROM pub_sigurnost.structure WHERE code = %s", code)
    return r[0] if r else None


def indicators():
    return {r["code"]: r for r in q("SELECT * FROM pub_sigurnost.indicator ORDER BY sort")}


def source_table(year, family):
    r = q("SELECT *, raw_sha256 AS sha256 FROM pub_sigurnost.source_table WHERE year = %s AND family = %s", year, family)
    return r[0] if r else None


def by_structure(year):
    """One row per structure (28 oblasts, 3 general directorates) and the country, from the table by structures."""
    if year is None:
        return []
    out = {}
    for r in q("""SELECT s.code, s.name, s.kind, s.sort, o.indicator, o.value, o.value_text FROM pub_sigurnost.observation o
                  JOIN pub_sigurnost.structure s ON s.code = o.structure_code WHERE o.family = 'structures' AND o.year = %s""", year):
        d = out.setdefault(r["code"], dict(code=r["code"], name=r["name"], kind=r["kind"], sort=r["sort"], year=year))
        d[r["indicator"]] = r["value"]
    rows = sorted(out.values(), key=lambda d: d["sort"])
    ranked = sorted((d for d in rows if d["kind"] == "oblast" and d.get("per100k") is not None), key=lambda d: -d["per100k"])
    for i, d in enumerate(ranked, 1):
        d["rank"] = i
    return rows


def measure_items(year, m):
    """The map: one value per oblast. m is reg, per100k or clearance. A missing value is None, never 0."""
    rows = [d for d in by_structure(year) if d["kind"] == "oblast"]
    vals = sorted((d.get(m) for d in rows if d.get(m) is not None), reverse=True)
    return [dict(code=d["code"], name=d["name"], v=None if d.get(m) is None else float(d[m]),
                 rank=(vals.index(d[m]) + 1) if d.get(m) is not None else None) for d in rows]


def country_row(year):
    r = [d for d in by_structure(year) if d["code"] == "BG"]
    return r[0] if r else None


def type_rows(year, code="BG"):
    """The rows of the table by crime types for the country, or of the oblast's block (a general directorate has none)."""
    fam = "types" if code == "BG" else "types_by_structure"
    inds = BLOCK if fam == "types_by_structure" else AB
    rows = {}
    for r in q("""SELECT r.row_no, r.code, r.text, r.level, r.parent_row_no, r.is_total, o.indicator, o.value, o.value_text, o.template
                  FROM pub_sigurnost.crime_row r JOIN pub_sigurnost.observation o USING (year, family, structure_code, row_no)
                  WHERE r.year = %s AND r.family = %s AND r.structure_code = %s ORDER BY r.row_no""", year, fam, code):
        d = rows.setdefault(r["row_no"], dict(row_no=r["row_no"], code=r["code"], text=r["text"], level=r["level"], parent=r["parent_row_no"],
                                              total=r["is_total"], template=r["template"], year=year))
        d[r["indicator"]] = r["value"]
        d["t_" + r["indicator"]] = r["value_text"]
    out = list(rows.values())
    for d in out:
        d["family"] = fam
    return out, inds


def _key(text):
    return re.sub(r"\s+", " ", text.lower()).strip()


def compare(rows, other, ind):
    """Adds `other_<ind>` and `diff_<ind>` from the same table of another year. Only the same template is compared; a changed
    template, a row the other year does not have, or a missing value gives None (never a zero)."""
    if not rows or not other:
        return
    same = rows[0]["template"] == other[0]["template"]
    idx = {_key(d["text"]): d for d in other}
    for d in rows:
        o = idx.get(_key(d["text"])) if same else None
        d["other_" + ind] = o.get(ind) if o else None
        a, b = d.get(ind), d["other_" + ind]
        d["diff_" + ind] = None if a is None or b is None else a - b


def series(code):
    """The yearly series of a structure from the table by structures, split where the template changes (no line across a break)."""
    rows = q("""SELECT o.year, o.template, o.indicator, o.value FROM pub_sigurnost.observation o
                WHERE o.family = 'structures' AND o.structure_code = %s AND o.indicator IN ('reg', 'solved', 'per100k', 'clearance')
                ORDER BY o.year""", code)
    return rows


def checks(year=None):
    return q("""SELECT year, family, check_id, scope, expected, got, diff, status, gating, detail, resource_uri FROM pub_sigurnost.reconciliation
                WHERE (%s::int IS NULL OR year = %s) ORDER BY year, family, check_id, n""", year, year)


def sources():
    ds = q("""SELECT * FROM pub_sigurnost.dataset ORDER BY kind, year NULLS LAST, title""")
    tables = q("SELECT *, raw_sha256 AS sha256 FROM pub_sigurnost.source_table ORDER BY year DESC, family")
    held = q("SELECT ref, reason, first_at AS first_seen FROM pub_sigurnost.held ORDER BY ref")
    links = q("SELECT name, url, period, source_updated_at FROM pub_sigurnost.link ORDER BY period DESC NULLS LAST, name")
    changes = q("SELECT detected_at, ref, field, old, new, cause FROM pub_sigurnost.change ORDER BY change_id DESC")
    known = C.known_differences()
    diffs = q("""SELECT year, family, check_id, scope, expected, got, diff, resource_uri FROM pub_sigurnost.reconciliation
                 WHERE status = 'source' AND gating ORDER BY year, family, check_id, scope""")
    for d in diffs:
        d["note"] = known.get((d["resource_uri"], d["check_id"], d["scope"], d["expected"], d["got"]), "")
    iso = list(C.isolated().values())
    mismatch = q("""SELECT set_year, title_year, resource_uri, family FROM pub_sigurnost.resource_note
                    WHERE title_year IS NOT NULL AND title_year IS DISTINCT FROM set_year ORDER BY set_year, resource_uri""")
    return dict(datasets=ds, links=links, diffs=diffs, isolated=iso, mismatch=mismatch, tables=tables, held=held, changes=changes,
                unmatched=q("SELECT * FROM pub_sigurnost.unmatched ORDER BY year, name"),
                issues=q("SELECT resource_uri, n_rows, issues FROM pub_sigurnost.resource_note WHERE issues > 0 ORDER BY issues DESC, resource_uri"))


def number(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v
