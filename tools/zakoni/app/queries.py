"""Every query takes its period as a parameter (STANDARD 3А): "the latest" is only the newest period, never fixed in SQL.

The pages read the serving layer only: pub_zakoni.* and pub.* (the places). Not the clean tables, not the landing zone,
not the ledger: the web role has no right to them, and the serving layer can be hosted without them.

One request is one snapshot. The first read of a request opens a read-only connection in REPEATABLE READ and reads the
number of the publication in it; every later read of the same request goes through that connection, so the answer is of
one version, the old one or the new one, never a mix."""
import contextlib
import contextvars
import csv
import datetime as dt
import io
import re

import psycopg
from psycopg.rows import dict_row

from ingest.config import DSN, STALE_HOURS

LIMIT = 300                   # rows on a page; the CSV and the JSON have all of them
RULE_FROM = dt.date(2016, 11, 4)   # Закон за нормативните актове, чл. 26, ал. 4, в сила от 04.11.2016
SOURCE_URL = "https://data.egov.bg/data/view/18da0fff-79b2-45c6-a9af-1509df96261b"


# the municipalities as the pages address them: by the ЕИК of the administration, with the name of the oblast to show
MUNI = ("(SELECT m.eik AS id, m.name_bg, o.label_bg AS oblast, o.nuts3_code AS nuts3 "
        "FROM pub.municipality m JOIN pub.oblast o USING (oblast_code))")


class Snapshot:
    """The connection of one request, opened on its first read."""

    def __init__(self):
        self.conn = None
        self.publication = None

    def open(self):
        if self.conn is None:
            self.conn = psycopg.connect(DSN, row_factory=dict_row)
            self.conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            self.conn.read_only = True
            self.publication = self.conn.execute(
                "SELECT publication_id, published_at FROM pub.publication ORDER BY publication_id DESC LIMIT 1").fetchone()
        return self.conn

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None


current = contextvars.ContextVar("zakoni_snapshot", default=None)


@contextlib.contextmanager
def connect():
    """The snapshot of the request when there is one; outside a request (a script, a test) a snapshot of its own."""
    snap = current.get()
    if snap is not None:
        yield snap.open()
        return
    own = Snapshot()
    try:
        yield own.open()
    finally:
        own.close()


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


# ---------------------------------------------------------------------------------------------- publication / freshness
def build():
    """The newest publication of this tool: what the pages show (its number and when it was made)."""
    return one("""SELECT publication_id AS build_id, published_at AS built_at, changes AS rows FROM pub.publication
                  WHERE tool = 'zakoni' ORDER BY publication_id DESC LIMIT 1""")


def archive_read():
    """When the Наблюдател archive last read data.egov.bg for the published data."""
    return one("SELECT max(last_read) AS t FROM pub_zakoni.source_state")["t"]


BAD = ("невалиден отговор", "липсва при източника", "грешка при четене", "неуспешно", "задържан до второ четене")


