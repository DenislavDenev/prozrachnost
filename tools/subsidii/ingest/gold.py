"""Gold from silver: the dimensions, the aggregates of one snapshot (SQL in db/gold/) and the checks (SQL in db/checks/).

A snapshot's gold is written in one transaction together with its checks: a violation rolls it all back, the earlier
snapshot stays what the pages show, and the build is recorded as not ok (the freshness check tells).
"""
import csv
import json
import time
from decimal import Decimal

import psycopg

from . import config, names

REF = config.ROOT / "db" / "ref"
GOLD = config.ROOT / "db" / "gold"
CHECKS = config.ROOT / "db" / "checks"
SOURCE_URL = "https://seu.dfz.bg/seu/f?p=727:8110:::NO"


class ChecksFailed(Exception):
    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = problems


def registry():
    return json.loads((REF / "registry.json").read_text(encoding="utf-8"))


def load_reference(c):
    """The list of municipalities, the funds and the registry of the datasets and indicators."""
    reg = registry()
    with c.transaction():
        c.execute("DELETE FROM gold.indicator")
        c.execute("DELETE FROM gold.dataset")
        c.execute("DELETE FROM gold.fund")
        for f in reg["funds"]:
            c.execute("INSERT INTO gold.fund VALUES (%s, %s, %s, %s, %s)", (f["code"], f["name_bg"], f["short_bg"], f["source_column"], f["ord"]))
        for d in reg["datasets"]:
            c.execute("INSERT INTO gold.dataset VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                      (d["id"], d["title_bg"], d["description_bg"], d["source_org"], d["source_url"], d["channel"], d["why_not_higher"], d["licence"],
                       d["licence_note"], d["distributed"], d["cadence"], d["personal_data"]))
        for i in reg["indicators"]:
            c.execute("INSERT INTO gold.indicator VALUES (%s, %s, %s, %s, %s, %s)", (i["code"], i["title_bg"], i["unit"], i["kind"], i["definition_bg"], i["dataset"]))
        for r in csv.DictReader((REF / "municipality.csv").open(encoding="utf-8-sig")):
            c.execute("""INSERT INTO gold.municipality VALUES (%s, %s, %s, %s, %s, %s)
                         ON CONFLICT (id) DO UPDATE SET name_bg = EXCLUDED.name_bg, oblast = EXCLUDED.oblast, nuts3 = EXCLUDED.nuts3, nuts2 = EXCLUDED.nuts2, nuts1 = EXCLUDED.nuts1""",
                      (r["id"], r["name_bg"], r["oblast"], r["nuts3"], r["nuts2"], r["nuts1"]))


def family(code):
    """The group of a measure by its code: 'I.4' is group I; no code is its own group. The source gives no names for the groups."""
    if code in ("-", ""):
        return "-", "без код"
    head = code.split(".")[0]
    return head, "код " + head


