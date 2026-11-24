"""The clean tables from the landing zone: the dimensions, the aggregates of one snapshot (SQL in db/gold/) and the
checks of a snapshot (SQL in db/checks/).

A snapshot's aggregates are written in one transaction together with its checks: a violation rolls it all back, the
earlier snapshot stays what is published, and the build is recorded as not ok (the freshness check tells).
"""
import json
import time

import psycopg

from . import config, names, pipeline

GOLD = config.ROOT / "db" / "gold"
CHECKS = config.ROOT / "db" / "checks"
SOURCE_URL = "https://seu.dfz.bg/seu/f?p=727:8110:::NO"


class ChecksFailed(Exception):
    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = problems


def family(code):
    """The group of a measure by its code: 'I.4' is group I; no code is its own group. The source gives no names for the groups."""
    if code in ("-", ""):
        return "-", "без код"
    head = code.split(".")[0]
    return head, "код " + head


def sync_dimensions(c):
    """Fiscal years, measures, recipients (kind, search key, municipality). Idempotent; only what is new is computed,
    unless the rules of ingest/names.py changed."""
    c.execute("""INSERT INTO subsidii.fiscal_year (fy, starts, ends, label_bg, currency, eur_per_unit, unit_evidence, in_form, gone_at, first_snap, last_snap, names_until)
                 SELECT y.fy, y.starts, y.ends, 'Финансова година ' || y.fy || ' (' || to_char(y.starts, 'DD.MM.YYYY') || ' - ' || to_char(y.ends, 'DD.MM.YYYY') || ')',
                        y.currency, CASE y.currency WHEN 'BGN' THEN round(1 / %s::numeric, 12) WHEN 'EUR' THEN 1 END, y.unit_evidence, y.in_form, y.gone_at,
                        (SELECT min(day) FROM subsidii.snapshot s WHERE s.fy = y.fy AND s.status = 'built'),
                        (SELECT max(day) FROM subsidii.snapshot s WHERE s.fy = y.fy AND s.status = 'built'),
                        y.ends + make_interval(years => subsidii.name_ttl_years())
                 FROM subsidii.source_year y
                 ON CONFLICT (fy) DO UPDATE SET currency = EXCLUDED.currency, eur_per_unit = EXCLUDED.eur_per_unit, unit_evidence = EXCLUDED.unit_evidence,
                    in_form = EXCLUDED.in_form, gone_at = EXCLUDED.gone_at, first_snap = EXCLUDED.first_snap, last_snap = EXCLUDED.last_snap,
                    names_until = EXCLUDED.names_until, label_bg = EXCLUDED.label_bg""", (config.BGN_PER_EUR,))
    for mid, code, name, obj in c.execute("SELECT measure_id, code, name, objective FROM subsidii_src.measure WHERE measure_id NOT IN (SELECT measure_id FROM subsidii.measure)").fetchall():
        fam, fam_bg = family(code)
        c.execute("INSERT INTO subsidii.measure VALUES (%s, %s, %s, %s, %s, %s)", (mid, code, name, obj, fam, fam_bg))
    rules = c.execute("SELECT value FROM subsidii.meta WHERE key = 'names_rules'").fetchone()
    if not rules or rules[0] != str(names.RULES):
        c.execute("DELETE FROM subsidii.beneficiary")
        c.execute("DELETE FROM subsidii.org WHERE org_id NOT IN (SELECT org_id FROM subsidii.beneficiary WHERE org_id IS NOT NULL)")
        c.execute("INSERT INTO subsidii.meta VALUES ('names_rules', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (str(names.RULES),))
    places = names.load_places(c)
    todo = c.execute("""SELECT beneficiary_id, name, surname, oblast, obshtina FROM subsidii_src.beneficiary
                        WHERE beneficiary_id NOT IN (SELECT beneficiary_id FROM subsidii.beneficiary) ORDER BY beneficiary_id""").fetchall()
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
        cur.executemany("INSERT INTO subsidii.org VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", [(oid, nn, nm) for oid, (nn, nm) in orgs.items()])
        cur.executemany("INSERT INTO subsidii.beneficiary (beneficiary_id, kind, name_norm, org_id, municipality_code) VALUES (%s, %s, %s, %s, %s)", rows)
        cur.executemany("INSERT INTO subsidii.place_match VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        [(o, b, m[0], m[1], m[2]) for (o, b), m in matches.items()])
    return {"beneficiaries": len(rows), "orgs": len(orgs)}


def _sql(name):
    return (GOLD / name).read_text(encoding="utf-8")


def run_checks(c, fy, snap):
    """Every file of db/checks/ returns the violations of one snapshot; the result is a list of messages."""
    problems = []
    for f in sorted(CHECKS.glob("*.sql")):
        with psycopg.ClientCursor(c) as cur:
            cur.execute(f.read_text(encoding="utf-8"), dict(fy=fy, snap=snap))
            problems += [r[0] for r in cur.fetchall()]
    return problems


def drop_year(c, fy):
    c.execute("DELETE FROM subsidii.observation WHERE period_start = (SELECT starts FROM subsidii.source_year WHERE fy = %s)", (fy,))
    for t in ("subsidii.payment_agg", "subsidii.municipality_fy", "subsidii.summary", "subsidii.build"):
        c.execute(f"DELETE FROM {t} WHERE fy = %s", (fy,))


def build_snapshot(c, fy, snap):
    t0 = time.monotonic()
    sha = c.execute("SELECT sha256 FROM subsidii.snapshot WHERE fy = %s AND day = %s AND status = 'built'", (fy, snap)).fetchone()[0]
    starts = c.execute("SELECT starts FROM subsidii.source_year WHERE fy = %s", (fy,)).fetchone()[0]
    with c.transaction():
        dims = sync_dimensions(c)
    code_sha = (pipeline.code_sha(config.ROOT) or "")[:12] or None
    try:
        with c.transaction():
            build = c.execute("""INSERT INTO subsidii.build (fy, snap_day, code_sha) VALUES (%s, %s, %s)
                                 ON CONFLICT (fy, snap_day) DO UPDATE SET started = now(), finished = NULL, ok = NULL, checks = NULL, code_sha = EXCLUDED.code_sha
                                 RETURNING build_id""", (fy, snap, code_sha)).fetchone()[0]
            with psycopg.ClientCursor(c) as cur:
                cur.execute("SET LOCAL work_mem = '256MB'")
                cur.execute(_sql("10_snapshot.sql"), dict(fy=fy, snap=snap, starts=starts, build=build, sha=sha))
            problems = run_checks(c, fy, snap)
            if problems:
                raise ChecksFailed(problems)
            counts = {r[0]: r[1] for r in c.execute(
                "SELECT 'summary', count(*) FROM subsidii.summary WHERE fy = %s AND snap_day = %s UNION ALL SELECT 'municipality_fy', count(*) FROM subsidii.municipality_fy WHERE fy = %s AND snap_day = %s "
                "UNION ALL SELECT 'payment_agg', count(*) FROM subsidii.payment_agg WHERE fy = %s AND snap_day = %s UNION ALL SELECT 'observation', count(*) FROM subsidii.observation WHERE published_at = %s AND period_start = %s",
                (fy, snap, fy, snap, fy, snap, snap, starts))}
            c.execute("UPDATE subsidii.build SET finished = now(), ok = true, rows = %s, checks = %s WHERE build_id = %s", (json.dumps(counts), json.dumps([]), build))
    except ChecksFailed as e:
        c.execute("""INSERT INTO subsidii.build (fy, snap_day, finished, ok, checks, code_sha) VALUES (%s, %s, now(), false, %s, %s)
                     ON CONFLICT (fy, snap_day) DO UPDATE SET finished = now(), ok = false, checks = EXCLUDED.checks, code_sha = EXCLUDED.code_sha""",
                  (fy, snap, json.dumps(e.problems, ensure_ascii=False), code_sha))
        raise
    return dict(fy=fy, day=str(snap), secs=round(time.monotonic() - t0, 1), dims=dims, rows=counts)


def build_missing(c, only=None, limit_secs=None, rebuild=False):
    """The aggregates of every written snapshot that has none, or whose build was not ok."""
    t0 = time.monotonic()
    q = """SELECT s.fy, s.day FROM subsidii.snapshot s LEFT JOIN subsidii.build b ON b.fy = s.fy AND b.snap_day = s.day
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
            out["problems"] += [f"изграждане {fy}/{day}: {p}" for p in e.problems]
    return out


def latest_ok(c, fy=None):
    """The newest snapshot with a good build, per year (or one year)."""
    rows = c.execute("""SELECT b.fy, max(b.snap_day) FROM subsidii.build b WHERE b.ok GROUP BY b.fy ORDER BY b.fy""").fetchall()
    return {r[0]: r[1] for r in rows if fy is None or r[0] == fy}
