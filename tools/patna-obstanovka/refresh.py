"""Refresh public road data and fail loudly when a snapshot is stale or broken."""
import argparse
import json
from datetime import datetime, timezone

from db import connect
from ingest import load_events, load_mvr, load_risk, load_toll

GROUPS = {
    "current": (load_toll, load_events),
    "mvr": (load_mvr,),
    "risk": (load_risk,),
}
KEYS = {
    "current": ("traffic", "weather", "lima_r01", "lima_r02", "lima_d01"),
    "mvr": ("crashes", "matches"),
    "risk": ("risk", "matches"),
}


def check(db, group, now=None):
    now = now or datetime.now(timezone.utc)
    results = []
    for key in KEYS[group]:
        row = db.execute("SELECT updated,count,error FROM sources WHERE key=?", (key,)).fetchone()
        if not row or row["error"] or not row["updated"]:
            results.append({"source": key, "ok": False, "reason": row["error"] if row else "missing"})
            continue
        age = (now - datetime.fromisoformat(row["updated"])).total_seconds() / 60
        ok = age <= 25 if group == "current" else age <= 180
        reason = None if ok else f"fetch is {age:.0f} minutes old"
        if group == "current" and key in ("traffic", "weather"):
            observed = db.execute(f"SELECT MAX(observed) FROM {key}").fetchone()[0]
            if not observed:
                ok, reason = False, "no observation timestamp"
            else:
                sample_age = (now - datetime.fromisoformat(observed.replace(" ", "T").replace("Z", "+00:00") + ("+00:00" if len(observed) == 19 else ""))).total_seconds() / 60
                if sample_age > 60 or sample_age < -10:
                    ok, reason = False, f"observation is {sample_age:.0f} minutes old"
        results.append({"source": key, "ok": ok, "count": row["count"], "reason": reason})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("group", choices=GROUPS)
    args = parser.parse_args()
    db = connect()
    for operation in GROUPS[args.group]:
        operation(db)
    results = check(db, args.group)
    print(json.dumps({"group": args.group, "results": results}, ensure_ascii=False))
    raise SystemExit(0 if all(item["ok"] for item in results) else 1)


if __name__ == "__main__":
    main()
