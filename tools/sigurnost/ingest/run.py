"""python -m ingest.run --step <step>

migrate     the schema and the reference lists (structures, indicators)
build       the archive into silver, then silver into gold (refuses an archive older than 30 hours)
gold        gold only (--year Y, --rebuild)
freshness   the problems list; empty when all is well
report      the weekly summary

build is a heavy step: `sigurnost-step` runs it under /run/lock/prozrachnost-heavy.lock. The archive is read; nothing is
downloaded and data.egov.bg is never asked.
"""
import argparse
import json
import sys

from . import archive, checks, config, db, gold, load


def step_build(c, a):
    st = archive.state()
    age = archive.age_hours(st)
    if age > config.MAX_ARCHIVE_AGE_H and not a.force:
        return {"problems": [f"Сигурност: архивът е остарял: последно успешно четене преди {age:.0f} ч (над {config.MAX_ARCHIVE_AGE_H} ч)"]}
    rep = load.ingest(c, st)
    rep["gold"] = gold.build(c, rebuild=a.rebuild)
    rep["problems"] += rep["gold"].pop("problems")
    return rep


def step_gold(c, a):
    rep = gold.build(c, [a.year] if a.year else None, rebuild=a.rebuild)
    return rep


def step_freshness(c, a):
    problems, info = checks.freshness(c)
    return {"problems": problems, "info": info}


def step_report(c, a):
    return {"problems": [], **checks.weekly(c)}


STEPS = {"build": step_build, "gold": step_gold, "freshness": step_freshness, "report": step_report}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--step", choices=["migrate", *STEPS], required=True)
    p.add_argument("--year", type=int)
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--force", action="store_true", help="build from an archive older than the threshold (never from the timer)")
    a = p.parse_args(argv)
    c = db.connect(autocommit=True)
    if a.step == "migrate":
        db.migrate(c)
        gold.load_reference(c)
        print(json.dumps({"problems": []}))
        return 0
    job = c.execute("INSERT INTO ops.job_run (step, status) VALUES (%s, 'работи') RETURNING id", (a.step,)).fetchone()[0]
    try:
        report = STEPS[a.step](c, a)
    except Exception as e:      # the report must say what happened; the exit code tells n8n
        report = {"problems": [f"Сигурност: {type(e).__name__}: {e}"]}
    c.execute("UPDATE ops.job_run SET finished_at = now(), status = %s, report = %s WHERE id = %s",
              ("неуспешно" if report.get("problems") else "наред", json.dumps(report, ensure_ascii=False, default=str), job))
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 1 if report.get("problems") else 0


if __name__ == "__main__":
    sys.exit(main())
