"""CLI for n8n and humans: python -m ingest.run --step <name> [--budget SECONDS]

Steps (lane):  migrate | eop, fx, normalize, derive, publish, build (all five) (build) |
               tr-seed, tr-read (reads, then name searches while idle), tr-changes, tr-names, tr-prune (tr) | enrich (enrich) |
               eop-check (validity check of the offers reader; exit 1 = repair needed), eop-offers (offers)
Prints one JSON line with the stats; exit code 1 on failure.
"""
import argparse
import datetime as dt
import json
import sys

from . import build, db, enrich, eop_offers, tr_worker

BUILD = {"eop": build.step_eop, "fx": build.step_fx, "normalize": build.step_normalize,
         "derive": build.step_derive, "publish": build.step_publish}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", required=True)
    ap.add_argument("--budget", type=int, default=3000, help="seconds, for tr-read / tr-names")
    ap.add_argument("--start")
    ap.add_argument("--end")
    a = ap.parse_args()
    out = {"step": a.step}
    try:
        if a.step == "migrate":
            with db.connect() as conn:
                out["applied"] = db.migrate(conn)
        elif a.step in BUILD:
            with db.job(a.step) as (conn, stats):
                if a.step == "eop":
                    build.step_eop(conn, stats, a.start, a.end)
                else:
                    BUILD[a.step](conn, stats)
                out.update(stats)
        elif a.step == "build":
            for name in ("eop", "fx", "normalize", "derive", "publish"):
                with db.job(name) as (conn, stats):
                    BUILD[name](conn, stats)
                    out[name] = stats
        elif a.step == "eop-check":  # own lane: it must run even while a long read holds the 'offers' lane
            with db.job(a.step, lane="offers-check") as (conn, stats):
                stats.update(eop_offers.check(conn))
                out.update(stats)
            if not stats["ok"]:
                raise RuntimeError("the offers reader failed its check: repair needed")
        elif a.step == "eop-offers":
            with db.job(a.step, lane="offers") as (conn, stats):
                stats["queued"] = eop_offers.enqueue(conn)
                eop_offers.work(conn, a.budget, stats)
                out.update(stats)
        elif a.step == "tr-seed":
            with db.job(a.step, lane="seed") as (conn, stats):
                stats["queued"] = tr_worker.seed_from_contracts(conn)
                out.update(stats)
        elif a.step == "tr-read":
            with db.job(a.step, lane="tr") as (conn, stats):
                tr_worker.work(conn, a.budget, stats)
                out.update(stats)
        elif a.step == "tr-prune":
            with db.job(a.step, lane="tr") as (conn, stats):
                stats.update(tr_worker.prune(conn))
                out.update(stats)
        elif a.step == "tr-changes":
            with db.job(a.step, lane="tr") as (conn, stats):
                today = dt.date.today()
                for pass_no, lag in ((1, 1), (2, 14)):
                    day = today - dt.timedelta(days=lag)
                    stats[f"pass{pass_no}_{day}"] = tr_worker.enqueue_changes(conn, day, pass_no)
                out.update(stats)
        elif a.step == "enrich":
            with db.job(a.step, lane="enrich") as (conn, stats):
                enrich.public_figure_candidates(conn, stats)
                enrich.commons_license(conn, stats)
                enrich.articles(conn, stats, budget_s=a.budget)
                out.update(stats)
        elif a.step == "tr-names":
            with db.job(a.step, lane="tr") as (conn, stats):
                tr_worker.search_names(conn, a.budget, stats)
                out.update(stats)
        else:
            ap.error(f"unknown step {a.step}")
        out["ok"] = True
    except db.Busy as e:
        out.update(ok=True, skipped=str(e))
    except Exception as e:  # noqa: BLE001 - reported to n8n as JSON, non-zero exit
        out.update(ok=False, error=repr(e)[:2000])
    print(json.dumps(out, default=str, ensure_ascii=False))
    sys.exit(0 if out["ok"] else 1)


if __name__ == "__main__":
    main()
