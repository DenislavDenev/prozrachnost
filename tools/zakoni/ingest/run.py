"""The steps of Закони и решения: python -m ingest.run --step <step>

    migrate    the tables and the registers (the foundation of danni must be there)
    build      the 14 resources of the data set from the archive into the landing zone, the totals compared, the clean
               tables, the rules, and the publication when every gating rule holds
               (--force: parse a file again even when it is the file of the current load)
    check      the clean tables again from the current loads, the rules, the publication
    replay     the files the old database was built from, through the same parser (the move); then as build
    parity     the old database against the shared one
    freshness  the list of problems, empty when all is well

Ingest and serving are apart: the steps fill zakoni_src and zakoni; the pages read pub_zakoni, which changes only by a
publication at the end of a step whose rules all hold. Prints one JSON line; exit 1 when `problems` is not empty.
"""
import argparse
import sys

from . import archive, build, checks, load, pipeline, ref
from .config import DSN, ROOT, TOOL


def finish(conn, r, problems):
    """The clean tables, the rules, and the publication when every gating rule holds. A count of a loaded file that does
    not agree with the landing zone stops it all: the pages keep the version they have."""
    if any("неуспешно" in p for p in problems):
        return problems + ["Закони: сверка на броя не мина: нищо не е изградено и публикувано"]
    build.build(conn, r)
    failed = pipeline.run_checks(conn, r)
    if failed:
        return problems + [f"Закони: {p}" for p in failed]
    return problems + [f"Закони: {p}" for p in pipeline.publish(conn, r, ROOT).problems]


def read_archive(conn, r, force=False, clock=None):
    """The newest file of every resource, at the time the archive last read data.egov.bg (a stale archive is refused;
    `clock` is the present moment, for the tests)."""
    now = archive.check_fresh(clock)
    archive.listing()       # the listing of the set must be there and be a list: it is what the source says it has
    r.stats["archive_read"] = now
    ref.load(conn, r, now=now)
    return load.read_all(conn, r, now, archive.verified, force=force)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--step", required=True, choices=["migrate", "build", "check", "replay", "parity", "freshness"])
    p.add_argument("--force", action="store_true")
    a = p.parse_args(argv)
    with pipeline.connect(DSN) as conn:
        if a.step == "migrate":
            pipeline.require_core(conn, "0009")
            return pipeline.report({"step": "migrate", "migrations": pipeline.migrate(conn, TOOL, ROOT)})
        if a.step == "freshness":
            return pipeline.report({"step": "freshness", "problems": checks.freshness(conn)})
        try:
            with pipeline.run(conn, TOOL, a.step, params={"force": a.force}, sha=pipeline.code_sha(ROOT)) as r:
                problems = []
                if a.step == "build":
                    problems = finish(conn, r, read_archive(conn, r, a.force))
                elif a.step == "check":
                    problems = finish(conn, r, [])
                elif a.step == "replay":
                    from .move import replay
                    problems = finish(conn, r, replay(conn, r))
                elif a.step == "parity":
                    from .move import parity
                    parity(conn, r)
                result = {"step": a.step, "stats": r.stats, "problems": problems}
        except pipeline.Busy as e:
            result = {"step": a.step, "status": "skipped", "note": str(e)}
        except (archive.ArchiveError, pipeline.ShapeError, pipeline.CorruptRaw) as e:
            result = {"step": a.step, "problems": [f"Закони: {e}"]}
        if a.step == "parity" and "stats" in result:
            result["problems"] = [f"{title}: старо {e}, ново {n}" for title, e, n in conn.execute(
                """SELECT title_bg, expected, actual FROM ops.check_latest
                   WHERE tool = %s AND kind = 'parity' AND ok = false ORDER BY name""", (TOOL,))]
    return pipeline.report(result)


if __name__ == "__main__":
    sys.exit(main())
