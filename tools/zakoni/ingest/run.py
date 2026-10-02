"""CLI for n8n and people: python -m ingest.run --step <name>

  migrate     apply db/migrations
  build       the 14 resources of the data set from the archive into silver, the totals compared, then gold and its checks
              (--force: parse again even when the file is the one built last)
  freshness   what is late, held or broken

Prints one JSON line with the stats. Exit code 1 on a failure or when `problems` is not empty.
"""
import argparse
import json
import sys

from . import checks, db, gold, load


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", required=True, choices=["migrate", "build", "freshness"])
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    out = {"step": a.step}
    try:
        if a.step == "migrate":
            with db.connect() as conn:
                out["applied"] = db.migrate(conn)
        elif a.step == "build":
            with db.job("build", {"force": a.force}) as (conn, st, run_id):
                problems = load.build(conn, st, run_id, force=a.force)
                st["problems"] = problems
                if any("неуспешно" in p for p in problems):
                    raise RuntimeError("сверка не мина: златото не се строи; " + "; ".join(problems)[:600])
                gold.build(conn, st, run_id)
                out.update(st)
                out["problems"] = problems
        elif a.step == "freshness":
            with db.connect(autocommit=True) as conn:
                out["problems"] = checks.freshness(conn)
        out["ok"] = not out.get("problems")
    except db.Busy as e:
        out.update(ok=True, skipped=str(e))
    except Exception as e:  # noqa: BLE001 - reported to n8n as JSON, non-zero exit
        out.update(ok=False, error=repr(e)[:2000])
    print(json.dumps(out, default=str, ensure_ascii=False))
    sys.exit(0 if out["ok"] else 1)


if __name__ == "__main__":
    main()
