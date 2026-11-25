"""What the pages, the CSV files and the JSON addresses read: the serving layer only (pub_subsidii, pub, and the
registrations Население publishes in pub_naselenie). Not the clean tables, not the landing zone, not the ledger: the web
role has no right to them, and the serving layer can be hosted without them.

One request is one snapshot: app.main opens one read-only REPEATABLE READ connection per request and every query of the
request goes through it, so an answer is of one publication, the old one or the new one, never a mix.

Every function takes the period as a parameter: a financial year and a snapshot day (`snap`, the day of the file of the
year that was read; any date resolves to the newest snapshot not after it). "The latest" is only the default. One Scope
carries the year, the snapshot and the filters, and the same Scope feeds the numbers, the chart, the table, the CSV and the JSON.

Names of people: a natural person or a sole trader is a row only above the threshold of ДФЗ (1 250 euro in the year),
with a masked name until two years after the year; below it only in the sums (pub_subsidii.below_threshold).
"""
import contextlib
import contextvars
import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from ingest import config, names

ROOT = Path(__file__).resolve().parent
KINDS = {"all": "Всички получатели", "legal": "Юридически лица", "sole_trader": "Еднолични търговци", "natural": "Физически лица"}
KIND_ONE = {"legal": "фирма", "sole_trader": "ЕТ", "natural": "физическо лице"}
PERSON_LABEL = {"sole_trader": "физическо лице (едноличен търговец)", "natural": "физическо лице"}
FUNDS = {"efgz": "ЕФГЗ", "ezfrs": "ЕЗФРСР", "nb": "Национален бюджет"}
LEVELS = [("obshtini", "Общини"), ("oblasti", "Области"), ("rayoni", "Райони"), ("makrorayoni", "Макрорайони")]
NUTS = {"obshtini": 4, "oblasti": 3, "rayoni": 2, "makrorayoni": 1}
SORTS = {"name": "name_export", "oblast": "m.oblast", "obshtina": "m.name_bg", "efgz": "r.efgz", "ezfrs": "r.ezfrs", "nb": "r.nb", "total": "r.total"}
PAGE = 50
ZERO = Decimal(0)
DATASET = "subsidii.paid"
# what the register of datasets does not say, for the sources page
DATASET_NOTES = dict(
    why_not_higher="ДФЗ няма API. Самата справка предлага CSV за изтегляне (канал „файл“), който Наблюдател архивира всеки понеделник. "
                   "Дали наборът е и в портала за отворени данни не е проверено: инструментът не праща заявки към портала.",
    licence_note="На страницата на справката няма посочен лиценз. Публикуването е по Регламент (ЕС) 2021/2116, чл. 98, за срок от две години. "
                 "Показва се с източника; не се раздава и не влиза в общия API, докато няма писмено потвърждение.",
    personal_data_bg="Юридическите лица са с наименованието си. Физическите лица и едноличните търговци с над 1 250 € за годината (прагът, "
                     "под който самият ДФЗ не публикува имена) са с маска „Иван ***“ само в списъка, до две години след края на "
                     "финансовата година; с 1 250 € или по-малко са само в сборовете. Пълните имена не са в слоя за показване, "
                     "в CSV и в JSON. Хора не се броят и не се свързват по име.")

# the municipalities with the names the pages use: the ЕИК of the municipality is its address (/obshtini/<ЕИК>), the
# oblast is its label ("София област"), and the codes of the oblast, region and macro-region come with it
MUN = """(SELECT m.municipality_code AS code, m.eik AS id, m.name_bg, o.label_bg AS oblast, o.oblast_code, o.nuts3_code AS nuts3,
                 n3.parent_code AS nuts2, n2.parent_code AS nuts1
          FROM pub.municipality m JOIN pub.oblast o USING (oblast_code)
          JOIN pub.nuts n3 ON n3.nuts_code = o.nuts3_code JOIN pub.nuts n2 ON n2.nuts_code = n3.parent_code
          WHERE NOT m.is_gone)"""
