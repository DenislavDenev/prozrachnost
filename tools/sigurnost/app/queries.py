"""Reads of gold for the pages, the CSV and the JSON. Every function takes the year (or years) as a parameter: "the latest" is
only the newest year gold holds. The same function feeds a page, its CSV and its JSON, so they have one scope."""
import re
from decimal import Decimal

from ingest import db, templates

AB = ["reg", "reg_unknown", "per100k", "solved", "clearance"]          # shown for the structures and the types of a country
BLOCK = ["reg", "per100k", "solved", "clearance", "persons", "persons_women", "persons_minors", "persons_foreigners"]
ROWS = {"BG": "types"}                                                    # the country's own table; an oblast's rows are in the table by structure


def q(sql, *args):
    with db.connect() as c:
        cur = c.execute(sql, args)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def years():
    return [r["year"] for r in q("SELECT DISTINCT year FROM gold.source_table WHERE family = 'structures' ORDER BY year DESC")]


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
    return q("SELECT code, name, kind, sort FROM gold.structure ORDER BY sort")


def structure(code):
    r = q("SELECT code, name, kind, sort FROM gold.structure WHERE code = %s", code)
    return r[0] if r else None


def indicators():
    return {r["code"]: r for r in q("SELECT * FROM gold.indicator ORDER BY sort")}


def source_table(year, family):
    r = q("SELECT * FROM gold.source_table WHERE year = %s AND family = %s", year, family)
    return r[0] if r else None


def by_structure(year):
    """One row per structure (28 oblasts, 3 general directorates) and the country, from the table by structures."""
    if year is None:
        return []
    out = {}
    for r in q("""SELECT s.code, s.name, s.kind, s.sort, o.indicator, o.value, o.value_text FROM gold.observation o
                  JOIN gold.structure s ON s.code = o.structure_code WHERE o.family = 'structures' AND o.year = %s""", year):
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
                  FROM gold.crime_row r JOIN gold.observation o USING (year, family, structure_code, row_no)
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
    rows = q("""SELECT o.year, o.template, o.indicator, o.value FROM gold.observation o
                WHERE o.family = 'structures' AND o.structure_code = %s AND o.indicator IN ('reg', 'solved', 'per100k', 'clearance')
                ORDER BY o.year""", code)
    return rows


def checks(year=None):
    return q("""SELECT year, family, check_id, scope, expected, got, diff, status, gating, detail, resource_uri FROM gold.check_result
                WHERE (%s::int IS NULL OR year = %s) ORDER BY year, family, check_id, id""", year, year)


def sources():
    ds = q("""SELECT d.*, (SELECT count(*) FROM silver.resource r WHERE r.set_uri = d.set_uri AND r.is_current AND r.status = 'built') AS built,
                     (SELECT count(*) FROM silver.resource r WHERE r.set_uri = d.set_uri AND r.status = 'invalid') AS invalid,
                     (SELECT count(*) FROM silver.resource r WHERE r.set_uri = d.set_uri AND r.is_current AND r.kind = 'empty') AS empty,
                     (SELECT max(read_at) FROM silver.resource r WHERE r.set_uri = d.set_uri) AS last_read
              FROM silver.dataset d ORDER BY d.kind, d.year NULLS LAST, d.title""")
    tables = q("SELECT * FROM gold.source_table ORDER BY year DESC, family")
    held = q("SELECT * FROM ops.held")
    links = q("SELECT name, url, period, source_updated_at FROM silver.link ORDER BY period DESC NULLS LAST, name")
    changes = q("SELECT detected_at, ref, field, old, new, cause FROM ops.change_log ORDER BY id DESC LIMIT 30")
    return dict(datasets=ds, links=links, tables=tables, held=held, changes=changes, unmatched=q("SELECT * FROM gold.unmatched ORDER BY year, name"),
                issues=q("""SELECT r.resource_uri, r.n_rows, r.issues FROM silver.resource r WHERE r.is_current AND r.issues > 0 ORDER BY r.issues DESC"""))


def number(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v
