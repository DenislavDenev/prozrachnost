"""Every query takes its period as a parameter (STANDARD 3А): "the latest" is only the newest period, never fixed in SQL.
The web user reads gold only (and ops for /sources)."""
import csv
import datetime as dt
import io
import re

import psycopg
from psycopg.rows import dict_row

from ingest import archive
from ingest import db as _db

LIMIT = 300                   # rows on a page; the CSV and the JSON have all of them
RULE_FROM = dt.date(2016, 11, 4)   # Закон за нормативните актове, чл. 26, ал. 4, в сила от 04.11.2016
SOURCE_URL = "https://data.egov.bg/data/view/18da0fff-79b2-45c6-a9af-1509df96261b"


def connect():
    return psycopg.connect(_db.DSN, row_factory=dict_row)


def rows(sql, args=()):
    with connect() as c:
        return c.execute(sql, args).fetchall()


def one(sql, args=()):
    with connect() as c:
        return c.execute(sql, args).fetchone()


def d(s, default=None):
    """A date from a query string; a wrong one is no date."""
    try:
        return dt.date.fromisoformat(s) if s else default
    except ValueError:
        return default


def tsq(q):
    """Words of the search, each matching from its beginning: 'бюджет 2026' -> 'бюджет:* & 2026:*'."""
    words = re.findall(r"\w+", q or "", re.U)
    return " & ".join(w + ":*" for w in words[:8]) or None


class Where:
    def __init__(self):
        self.parts, self.args = [], []

    def add(self, sql, *args):
        self.parts.append(sql)
        self.args += args
        return self

    @property
    def sql(self):
        return (" WHERE " + " AND ".join(self.parts)) if self.parts else ""


# ---------------------------------------------------------------------------------------------- build / freshness
def build():
    return one("SELECT build_id, built_at, rows FROM gold.build WHERE ok ORDER BY build_id DESC LIMIT 1")


def archive_read():
    try:
        return archive.last_ok()
    except archive.ArchiveError:
        return None


# ---------------------------------------------------------------------------------------------- acts of the Council of Ministers
ACT_TYPES = ["Постановления", "Решения", "Протоколни решения", "Разпореждания", "Протоколи", "Стенограми"]


def acts_where(f):
    w = Where()
    if f.get("q"):
        t = tsq(f["q"])
        if t:
            w.add("to_tsvector('simple', a.about) @@ to_tsquery('simple', %s)", t)
    if f.get("type"):
        w.add("a.act_type = %s", f["type"])
    if f.get("importer"):
        w.add("a.importer = %s", f["importer"])
    if f.get("year"):
        w.add("a.year = %s", int(f["year"]))
    if f.get("from"):
        w.add("a.accepted >= %s", f["from"])
    if f.get("to"):
        w.add("a.accepted <= %s", f["to"])
    if f.get("gazette") == "da":
        w.add("a.gazette_number IS NOT NULL")
    elif f.get("gazette") == "ne":
        w.add("a.gazette_number IS NULL")
    return w


ACT_COLS = ("a.pris_id, a.doc_num, a.accepted, a.act_type, a.about, a.importer, a.legal_reason, a.protocol, a.gazette_number, "
            "a.gazette_year, a.confidential, a.active, a.consultation_reg_num, a.origin")


def acts(f, limit=LIMIT):
    w = acts_where(f)
    total = one(f"SELECT count(*) AS n FROM gold.act a{w.sql}", w.args)["n"]
    data = rows(f"SELECT {ACT_COLS} FROM gold.act a{w.sql} ORDER BY a.accepted DESC, a.pris_id DESC" + (" LIMIT %s" if limit else ""),
                [*w.args, limit] if limit else w.args)
    return data, total


def act_years():
    return [r["year"] for r in rows("SELECT DISTINCT year FROM gold.act ORDER BY year DESC")]


def importers():
    return [r["importer"] for r in rows("SELECT importer, count(*) FROM gold.act WHERE importer IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 80")]


def acts_by_week(frm, to):
    """Acts of the Council of Ministers (постановления, решения, разпореждания) per ISO week, for [frm, to]."""
    return rows("""SELECT date_trunc('week', accepted)::date AS week, count(*) FILTER (WHERE act_type = 'Постановления') AS postanovleniya,
                          count(*) FILTER (WHERE act_type = 'Решения') AS resheniya,
                          count(*) FILTER (WHERE act_type = 'Разпореждания') AS razporezhdaniya, count(*) AS total
                   FROM gold.act WHERE accepted BETWEEN %s AND %s AND act_type IN ('Постановления', 'Решения', 'Разпореждания')
                   GROUP BY 1 ORDER BY 1""", (frm, to))


