"""python -m ingest.run --step <step>

migrate      the tables and the registers (the foundation of danni must be there)
build        every archived day that is not built yet (or --from/--to), its daily observations, the checks and the
             publication; refuses an archive whose last good read is older than 30 hours
rebuild      one month again from its accepted files (--month YYYY-MM), its observations, the publication
gold         the daily observations for the built days that have none (or --from/--to, --rebuild), the publication
fuel         the weekly Oil Bulletin of the European Commission (one request), the publication
replay       the move to the shared database: every day of the archive and every bulletin read before, through the same
             parser, without holds; then the observations and the publication. Resumable: a built day is not built again
parity       the old database of the tool against the shared one, day by day and chain by chain (ops.check_result)
publish      the serving layer again (--from: the window of the large tables; without it, everything is compared)
sample       random products of chosen chains for the manual check against their sites (--day --seed --chains -n)
check        every quality rule
freshness    the rules, then the list of problems; empty when all is well
report       the weekly summary

Ingest and serving are apart. The steps fill pazar_src and pazar; the pages read pub_pazar, which changes only by a
publication at the end of a step whose rules all hold (db/publish/*.sql). The heavy steps (build, rebuild, gold,
replay, parity) are run by pazar-step under the common lock /run/lock/prozrachnost-heavy.lock. Nothing is downloaded
from "Колко струва": the archive of Наблюдател is read.
"""
import argparse
import datetime as dt
import sys

from . import archive, build, config, db, fuel, gold, parity, pipeline, verify
from .config import ROOT, TOOL

FAR = dt.date(9999, 1, 1)     # a window with nothing in it: the step changed no price and no observation


def d(s):
    return None if s is None else s if isinstance(s, dt.date) else dt.date.fromisoformat(str(s)[:10])


def publish(c, r, since):
    """The serving layer. The prices and the daily observations are compared only from `since` (the day before the
    earliest one that changed); the other tables whole. `since=None` compares everything."""
    months = [d(x) for (x,) in c.execute("""SELECT DISTINCT date_trunc('month', from_day)::date FROM pazar.price_span
                                            UNION SELECT DISTINCT date_trunc('month', day)::date FROM pazar.category_day""")]
    with c.transaction():
        for m in months:
            c.execute("SELECT pub_pazar.ensure_month(%s)", (m,))
    window = {"since": since or dt.date(1900, 1, 1)}
    done = pipeline.publish(c, r, ROOT, window=window)
    if done.publication_id:
        # a table just filled has no statistics, and the first page after it would be planned blind
        for table in done.changes:
            if table.startswith("pub_pazar.") and table not in ("pub_pazar.price_span", "pub_pazar.category_day"):
                c.execute(f"ANALYZE {table}")
        for m in months:
            if since is None or m >= since.replace(day=1):
                c.execute(f"ANALYZE pub_pazar.price_span_{m:%Y%m}")
                c.execute(f"ANALYZE pub_pazar.category_day_{m:%Y%m}")
    r.stats["published_since"] = str(window["since"])
    return done.problems


BLOCKING = ["reconcile", "unique", "not_null", "fk"]     # a data rule that fails stops the publication
REPORTED = ["drop", "unmatched"]                          # late or thin sources are reported; what is there is published


def checked_publish(c, r, since):
    """The rules, then the publication when no rule on the data itself failed. Returns the problems."""
    blocking = pipeline.run_checks(c, r, kinds=BLOCKING)
    problems = blocking + pipeline.run_checks(c, r, kinds=REPORTED)
    if blocking:
        return problems + ["Не е публикувано: правило върху данните не минава"]
    return problems + publish(c, r, since)


def since_of(*days):
    """The day before the earliest day that changed, or FAR when none did."""
    days = [d(x) for x in days if x]
    return min(days) - dt.timedelta(days=1) if days else FAR


def step_build(c, r, a):
    st = archive.state()
    if not a.first and not a.last:
        age = archive.age_hours(st)
        if age > config.MAX_ARCHIVE_AGE_H:
            return {"problems": [f"Архивът е остарял: последно успешно четене преди {age:.0f} ч (над {config.MAX_ARCHIVE_AGE_H} ч)"]}
    rep = build.catch_up(c, r, a.first, a.last, st=st, limit_secs=a.budget)
    if not a.no_gold:
        rep["gold"] = gold.build_missing(c, a.first, a.last, limit_secs=a.budget)
    if a.no_publish:
        rep["problems"] += pipeline.run_checks(c, r)
    else:
        rep["problems"] += checked_publish(c, r, since_of(rep.get("changed_from"), rep.get("gold", {}).get("first")))
    return rep


