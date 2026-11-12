"""CLI for n8n and people: python -m ingest.run --step <name>

  migrate                     apply db/migrations (the foundation of danni must be there); the assemblies from db/ref/assemblies.csv
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
  build                       what is built (groups, lines, the MPs' statistics, the links) again for every assembly
  replay                      the move: the database of its own (until 13.11.2026), from one snapshot, then the builds
  parity                      the old database against the clean tables and the serving layer, table by table
  check                       every quality rule, then the publication
  freshness                   what is late, held or broken

Ingest and serving are apart. Every step that writes ends with the quality rules and, when they hold, a publication
of the serving layer pub_parlament (db/publish/*.sql), which is what the pages read. The publication compares the
whole layer, so it waits for the heavy lock of CT 118.

Prints one JSON line with the stats. Exit code 1 on a failure or when `problems` is not empty: that is what the n8n
card reports.
"""
import argparse
import datetime as dt
import json
import sys

from . import bills, checks, db, load, move, people, pipeline, stats
from .config import ROOT, TOOL

STEPS = ["migrate", "roster", "sittings", "recheck", "people", "absences", "pdfs", "bills", "build", "replay", "parity",
         "check", "freshness"]


def month(text):
    if not text:
        return None
    d = dt.date.fromisoformat(text + "-01")
    return d.year, d.month


def publish(conn):
    """The quality rules; when every gating one holds, the serving layer follows. -> problems."""
    r = db.current()
    failed = pipeline.run_checks(conn, r)
    if failed:
        return failed
    with db.heavy():
        return pipeline.publish(conn, r, ROOT).problems


def step(name, a, conn, st):
    """One step that writes, inside its run. -> its problems."""
    if name == "roster":
        load.roster(conn, st)
        if conn.execute("SELECT 1 FROM parlament.mp WHERE assembly = %s LIMIT 1", (st["assembly"],)).fetchone():
            stats.rebuild(conn, [st["assembly"]])
    elif name == "sittings":
        load.load(conn, st, first=month(a.first))
        # the bills of the last two months get their votes (a sitting's files come after the bill's step)
        st["bill_votes"] = bills.link(conn, [b for b, in conn.execute(
            "SELECT DISTINCT bill FROM parlament.bill_step WHERE date >= current_date - 60")])
    elif name == "recheck":
        load.recheck(conn, st)
    elif name == "people":
        people.people(conn, st, everyone=a.all)
    elif name == "absences":
        people.absences(conn, st)
    elif name == "bills":
        bills.load(conn, st, first=month(a.first))
    elif name == "pdfs":
        load.pdfs(conn, st, limit=a.limit)
    elif name == "build":
        move.build(conn, db.current())
    elif name == "replay":
        move.replay(conn, db.current())
    elif name == "parity":
        move.parity(conn, db.current())
        return [f"сверката: {x}" for x in st["detail"]]
    problems = list(st.pop("problems", []))
    return problems + publish(conn)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", required=True, choices=STEPS)
    ap.add_argument("--from", dest="first", help="sittings, bills: the first month, YYYY-MM")
    ap.add_argument("--all", action="store_true", help="people: read every profile again")
    ap.add_argument("--limit", type=int, default=0, help="pdfs: at most this many")
    a = ap.parse_args(argv)
    out = {"step": a.step}
    try:
        if a.step == "migrate":
            with db.connect() as conn:
                out["applied"] = db.migrate(conn)
                with conn.transaction():
                    out["assemblies"] = people.assemblies(conn)
        elif a.step == "freshness":
            with db.connect() as conn:
                out["problems"] = checks.freshness(conn) + pipeline.freshness(conn, TOOL)
        else:
            # recheck is a read of the sittings: one of the two at a time
            name = "sittings" if a.step == "recheck" else a.step
            inputs = {"from": a.first} if a.step in ("sittings", "bills") else {"all": a.all} if a.step == "people" else \
                {"limit": a.limit} if a.step == "pdfs" else {"recheck": True} if a.step == "recheck" else {}
            with db.job(name, inputs) as (conn, st):
                st["problems"] = step(a.step, a, conn, st)   # kept in ops.run with the stats
                out.update(st)
        out["ok"] = not out.get("problems")
    except db.Busy as e:
        out.update(ok=True, skipped=str(e))
    except Exception as e:  # noqa: BLE001 - reported to n8n as JSON, non-zero exit
        out.update(ok=False, error=repr(e)[:2000])
    print(json.dumps(out, default=str, ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
