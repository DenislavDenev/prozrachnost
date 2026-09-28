"""CLI for n8n and people: python -m ingest.run --step <name>

  migrate                     apply db/migrations
  roster                      the current assembly's MPs (profile, group, constituency)
  sittings [--from YYYY-MM]   every sitting from that month (default: from the month before the last one we hold)
  recheck                     read again the sittings refused or without files; rebuild every assembly
  freshness                   what is late, held or broken

Prints one JSON line with the stats. Exit code 1 on a failure or when `problems` is not empty: that is what the n8n
card reports.
"""
import argparse
import datetime as dt
import json
import sys

from . import checks, db, load, stats

STEPS = ["migrate", "roster", "sittings", "recheck", "freshness"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", required=True, choices=STEPS)
    ap.add_argument("--from", dest="first", help="sittings: the first month, YYYY-MM")
    a = ap.parse_args()
    out = {"step": a.step}
    try:
        if a.step == "migrate":
            with db.connect() as conn:
                out["applied"] = db.migrate(conn)
        elif a.step == "roster":
            with db.job("roster") as (conn, st):
                load.roster(conn, st)
                if conn.execute("SELECT 1 FROM live.mp WHERE assembly = %s LIMIT 1", (st["assembly"],)).fetchone():
                    stats.rebuild(conn, [st["assembly"]])
                out.update(st)
        elif a.step == "sittings":
            first = None
            if a.first:
                d = dt.date.fromisoformat(a.first + "-01")
                first = (d.year, d.month)
            with db.job("sittings", {"from": a.first}) as (conn, st):
                load.load(conn, st, first=first)
                out.update(st)
        elif a.step == "recheck":
            with db.job("sittings", {"recheck": True}) as (conn, st):
                load.recheck(conn, st)
                out.update(st)
        elif a.step == "freshness":
            with db.connect(autocommit=True) as conn:
                out["problems"] = checks.freshness(conn)
        out["ok"] = not out.get("problems")
    except db.Busy as e:
        out.update(ok=True, skipped=str(e))
    except Exception as e:  # noqa: BLE001 - reported to n8n as JSON, non-zero exit
        out.update(ok=False, error=repr(e)[:2000])
    print(json.dumps(out, default=str, ensure_ascii=False))
    sys.exit(0 if out["ok"] else 1)


if __name__ == "__main__":
    main()