# the name a row of the list shows, and the one an export has (never a person's, not even masked)
NAME_SHOWN = """CASE WHEN r.kind = 'legal' THEN r.org_name ELSE coalesce(r.person_masked::text,
                CASE r.kind WHEN 'sole_trader' THEN 'физическо лице (едноличен търговец)' ELSE 'физическо лице' END) END"""
NAME_EXPORT = """CASE WHEN r.kind = 'legal' THEN r.org_name WHEN r.kind = 'sole_trader' THEN 'физическо лице (едноличен търговец)' ELSE 'физическо лице' END"""
SPAN = "r.fy = %s AND r.from_day <= %s AND (r.to_day IS NULL OR r.to_day >= %s)"

CONN = contextvars.ContextVar("subsidii_conn", default=None)


@contextlib.contextmanager
def snapshot():
    """A read-only connection whose every statement sees the database as of its first one."""
    with psycopg.connect(config.DSN) as c:
        c.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        c.read_only = True
        c.execute("SET TIME ZONE 'UTC'")
        yield c


def q(sql, *args, one=False):
    c = CONN.get()
    if c is None:              # outside a request (tests, a script): its own snapshot
        with snapshot() as own:
            token = CONN.set(own)
            try:
                return q(sql, *args, one=one)
            finally:
                CONN.reset(token)
    with c.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, args)
        return cur.fetchone() if one else cur.fetchall()


def version():
    """The publication the pages are showing: its number and when it was made (None before the first one)."""
    return q("SELECT publication_id, published_at FROM pub.publication ORDER BY publication_id DESC LIMIT 1", one=True)


# ---------- years, snapshots, the references ----------

def years():
    """The financial years that have a good build, newest first, each with its snapshot days (newest first)."""
    return q("""SELECT y.*, (SELECT array_agg(b.snap_day ORDER BY b.snap_day DESC) FROM pub_subsidii.build b WHERE b.fy = y.fy AND b.ok) AS snaps
                FROM pub_subsidii.fiscal_year y WHERE EXISTS (SELECT 1 FROM pub_subsidii.build b WHERE b.fy = y.fy AND b.ok) ORDER BY y.fy DESC""")


def latest():
    ys = years()
    return ys[0]["snaps"][0] if ys else None


def snap_for(fy_row, snap=None):
    """The newest snapshot of the year not after `snap` (a date or None for the newest)."""
    if snap is None:
        return fy_row["snaps"][0]
    snap = snap if isinstance(snap, dt.date) else dt.date.fromisoformat(str(snap))
    ok = [d for d in fy_row["snaps"] if d <= snap]
    return ok[0] if ok else None


_REF = None


def refs():
    global _REF
    if _REF is None:
        _REF = q(f"SELECT id, code, name_bg, oblast, oblast_code, nuts3, nuts2, nuts1 FROM {MUN} m ORDER BY name_bg")
    return _REF


def oblasti():
    return sorted({r["oblast"] for r in refs()})


def oblast_code(label):
    return next((r["oblast_code"] for r in refs() if r["oblast"] == label), None)


_GEO = None


def geo_names():
    """code -> name of the areas of the map (oblasts, regions, macro-regions), from the same outlines the map draws."""
    global _GEO
    if _GEO is None:
        M = json.loads((ROOT / "static" / "bg-municipalities.json").read_text(encoding="utf-8"))
        n = dict(M.get("names", {}))
        n.update({k: v["n"] for k, v in M["oblasts"].items() if "n" in v})
        _GEO = n
    return _GEO