def step_rebuild(c, r, a):
    ms = dt.date.fromisoformat(a.month + "-01")
    rep = build.rebuild_month(c, r, ms)
    rep["gold"] = gold.build_missing(c, str(ms), str((ms + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)), rebuild=True)
    rep["problems"] = checked_publish(c, r, ms)
    return rep


def step_gold(c, r, a):
    rep = {"gold": gold.build_missing(c, a.first, a.last, rebuild=a.rebuild, limit_secs=a.budget)}
    rep["problems"] = checked_publish(c, r, since_of(rep["gold"].get("first")))
    return rep


def step_fuel(c, r, a):
    rep = fuel.store(c, r, fuel.fetch())
    rep["problems"] = rep["problems"] + checked_publish(c, r, FAR)
    return rep


def step_replay(c, r, a):
    """Every day of the archive, in order, as it is now, and every bulletin kept before the move, oldest first."""
    rep = build.catch_up(c, r, a.first, a.last, st=archive.state(), limit_secs=a.budget, replay=True)
    kept = sorted((config.DATA / "raw" / "oil-bulletin").glob("*.xlsx"))
    rep["fuel"] = []
    for path in kept:          # <day>.<sha12>.xlsx, the name the old step gave them
        at = dt.datetime.fromisoformat(path.name[:10] + "T07:00:00+00:00")
        rep["fuel"].append(fuel.store(c, r, path.read_bytes(), now=at, path=path).get("state"))
    if not a.no_gold:
        rep["gold"] = gold.build_missing(c, a.first, a.last, limit_secs=a.budget)
    if not a.no_publish and not rep.get("stopped") and not rep.get("gold", {}).get("stopped"):
        rep["problems"] += checked_publish(c, r, None)
    return rep


def step_parity(c, r, a):
    return parity.compare(c, r, config.OLD_DSN, a.first, a.last)


def step_publish(c, r, a):
    return {"problems": checked_publish(c, r, d(a.first))}


def step_sample(c, r, a):
    day = dt.date.fromisoformat(a.day)
    return {"rows": verify.sample(c, day, a.seed, a.chains.split(","), a.n), "problems": []}


def step_check(c, r, a):
    return {"problems": pipeline.run_checks(c, r)}


def step_report(c, r, a):
    since = dt.date.today() - dt.timedelta(days=7)
    row = c.execute("""SELECT count(*), coalesce(sum(valid), 0), coalesce(sum(bad), 0), coalesce(max(chains), 0), coalesce(max(stores), 0)
                       FROM pazar.day WHERE status = 'built' AND day >= %s""", (since,)).fetchone()
    last = c.execute("SELECT max(day) FROM pazar.day WHERE status = 'built'").fetchone()[0]
    return {"problems": [], "days": row[0], "valid": row[1], "bad": row[2], "chains": row[3], "stores": row[4], "last": str(last)}


STEPS = {"build": step_build, "rebuild": step_rebuild, "gold": step_gold, "fuel": step_fuel, "replay": step_replay,
         "parity": step_parity, "publish": step_publish, "sample": step_sample, "check": step_check, "report": step_report}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--step", choices=["migrate", "freshness", *STEPS], required=True)
    p.add_argument("--from", dest="first")
    p.add_argument("--to", dest="last")
    p.add_argument("--month")
    p.add_argument("--day")
    p.add_argument("--seed", default="1")
    p.add_argument("--chains", default="")
    p.add_argument("-n", type=int, default=6)
    p.add_argument("--budget", type=int, help="seconds after which the step stops and reports where it got to")
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--no-gold", action="store_true")
    p.add_argument("--no-publish", action="store_true")
    a = p.parse_args(argv)
    with db.connect() as c:
        if a.step == "migrate":
            applied = db.migrate(c)
            gold.load_reference(c)
            return pipeline.report({"applied": applied, "problems": []})
        if a.step == "freshness":
            with pipeline.run(c, TOOL, "check", sha=pipeline.code_sha(ROOT)) as r:
                pipeline.run_checks(c, r)
            return pipeline.report({"problems": pipeline.freshness(c, TOOL)})
        params = {k: v for k, v in vars(a).items() if v not in (None, False, "", 6, "1") and k != "step"}
        try:
            with pipeline.run(c, TOOL, a.step, params, sha=pipeline.code_sha(ROOT)) as r:
                report = STEPS[a.step](c, r, a)
                r.stats.update({k: v for k, v in report.items() if k in ("built", "unchanged", "held", "invalid", "rebuilt", "stopped")})
                if report.get("problems"):
                    r.stats["problems"] = report["problems"][:50]
        except pipeline.Busy as e:
            report = {"problems": [], "skipped": str(e)}
        except Exception as e:      # the report must say what happened; the exit code tells n8n
            report = {"problems": [f"{type(e).__name__}: {e}"]}
        return pipeline.report(report)


if __name__ == "__main__":
    sys.exit(main())
