"""python -m ingest.run --step <step>

migrate     the schema, the reference data (municipalities, funds, registry, the currency of each year)
dfz         every file of the archive that is not loaded yet, then the gold of the new snapshots; refuses an archive older than 8 days
gold        the gold of the built snapshots that have none or a failed one (or all with --rebuild)
rebuild     one financial year again from the files of the archive (--fy)
population  the number of inhabitants of each municipality, from the tool Население
sample      random legal recipients with the lines of the file, for the manual check (--fy --seed -n)
freshness   the problems list; empty when all is well
report      the weekly summary

The heavy steps (dfz, gold, rebuild) are run by subsidii-step under the common lock /run/lock/prozrachnost-heavy.lock.
The archive is read; nothing is downloaded and the source (ДФЗ) is never asked.
"""
import argparse
import json
import random
import sys

from . import archive, build, checks, config, db, gold, load, parse, population


def step_dfz(c, a):
    st = archive.state()
    if not a.force_stale:
        age = archive.age_hours(st)
        if age > config.MAX_ARCHIVE_AGE_H:
            return {"problems": [f"Субсидии: ДФЗ: архивът е остарял: последно успешно четене преди {age:.0f} ч (над {config.MAX_ARCHIVE_AGE_H} ч)"]}
    rep = build.catch_up(c, st=st, limit_secs=a.budget, only={a.fy} if a.fy else None)
    if not a.no_gold:
        rep["gold"] = gold.build_missing(c, limit_secs=a.budget)
        rep["problems"] += rep["gold"]["problems"]
    return rep


def step_gold(c, a):
    rep = gold.build_missing(c, only={a.fy} if a.fy else None, limit_secs=a.budget, rebuild=a.rebuild)
    return {"gold": rep, "problems": rep["problems"]}


def step_rebuild(c, a):
    if not a.fy:
        return {"problems": ["--fy е задължителен"]}
    idx = archive.index_shas()
    rep = build.rebuild_year(c, a.fy, archive.snapshots(idx=idx), idx=idx, st=archive.state())
    rep["gold"] = gold.build_missing(c, only={a.fy})
    rep["problems"] = rep["gold"]["problems"]
    return rep


def step_population(c, a):
    rep = population.run(c, pause=a.pause)
    rep["problems"] = rep["problems"] if rep["municipalities"] < rep["of"] else []
    return rep


def step_sample(c, a):
    """Random legal recipients: their ОБЩО row and payment rows from the archive file, next to what gold has, to be checked by hand."""
    snap = archive.snapshots()[a.fy][-1]
    raw, sha, _ = archive.read(snap)
    rnd = random.Random(a.seed)
    blocks, cur = [], None
    for ln in parse.read_lines(raw):
        if ln.total:
            cur = [ln, []]
            blocks.append(cur)
        else:
            cur[1].append(ln)
    from . import names
    legal = [b for b in blocks if names.kind_of(b[0].f[parse.NAME], b[0].f[parse.SURNAME]) == "legal"]
    out = []
    for ln, pays in rnd.sample(legal, min(a.n, len(legal))):
        out.append(dict(name=ln.f[parse.NAME], oblast=ln.f[parse.OBLAST], obshtina=ln.f[parse.OBSHTINA], total_row=dict(zip(parse.HEADER[10:], ln.f[10:])),
                        payments=len(pays), efgz=str(sum(parse.amount(p.f[parse.EFGZ]) or 0 for p in pays)),
                        ezfrs=str(sum(parse.amount(p.f[parse.EZFRS]) or 0 for p in pays)), nb=str(sum(parse.amount(p.f[parse.NB]) or 0 for p in pays))))
    return {"file": snap.rel, "rows": out, "problems": []}


def step_freshness(c, a):
    return {"problems": checks.freshness(c)}


def step_report(c, a):
    return {"problems": [], **checks.weekly(c)}


STEPS = {"dfz": step_dfz, "gold": step_gold, "rebuild": step_rebuild, "population": step_population, "sample": step_sample,
         "freshness": step_freshness, "report": step_report}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--step", choices=["migrate", *STEPS], required=True)
    p.add_argument("--fy", type=int)
    p.add_argument("--seed", default="1")
    p.add_argument("-n", type=int, default=6)
    p.add_argument("--budget", type=int, help="seconds after which the step stops and reports where it got to")
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--no-gold", action="store_true")
    p.add_argument("--force-stale", action="store_true", help="build from an archive older than the limit (a catch-up by hand)")
    p.add_argument("--pause", type=float, default=1.0)
    a = p.parse_args(argv)
    c = db.connect(autocommit=True)
    if a.step == "migrate":
        db.migrate(c)
        gold.load_reference(c)
        load.load_units(c)
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
