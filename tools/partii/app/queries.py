"""Every number of the pages, the CSV and the JSON comes from here, so the three always have the same scope.

Each function takes `as_of` (a moment; None is "now"): the rows are those of the newest reading accepted by then
(pub_partii.as_of). "Now" is simply the newest moment. `compare` puts two moments of the same election side by side with
the same function the pages use for one. Only the serving layer is read: `pub_partii` and `pub`.
"""
import datetime as dt
from decimal import Decimal

import psycopg

from ingest import config

KIND_BG = {"party": "партия", "coalition": "коалиция", "committee": "инициативен комитет", "local_coalition": "местна коалиция"}
REG_BG = {1: "ЦИК", 2: "РИК", 3: "ОИК"}
ELECTION_BG = {"president": "президентски", "parliament": "парламентарни", "european": "европейски", "local": "местни",
               "local_partial": "частични местни", "local_new": "нови местни", "other": "други"}
DECL_BG = {"published": "има декларация", "processing": "декларацията се обработва", "none": "няма декларация"}
ERIK = "https://erik.bulnao.government.bg"
NOW = dt.datetime(9999, 1, 1, tzinfo=dt.timezone.utc)


def connect():
    c = psycopg.connect(config.DSN, autocommit=True)
    c.execute("SET TIME ZONE 'UTC'")
    return c


def rows(sql, args=()):
    with connect() as c:
        cur = c.execute(sql, args)
        names = [d.name for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]


def one(sql, args=()):
    r = rows(sql, args)
    return r[0] if r else None


def version():
    """The publication the pages are showing: its number and when it was made."""
    return one("SELECT publication_id, published_at FROM pub.publication WHERE tool = 'partii' ORDER BY publication_id DESC LIMIT 1")


