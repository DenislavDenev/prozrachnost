"""Gold from silver: the reference tables, the dimensions and the daily observations (SQL in db/gold/)."""
import csv
import datetime as dt
import time

import psycopg

from . import config

REF = config.ROOT / "db" / "ref"
GOLD = config.ROOT / "db" / "gold"


def _sql(name):
    return (GOLD / name).read_text(encoding="utf-8")


def load_reference(c):
    """Categories (the official list of КЗП), municipalities and places (ЕКАТТЕ with the municipality id)."""
    with c.transaction():
        for t in ("gold.place", "gold.municipality", "gold.category"):
            c.execute(f"DELETE FROM {t}")
        cats = [(int(r["code"]), r["name_bg"], r["group_bg"], r["source_url"], r["group_source_url"])
                for r in csv.DictReader((REF / "kzp_categories.csv").open(encoding="utf-8"))]
        c.cursor().executemany("INSERT INTO gold.category VALUES (%s, %s, %s, %s, %s)", cats)
        muni = [(r["id"], r["name_bg"], r["oblast"], r["nuts3"], r["nuts2"], r["nuts1"])
                for r in csv.DictReader((REF / "municipality.csv").open(encoding="utf-8-sig"))]
        c.cursor().executemany("INSERT INTO gold.municipality VALUES (%s, %s, %s, %s, %s, %s)", muni)
        places = [(r["ekatte"], r["name"], r["kind"], r["obshtina"], r["municipality_id"], r["oblast"], r["nuts3"])
                  for r in csv.DictReader((REF / "ekatte_municipality.csv").open(encoding="utf-8"))]
        c.cursor().executemany("INSERT INTO gold.place VALUES (%s, %s, %s, %s, %s, %s, %s)", places)


def refresh_dimensions(c):
    with c.transaction(), psycopg.ClientCursor(c) as cur:
        cur.execute(_sql("10_dimensions.sql"))


def build_day(c, day):
    t0 = time.monotonic()
    ms = day.replace(day=1)
    pms = (day - dt.timedelta(days=7)).replace(day=1)
    sha = c.execute("SELECT zip_sha256 FROM silver.day WHERE day = %s AND status = 'built'", (day,)).fetchone()[0]
    c.execute("SELECT gold.ensure_month(%s)", (day,))
    with c.transaction(), psycopg.ClientCursor(c) as cur:
        cur.execute("SET LOCAL work_mem = '256MB'")
        cur.execute(_sql("20_category_day.sql"), dict(d=day, ms=ms, pms=pms, rate=config.BGN_PER_EUR))
        rows = c.execute("SELECT count(*) FROM gold.category_day WHERE day = %s", (day,)).fetchone()[0]
        secs = round(time.monotonic() - t0, 1)
        c.execute("""INSERT INTO gold.day (day, rows, secs, silver_sha256) VALUES (%s, %s, %s, %s)
                     ON CONFLICT (day) DO UPDATE SET rows = EXCLUDED.rows, secs = EXCLUDED.secs, silver_sha256 = EXCLUDED.silver_sha256, built_at = now()""",
                  (day, rows, secs, sha))
    return {"day": str(day), "rows": rows, "secs": secs}


def build_missing(c, first=None, last=None, rebuild=False, limit_secs=None):
    """Gold for every built day that has none, or whose silver was built from another file."""
    t0 = time.monotonic()
    refresh_dimensions(c)
    q = """SELECT d.day FROM silver.day d LEFT JOIN gold.day g USING (day)
           WHERE d.status = 'built' AND (g.day IS NULL OR g.silver_sha256 <> d.zip_sha256 OR %s)
             AND (%s::date IS NULL OR d.day >= %s::date) AND (%s::date IS NULL OR d.day <= %s::date) ORDER BY d.day"""
    days = [r[0] for r in c.execute(q, (rebuild, first, first, last, last))]
    out = {"days": 0, "rows": 0}
    for day in days:
        if limit_secs and time.monotonic() - t0 > limit_secs:
            out["stopped"] = f"бюджетът от време свърши при {day}"
            break
        r = build_day(c, day)
        out["days"] += 1
        out["rows"] += r["rows"]
    return out