def sync_dimensions(c):
    """Fiscal years, measures, recipients (kind, search key, municipality). Idempotent; only what is new is computed,
    unless the rules of ingest/names.py changed."""
    c.execute("""INSERT INTO gold.fiscal_year (fy, starts, ends, label_bg, currency, eur_per_unit, unit_evidence, in_form, gone_at, first_snap, last_snap, names_until)
                 SELECT y.fy, y.starts, y.ends, 'Финансова година ' || y.fy || ' (' || to_char(y.starts, 'DD.MM.YYYY') || ' - ' || to_char(y.ends, 'DD.MM.YYYY') || ')',
                        y.currency, CASE y.currency WHEN 'BGN' THEN round(1 / %s::numeric, 12) WHEN 'EUR' THEN 1 END, y.unit_evidence, y.in_form, y.gone_at,
                        (SELECT min(day) FROM silver.snapshot s WHERE s.fy = y.fy AND s.status = 'built'),
                        (SELECT max(day) FROM silver.snapshot s WHERE s.fy = y.fy AND s.status = 'built'),
                        y.ends + make_interval(years => gold.name_ttl_years())
                 FROM silver.fiscal_year y
                 ON CONFLICT (fy) DO UPDATE SET currency = EXCLUDED.currency, eur_per_unit = EXCLUDED.eur_per_unit, unit_evidence = EXCLUDED.unit_evidence,
                    in_form = EXCLUDED.in_form, gone_at = EXCLUDED.gone_at, first_snap = EXCLUDED.first_snap, last_snap = EXCLUDED.last_snap,
                    names_until = EXCLUDED.names_until, label_bg = EXCLUDED.label_bg""", (config.BGN_PER_EUR,))
    for mid, code, name, obj in c.execute("SELECT id, code, name, objective FROM silver.measure WHERE id NOT IN (SELECT measure_id FROM gold.measure)").fetchall():
        fam, fam_bg = family(code)
        c.execute("INSERT INTO gold.measure VALUES (%s, %s, %s, %s, %s, %s)", (mid, code, name, obj, fam, fam_bg))
    rules = c.execute("SELECT value FROM gold.meta WHERE key = 'names_rules'").fetchone()
    if not rules or rules[0] != str(names.RULES):
        c.execute("DELETE FROM gold.beneficiary")
        c.execute("DELETE FROM gold.org")
        c.execute("INSERT INTO gold.meta VALUES ('names_rules', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (str(names.RULES),))
    places = names.load_places()
    todo = c.execute("""SELECT id, name, surname, oblast, obshtina FROM silver.beneficiary
                        WHERE id NOT IN (SELECT beneficiary_id FROM gold.beneficiary) ORDER BY id""").fetchall()
    rows, orgs, matches = [], {}, {}
    for bid, name, surname, oblast, obshtina in todo:
        kind = names.kind_of(name, surname)
        nn = names.norm(name) if kind == "legal" else None
        oid = names.org_id(nn) if nn else None
        m = names.match_place(oblast, obshtina, places)
        if m:
            matches[(oblast, obshtina)] = m
        if oid and oid not in orgs:
            orgs[oid] = (nn, name.strip())
        rows.append((bid, kind, nn, oid, m[0] if m else None))
    with c.cursor() as cur:
        for oid, (nn, nm) in orgs.items():
            cur.execute("INSERT INTO gold.org VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (oid, nn, nm))
        cur.executemany("INSERT INTO gold.beneficiary (beneficiary_id, kind, name_norm, org_id, municipality_id) VALUES (%s, %s, %s, %s, %s)", rows)
        cur.executemany("INSERT INTO gold.place_match VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        [(o, b, m[0], m[1], m[2]) for (o, b), m in matches.items()])
    return {"beneficiaries": len(rows), "orgs": len(orgs)}


def _sql(name):
    return (GOLD / name).read_text(encoding="utf-8")


def run_checks(c, fy, snap):
    """Every file of db/checks/ returns the violations; the result is a list of messages."""
    problems = []
    for f in sorted(CHECKS.glob("*.sql")):
        with psycopg.ClientCursor(c) as cur:
            cur.execute(f.read_text(encoding="utf-8"), dict(fy=fy, snap=snap))
            problems += [r[0] for r in cur.fetchall()]
    return problems


def drop_year(c, fy):
    for t in ("gold.observation", ):
        c.execute(f"DELETE FROM {t} WHERE period_start = (SELECT starts FROM silver.fiscal_year WHERE fy = %s)", (fy,))
    for t in ("gold.payment_agg", "gold.municipality_fy", "gold.summary", "gold.build"):
        c.execute(f"DELETE FROM {t} WHERE fy = %s", (fy,))


def build_snapshot(c, fy, snap):
    t0 = time.monotonic()
    sha = c.execute("SELECT sha256 FROM silver.snapshot WHERE fy = %s AND day = %s AND status = 'built'", (fy, snap)).fetchone()[0]
    starts = c.execute("SELECT starts FROM silver.fiscal_year WHERE fy = %s", (fy,)).fetchone()[0]
    with c.transaction():
        dims = sync_dimensions(c)
    code_sha = _code_sha()
    try:
        with c.transaction():
            build = c.execute("""INSERT INTO gold.build (fy, snap_day, code_sha) VALUES (%s, %s, %s)
                                 ON CONFLICT (fy, snap_day) DO UPDATE SET started = now(), finished = NULL, ok = NULL, checks = NULL, code_sha = EXCLUDED.code_sha
                                 RETURNING build_id""", (fy, snap, code_sha)).fetchone()[0]
            with psycopg.ClientCursor(c) as cur:
                cur.execute("SET LOCAL work_mem = '256MB'")
                cur.execute(_sql("10_snapshot.sql"), dict(fy=fy, snap=snap, starts=starts, build=build, sha=sha))
            problems = run_checks(c, fy, snap)
            if problems:
                raise ChecksFailed(problems)
            counts = {r[0]: r[1] for r in c.execute(
                "SELECT 'summary', count(*) FROM gold.summary WHERE fy = %s AND snap_day = %s UNION ALL SELECT 'municipality_fy', count(*) FROM gold.municipality_fy WHERE fy = %s AND snap_day = %s "
                "UNION ALL SELECT 'payment_agg', count(*) FROM gold.payment_agg WHERE fy = %s AND snap_day = %s UNION ALL SELECT 'observation', count(*) FROM gold.observation WHERE published_at = %s AND period_start = %s",
                (fy, snap, fy, snap, fy, snap, snap, starts))}
            c.execute("UPDATE gold.build SET finished = now(), ok = true, rows = %s, checks = %s WHERE build_id = %s", (json.dumps(counts), json.dumps([]), build))
    except ChecksFailed as e:
        c.execute("""INSERT INTO gold.build (fy, snap_day, finished, ok, checks, code_sha) VALUES (%s, %s, now(), false, %s, %s)
                     ON CONFLICT (fy, snap_day) DO UPDATE SET finished = now(), ok = false, checks = EXCLUDED.checks, code_sha = EXCLUDED.code_sha""",
                  (fy, snap, json.dumps(e.problems, ensure_ascii=False), code_sha))
        raise
    return dict(fy=fy, day=str(snap), secs=round(time.monotonic() - t0, 1), dims=dims, rows=counts)


def _code_sha():
    f = config.ROOT / ".sha"
    return f.read_text().strip()[:12] if f.exists() else None


def build_missing(c, only=None, limit_secs=None, rebuild=False):
    """Gold for every built snapshot that has none, or whose build was not ok, or was made from another file."""
    t0 = time.monotonic()
    q = """SELECT s.fy, s.day FROM silver.snapshot s LEFT JOIN gold.build b ON b.fy = s.fy AND b.snap_day = s.day
           WHERE s.status = 'built' AND (b.build_id IS NULL OR b.ok IS NOT TRUE OR %s) ORDER BY s.fy, s.day"""
    out = {"built": 0, "problems": [], "snapshots": []}
    for fy, day in c.execute(q, (rebuild,)).fetchall():
        if only and fy not in only:
            continue
        if limit_secs and time.monotonic() - t0 > limit_secs:
            out["stopped"] = f"бюджетът от време свърши при {fy}/{day}"
            break
        try:
            out["snapshots"].append(build_snapshot(c, fy, day))
            out["built"] += 1
        except ChecksFailed as e:
            out["problems"] += [f"злато {fy}/{day}: {p}" for p in e.problems]
    return out


def latest_ok(c, fy=None):
    """The newest snapshot with a good gold build, per year (or one year)."""
    rows = c.execute("""SELECT b.fy, max(b.snap_day) FROM gold.build b WHERE b.ok GROUP BY b.fy ORDER BY b.fy""").fetchall()
    return {r[0]: r[1] for r in rows if fy is None or r[0] == fy}
