"""python -m ingest.run --step <step>

migrate      the schema
build        every archived day that is not built yet (or --from/--to); refuses an archive older than 30 hours
rebuild      one month again from the archive (--month YYYY-MM)
gold         the gold tables for the built days that have none (or --from/--to), and the dimensions
fuel         the weekly Oil Bulletin of the European Commission (one request)
freshness    the problems list; empty when all is well
report       the weekly summary

The heavy steps (build, rebuild, gold) are run by pazar-step under the common lock /run/lock/prozrachnost-heavy.lock.
The archive is read; nothing is downloaded.
"""
import argparse
import datetime as dt
import json
import sys

from . import archive, build, checks, config, db, fuel, gold


def step_build(c, a):
    st = archive.state()
    problems = []
    if not a.first and not a.last:
        age = archive.age_hours(st)
        if age > config.MAX_ARCHIVE_AGE_H:
            return {"problems": [f"Архивът е остарял: последно успешно четене преди {age:.0f} ч (над {config.MAX_ARCHIVE_AGE_H} ч)"]}
    rep = build.catch_up(c, a.first, a.last, st=st, limit_secs=a.budget)
    rep["problems"] = problems + rep["problems"]
    if not a.no_gold:
        rep["gold"] = gold.build_missing(c, a.first, a.last, limit_secs=a.budget)
    return rep


def step_rebuild(c, a):
    ms = dt.date.fromisoformat(a.month + "-01")
    rep = build.rebuild_month(c, ms, st=archive.state())
    rep["gold"] = gold.build_missing(c, str(ms), str((ms + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)), rebuild=True)
    rep["problems"] = []
    return rep


def step_gold(c, a):
    return {"gold": gold.build_missing(c, a.first, a.last, rebuild=a.rebuild, limit_secs=a.budget), "problems": []}


def step_fuel(c, a):
    return fuel.store(c, fuel.fetch())


def step_freshness(c, a):
    return {"problems": checks.freshness(c)}


def step_report(c, a):
    return {"problems": [], **checks.weekly(c)}


STEPS = {"build": step_build, "rebuild": step_rebuild, "gold": step_gold, "fuel": step_fuel, "freshness": step_freshness, "report": step_report}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--step", choices=["migrate", *STEPS], required=True)
    p.add_argument("--from", dest="first")
    p.add_argument("--to", dest="last")
    p.add_argument("--month")
    p.add_argument("--budget", type=int, help="seconds after which the step stops and reports where it got to")
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--no-gold", action="store_true")
    a = p.parse_args(argv)
    c = db.connect(autocommit=True)
    if a.step == "migrate":
        db.migrate(c)
        gold.load_reference(c)
        print(json.dumps({"problems": []}))
        return 0
    fn = STEPS[a.step]
    job = c.execute("INSERT INTO ops.job_run (step, status) VALUES (%s, 'работи') RETURNING id", (a.step,)).fetchone()[0]
    try:
        report = fn(c, a)
    except Exception as e:      # the report must say what happened; the exit code tells n8n
        report = {"problems": [f"{type(e).__name__}: {e}"]}
    c.execute("UPDATE ops.job_run SET finished_at = now(), status = %s, report = %s WHERE id = %s",
              ("неуспешно" if report.get("problems") else "наред", json.dumps(report, ensure_ascii=False, default=str), job))
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 1 if report.get("problems") else 0


if __name__ == "__main__":
    sys.exit(main())