def problems(now=None):
    """What is not well, from the published state of the source; how old the last read is, is counted at the request."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out = []
    t = archive_read()
    if t is None or now - t > dt.timedelta(hours=STALE_HOURS):
        out.append("Архивът не е чел data.egov.bg от " + (f"{t:%d.%m.%Y %H:%M}" if t else "никога"))
    for r in rows("SELECT ref, status, error FROM pub_zakoni.source_state WHERE status = ANY(%s) ORDER BY ref", (list(BAD),)):
        out.append(f"Ресурс {r['ref'][:8]}: {r['status']}" + (f": {r['error'][:200]}" if r["error"] else ""))
    for r in rows("SELECT name, expected, actual, note FROM pub_zakoni.reconciliation WHERE kind = 'block' AND NOT ok ORDER BY name"):
        out.append(f"Сверка {r['note']}: {r['expected']} срещу {r['actual']}")
    return out


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
    total = one(f"SELECT count(*) AS n FROM pub_zakoni.act a{w.sql}", w.args)["n"]
    data = rows(f"SELECT {ACT_COLS} FROM pub_zakoni.act a{w.sql} ORDER BY a.accepted DESC, a.pris_id DESC" + (" LIMIT %s" if limit else ""),
                [*w.args, limit] if limit else w.args)
    return data, total


def act_years():
    return [r["year"] for r in rows("SELECT DISTINCT year FROM pub_zakoni.act ORDER BY year DESC")]


def importers():
    return [r["importer"] for r in rows("SELECT importer, count(*) FROM pub_zakoni.act WHERE importer IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 80")]


def acts_by_week(frm, to):
    """Acts of the Council of Ministers (постановления, решения, разпореждания) per ISO week, for [frm, to]."""
    return rows("""SELECT date_trunc('week', accepted)::date AS week, count(*) FILTER (WHERE act_type = 'Постановления') AS postanovleniya,
                          count(*) FILTER (WHERE act_type = 'Решения') AS resheniya,
                          count(*) FILTER (WHERE act_type = 'Разпореждания') AS razporezhdaniya, count(*) AS total
                   FROM pub_zakoni.act WHERE accepted BETWEEN %s AND %s AND act_type IN ('Постановления', 'Решения', 'Разпореждания')
                   GROUP BY 1 ORDER BY 1""", (frm, to))


def act(pris_id):
    a = one("SELECT a.* FROM pub_zakoni.act a WHERE pris_id = %s", (pris_id,))
    if not a:
        return None
    a["institutions"] = rows("SELECT institution_id, name FROM pub_zakoni.act_institution WHERE pris_id = %s ORDER BY name", (pris_id,))
    a["relations"] = rows("SELECT * FROM pub_zakoni.act_relation WHERE pris_id = %s ORDER BY ord", (pris_id,))
    a["consultation"] = one("SELECT reg_num, name, opened, closes FROM pub_zakoni.consultation WHERE reg_num = %s", (a["consultation_reg_num"],)) if a["consultation_reg_num"] else None
    a["changes"] = rows("""SELECT detected_at, field, old, new, cause FROM pub_zakoni.act_change
                           WHERE pris_id = %s ORDER BY change_id DESC LIMIT 50""", (pris_id,))
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
        w.add(f"c.municipality_id IN (SELECT id FROM {MUNI} x WHERE oblast = %s)", f["oblast"])
    if f.get("municipality"):
        w.add("c.municipality_id = %s", f["municipality"])
    return w


CONS_COLS = ("c.reg_num, c.name, c.level, c.act_type, c.opened, c.closes, c.days, c.short_term_applies, c.short_term_reason, c.reason_given, "
             "c.policy_area, c.institution_id, c.institution_name, c.municipality_id, m.name_bg AS municipality, m.oblast, c.comment_count, c.act_pris_id")


def consultations(f, limit=LIMIT):
    w = consultations_where(f)
    j = f"FROM pub_zakoni.consultation c LEFT JOIN {MUNI} m ON m.id = c.municipality_id"
    total = one(f"SELECT count(*) AS n {j}{w.sql}", w.args)["n"]
    data = rows(f"SELECT {CONS_COLS} {j}{w.sql} ORDER BY c.opened DESC, c.number DESC" + (" LIMIT %s" if limit else ""),
                [*w.args, limit] if limit else w.args)
    return data, total


def consultation(reg):
    c = one(f"""SELECT c.*, m.name_bg AS municipality, m.oblast FROM pub_zakoni.consultation c LEFT JOIN {MUNI} m ON m.id = c.municipality_id
               WHERE reg_num = %s""", (reg,))
    if not c:
        return None
    c["files"] = rows("SELECT kind, doc_date, link FROM pub_zakoni.consultation_file WHERE reg_num = %s ORDER BY ord", (reg,))
    c["act"] = one("SELECT pris_id, act_type, doc_num, accepted, about FROM pub_zakoni.act WHERE pris_id = %s", (c["act_pris_id"],)) if c["act_pris_id"] else None
    c["acts"] = rows("SELECT pris_id, act_type, doc_num, accepted FROM pub_zakoni.act WHERE consultation_reg_num = %s ORDER BY accepted", (reg,))
    c["description_plain"] = c["description"]
    return c


def consultation_filters():
    return dict(
        levels=[r["level"] for r in rows("SELECT DISTINCT level FROM pub_zakoni.consultation ORDER BY 1")],
        act_types=[r["act_type"] for r in rows("SELECT act_type, count(*) FROM pub_zakoni.consultation WHERE act_type IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")],
        areas=[r["policy_area"] for r in rows("SELECT policy_area, count(*) FROM pub_zakoni.consultation WHERE NOT archived_area GROUP BY 1 ORDER BY 1")],
        years=[int(r["y"]) for r in rows("SELECT DISTINCT extract(year FROM opened) AS y FROM pub_zakoni.consultation ORDER BY 1 DESC")],
        oblasts=[r["oblast"] for r in rows(f"SELECT DISTINCT oblast FROM {MUNI} m ORDER BY 1")],
        municipalities=rows(f"SELECT id, name_bg, oblast FROM {MUNI} m ORDER BY name_bg"),
    )


def open_now(on, limit=12):
    return rows("""SELECT reg_num, name, closes, days, short_term_applies, institution_name, (closes - %s) AS left_days
                   FROM pub_zakoni.consultation WHERE opened <= %s AND closes >= %s ORDER BY closes, reg_num LIMIT %s""", (on, on, on, limit))


def open_count(on):
    return one("""SELECT count(*) AS n, count(*) FILTER (WHERE short_term_applies) AS short FROM pub_zakoni.consultation
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
    total = one(f"SELECT count(*) AS n FROM pub_zakoni.strategy_doc s{w.sql}", w.args)["n"]
    data = rows("SELECT s.doc_key, s.name, s.level, s.policy_area, s.doc_type, s.authority, s.accepted, s.expires, s.act_pris_id, s.files "
                f"FROM pub_zakoni.strategy_doc s{w.sql} ORDER BY s.accepted DESC NULLS LAST, s.name" + (" LIMIT %s" if limit else ""),
                [*w.args, limit] if limit else w.args)
    return data, total


