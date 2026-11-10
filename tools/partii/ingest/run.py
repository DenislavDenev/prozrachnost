"""The steps of Партии и пари: python -m ingest.run --step <step>

    migrate    the tables and the registers (the foundation of danni must be there)
    erik       every archived answer of ЕРИК that is not read yet, the clean tables, the rules, the publication; refuses an archive whose
               last good read is older than 30 hours (--any-age for a catch-up from an old archive)
    build      the clean tables again from the landing zone, the rules, the publication
    check      every quality rule and, when they pass, the publication of the serving layer
    freshness  the list of problems, empty when all is well
    summary    the weekly card: counts, last good reads, changes, held answers, problems

ЕРИК is never read from here: the archive of Наблюдател reads it once a day and this tool builds from its files.
"""
import argparse
import sys

from . import archive, config, pipeline, store
from .config import DSN, ROOT, TOOL


def erik(conn, r, any_age=False, limit=None, root=None, st=None):
    st = st or archive.state(root)
    if not any_age:
        age = archive.age_hours(st)
        if age > config.MAX_ARCHIVE_AGE_H:
            return {"problems": [f"Архивът е остарял: последно успешно четене на ЕРИК преди {age:.0f} ч (над {config.MAX_ARCHIVE_AGE_H} ч)"]}
    # a commit that waits for the disk costs 0,2 s on the server; the archive is the truth, and a commit lost in a crash is an
    # answer that is simply read again on the next run
    conn.execute("SET synchronous_commit = off")
    rep = store.read_archive(conn, r, root=root, st=st, limit=limit)
    store.build(conn, rep)
    rep["problems"] = rep.pop("notices", []) + rep["problems"] + checked(conn, r, rep)
    return rep


def checked(conn, r, rep):
    """The rules, and only when all of them pass, the publication of the serving layer: the pages keep the version they had
    until the new one is sound."""
    failed = pipeline.run_checks(conn, r)
    if failed:
        return failed
    done = pipeline.publish(conn, r, ROOT)
    rep["publication"] = done.publication_id
    return done.problems


def summary(conn):
    row = conn.execute("""SELECT (SELECT count(*) FROM partii.election), (SELECT count(*) FROM partii.election WHERE gone_at IS NOT NULL),
                                 (SELECT coalesce(sum(records_total), 0) FROM partii.as_of('infinity') WHERE tbl = 'donors'),
                                 (SELECT count(*) FROM partii.participant), (SELECT count(*) FROM partii.agency),
                                 (SELECT count(*) FROM ops.change_log WHERE tool = 'partii' AND detected_at > now() - interval '7 days'),
                                 (SELECT count(*) FROM ops.hold WHERE tool = 'partii'),
                                 (SELECT max(read_at) FROM partii.reading),
                                 (SELECT max(loaded_at) FROM ops.load WHERE tool = 'partii' AND source = 'home'),
                                 (SELECT max(finished_at) FROM ops.run WHERE tool = 'partii' AND step = 'erik' AND status = 'ok')""").fetchone()
    stamp = lambda t: t and f"{t:%Y-%m-%d %H:%M}"
    return {"tool": "Партии и пари", "counts": {"избори": row[0], "изчезнали от ЕРИК": row[1], "дарения (редове)": int(row[2]), "участници": row[3], "агенции с ЕИК": row[4]},
            "last_ok": {"ЕРИК (архив)": stamp(row[8]), "последен нов отговор": stamp(row[7]), "изграждане": stamp(row[9])},
            "changes": row[5], "held": row[6], "problems": pipeline.freshness(conn, TOOL)}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--step", choices=["migrate", "erik", "build", "check", "freshness", "summary"], required=True)
    p.add_argument("--any-age", action="store_true", help="build even from an archive that has not been read lately")
    p.add_argument("--limit", type=int, help="read at most this many archived answers in this run")
    a = p.parse_args(argv)
    with pipeline.connect(DSN) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        if a.step == "migrate":
            pipeline.require_core(conn, "0008")
            return pipeline.report({"applied": pipeline.migrate(conn, TOOL, ROOT), "problems": []})
        if a.step == "freshness":
            return pipeline.report({"problems": pipeline.freshness(conn, TOOL)})
        if a.step == "summary":
            return pipeline.report(summary(conn))
        try:
            with pipeline.run(conn, TOOL, a.step, {"any_age": a.any_age, "limit": a.limit}, sha=pipeline.code_sha(ROOT)) as r:
                if a.step == "erik":
                    rep = erik(conn, r, a.any_age, a.limit)
                elif a.step == "build":
                    rep = store.build(conn, {"problems": []})
                    rep["problems"] = rep.pop("notices", []) + rep["problems"] + checked(conn, r, rep)
                else:
                    rep = {}
                    rep["problems"] = checked(conn, r, rep)
                r.stats.update({k: v for k, v in rep.items() if isinstance(v, int)})
        except pipeline.Busy as e:
            rep = {"skipped": str(e), "problems": []}
        except Exception as e:      # the report must say what happened; the exit code tells n8n
            rep = {"problems": [f"{type(e).__name__}: {e}"]}
        return pipeline.report(rep)


if __name__ == "__main__":
    sys.exit(main())