def act(pris_id):
    a = one("SELECT a.* FROM gold.act a WHERE pris_id = %s", (pris_id,))
    if not a:
        return None
    a["institutions"] = rows("SELECT institution_id, name FROM gold.act_institution WHERE pris_id = %s ORDER BY name", (pris_id,))
    a["relations"] = rows("SELECT * FROM gold.act_relation WHERE pris_id = %s ORDER BY ord", (pris_id,))
    a["consultation"] = one("SELECT reg_num, name, opened, closes FROM gold.consultation WHERE reg_num = %s", (a["consultation_reg_num"],)) if a["consultation_reg_num"] else None
    a["changes"] = rows("""SELECT detected_at, field, old, new, cause FROM ops.change_log
                           WHERE source = 'egov' AND ref = %s ORDER BY id DESC LIMIT 50""", (f"pris_act/{pris_id}",))
    a["bill_title"] = None
    m = re.search(r"ЗАКОНОПРОЕКТ\s*/\s*(.+?)\s*/", a["about"], re.I | re.S)
    if m and "одобряване" in a["about"].lower():
        a["bill_title"] = m.group(1)
    return a


# ---------------------------------------------------------------------------------------------- consultations
def consultations_where(f):
    w = Where()
    if f.get("q"):
        t = tsq(f["q"])
        if t:
            w.add("to_tsvector('simple', c.name) @@ to_tsquery('simple', %s)", t)
    if f.get("level"):
        w.add("c.level = %s", f["level"])
    if f.get("act_type"):
        w.add("c.act_type = %s", f["act_type"])
    if f.get("area"):
        w.add("c.policy_area = %s", f["area"])
    if f.get("institution"):
        w.add("c.institution_id = %s", int(f["institution"]))
    if f.get("year"):
        w.add("extract(year FROM c.opened) = %s", int(f["year"]))
    if f.get("from"):
        w.add("c.opened >= %s", f["from"])
    if f.get("to"):
        w.add("c.opened <= %s", f["to"])
    if f.get("open_on"):                    # as_of: open on that day
        w.add("c.opened <= %s AND c.closes >= %s", f["open_on"], f["open_on"])
    if f.get("short") == "da":
        w.add("c.short_term_applies")
    elif f.get("short") == "bez-motiv":
        w.add("c.short_term_applies AND NOT c.reason_given")
    if f.get("oblast"):
        w.add("c.municipality_id IN (SELECT id FROM ref.municipality WHERE oblast = %s)", f["oblast"])
    if f.get("municipality"):
        w.add("c.municipality_id = %s", f["municipality"])
    return w


CONS_COLS = ("c.reg_num, c.name, c.level, c.act_type, c.opened, c.closes, c.days, c.short_term_applies, c.short_term_reason, c.reason_given, "
             "c.policy_area, c.institution_id, c.institution_name, c.municipality_id, m.name_bg AS municipality, m.oblast, c.comment_count, c.act_pris_id")


def consultations(f, limit=LIMIT):
    w = consultations_where(f)
    j = "FROM gold.consultation c LEFT JOIN ref.municipality m ON m.id = c.municipality_id"
    total = one(f"SELECT count(*) AS n {j}{w.sql}", w.args)["n"]
    data = rows(f"SELECT {CONS_COLS} {j}{w.sql} ORDER BY c.opened DESC, c.number DESC" + (" LIMIT %s" if limit else ""),
                [*w.args, limit] if limit else w.args)
    return data, total


def consultation(reg):
    c = one("""SELECT c.*, m.name_bg AS municipality, m.oblast FROM gold.consultation c LEFT JOIN ref.municipality m ON m.id = c.municipality_id
               WHERE reg_num = %s""", (reg,))
    if not c:
        return None
    c["files"] = rows("SELECT kind, doc_date, link FROM silver.consultation_file WHERE reg_num = %s AND valid_to IS NULL ORDER BY ord", (reg,))
    c["act"] = one("SELECT pris_id, act_type, doc_num, accepted, about FROM gold.act WHERE pris_id = %s", (c["act_pris_id"],)) if c["act_pris_id"] else None
    c["acts"] = rows("SELECT pris_id, act_type, doc_num, accepted FROM gold.act WHERE consultation_reg_num = %s ORDER BY accepted", (reg,))
    c["description_plain"] = one("SELECT description FROM silver.consultation WHERE reg_num = %s AND valid_to IS NULL", (reg,))["description"]
    return c