@dataclass
class Scope:
    fy_row: dict
    snap: dt.date
    snap_given: bool = False
    oblast: str = ""
    obshtina: str = ""
    kind: str = "all"
    fond: str = ""

    @property
    def fy(self):
        return self.fy_row["fy"]

    def eur(self, v):
        """The amount in euro, or None while the currency of the year is not confirmed."""
        k = self.fy_row["eur_per_unit"]
        return None if v is None or k is None else (Decimal(v) * k).quantize(Decimal("0.01"))

    @property
    def currency(self):
        return self.fy_row["currency"]

    def params(self, **over):
        """The parameters of this scope for a link, the CSV and the JSON of the same page."""
        p = {"fy": self.fy}
        if self.snap_given:
            p["snap"] = str(self.snap)
        if self.oblast:
            p["oblast"] = self.oblast
        if self.obshtina:
            p["obshtina"] = self.obshtina
        if self.kind != "all":
            p["tip"] = self.kind
        if self.fond:
            p["fond"] = self.fond
        p.update({k: v for k, v in over.items() if v not in (None, "")})
        return p

    def where_places(self, alias="m"):
        """SQL for the oblast and municipality filter on a join with MUN, and its arguments."""
        sql, args = "", []
        if self.oblast:
            sql += f" AND {alias}.oblast = %s"
            args.append(self.oblast)
        if self.obshtina:
            sql += f" AND {alias}.id = %s"
            args.append(self.obshtina)
        return sql, args

    def where_kind(self, col):
        return (f" AND {col} = %s", [self.kind]) if self.kind != "all" else ("", [])


def resolve(fy=None, snap=None, oblast="", obshtina="", kind="all", fond=""):
    """The scope from the parameters of a request; nothing unknown is taken as it is. None when there is no data at all."""
    ys = years()
    if not ys:
        return None
    row = next((y for y in ys if fy is not None and y["fy"] == int(fy)), ys[0]) if str(fy or "").isdigit() or fy is None else ys[0]
    when = None
    given = False
    if snap:
        try:
            when = snap_for(row, dt.date.fromisoformat(str(snap)))
            given = when is not None
        except ValueError:
            when = None
    when = when or snap_for(row)
    ob = oblast if oblast in oblasti() else ""
    mu = next((r for r in refs() if r["id"] == obshtina), None)
    if mu and ob and mu["oblast"] != ob:
        mu = None                  # the municipality is not in the oblast chosen: the oblast wins, the municipality is dropped
    return Scope(row, when, given, ob, mu["id"] if mu else "", kind if kind in KINDS else "all", fond if fond in FUNDS else "")


# ---------- the headline numbers ----------

def totals(s):
    """By fund, for the scope (recipients by the ОБЩО rows of the file)."""
    ws, wa = s.where_places()
    wk, ka = s.where_kind("f.kind")
    return q(f"""SELECT coalesce(sum(f.recipients), 0) AS recipients, sum(f.efgz) AS efgz, sum(f.ezfrs) AS ezfrs, sum(f.nb) AS nb, sum(f.total) AS total
                 FROM pub_subsidii.municipality_fy f JOIN {MUN} m ON m.code = f.municipality_code
                 WHERE f.fy = %s AND f.snap_day = %s{ws}{wk}""", s.fy, s.snap, *wa, *ka, one=True)


def by_kind(s):
    ws, wa = s.where_places()
    return q(f"""SELECT f.kind, sum(f.recipients) AS recipients, sum(f.efgz) AS efgz, sum(f.ezfrs) AS ezfrs, sum(f.nb) AS nb, sum(f.total) AS total
                 FROM pub_subsidii.municipality_fy f JOIN {MUN} m ON m.code = f.municipality_code
                 WHERE f.fy = %s AND f.snap_day = %s{ws} GROUP BY f.kind ORDER BY sum(f.total) DESC NULLS LAST""", s.fy, s.snap, *wa)


def concentration(s):
    """The share of the money that the top 1 percent of the recipient ROWS (a name in a municipality) receive. Never people.
    Counted on the ingest side from every row, so it needs no row of a person below the threshold."""
    if s.obshtina:
        level, place = "municipality", next(r["code"] for r in refs() if r["id"] == s.obshtina)
    elif s.oblast:
        level, place = "oblast", oblast_code(s.oblast)
    else:
        level, place = "bg", "BG"
    row = q("""SELECT n, total, top_total, top_n FROM pub_subsidii.concentration
               WHERE fy = %s AND snap_day = %s AND place_level = %s AND place = %s AND kind = %s""", s.fy, s.snap, level, place, s.kind, one=True)
    if not row or not row["n"] or not row["total"]:
        return None
    return dict(row, share=Decimal(row["top_total"]) / Decimal(row["total"]))


def fund_value(row, fond):
    return row[fond] if fond else row["total"]


