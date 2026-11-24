"""The steps of Сигурност: python -m ingest.run --step <step>

migrate     the tables and the registers (the foundation of danni must be there)
build       the lists and tables of the archive into the landing zone, then the clean tables, the rules, the publication
            (refuses an archive older than 30 hours; --reread reads again the files already read, after a parser change)
gold        the clean tables again from the landing zone (--year Y, --rebuild), the rules, the publication
check       every rule and, when they pass, the publication of the serving layer
replay      the lists and tables the old database holds, from their archive files and through the same parser (the move to
            the shared database)
parity      the old database of its own against the shared one, number by number
freshness   the problems list; empty when all is well
summary     the weekly card: counts, last good reads, changes, held answers, problems

build is a heavy step: `sigurnost-step` runs it under /run/lock/prozrachnost-heavy.lock. The archive of Наблюдател is
read; nothing is downloaded and data.egov.bg is never asked. Ingest and serving are apart: the steps fill sigurnost_src
and sigurnost; the pages read pub_sigurnost, which changes only by a publication at the end of a step whose rules hold.
"""
import argparse
import sys

import psycopg

from . import archive, checks, config, db, gold, load, parity, pipeline
from .config import ROOT, TOOL


def state_notes(conn):
    """The problems and notes of freshness as they are at the end of the step, for the page of the sources."""
    problems, info = checks.freshness(conn)
    problems = pipeline.freshness(conn, TOOL) + problems
    with conn.transaction():
        conn.execute("DELETE FROM sigurnost.state_note")
        for kind, lines in (("problem", problems), ("info", info)):
            for n, text in enumerate(lines, 1):
                conn.execute("INSERT INTO sigurnost.state_note (kind, n, text) VALUES (%s,%s,%s)", (kind, n, text))


def finish(conn, r, problems):
    """The rules, and only when all of them pass, the publication of the serving layer: the pages keep the version they
    had until the new one is sound."""
    state_notes(conn)
    failed = pipeline.run_checks(conn, r)
    return problems + (failed or pipeline.publish(conn, r, ROOT).problems)


def step_build(conn, r, a):
    st = archive.state()
    age = archive.age_hours(st)
    if age > config.MAX_ARCHIVE_AGE_H and not a.force:
        raise archive.ArchiveError(f"архивът е остарял: последно успешно четене преди {age:.0f} ч (над {config.MAX_ARCHIVE_AGE_H} ч)")
    gold.load_reference(conn)
    rep = load.ingest(conn, r, st, reread=a.reread)
    rep["gold"] = gold.build(conn, rebuild=a.rebuild)
    rep["problems"] += rep["gold"].pop("problems")
    return rep


def step_gold(conn, r, a):
    gold.load_reference(conn)
    load.sync_datasets(conn)
    rep = gold.build(conn, [a.year] if a.year else None, rebuild=a.rebuild)
    return rep


def replay(conn, r, old_dsn=None):
    """Load what the old database holds from the archive files it names and through the same parser, with the time the
    archive read each file. Nothing is copied from its tables: the lists first, then every version of every table in the
    order the archive read them; then the clean tables from the landing zone."""
    with psycopg.connect(old_dsn or config.OLD_DSN, autocommit=True) as old:
        old.execute("SET default_transaction_read_only = on")
        lists = old.execute("""SELECT set_uri, list_path, list_sha256, list_read_at FROM silver.dataset
                               WHERE list_path IS NOT NULL ORDER BY list_read_at, set_uri""").fetchall()
        tables = old.execute("""SELECT r.resource_uri, r.sha256, r.path, r.read_at, r.name, r.version, r.source_updated_at, r.set_uri
                                FROM silver.resource r ORDER BY r.read_at, r.resource_uri, r.sha256""").fetchall()
    gold.load_reference(conn)
    for set_uri, path, sha, read_at in lists:
        load.load_list(conn, r, set_uri, path, sha, read_at, now=read_at)
    load.sync_datasets(conn)
    sets = {d["set_uri"]: d for d in config.datasets()}
    rep = {"lists": len(lists), "tables": len(tables), "outcomes": {}, "problems": []}
    for uri, sha, path, read_at, name, version, updated, set_uri in tables:
        raw = (config.ARCHIVE / path).read_bytes()
        res = dict(uri=uri, name=name, version=version, updated_at=updated)
        out = load.store(conn, r, sets[set_uri], res, (raw, sha, path, read_at), now=read_at)
        rep["outcomes"][out] = rep["outcomes"].get(out, 0) + 1
    rep["gold"] = gold.build(conn, rebuild=True)
    rep["problems"] += rep["gold"].pop("problems")
    return rep


