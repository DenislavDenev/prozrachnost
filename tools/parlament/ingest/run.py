"""CLI for n8n and people: python -m ingest.run --step <name>

  migrate                     apply db/migrations; the assemblies from db/ref/assemblies.csv
  roster                      the current assembly's MPs (profile, group, constituency)
  sittings [--from YYYY-MM]   every sitting from that month (default: from the month before the last one we hold);
                              its stenogram, video and votes (from 1879: --from 1879-02)
  recheck                     read again the sittings refused or without readable files; rebuild every assembly
  people [--all]              the assemblies of the API (39th-), their bodies and every MP's profile (read again
                              only the current assembly's, unless --all); the same person across assemblies
  absences                    the official absences and penalties the Assembly shows now, kept
  pdfs [--limit N]            archive the scanned stenograms (before 1992) not archived yet
  bills [--from YYYY-MM]      the bills brought in since that month (default: the last two), the open ones of the
                              current assembly not read for a week, and their votes (from 2001: --from 2001-07)
  freshness                   what is late, held or broken

Prints one JSON line with the stats. Exit code 1 on a failure or when `problems` is not empty: that is what the n8n
card reports.
"""
import argparse
import datetime as dt
import json
import sys

from . import bills, checks, db, load, people, stats

STEPS = ["migrate", "roster", "sittings", "recheck", "people", "absences", "pdfs", "bills", "freshness"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", required=True, choices=STEPS)
    ap.add_argument("--from", dest="first", help="sittings: the first month, YYYY-MM")
    ap.add_argument("--all", action="store_true", help="people: read every profile again")
    ap.add_argument("--limit", type=int, default=0, help="pdfs: at most this many")
    a = ap.parse_args()
    out = {"step": a.step}
    try:
        if a.step == "migrate":
            with db.connect() as conn:
                out["applied"] = db.migrate(conn)
                with conn.transaction():
                    out["assemblies"] = people.assemblies(conn)
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
                # the bills of the last two months get their votes (a sitting's files come after the bill's step)
                st["bill_votes"] = bills.link(conn, [b for b, in conn.execute(
                    "SELECT DISTINCT bill FROM live.bill_step WHERE date >= current_date - 60")])
                out.update(st)
        elif a.step == "recheck":
            with db.job("sittings", {"recheck": True}) as (conn, st):
                load.recheck(conn, st)
                out.update(st)
        elif a.step == "people":
            with db.job("people", {"all": a.all}) as (conn, st):
                people.people(conn, st, everyone=a.all)
                out.update(st)
        elif a.step == "absences":
            with db.job("absences") as (conn, st):
                people.absences(conn, st)
                out.update(st)
        elif a.step == "bills":
            first = None
            if a.first:
                d = dt.date.fromisoformat(a.first + "-01")
                first = (d.year, d.month)
            with db.job("bills", {"from": a.first}) as (conn, st):
                bills.load(conn, st, first=first)
                out.update(st)
        elif a.step == "pdfs":
            with db.job("pdfs", {"limit": a.limit}) as (conn, st):
                load.pdfs(conn, st, limit=a.limit)
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