def fund_rows(s):
    """The three funds and the total, in euro and in the original, for the scope: one list for the chart and the table."""
    t = totals(s)
    out = []
    for k, name in FUNDS.items():
        out.append(dict(code=k, name=name, original=t[k], eur=s.eur(t[k])))
    out.append(dict(code="total", name="Общо", original=t["total"], eur=s.eur(t["total"])))
    return out, t


def year_series(s):
    """The funds for every financial year (at the newest snapshot of each), for the scope's places and kind: the chart on the dashboard."""
    out = []
    for y in reversed(years()):
        other = Scope(y, snap_for(y), False, s.oblast, s.obshtina, s.kind, s.fond)
        t = totals(other)
        out.append(dict(fy=y["fy"], label=y["label_bg"], snap=other.snap, currency=y["currency"], recipients=t["recipients"],
                        **{k: other.eur(t[k]) for k in FUNDS}, total=other.eur(t["total"]), original_total=t["total"]))
    return out


def scope_codes(fy, snap, s):
    """The set of measure codes of a year in the scope's places: whether two years have the same scope is whether these sets are equal."""
    ws, wa = s.where_places()
    rows = q(f"""SELECT DISTINCT ms.code FROM pub_subsidii.payment_agg a JOIN {MUN} m ON m.code = a.municipality_code JOIN pub_subsidii.measure ms USING (measure_id)
                 WHERE a.fy = %s AND a.snap_day = %s{ws}""", fy, snap, *wa)
    return {r["code"] for r in rows}


def compare(a, b):
    """Two scopes side by side (any two years or snapshots, same places and kind). The numbers are put next to each other
    only as comparable when the measures of both are the same; otherwise the list of what differs comes with them."""
    ta, tb = totals(a), totals(b)
    ca, cb = scope_codes(a.fy, a.snap, a), scope_codes(b.fy, b.snap, b)
    rows = []
    for k, name in [*FUNDS.items(), ("total", "Общо")]:
        va, vb = a.eur(ta[k]), b.eur(tb[k])
        rows.append(dict(code=k, name=name, a=va, b=vb, a_original=ta[k], b_original=tb[k],
                         change=None if not va or vb is None else (vb - va) / va))
    return dict(a=a, b=b, rows=rows, same_scope=ca == cb, only_a=sorted(ca - cb), only_b=sorted(cb - ca),
                same_currency=a.currency == b.currency and a.currency is not None, recipients=(ta["recipients"], tb["recipients"]))


# ---------- the inhabitants (Население's serving layer) ----------

def population_date(fy_row):
    """The date of the inhabitants used for a year: the newest on or before its end; else the first after it (said so).
    The monthly table of ГРАО by municipality, as Население publishes it."""
    ds = q("""SELECT as_of AS day FROM pub_naselenie.population WHERE table_kind = 'monthly' GROUP BY as_of
              HAVING count(DISTINCT municipality_code) >= 250 ORDER BY as_of""")
    before = [d["day"] for d in ds if d["day"] <= fy_row["ends"]]
    if before:
        return before[-1], False
    if ds:
        return ds[0]["day"], True
    return None, False


def residents(day):
    """{municipality code: people with a permanent address} on `day`."""
    return {r["municipality_code"]: r["permanent"] for r in q("""SELECT municipality_code, permanent FROM pub_naselenie.population
                                                                    WHERE table_kind = 'monthly' AND as_of = %s""", day)} if day else {}


# ---------- the map ----------