def consultation_filters():
    return dict(
        levels=[r["level"] for r in rows("SELECT DISTINCT level FROM gold.consultation ORDER BY 1")],
        act_types=[r["act_type"] for r in rows("SELECT act_type, count(*) FROM gold.consultation WHERE act_type IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")],
        areas=[r["policy_area"] for r in rows("SELECT policy_area, count(*) FROM gold.consultation WHERE NOT archived_area GROUP BY 1 ORDER BY 1")],
        years=[int(r["y"]) for r in rows("SELECT DISTINCT extract(year FROM opened) AS y FROM gold.consultation ORDER BY 1 DESC")],
        oblasts=[r["oblast"] for r in rows("SELECT DISTINCT oblast FROM ref.municipality ORDER BY 1")],
        municipalities=rows("SELECT id, name_bg, oblast FROM ref.municipality ORDER BY name_bg"),
    )


def open_now(on, limit=12):
    return rows("""SELECT reg_num, name, closes, days, short_term_applies, institution_name, (closes - %s) AS left_days
                   FROM gold.consultation WHERE opened <= %s AND closes >= %s ORDER BY closes, reg_num LIMIT %s""", (on, on, on, limit))


def open_count(on):
    return one("""SELECT count(*) AS n, count(*) FILTER (WHERE short_term_applies) AS short FROM gold.consultation
                  WHERE opened <= %s AND closes >= %s""", (on, on))


# ---------------------------------------------------------------------------------------------- strategic documents
def strategies_where(f):
    w = Where()
    if f.get("q"):
        t = tsq(f["q"])
        if t:
            w.add("to_tsvector('simple', s.name) @@ to_tsquery('simple', %s)", t)
    if f.get("level"):
        w.add("s.level = %s", f["level"])
    if f.get("type"):
        w.add("s.doc_type = %s", f["type"])
    if f.get("year"):
        w.add("extract(year FROM s.accepted) = %s", int(f["year"]))
    if f.get("valid_on"):
        w.add("s.accepted <= %s AND (s.expires IS NULL OR s.expires >= %s)", f["valid_on"], f["valid_on"])
    return w


def strategies(f, limit=LIMIT):
    w = strategies_where(f)
    total = one(f"SELECT count(*) AS n FROM gold.strategy_doc s{w.sql}", w.args)["n"]
    data = rows("SELECT s.doc_key, s.name, s.level, s.policy_area, s.doc_type, s.authority, s.accepted, s.expires, s.act_pris_id, s.files "
                f"FROM gold.strategy_doc s{w.sql} ORDER BY s.accepted DESC NULLS LAST, s.name" + (" LIMIT %s" if limit else ""),
                [*w.args, limit] if limit else w.args)
    return data, total


def strategy_filters():
    return dict(levels=[r["level"] for r in rows("SELECT DISTINCT level FROM gold.strategy_doc WHERE level IS NOT NULL ORDER BY 1")],
                types=[r["doc_type"] for r in rows("SELECT doc_type, count(*) FROM gold.strategy_doc WHERE doc_type IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")],
                years=[int(r["y"]) for r in rows("SELECT DISTINCT extract(year FROM accepted) AS y FROM gold.strategy_doc WHERE accepted IS NOT NULL ORDER BY 1 DESC")])


# ---------------------------------------------------------------------------------------------- search
def search(q, limit=15):
    t = tsq(q)
    if not t:
        return dict(acts=[], consultations=[], strategies=[], counts=dict(acts=0, consultations=0, strategies=0))
    out = {}
    out["acts"] = rows(f"""SELECT pris_id, act_type, doc_num, accepted, about FROM gold.act WHERE to_tsvector('simple', about) @@ to_tsquery('simple', %s)
                          ORDER BY accepted DESC LIMIT %s""", (t, limit))
    out["consultations"] = rows("""SELECT reg_num, name, opened, closes, institution_name FROM gold.consultation
                                  WHERE to_tsvector('simple', name) @@ to_tsquery('simple', %s) ORDER BY opened DESC LIMIT %s""", (t, limit))
    out["strategies"] = rows("""SELECT doc_key, name, level, accepted FROM gold.strategy_doc WHERE to_tsvector('simple', name) @@ to_tsquery('simple', %s)
                               ORDER BY accepted DESC NULLS LAST LIMIT %s""", (t, limit))
    out["counts"] = dict(
        acts=one("SELECT count(*) AS n FROM gold.act WHERE to_tsvector('simple', about) @@ to_tsquery('simple', %s)", (t,))["n"],
        consultations=one("SELECT count(*) AS n FROM gold.consultation WHERE to_tsvector('simple', name) @@ to_tsquery('simple', %s)", (t,))["n"],
        strategies=one("SELECT count(*) AS n FROM gold.strategy_doc WHERE to_tsvector('simple', name) @@ to_tsquery('simple', %s)", (t,))["n"])
    return out


