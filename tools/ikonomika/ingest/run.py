"""The steps of Икономика: python -m ingest.run --step <step>

  migrate                     the tables and the registers (the foundation of danni must be there)
  eurostat [--only a,b]       every indicator of db/ref/indicators.csv (or the named ones)
  bnb [--backfill]            the exchange rates of the quarters of the last 31 days (or from 1991)
  ecb                         the banks' interest rates, loans and deposits (ECB Data Portal)
  mf                          the Ministry of Finance's macroeconomic forecasts (data.egov.bg)
  build                       the clean tables again from the landing zone
  replay                      the answers the old database kept in raw/ (the move to the shared database)
  parity                      the old database against the shared one, indicator by indicator and period by period
  check                       every quality rule
  freshness                   what is late, held or broken

Ingest and serving are apart. The steps fill ikonomika_src and ikonomika; the pages read pub_ikonomika, which
changes only by a publication at the end of a step whose rules all hold (db/publish/*.sql).

Prints one JSON line. Exit code 1 when `problems` is not empty: that is what the n8n card reports.
"""
import argparse

from . import bnb, checks, ecb, eurostat, mf, move, pipeline, store
from .config import DSN, ROOT, TOOL
from .jsonstat import ShapeError

STEPS = ["migrate", "eurostat", "bnb", "ecb", "mf", "build", "replay", "parity", "check", "freshness"]


def all_indicators():
    return eurostat.indicators() + ecb.INDICATORS + mf.INDICATORS


def step(conn, r, name, only=None, backfill=False):
    """One step that writes -> its problems. Every one but parity ends with the clean tables, the rules and, when
    they hold, a publication. One indicator a source refused is not a reason to hold the rest."""
    problems = []
    if name != "parity":
        store.load_ref(conn, r)
    if name == "eurostat":
        problems = eurostat.load(conn, r, only=only)["problems"]
    elif name == "bnb":
        problems = bnb.load(conn, r, backfill=backfill)["problems"]
    elif name == "ecb":
        problems = ecb.load(conn, r)["problems"]
    elif name == "mf":
        problems = mf.load(conn, r)["problems"]
    elif name == "replay":
        problems = move.replay(conn, r)
    elif name == "parity":
        move.parity(conn, r)
        return [f"{title}: старо {e}, ново {n}" for title, e, n in conn.execute(
            """SELECT title_bg, expected, actual FROM ops.check_latest WHERE tool = %s AND kind = 'parity' AND ok = false
               ORDER BY name""", (TOOL,))]
    store.build(conn, r)
    failed = pipeline.run_checks(conn, r)
    return problems + (failed or pipeline.publish(conn, r, ROOT).problems)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", required=True, choices=STEPS)
    ap.add_argument("--only", help="eurostat: comma-separated indicator ids")
    ap.add_argument("--backfill", action="store_true", help="bnb: from 1991, skipping quarters already read")
    a = ap.parse_args(argv)
    with pipeline.connect(DSN) as conn:
        if a.step == "migrate":
            pipeline.require_core(conn, "0011")
            result = {"step": a.step, "migrations": pipeline.migrate(conn, TOOL, ROOT)}
        elif a.step == "freshness":
            result = {"step": a.step, "problems": pipeline.freshness(conn, TOOL) + checks.freshness(conn, all_indicators())}
        else:
            try:
                with pipeline.run(conn, TOOL, a.step, params={"only": a.only, "backfill": a.backfill},
                                  sha=pipeline.code_sha(ROOT)) as r:
                    problems = step(conn, r, a.step, only=a.only.split(",") if a.only else None, backfill=a.backfill)
                    result = {"step": a.step, "stats": r.stats, "problems": problems}
            except pipeline.Busy as e:
                result = {"step": a.step, "skipped": str(e), "problems": []}
            except (ShapeError, pipeline.ShapeError, pipeline.CorruptRaw) as e:
                result = {"step": a.step, "problems": [f"Икономика: {e}"]}
            except Exception as e:  # noqa: BLE001 - reported to n8n as JSON, non-zero exit; ops.run has it as failed
                result = {"step": a.step, "problems": [f"Икономика: {e!r}"[:2000]]}
    return pipeline.report(result)


if __name__ == "__main__":
    raise SystemExit(main())