def mapdata(level="obshtini", fy=None, snap=None, fond="total", per="all"):
    level = level if level in NUTS else "obshtini"
    s = resolve(fy, snap)
    if s is None:
        return None
    fond = fond if fond in FUNDS else "total"
    per = per if per in ("all", "resident") else "all"
    n = NUTS[level]
    key = {4: "id", 3: "nuts3", 2: "nuts2", 1: "nuts1"}[n]
    rows = q(f"""SELECT m.{key} AS code, sum(f.efgz) AS efgz, sum(f.ezfrs) AS ezfrs, sum(f.nb) AS nb, sum(f.total) AS total, sum(f.recipients) AS recipients
                 FROM pub_subsidii.municipality_fy f JOIN {MUN} m ON m.code = f.municipality_code
                 WHERE f.fy = %s AND f.snap_day = %s GROUP BY 1""", s.fy, s.snap)
    have = {r["code"]: r for r in rows}
    pop_day, late = population_date(s.fy_row)
    pop = {}
    for code, persons in residents(pop_day).items():
        m = next((r for r in refs() if r["code"] == code), None)
        if m:
            p = pop.setdefault(m[key], dict(persons=0, n=0))
            p["persons"] += persons
            p["n"] += 1
    members = {}
    for r in refs():
        members[r[key]] = members.get(r[key], 0) + 1
    gn = geo_names()
    labels = {r["id"]: r["name_bg"] for r in refs()}
    items = []
    for code, nmembers in members.items():
        r = have.get(code)
        v = None
        if r is not None and r[fond] is not None:
            amount = s.eur(r[fond])
            if amount is not None:
                if per == "resident":
                    p = pop.get(code)
                    v = float(amount / p["persons"]) if p and p["n"] == nmembers and p["persons"] else None
                else:
                    v = float(amount)
        name = labels.get(code) if n == 4 else gn.get(code, code)
        items.append(dict(code=code, name=name or code, v=v, recipients=r["recipients"] if r else 0,
                          href=f"/obshtini/{code}?fy={s.fy}" if n == 4 else None))
    ranked = sorted((i for i in items if i["v"] is not None), key=lambda i: -i["v"])
    for k, i in enumerate(ranked, 1):
        i["rank"] = k
    items.sort(key=lambda i: (i["v"] is None, -(i["v"] or 0), i["name"]))
    shown = sum(1 for i in items if i["v"] is not None)
    unit = "€ на жител" if per == "resident" else "€"
    return dict(m=fond, l=level, o="bg", nuts=n, fy=s.fy, y=str(s.snap), years=[str(d) for d in s.fy_row["snaps"]], items=items, shown=shown, total=len(items),
                unit=unit, digits=2 if per == "resident" else 0, per=per, fond=fond, levels=LEVELS, countries={}, bg=None, base=None,
                title=("Изплатено на жител: " if per == "resident" else "Изплатено: ") + ("общо" if fond == "total" else FUNDS[fond]),
                plural=dict(LEVELS)[level], single={"obshtini": "Община", "oblasti": "Област", "rayoni": "Район", "makrorayoni": "Макрорайон"}[level],
                population_date=str(pop_day) if pop_day else None, population_late=late, currency=s.currency, fy_label=s.fy_row["label_bg"],
                dataset="ДФЗ, изплатени субсидии", url="/sources", csv="/export-karta.csv?" + _qs(dict(level=level, fy=s.fy, fond=fond, per=per)),
                filters=dict(fy=s.fy, fond=fond, per=per))


def _qs(p):
    from urllib.parse import urlencode
    return urlencode({k: v for k, v in p.items() if v not in (None, "")})


# ---------- the recipients ----------