# ---------------------------------------------------------------------------------------------- one comparison for every page, CSV and JSON
def period_stats(frm, to):
    """The numbers of one period [frm, to]: acts by type, consultations opened, short-term ones, the days."""
    a = rows("SELECT act_type, count(*) AS n FROM gold.act WHERE accepted BETWEEN %s AND %s GROUP BY 1 ORDER BY 2 DESC", (frm, to))
    c = one("""SELECT count(*) AS n, count(*) FILTER (WHERE short_term_applies) AS short,
                      count(*) FILTER (WHERE short_term_applies AND NOT reason_given) AS short_without_reason,
                      count(*) FILTER (WHERE rule_applies) AS under_rule,
                      percentile_cont(0.5) WITHIN GROUP (ORDER BY days) AS median_days, sum(comment_count) AS comments
               FROM (SELECT *, opened >= DATE '2016-11-04' AS rule_applies FROM gold.consultation) x WHERE opened BETWEEN %s AND %s""", (frm, to))
    return dict(frm=frm, to=to, acts=a, acts_total=sum(r["n"] for r in a), consultations=c)


def compare(a_from, a_to, b_from, b_to):
    """Two periods, the same scope and the same measures. A period before 04.11.2016 has no short-term indicator (the rule did
    not exist), and the comparison says so instead of putting a zero beside a number."""
    a, b = period_stats(a_from, a_to), period_stats(b_from, b_to)
    notes = []
    for name, p in (("първия", a), ("втория", b)):
        if p["frm"] < RULE_FROM:
            notes.append(f"Периодът преди {RULE_FROM:%d.%m.%Y} (в {name} избор) е преди правилото за 30 дни: индикаторът за кратък срок не се смята за него.")
    return dict(a=a, b=b, notes=notes)


def csv_bytes(data, columns):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(columns)
    for r in data:
        w.writerow(["" if r.get(c) is None else r[c] for c in columns])
    return ("﻿" + out.getvalue()).encode("utf-8")


# ---------------------------------------------------------------------------------------------- /sources
def sources():
    return rows("""SELECT s.ref, s.role, s.status, s.error, s.rows, s.sha256, s.version, s.last_read, s.last_ok, s.last_change,
                          (SELECT max(detected_at) FROM ops.change_log l WHERE l.ref = s.ref) AS last_logged
                   FROM ops.source_state s ORDER BY s.role, s.ref""")


def reconciliations():
    return rows("""SELECT DISTINCT ON (name) name, checked_at, expected, actual, ok, kind, note FROM ops.reconciliation ORDER BY name, checked_at DESC""")


def map_data(measure, level, year):
    """Municipal consultations by municipality (or oblast) opened in `year`: all, with a short term, share with a short term."""
    key = {"obshtini": "m.id", "oblasti": "m.nuts3"}[level]
    name = {"obshtini": "m.name_bg", "oblasti": "m.oblast"}[level]
    rs = rows(f"""SELECT {key} AS code, {name} AS name, count(c.reg_num) AS n, count(c.reg_num) FILTER (WHERE c.short_term_applies) AS short
                  FROM ref.municipality m LEFT JOIN gold.consultation c ON c.municipality_id = m.id AND extract(year FROM c.opened) = %s
                  GROUP BY 1, 2""", (int(year),))
    items = []
    for r in rs:
        v = r["n"] if measure == "n" else r["short"] if measure == "short" else (round(100 * r["short"] / r["n"], 1) if r["n"] else None)
        items.append(dict(code=r["code"], name=r["name"], v=v, n=r["n"], short=r["short"]))
    items.sort(key=lambda x: (x["v"] is None, -(x["v"] or 0), x["name"]))
    for i, it in enumerate(items, 1):
        it["rank"] = i if it["v"] is not None else None
    return items
