"""CLI for n8n and people: python -m ingest.run --step <name>

  migrate                     apply db/migrations
  eurostat [--only a,b]       every indicator of db/ref/indicators.csv (or the named ones)
  bnb [--backfill]            the exchange rates of the last 31 days (or from 1991)
  freshness                   what is late, held or broken

Prints one JSON line with the stats. Exit code 1 on a failure or when `problems` is not empty: that is
what the n8n card reports.
"""
import argparse
import json
import sys

from . import bnb, checks, db, eurostat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", required=True, choices=["migrate", "eurostat", "bnb", "freshness"])
    ap.add_argument("--only", help="eurostat: comma-separated indicator ids")
    ap.add_argument("--backfill", action="store_true", help="bnb: from 1991, skipping quarters already read")
    a = ap.parse_args()
    out = {"step": a.step}
    try:
        if a.step == "migrate":
            with db.connect() as conn:
                out["applied"] = db.migrate(conn)
        elif a.step == "eurostat":
            with db.job("eurostat", {"only": a.only}) as (conn, stats):
                eurostat.load(conn, stats, only=a.only.split(",") if a.only else None)
                out.update(stats)
        elif a.step == "bnb":
            with db.job("bnb", {"backfill": a.backfill}) as (conn, stats):
                bnb.load(conn, stats, backfill=a.backfill)
                out.update(stats)
        elif a.step == "freshness":
            with db.connect(autocommit=True) as conn:
                out["problems"] = checks.freshness(conn, eurostat.indicators())
        out["ok"] = not out.get("problems")
    except db.Busy as e:
        out.update(ok=True, skipped=str(e))
    except Exception as e:  # noqa: BLE001 - reported to n8n as JSON, non-zero exit
        out.update(ok=False, error=repr(e)[:2000])
    print(json.dumps(out, default=str, ensure_ascii=False))
    sys.exit(0 if out["ok"] else 1)


if __name__ == "__main__":
    main()