def problems(now=None):
    """What is not well, from the published state of the source: the same four things the shared freshness check tells.
    How old the last look at the source is, is counted at the moment of the request."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out = []
    for r in rows("SELECT kind, name, title_bg, is_ok, since_at, limit_h, detail FROM pub_partii.health ORDER BY kind DESC, name"):
        late = r["limit_h"] is not None and (r["since_at"] is None or now - r["since_at"] > dt.timedelta(hours=r["limit_h"]))
        if r["kind"] == "source" and late:
            out.append(f"{r['title_bg']}: " + ("няма успешно четене" if r["since_at"] is None else f"последно успешно четене {r['since_at']:%d.%m.%Y %H:%M} UTC"))
        elif r["kind"] == "hold" and late:
            out.append(f"{r['title_bg']}: задържан от {r['since_at']:%d.%m.%Y} ({r['detail']})")
        elif r["kind"] in ("check", "run") and r["is_ok"] is False:
            out.append(f"{r['title_bg']}: {r['detail'] or 'грешка'}")
    return out


def moment(as_of):
    """A day or a moment as text, or None, to the moment the readings are taken at: a day means its end."""
    if not as_of:
        return NOW
    if isinstance(as_of, dt.datetime):
        return as_of
    try:
        if len(str(as_of)) == 10:
            return dt.datetime.combine(dt.date.fromisoformat(str(as_of)), dt.time.max, dt.timezone.utc)
        return dt.datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
    except ValueError:
        return NOW


def pkey(r):
    return f"{r['erik_id']}-{r['reg_in']}-{r['source_id']}"


# ---------- elections ----------

def elections(as_of=None):
    """Every election with what its tables say at the moment. A table that was never read gives None, not 0."""
    return rows("""
        SELECT e.erik_id, e.title, e.kind, e.election_day, e.list_position AS position, e.gone_at, e.threshold_eur, e.threshold_shown,
               e.listed_cik, e.listed_rik, e.listed_oik, e.report_currency, e.report_currency_evidence,
               d.records_total AS donations, d.total_money AS money, d.total_in_kind AS in_kind, d.read_at AS donations_read,
               k.records_total AS candidate_rows, k.total_money AS candidate_money, k.total_in_kind AS candidate_in_kind,
               a.records_total AS agency_rows,
               (SELECT count(*) FROM pub_partii.participant p WHERE p.erik_id = e.erik_id) AS participants,
               (SELECT count(DISTINCT x.participant_id) FROM pub_partii.donation x WHERE x.load_id = d.load_id) AS recipients
        FROM pub_partii.election e
        LEFT JOIN pub_partii.as_of(%(t)s) d ON d.erik_id = e.erik_id AND d.tbl = 'donors'
        LEFT JOIN pub_partii.as_of(%(t)s) k ON k.erik_id = e.erik_id AND k.tbl = 'candidates'
        LEFT JOIN pub_partii.as_of(%(t)s) a ON a.erik_id = e.erik_id AND a.tbl = 'agencies'
        ORDER BY e.election_day DESC NULLS LAST, e.erik_id DESC""", dict(t=moment(as_of)))


def election(eid, as_of=None):
    return next((e for e in elections(as_of) if e["erik_id"] == eid), None)


def default_election():
    """The newest election whose register has donations."""
    r = one("""SELECT e.erik_id FROM pub_partii.election e JOIN pub_partii.as_of('infinity') s ON s.erik_id = e.erik_id AND s.tbl = 'donors'
               WHERE s.records_total > 0 ORDER BY e.election_day DESC NULLS LAST, e.erik_id DESC LIMIT 1""")
    return r["erik_id"] if r else None


def moments(eid):
    """The accepted readings of the register of donations of the election (for "as of")."""
    return rows("""SELECT read_at AS applied_at, records_total, total_money, total_in_kind FROM pub_partii.reading
                   WHERE erik_id = %s AND tbl = 'donors' ORDER BY read_at, load_id""", (eid,))


def recipients(eid, as_of=None):
    """The participants of the election that received donations, with the money and the non-monetary value apart."""
    return rows("""
        SELECT p.erik_id, p.reg_in, p.source_id, p.name, p.kind, p.label, count(*) AS donations, sum(d.money) AS money, sum(d.in_kind) AS in_kind,
               count(*) FILTER (WHERE d.money + d.in_kind >= e.threshold_eur) AS over_threshold,
               count(*) FILTER (WHERE d.money + d.in_kind >= e.threshold_eur AND d.decl = 'none') AS over_none,
               min(d.donation_day) AS first_day, max(d.donation_day) AS last_day
        FROM pub_partii.as_of(%(t)s) s JOIN pub_partii.donation d USING (load_id) JOIN pub_partii.participant p USING (participant_id)
        JOIN pub_partii.election e ON e.erik_id = s.erik_id
        WHERE s.tbl = 'donors' AND s.erik_id = %(e)s
        GROUP BY p.participant_id ORDER BY sum(d.money) + sum(d.in_kind) DESC, p.name""", dict(t=moment(as_of), e=eid))


def donations(eid, as_of=None, participant=None):
    """The rows of the register. The giver is a natural person and is never named: the row says only that."""
    return rows("""
        SELECT d.erik_id, d.row_key, d.ordinal, p.reg_in, p.source_id, p.name AS recipient, p.kind AS recipient_kind, d.donation_day, d.kind, d.money, d.in_kind,
               d.decl, d.donor_kind, d.donor_name, (d.money + d.in_kind >= e.threshold_eur) AS over_threshold
        FROM pub_partii.as_of(%(t)s) s JOIN pub_partii.donation d USING (load_id) JOIN pub_partii.participant p USING (participant_id)
        JOIN pub_partii.election e ON e.erik_id = s.erik_id
        WHERE s.tbl = 'donors' AND s.erik_id = %(e)s AND (%(p)s::int IS NULL OR p.source_id = %(p)s::int) AND (%(r)s::int IS NULL OR p.reg_in = %(r)s::int)
        ORDER BY d.donation_day NULLS LAST, d.row_no""", dict(t=moment(as_of), e=eid, p=participant[1] if participant else None, r=participant[0] if participant else None))


def by_day(eid, as_of=None, participant=None):
    """[(day, money that day, non-monetary that day)] by the date of the donation; rows without a date are counted apart."""
    pts, undated = [], Decimal(0)
    for r in donations(eid, as_of, participant):
        if r["donation_day"] is None:
            undated += r["money"] + r["in_kind"]
        elif pts and pts[-1][0] == r["donation_day"]:
            pts[-1] = (pts[-1][0], pts[-1][1] + r["money"], pts[-1][2] + r["in_kind"])
        else:
            pts.append((r["donation_day"], r["money"], r["in_kind"]))
    return pts, undated


def compare(eid, a, b):
    """The same election at two moments, by recipient: what the register said at `a`, at `b`, and the difference.
    One function for the page, the CSV and the JSON; both sides have the same scope and the same unit (euro)."""
    left = {pkey(r): r for r in recipients(eid, a)}
    right = {pkey(r): r for r in recipients(eid, b)}
    out = []
    for k in sorted(set(left) | set(right), key=lambda k: -((right.get(k) or left[k])["money"])):
        x, y = left.get(k), right.get(k)
        base = y or x
        out.append(dict(key=k, name=base["name"], kind=base["kind"],
                        donations_a=x and x["donations"], money_a=x and x["money"], in_kind_a=x and x["in_kind"],
                        donations_b=y and y["donations"], money_b=y and y["money"], in_kind_b=y and y["in_kind"],
                        money_diff=(y["money"] if y else Decimal(0)) - (x["money"] if x else Decimal(0)),
                        in_kind_diff=(y["in_kind"] if y else Decimal(0)) - (x["in_kind"] if x else Decimal(0))))
    return out


def candidate_funds(eid, as_of=None, participant_id=None):
    """The money of candidates and committee members, by participant and role (no names)."""
    return rows("""
        SELECT f.participant_label, p.erik_id, p.reg_in, p.source_id, p.name, f.role, count(*) AS n, sum(f.money) AS money, sum(f.in_kind) AS in_kind,
               count(*) FILTER (WHERE f.money + f.in_kind >= e.threshold_eur AND f.decl = 'none') AS over_none
        FROM pub_partii.as_of(%(t)s) s JOIN pub_partii.candidate_fund f USING (load_id) LEFT JOIN pub_partii.participant p USING (participant_id)
        JOIN pub_partii.election e ON e.erik_id = s.erik_id
        WHERE s.tbl = 'candidates' AND s.erik_id = %(e)s AND (%(p)s::bigint IS NULL OR f.participant_id = %(p)s::bigint)
        GROUP BY f.participant_label, p.participant_id, f.role ORDER BY sum(f.money) + sum(f.in_kind) DESC, f.participant_label""",
                dict(t=moment(as_of), e=eid, p=participant_id))


def agency_rows(eid=None, as_of=None, participant_id=None, eik=None):
    return rows("""
        SELECT u.erik_id, e.title, e.election_day, u.participant_label, p.reg_in, p.source_id, p.name AS participant, u.agency_name, u.agency_eik,
               u.is_eik_valid AS eik_valid, u.agency_kind, u.descr, count(*) AS n
        FROM pub_partii.as_of(%(t)s) s JOIN pub_partii.agency_use u USING (load_id) LEFT JOIN pub_partii.participant p USING (participant_id)
        JOIN pub_partii.election e ON e.erik_id = s.erik_id
        WHERE s.tbl = 'agencies' AND (%(e)s::int IS NULL OR s.erik_id = %(e)s::int) AND (%(p)s::bigint IS NULL OR u.participant_id = %(p)s::bigint)
          AND (%(k)s::text IS NULL OR (u.agency_eik = %(k)s::text AND u.is_eik_valid))
        GROUP BY u.erik_id, e.title, e.election_day, u.participant_label, p.participant_id, u.agency_name, u.agency_eik, u.is_eik_valid, u.agency_kind, u.descr
        ORDER BY e.election_day DESC NULLS LAST, u.participant_label, u.agency_name""", dict(t=moment(as_of), e=eid, p=participant_id, k=eik))


def income_check(eid, as_of=None):
    return one("""SELECT s.checked, s.compliant, s.total_money AS money, s.total_in_kind AS in_kind, s.total_sum AS total, s.read_at
                  FROM pub_partii.as_of(%s) s WHERE s.tbl = 'results' AND s.erik_id = %s""", (moment(as_of), eid))


def eur_rate(currency):
    return Decimal(1) if currency == "EUR" else 1 / config.BGN_PER_EUR if currency == "BGN" else None


def reports(eid, as_of=None):
    """The totals of every report of the election as filed, with the euro beside them. A cell with nothing filed is None."""
    out = rows("""
        SELECT r.layout, r.row_no AS n, r.load_id, r.participant_label, r.filled, p.erik_id, p.reg_in, p.source_id, e.report_currency AS currency,
               max(c.amount) FILTER (WHERE k.code = 'income.total') AS income, max(c.amount) FILTER (WHERE k.code = 'expense.total') AS expense,
               max(c.amount) FILTER (WHERE k.code = 'in_kind.total') AS in_kind, max(c.amount) FILTER (WHERE k.code = 'total') AS total
        FROM pub_partii.as_of(%s) s JOIN pub_partii.report_row r USING (load_id) JOIN pub_partii.election e ON e.erik_id = s.erik_id
        JOIN pub_partii.report_cell c ON c.load_id = r.load_id AND c.row_no = r.row_no
        JOIN pub_partii.report_column k ON k.layout = r.layout AND k.col = c.col
        LEFT JOIN pub_partii.participant p ON p.participant_id = r.participant_id
        WHERE s.tbl = 'report' AND s.erik_id = %s
        GROUP BY r.layout, r.row_no, r.load_id, r.participant_label, r.filled, p.participant_id, e.report_currency
        ORDER BY max(c.amount) FILTER (WHERE k.code = 'expense.total') DESC NULLS LAST, r.participant_label""", (moment(as_of), eid))
    for r in out:
        rate = eur_rate(r["currency"])
        for k in ("income", "expense", "in_kind", "total"):
            r[k + "_eur"] = None if r[k] is None or rate is None else r[k] * rate
    return out


def report_lines(eid, layout, n=None, participant_id=None, as_of=None):
    """Every line of one report, in the order of the form, with its place in the hierarchy."""
    out = rows("""
        SELECT r.participant_label, k.col, k.code, k.label, k.section, k.depth, k.parent_code AS parent, c.amount_raw, c.amount, e.report_currency AS currency,
               EXISTS (SELECT 1 FROM pub_partii.report_column x WHERE x.layout = k.layout AND x.parent_code = k.code) AS has_parts
        FROM pub_partii.as_of(%(t)s) s JOIN pub_partii.report_row r USING (load_id) JOIN pub_partii.election e ON e.erik_id = s.erik_id
        JOIN pub_partii.report_cell c ON c.load_id = r.load_id AND c.row_no = r.row_no
        JOIN pub_partii.report_column k ON k.layout = r.layout AND k.col = c.col
        WHERE s.tbl = 'report' AND s.erik_id = %(e)s AND r.layout = %(l)s
          AND ((%(n)s::int IS NOT NULL AND r.row_no = %(n)s::int) OR (%(p)s::bigint IS NOT NULL AND r.participant_id = %(p)s::bigint))
        ORDER BY r.row_no, k.col""", dict(t=moment(as_of), e=eid, l=layout, n=n, p=participant_id))
    for r in out:
        rate = eur_rate(r["currency"])
        r["amount_eur"] = None if r["amount"] is None or rate is None else r["amount"] * rate
    return out


# ---------- participants ----------

MONEY = """m.donation_count AS donations, m.donated_money, m.donated_in_kind, m.over_threshold, m.over_threshold_no_decl, m.candidate_rows, m.candidate_money,
           m.candidate_in_kind, m.agency_count AS agencies, m.report_income, m.report_expense, m.report_total, m.report_currency, m.report_income_eur,
           m.report_expense_eur, m.report_total_eur"""


def participants(q=None, eid=None, limit=2000):
    """The participants, each with what the newest readings say about it. The search is on the name of the party, coalition or committee."""
    return rows(f"""
        SELECT g.erik_id, g.reg_in, g.source_id, g.name, g.kind, g.municipality, g.settlement, g.sub_election, e.title, e.election_day, {MONEY}
        FROM pub_partii.participant g JOIN pub_partii.participant_money m USING (participant_id) JOIN pub_partii.election e ON e.erik_id = g.erik_id
        WHERE (%(e)s::int IS NULL OR g.erik_id = %(e)s::int)
          AND (%(q)s::text IS NULL OR g.name_norm LIKE '%%' || btrim(regexp_replace(lower(%(q)s::text), '[^[:alnum:]]+', ' ', 'g')) || '%%')
        ORDER BY e.election_day DESC NULLS LAST, m.donated_money + m.donated_in_kind + m.candidate_money + m.candidate_in_kind DESC, g.name
        LIMIT %(n)s""", dict(e=eid, q=q or None, n=limit))


def participant(eid, reg_in, source_id):
    return one(f"""
        SELECT g.participant_id, g.erik_id, g.reg_in, g.source_id, g.name, g.kind, g.municipality, g.settlement, g.sub_election, g.name_norm,
               g.registry_number, g.is_listed AS listed, e.title, e.election_day, e.threshold_shown, {MONEY}
        FROM pub_partii.participant g JOIN pub_partii.participant_money m USING (participant_id) JOIN pub_partii.election e ON e.erik_id = g.erik_id
        WHERE g.erik_id = %s AND g.reg_in = %s AND g.source_id = %s""", (eid, reg_in, source_id))


def same_name(p):
    """Registrations in other elections under exactly the same name. The same name is not proof of the same organisation."""
    return rows("""SELECT g.erik_id, g.reg_in, g.source_id, g.name, g.kind, e.title, e.election_day, m.donated_money, m.donated_in_kind, m.donation_count AS donations
                   FROM pub_partii.participant g JOIN pub_partii.election e ON e.erik_id = g.erik_id JOIN pub_partii.participant_money m USING (participant_id)
                   WHERE g.name_norm = %s AND g.kind = %s AND g.participant_id <> %s ORDER BY e.election_day DESC NULLS LAST, g.erik_id DESC LIMIT 200""",
                (p["name_norm"], p["kind"], p["participant_id"]))


# ---------- agencies ----------

def agencies(q=None):
    return rows("""SELECT a.eik, a.name, a.name_count AS names, a.election_count AS elections, a.participant_count AS participants, a.row_count AS rows FROM pub_partii.agency a
                   WHERE %(q)s::text IS NULL OR lower(a.name) LIKE '%%' || lower(%(q)s::text) || '%%' OR a.eik = %(q)s::text
                   ORDER BY a.participant_count DESC, a.name""", dict(q=(q or "").strip() or None))


def agency(eik):
    return one("SELECT eik, name, name_count AS names, election_count AS elections, participant_count AS participants, row_count AS rows FROM pub_partii.agency WHERE eik = %s", (eik,))


def unmatched(kind=None):
    return rows("SELECT kind, key, row_count AS n, amount, reason FROM pub_partii.unmatched WHERE %(k)s::text IS NULL OR kind = %(k)s::text ORDER BY kind, row_count DESC, key", dict(k=kind))


# ---------- sources ----------

def latest():
    r = one("SELECT max(read_at) AS t FROM pub_partii.reading")
    return r["t"] if r else None


def sources():
    return dict(
        tables=rows("""SELECT tbl, count(DISTINCT erik_id) AS elections, sum(records_total) AS records, sum(total_money) AS money, sum(total_in_kind) AS in_kind,
                              max(read_at) AS newest, min(read_at) AS oldest FROM pub_partii.as_of('infinity') GROUP BY tbl ORDER BY tbl"""),
        statuses=rows("SELECT status, count(*) AS n, min(loaded_at) AS first, max(loaded_at) AS last FROM pub_partii.source_state GROUP BY status ORDER BY status"),
        held=rows("""SELECT source, ref, loaded_at, reason, status FROM pub_partii.source_state WHERE status IN ('held', 'rejected') ORDER BY load_id DESC LIMIT 50"""),
        elections=rows("""SELECT e.erik_id, e.title, e.election_day, e.gone_at, e.first_seen_at, e.report_currency, e.report_currency_evidence,
                                 d.records_total, d.total_money, d.total_in_kind, d.read_at, d.sha256,
                                 (SELECT count(*) FROM pub_partii.reading x WHERE x.erik_id = e.erik_id AND x.tbl = 'donors') AS reads
                          FROM pub_partii.election e LEFT JOIN pub_partii.as_of('infinity') d ON d.erik_id = e.erik_id AND d.tbl = 'donors'
                          ORDER BY e.election_day DESC NULLS LAST, e.erik_id DESC"""),
        jobs=rows("SELECT name AS step, since_at AS started_at, is_ok FROM pub_partii.health WHERE kind = 'run' ORDER BY since_at DESC"),
        datasets=rows("""SELECT dataset AS id, title_bg, description_bg, source_org, source_url, channel, licence, personal_data, cadence, is_distributable AS distributed
                         FROM pub.dataset WHERE tool = 'partii' ORDER BY dataset"""),
        indicators=rows("SELECT indicator_code AS code, dataset, title_bg, unit_code AS unit, kind, definition_bg FROM pub.indicator WHERE dataset IN (SELECT dataset FROM pub.dataset WHERE tool = 'partii') ORDER BY 1"),
        unmatched=rows("SELECT kind, count(*) AS keys, sum(row_count) AS rows FROM pub_partii.unmatched GROUP BY kind ORDER BY kind"),
        checks=rows("SELECT name, title_bg, is_ok AS ok, detail FROM pub_partii.health WHERE kind = 'check' ORDER BY name"))