def recipients(s, qtext="", sort="total", direction="desc", page=1, size=PAGE):
    """The recipient rows of the scope as the file had them on the snapshot day. A search is for legal entities only.
    The natural persons and sole traders at or below the threshold are not rows: `below` says how many there are."""
    ws, wa = s.where_places()
    wk, ka = s.where_kind("r.kind")
    extra, ea = "", []
    if s.fond:
        extra += f" AND r.{s.fond} > 0"
    text = names.norm(qtext) if qtext else ""
    if text:
        extra += " AND r.kind = 'legal' AND r.org_id IN (SELECT org_id FROM pub_subsidii.org WHERE position(%s in name_norm) > 0)"
        ea.append(text)
    order = SORTS.get(sort, SORTS["total"])
    direction = "ASC" if direction == "asc" else "DESC"
    page = max(1, int(page))
    base = f"""FROM pub_subsidii.recipient r LEFT JOIN {MUN} m ON m.code = r.municipality_code
               WHERE {SPAN}{ws}{wk}{extra}"""
    args = [s.fy, s.snap, s.snap, *wa, *ka, *ea]
    count = q(f"SELECT count(*) AS n, sum(r.total) AS total {base}", *args, one=True)
    sql = f"""SELECT r.holder_id, r.org_id, r.kind, {NAME_SHOWN} AS name_shown, {NAME_EXPORT} AS name_export,
                     (r.kind <> 'legal' AND r.person_masked IS NULL) AS name_expired, r.oblast_raw, r.obshtina_raw, m.id AS municipality_id,
                     m.name_bg AS municipality, m.oblast, r.efgz, r.ezfrs, r.nb, r.total {base}
              ORDER BY {order} {direction} NULLS LAST, r.holder_id"""
    if size:
        rows = q(sql + " LIMIT %s OFFSET %s", *args, size, (page - 1) * size)
    else:
        rows = q(sql, *args)
    for r in rows:
        for k in ("efgz", "ezfrs", "nb", "total"):
            r[k + "_eur"] = s.eur(r[k])
    below = None
    if s.kind != "legal" and not text:
        wb, ba = s.where_kind("t.kind")
        below = q(f"""SELECT coalesce(sum(t.recipients), 0) AS recipients, sum(t.total) AS total FROM pub_subsidii.below_threshold t
                      JOIN {MUN} m ON m.code = t.municipality_code WHERE t.fy = %s AND t.snap_day = %s{ws}{wb}""", s.fy, s.snap, *wa, *ba, one=True)
        below["total_eur"] = s.eur(below["total"])
        below["threshold_eur"] = s.fy_row["name_threshold_eur"]
    return dict(rows=rows, count=count["n"], total=count["total"], page=page, size=size, pages=max(1, -(-count["n"] // size)) if size else 1,
                sort=sort if sort in SORTS else "total", direction=direction.lower(), q=qtext, below=below)


def org(org_id):
    """A legal entity (by the normalized name, never by a company number): its rows in every year, at the newest snapshot of each."""
    o = q("SELECT org_id, name_bg, name_norm FROM pub_subsidii.org WHERE org_id = %s", org_id, one=True)
    if not o:
        return None
    out = []
    for y in reversed(years()):
        snap = snap_for(y)
        s = Scope(y, snap)
        rows = q(f"""SELECT r.holder_id, m.id AS municipality_id, m.name_bg AS municipality, m.oblast, r.efgz, r.ezfrs, r.nb, r.total, r.org_name AS name_shown
                     FROM pub_subsidii.recipient r JOIN {MUN} m ON m.code = r.municipality_code
                     WHERE r.org_id = %s AND {SPAN} ORDER BY r.total DESC NULLS LAST""", org_id, y["fy"], snap, snap)
        if not rows:
            continue
        tot = {k: sum((r[k] or ZERO for r in rows), ZERO) for k in ("efgz", "ezfrs", "nb", "total")}
        pays = q("""SELECT ms.code, ms.name, ms.objective, sum(p.payments) AS payments, sum(p.efgz) AS efgz, sum(p.ezfrs) AS ezfrs, sum(p.nb) AS nb
                    FROM pub_subsidii.org_measure p JOIN pub_subsidii.measure ms USING (measure_id)
                    WHERE p.org_id = %s AND p.fy = %s AND p.snap_day = %s
                    GROUP BY ms.code, ms.name, ms.objective ORDER BY coalesce(sum(p.efgz), 0) + coalesce(sum(p.ezfrs), 0) + coalesce(sum(p.nb), 0) DESC""",
                 org_id, y["fy"], snap)
        for r in [*rows, tot, *pays]:
            for k in ("efgz", "ezfrs", "nb", "total"):
                if k in r:
                    r[k + "_eur"] = s.eur(r[k])
        for p in pays:
            p["total"] = sum((p[k] or ZERO for k in ("efgz", "ezfrs", "nb")), ZERO)
            p["total_eur"] = s.eur(p["total"])
        out.append(dict(fy=y["fy"], label=y["label_bg"], snap=snap, currency=y["currency"], rows=rows, total=tot, payments=pays))
    return dict(o, years=out)


def search_orgs(text, limit=20):
    t = names.norm(text)
    if len(t) < 3:
        return []
    return q("""SELECT o.org_id, o.name_bg, count(DISTINCT r.municipality_code) AS municipalities
                FROM pub_subsidii.org o JOIN pub_subsidii.recipient r ON r.org_id = o.org_id
                WHERE position(%s in o.name_norm) > 0 GROUP BY o.org_id, o.name_bg ORDER BY o.name_bg LIMIT %s""", t, limit)


# ---------- measures ----------

def measures(s):
    """The payment rows of the scope by measure: the amounts of the PAYMENTS, which differ a little from the ОБЩО rows (said on the page)."""
    ws, wa = s.where_places()
    wk, ka = s.where_kind("a.kind")
    rows = q(f"""SELECT ms.measure_id, ms.code, ms.name, ms.objective, ms.family_bg, sum(a.payments) AS payments, sum(a.efgz) AS efgz, sum(a.ezfrs) AS ezfrs,
                        sum(a.nb) AS nb, sum(a.total) AS total
                 FROM pub_subsidii.payment_agg a JOIN {MUN} m ON m.code = a.municipality_code JOIN pub_subsidii.measure ms USING (measure_id)
                 WHERE a.fy = %s AND a.snap_day = %s{ws}{wk} GROUP BY ms.measure_id, ms.code, ms.name, ms.objective, ms.family_bg""", s.fy, s.snap, *wa, *ka)
    if s.fond:
        rows = [dict(r, total=r[s.fond]) for r in rows if r[s.fond]]
    rows.sort(key=lambda r: -(r["total"] or ZERO))
    for r in rows:
        for k in ("efgz", "ezfrs", "nb", "total"):
            r[k + "_eur"] = s.eur(r[k])
    return rows


# ---------- municipalities ----------

def municipalities(s):
    """Every municipality of the scope with the amounts of the year and, when the inhabitants are known, per inhabitant."""
    ws, wa = s.where_places()
    wk, ka = s.where_kind("f.kind")
    rows = q(f"""SELECT m.id, m.code, m.name_bg AS name, m.oblast, sum(f.recipients) AS recipients, sum(f.efgz) AS efgz, sum(f.ezfrs) AS ezfrs, sum(f.nb) AS nb, sum(f.total) AS total
                 FROM {MUN} m LEFT JOIN pub_subsidii.municipality_fy f ON f.municipality_code = m.code AND f.fy = %s AND f.snap_day = %s{wk}
                 WHERE true{ws} GROUP BY m.id, m.code, m.name_bg, m.oblast ORDER BY m.name_bg""", s.fy, s.snap, *ka, *wa)
    day, late = population_date(s.fy_row)
    pop = residents(day)
    for r in rows:
        r["has_data"] = r["total"] is not None
        for k in ("efgz", "ezfrs", "nb", "total"):
            r[k + "_eur"] = s.eur(r[k])
        r["persons"] = pop.get(r["code"])
        r["per_resident"] = (r["total_eur"] / r["persons"]).quantize(Decimal("0.01")) if r["total_eur"] is not None and r["persons"] else None
    return dict(rows=rows, population_date=day, population_late=late)


def municipality(mid, s):
    m = next((r for r in refs() if r["id"] == mid), None)
    if not m:
        return None
    series = []
    for y in reversed(years()):
        other = Scope(y, snap_for(y), False, "", mid, "all", "")
        t = totals(other)
        if t["total"] is None:
            continue
        kinds = q("""SELECT f.kind, f.recipients, f.total FROM pub_subsidii.municipality_fy f WHERE f.fy = %s AND f.snap_day = %s AND f.municipality_code = %s
                     ORDER BY f.total DESC NULLS LAST, f.kind""", y["fy"], other.snap, m["code"])
        series.append(dict(fy=y["fy"], label=y["label_bg"], snap=other.snap, currency=y["currency"], t=t, kinds={k["kind"]: k for k in kinds},
                           eur={k: other.eur(t[k]) for k in ("efgz", "ezfrs", "nb", "total")}))
    return dict(m=m, series=series)


def top_recipients(s, limit=10):
    """The biggest legal entities of the scope: firms only (a person is never in a ranking by name)."""
    ws, wa = s.where_places()
    rows = q(f"""SELECT r.org_id, o.name_bg, count(*) AS rows, sum(r.efgz) AS efgz, sum(r.ezfrs) AS ezfrs, sum(r.nb) AS nb, sum(r.total) AS total
                 FROM pub_subsidii.recipient r JOIN {MUN} m ON m.code = r.municipality_code JOIN pub_subsidii.org o ON o.org_id = r.org_id
                 WHERE {SPAN} AND r.kind = 'legal'{ws}
                 GROUP BY r.org_id, o.name_bg ORDER BY sum(r.total) DESC NULLS LAST LIMIT %s""", s.fy, s.snap, s.snap, *wa, limit)
    for r in rows:
        for k in ("efgz", "ezfrs", "nb", "total"):
            r[k + "_eur"] = s.eur(r[k])
    return rows


# ---------- the sources page ----------

def problems(now=None):
    """What is not well, from the published state of the source: the same things the shared freshness check tells.
    How old the last good read is, is counted at the moment of the request."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out = []
    for r in q("SELECT kind, name, title_bg, is_ok, since_at, limit_h, detail FROM pub_subsidii.health ORDER BY kind DESC, name"):
        late = r["limit_h"] is not None and (r["since_at"] is None or now - r["since_at"] > dt.timedelta(hours=r["limit_h"]))
        if r["kind"] == "source" and late:
            out.append(f"{r['title_bg']}: " + ("няма успешно четене" if r["since_at"] is None else f"последно успешно четене {r['since_at']:%d.%m.%Y %H:%M} UTC"))
        elif r["kind"] == "hold" and late:
            out.append(f"{r['title_bg']}: задържан от {r['since_at']:%d.%m.%Y} ({r['detail']})")
        elif r["kind"] in ("check", "run") and r["is_ok"] is False:
            out.append(f"{r['title_bg']}: {r['detail'] or 'грешка'}")
    return out


def sources():
    ds = q("SELECT * FROM pub.dataset WHERE dataset = %s", DATASET, one=True) or {}
    pop = q("""SELECT max(as_of) AS day FROM (SELECT as_of FROM pub_naselenie.population WHERE table_kind = 'monthly'
               GROUP BY as_of HAVING count(DISTINCT municipality_code) >= 250) x""", one=True)
    pop["municipalities"] = len(residents(pop["day"]))
    return dict(
        snapshots=q("SELECT * FROM pub_subsidii.snapshot ORDER BY fy DESC, day DESC"),
        years=q("SELECT fy, label_bg, currency, unit_evidence, in_form, gone_at, first_snap, last_snap, names_until, name_threshold_eur FROM pub_subsidii.fiscal_year ORDER BY fy DESC"),
        builds=q("SELECT fy, snap_day, finished, ok, rows, checks FROM pub_subsidii.build ORDER BY snap_day DESC, fy DESC LIMIT 12"),
        diffs=q("SELECT fy, day, line_no, fund, stated, summed, kind, name_export, obshtina FROM pub_subsidii.block_diff ORDER BY fy DESC, day DESC, line_no LIMIT 60"),
        diff_counts=q("SELECT fy, day, count(DISTINCT line_no) AS blocks FROM pub_subsidii.block_diff GROUP BY fy, day ORDER BY fy DESC, day DESC"),
        changes=q("SELECT detected_at, ref, field, old, new, cause FROM pub_subsidii.change ORDER BY change_id DESC LIMIT 20"),
        held=q("SELECT name AS ref, since_at AS first_seen, detail AS reason FROM pub_subsidii.health WHERE kind = 'hold' ORDER BY since_at"),
        unmatched=q("SELECT key, n, amount, reason FROM pub_subsidii.unmatched ORDER BY n DESC LIMIT 20"),
        dataset=dict(ds, id=ds.get("dataset"), distributed=bool(ds.get("is_distributable")), **DATASET_NOTES),
        population=pop,
    )


def csv_rows(rows, headers):
    return [{h: r.get(h) for h in headers} for r in rows]
