"""python -m ingest.run --step <step>

migrate     the tables and the registers (the foundation of danni must be there)
dfz         every file of the archive that is not loaded yet (the newest of each year is looked at every time), the
            aggregates of the new snapshots, the rules and the publication; refuses an archive older than 8 days
gold        the aggregates of the written snapshots that have none or a failed one (or all with --rebuild), rules, publication
rebuild     one financial year again from the files of the archive (--fy), rules, publication
replay      every year again from the files of the archive (the move into the shared database), rules, publication
parity      the old database against this one: counts and sums by year and snapshot (--old-dsn)
check       the rules and, when they pass, the publication of the serving layer
sample      random legal recipients with the lines of the file, for the manual check (--fy --seed -n)
freshness   the problems list; empty when all is well
summary     the weekly card

The heavy steps (dfz, gold, rebuild, replay) are run by subsidii-step under the common lock /run/lock/prozrachnost-heavy.lock.
The archive is read; nothing is downloaded and the source (ДФЗ) is never asked. The number of inhabitants is the one
Население publishes (pub_naselenie); this tool does not read it again.
"""
import argparse
import random
import sys

from . import archive, build, checks, config, gold, load, move, parse, pipeline


def step_dfz(c, r, a):
    st = archive.state()
    if not a.force_stale:
        age = archive.age_hours(st)
        if age > config.MAX_ARCHIVE_AGE_H:
            return {"problems": [f"Субсидии: ДФЗ: архивът е остарял: последно успешно четене преди {age:.0f} ч (над {config.MAX_ARCHIVE_AGE_H} ч)"]}
    rep = build.catch_up(c, r, st=st, limit_secs=a.budget, only={a.fy} if a.fy else None)
    rep["gold"] = gold.build_missing(c, limit_secs=a.budget)
    rep["problems"] += rep["gold"]["problems"]
    return rep


def step_gold(c, r, a):
    rep = gold.build_missing(c, only={a.fy} if a.fy else None, limit_secs=a.budget, rebuild=a.rebuild)
    return {"gold": rep, "problems": rep["problems"]}


def step_rebuild(c, r, a, years=None):
    st = archive.state()
    idx = archive.index_shas()
    snaps = archive.snapshots(idx=idx)
    rep = {"years": [], "problems": []}
    for fy in years or [a.fy]:
        rep["years"].append(build.replay_year(c, r, fy, snaps, idx, st=st))
    build._finish(c, r, st, rep)
    g = gold.build_missing(c, only=set(years or [a.fy]))
    rep["gold"], rep["problems"] = g, rep["problems"] + g["problems"]
    return rep


def step_replay(c, r, a):
    return step_rebuild(c, r, a, years=sorted(archive.snapshots()))


def step_sample(c, r, a):
    """Random legal recipients: their ОБЩО row and payment rows from the archive file, next to what is stored, to be checked by hand."""
    snap = archive.snapshots()[a.fy][-1]
    raw, sha, _ = archive.read(snap)
    rnd = random.Random(a.seed)
    blocks = []
    for ln in parse.read_lines(raw):
        if ln.total:
            blocks.append([ln, []])
        else:
            blocks[-1][1].append(ln)
    from . import names
    legal = [b for b in blocks if names.kind_of(b[0].f[parse.NAME], b[0].f[parse.SURNAME]) == "legal"]
    out = []
    for ln, pays in rnd.sample(legal, min(a.n, len(legal))):
        out.append(dict(name=ln.f[parse.NAME], oblast=ln.f[parse.OBLAST], obshtina=ln.f[parse.OBSHTINA], total_row=dict(zip(parse.HEADER[10:], ln.f[10:])),
                        payments=len(pays), efgz=str(sum(parse.amount(p.f[parse.EFGZ]) or 0 for p in pays)),
                        ezfrs=str(sum(parse.amount(p.f[parse.EZFRS]) or 0 for p in pays)), nb=str(sum(parse.amount(p.f[parse.NB]) or 0 for p in pays))))
    return {"file": snap.rel, "rows": out, "problems": []}


def checked(c, r, rep):
    """The rules, and only when all of them pass, the publication of the serving layer: the pages keep the version they had
    until the new one is sound."""
    failed = pipeline.run_checks(c, r)
    if failed:
        return failed
    done = pipeline.publish(c, r, config.ROOT)
    rep["publication"] = done.publication_id
    rep["published"] = done.changes
    return done.problems


STEPS = {"dfz": step_dfz, "gold": step_gold, "rebuild": step_rebuild, "replay": step_replay, "check": lambda c, r, a: {"problems": []},
         "sample": step_sample}
PUBLISHING = {"dfz", "gold", "rebuild", "replay", "check"}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--step", choices=["migrate", "parity", "freshness", "summary", *STEPS], required=True)
    p.add_argument("--fy", type=int)
    p.add_argument("--seed", default="1")
    p.add_argument("-n", type=int, default=6)
    p.add_argument("--budget", type=int, help="seconds after which the step stops and reports where it got to")
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--force-stale", action="store_true", help="build from an archive older than the limit (a catch-up by hand)")
    p.add_argument("--old-dsn", default=move.OLD_DSN, help="the old database, for the step parity")
    a = p.parse_args(argv)
    if a.step == "rebuild" and not a.fy:
        return pipeline.report({"problems": ["--fy е задължителен"]})
    with pipeline.connect(config.DSN) as c:
        c.execute("SET TIME ZONE 'UTC'")
        if a.step == "migrate":
            pipeline.require_core(c, "0009")
            applied = pipeline.migrate(c, config.TOOL, config.ROOT)
            with c.transaction():
                for fy in c.execute("SELECT fy FROM subsidii.source_year").fetchall():
                    load.ensure_year(c, fy[0])
                load.load_units(c)
            return pipeline.report({"applied": applied, "problems": []})
        if a.step == "freshness":
            return pipeline.report({"problems": checks.all_problems(c)})
        if a.step == "summary":
            return pipeline.report(checks.summary(c))
        try:
            with pipeline.run(c, config.TOOL, a.step, {k: v for k, v in vars(a).items() if v not in (None, False) and k != "old_dsn"},
                              sha=pipeline.code_sha(config.ROOT)) as r:
                if a.step == "parity":
                    rep = move.parity(c, r, a.old_dsn)
                else:
                    rep = STEPS[a.step](c, r, a)
                    if a.step in PUBLISHING:
                        rep["problems"] = rep.get("problems", []) + checked(c, r, rep)
                r.stats.update({k: v for k, v in rep.items() if isinstance(v, int) and not isinstance(v, bool)})
                if rep.get("problems"):
                    r.stats["problems"] = rep["problems"][:20]
        except pipeline.Busy as e:
            rep = {"skipped": str(e), "problems": []}
        except Exception as e:      # the report must say what happened; the exit code tells n8n
            rep = {"problems": [f"{type(e).__name__}: {e}"]}
        return pipeline.report(rep)


if __name__ == "__main__":
    sys.exit(main())