def summary(conn):
    """The weekly card of n8n (the shape Партии and Контрол give it)."""
    w = checks.weekly(conn)
    last = conn.execute("""SELECT max(finished_at) FILTER (WHERE step = 'build' AND status = 'ok'),
                                  (SELECT max(loaded_at) FROM ops.load WHERE tool = %s AND source = 'egov-list' AND status IN ('loaded', 'confirmed', 'unchanged'))
                           FROM ops.run WHERE tool = %s""", (TOOL, TOOL)).fetchone()
    stamp = lambda t: t and f"{t:%Y-%m-%d %H:%M}"
    problems, _ = checks.freshness(conn)
    return {"tool": "Престъпност и пожари",
            "counts": {"таблици": w["resources"], "редове": int(w["rows"]), "отказани": w["invalid"], "публикувани (година, вид)": w["gold_tables"],
                       "години": f"{w['from']}-{w['to']}" if w["from"] else "няма"},
            "last_ok": {"списъци (архив)": stamp(last[1]), "изграждане": stamp(last[0])},
            "changes": conn.execute("SELECT count(*) FROM ops.change_log WHERE tool = %s AND detected_at > now() - interval '7 days'", (TOOL,)).fetchone()[0],
            "held": conn.execute("SELECT count(*) FROM ops.hold WHERE tool = %s", (TOOL,)).fetchone()[0],
            "problems": pipeline.freshness(conn, TOOL) + problems}


STEPS = ("migrate", "build", "gold", "check", "replay", "parity", "freshness", "summary")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--step", choices=STEPS, required=True)
    p.add_argument("--year", type=int)
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--reread", action="store_true", help="read again the archive files already read (after a change of the parser)")
    p.add_argument("--force", action="store_true", help="build from an archive older than the threshold (never from the timer)")
    a = p.parse_args(argv)
    with db.connect() as conn:
        # a commit that waits for the disk costs 0,4 s on the server; the archive is the truth, and a commit lost in a crash
        # is an answer that is simply read again on the next run
        conn.execute("SET synchronous_commit = off")
        if a.step == "migrate":
            pipeline.require_core(conn, "0009")
            return pipeline.report({"migrations": pipeline.migrate(conn, TOOL, ROOT), "problems": []})
        if a.step == "freshness":
            problems, info = checks.freshness(conn)
            return pipeline.report({"problems": pipeline.freshness(conn, TOOL) + problems, "info": info})
        if a.step == "summary":
            return pipeline.report(summary(conn))
        try:
            with pipeline.run(conn, TOOL, a.step, params={"year": a.year, "rebuild": a.rebuild, "reread": a.reread, "force": a.force},
                              sha=pipeline.code_sha(ROOT)) as r:
                if a.step == "parity":
                    rep = {"problems": parity.parity(conn, r)}
                else:
                    if a.step == "build":
                        rep = step_build(conn, r, a)
                    elif a.step == "gold":
                        rep = step_gold(conn, r, a)
                    elif a.step == "replay":
                        rep = replay(conn, r)
                    else:
                        gold.load_reference(conn)
                        rep = {"problems": []}
                    rep["problems"] = finish(conn, r, rep["problems"])
                    rep["publication"] = r.stats.get("publication")
                r.stats.update({k: v for k, v in rep.items() if isinstance(v, (int, dict)) and k != "gold"})
        except pipeline.Busy as e:
            rep = {"step": a.step, "status": "skipped", "note": str(e), "problems": []}
        except Exception as e:      # the report must say what happened; the exit code tells n8n
            rep = {"step": a.step, "problems": [f"Сигурност: {type(e).__name__}: {e}"]}
    return pipeline.report(rep)


if __name__ == "__main__":
    sys.exit(main())
