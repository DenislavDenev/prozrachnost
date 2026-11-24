"""The daily observations from the spans: the categories (the official list of КЗП), the dimensions, and per built
day the observations by category and place, the shops per municipality and the promotions (SQL in db/build/)."""
import csv
import datetime as dt
import time

import psycopg

from . import config

REF = config.ROOT / "db" / "ref"
BUILD = config.ROOT / "db" / "build"


def _sql(name):
    return (BUILD / name).read_text(encoding="utf-8")


def load_reference(c):
    """The 101 categories of КЗП with the groups of the portal, from db/ref/kzp_categories.csv (with the source of each)."""
    cats = [(int(r["code"]), r["name_bg"], r["group_bg"], r["source_url"], r["group_source_url"])
            for r in csv.DictReader((REF / "kzp_categories.csv").open(encoding="utf-8"))]
    with c.transaction(), c.cursor() as cur:
        cur.executemany("""INSERT INTO pazar.category (code, name_bg, group_bg, source_url, group_source_url) VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (code) DO UPDATE SET name_bg = EXCLUDED.name_bg, group_bg = EXCLUDED.group_bg,
                             source_url = EXCLUDED.source_url, group_source_url = EXCLUDED.group_source_url""", cats)
        cur.execute("DELETE FROM pazar.category WHERE NOT (code = ANY(%s))", ([x[0] for x in cats],))


def refresh_dimensions(c):
    with c.transaction(), psycopg.ClientCursor(c) as cur:
        cur.execute(_sql("10_dimensions.sql"))


def build_day(c, day):
    t0 = time.monotonic()
    ms = day.replace(day=1)
    pms = (day - dt.timedelta(days=7)).replace(day=1)
    p30 = (day - dt.timedelta(days=30)).replace(day=1)
    sha = c.execute("SELECT zip_sha256 FROM pazar.day WHERE day = %s AND status = 'built'", (day,)).fetchone()[0]
    c.execute("SELECT pazar.ensure_month(%s)", (day,))
    with c.transaction(), psycopg.ClientCursor(c) as cur:
        cur.execute("SET LOCAL work_mem = '256MB'")
        cur.execute(_sql("20_category_day.sql"), dict(d=day, ms=ms, pms=pms, p30=p30, rate=config.BGN_PER_EUR))
        rows = c.execute("SELECT count(*) FROM pazar.category_day WHERE day = %s", (day,)).fetchone()[0]
        secs = round(time.monotonic() - t0, 1)
        c.execute("""INSERT INTO pazar.aggregate_day (day, rows, secs, silver_sha256) VALUES (%s, %s, %s, %s)
                     ON CONFLICT (day) DO UPDATE SET rows = EXCLUDED.rows, secs = EXCLUDED.secs, silver_sha256 = EXCLUDED.silver_sha256, built_at = now()""",
                  (day, rows, secs, sha))
    return {"day": str(day), "rows": rows, "secs": secs}


def build_missing(c, first=None, last=None, rebuild=False, limit_secs=None):
    """The observations for every built day that has none, or whose spans were built from another file.
    Returns the report; `first` in it is the earliest day built, for the window of the publication."""
    t0 = time.monotonic()
    refresh_dimensions(c)
    q = """SELECT d.day FROM pazar.day d LEFT JOIN pazar.aggregate_day g USING (day)
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
        out.setdefault("first", str(day))
    # days that are no longer built (a held day that was built and is not now): their observations go
    gone = c.execute("""DELETE FROM pazar.aggregate_day g WHERE NOT EXISTS (SELECT 1 FROM pazar.day d WHERE d.day = g.day AND d.status = 'built')
                        RETURNING day""").fetchall()
    for (day,) in gone:
        for t in ("pazar.category_day", "pazar.municipality_day", "pazar.promo_day"):
            c.execute(f"DELETE FROM {t} WHERE day = %s", (day,))
        out["first"] = min(out.get("first", str(day)), str(day))
    return out