def strategy_filters():
    return dict(levels=[r["level"] for r in rows("SELECT DISTINCT level FROM pub_zakoni.strategy_doc WHERE level IS NOT NULL ORDER BY 1")],
                types=[r["doc_type"] for r in rows("SELECT doc_type, count(*) FROM pub_zakoni.strategy_doc WHERE doc_type IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")],
                years=[int(r["y"]) for r in rows("SELECT DISTINCT extract(year FROM accepted) AS y FROM pub_zakoni.strategy_doc WHERE accepted IS NOT NULL ORDER BY 1 DESC")])


# ---------------------------------------------------------------------------------------------- search
def search(q, limit=15):
    t = tsq(q)
    if not t:
        return dict(acts=[], consultations=[], strategies=[], counts=dict(acts=0, consultations=0, strategies=0))
    out = {}
    out["acts"] = rows(f"""SELECT pris_id, act_type, doc_num, accepted, about FROM pub_zakoni.act WHERE to_tsvector('simple', about) @@ to_tsquery('simple', %s)
                          ORDER BY accepted DESC LIMIT %s""", (t, limit))
    out["consultations"] = rows("""SELECT reg_num, name, opened, closes, institution_name FROM pub_zakoni.consultation
                                  WHERE to_tsvector('simple', name) @@ to_tsquery('simple', %s) ORDER BY opened DESC LIMIT %s""", (t, limit))
    out["strategies"] = rows("""SELECT doc_key, name, level, accepted FROM pub_zakoni.strategy_doc WHERE to_tsvector('simple', name) @@ to_tsquery('simple', %s)
                               ORDER BY accepted DESC NULLS LAST LIMIT %s""", (t, limit))
    out["counts"] = dict(
        acts=one("SELECT count(*) AS n FROM pub_zakoni.act WHERE to_tsvector('simple', about) @@ to_tsquery('simple', %s)", (t,))["n"],
        consultations=one("SELECT count(*) AS n FROM pub_zakoni.consultation WHERE to_tsvector('simple', name) @@ to_tsquery('simple', %s)", (t,))["n"],
        strategies=one("SELECT count(*) AS n FROM pub_zakoni.strategy_doc WHERE to_tsvector('simple', name) @@ to_tsquery('simple', %s)", (t,))["n"])
    return out


# ---------------------------------------------------------------------------------------------- one comparison for every page, CSV and JSON
def period_stats(frm, to):
    """The numbers of one period [frm, to]: acts by type, consultations opened, short-term ones, the days."""
    a = rows("SELECT act_type, count(*) AS n FROM pub_zakoni.act WHERE accepted BETWEEN %s AND %s GROUP BY 1 ORDER BY 2 DESC", (frm, to))
    c = one("""SELECT count(*) AS n, count(*) FILTER (WHERE short_term_applies) AS short,
                      count(*) FILTER (WHERE short_term_applies AND NOT reason_given) AS short_without_reason,
                      count(*) FILTER (WHERE rule_applies) AS under_rule,
                      percentile_cont(0.5) WITHIN GROUP (ORDER BY days) AS median_days, sum(comment_count) AS comments
               FROM (SELECT *, opened >= DATE '2016-11-04' AS rule_applies FROM pub_zakoni.consultation) x WHERE opened BETWEEN %s AND %s""", (frm, to))
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
    return rows("""SELECT s.ref, s.role, s.status, s.error, s.rows, s.sha256, s.version, s.last_read, s.last_ok, s.last_change, s.last_logged
                   FROM pub_zakoni.source_state s ORDER BY s.role, s.ref""")


def reconciliations():
    return rows("SELECT name, checked_at, expected, actual, ok, kind, note FROM pub_zakoni.reconciliation ORDER BY name")


def map_data(measure, level, year):
    """Municipal consultations by municipality (or oblast) opened in `year`: all, with a short term, share with a short term."""
    key = {"obshtini": "m.id", "oblasti": "m.nuts3"}[level]
    name = {"obshtini": "m.name_bg", "oblasti": "m.oblast"}[level]
    rs = rows(f"""SELECT {key} AS code, {name} AS name, count(c.reg_num) AS n, count(c.reg_num) FILTER (WHERE c.short_term_applies) AS short
                  FROM {MUNI} m LEFT JOIN pub_zakoni.consultation c ON c.municipality_id = m.id AND extract(year FROM c.opened) = %s
                  GROUP BY 1, 2""", (int(year),))
    items = []
    for r in rs:
        v = r["n"] if measure == "n" else r["short"] if measure == "short" else (round(100 * r["short"] / r["n"], 1) if r["n"] else None)
        items.append(dict(code=r["code"], name=r["name"], v=v, n=r["n"], short=r["short"]))
    items.sort(key=lambda x: (x["v"] is None, -(x["v"] or 0), x["name"]))
    for i, it in enumerate(items, 1):
        it["rank"] = i if it["v"] is not None else None
    return items
