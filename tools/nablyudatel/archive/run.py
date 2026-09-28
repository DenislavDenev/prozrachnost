"""The archive's command line (what the step script runs on the server).

    python -m archive.run kolkostruva erik ...   # these sources; `all` for every one
        --full            # ЕРИК: every election, not only the newest; Колко струва: ask again for old missing days
        --budget SECONDS  # stop a long source in time; the next run goes on from where this one stopped (default 3000)
    python -m archive.run freshness            # exit 1 with the problems when a source failed or is late
    python -m archive.run summary              # files and MB per source, the last good read of each

ARHIV_DIR is where the archive lives. One JSON line per source on stdout; exit 1 when a source failed.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

from .core import Http, Store, now
from .sources import SOURCES


def freshness(states, at=None):
    at = at or datetime.now(timezone.utc)
    problems = []
    for name, cfg in SOURCES.items():
        s = states.get(name, {})
        ok, err = s.get("last_ok", ""), s.get("last_error_at", "")
        if err and err >= ok:
            problems.append(f"{name} ({cfg['label']}): {s.get('last_error')}")
        elif not ok:
            problems.append(f"{name} ({cfg['label']}): never read")
        else:
            hours = (at - datetime.strptime(ok, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() / 3600
            if hours > cfg["max_age"]:
                problems.append(f"{name} ({cfg['label']}): last good read {ok}, {hours:.0f} h ago (at most {cfg['max_age']})")
    return problems


def run(store, names, full=False, budget=3000):
    rc = 0
    for name in names:
        cfg, s, t = SOURCES[name], store.src(name), time.monotonic()
        s["run_at"] = now()
        try:
            out = cfg["fn"](store, Http(pause=cfg["pause"], cookies=cfg.get("cookies", False)), full, t + budget)
            s.update(last_ok=now(), last=out)
            print(json.dumps({"source": name, "ok": True, "sec": round(time.monotonic() - t), **out}, ensure_ascii=False))
        except Exception as e:   # every failure is recorded in the state, where the freshness check finds it
            s.update(last_error=f"{type(e).__name__}: {e}"[:800], last_error_at=now())
            print(json.dumps({"source": name, "ok": False, "sec": round(time.monotonic() - t), "error": s["last_error"]},
                             ensure_ascii=False))
            rc = 1
        finally:   # written and dropped: a later source of this run never writes an old copy of this one's state
            store.save()
            store.state.clear()
    return rc


def main(argv):
    store = Store(os.environ.get("ARHIV_DIR", "/opt/tender/arhiv"))
    if argv[:1] == ["freshness"]:
        problems = freshness(store.all_states())
        print(json.dumps({"step": "freshness", "ok": not problems, "problems": problems}, ensure_ascii=False))
        return 1 if problems else 0
    if argv[:1] == ["summary"]:   # the weekly card: what the archive holds
        files = {}
        for d in sorted(p for p in store.root.iterdir() if p.is_dir() and p.name != "state"):
            sizes = [f.stat().st_size for f in d.rglob("*") if f.is_file()]
            files[d.name] = {"files": len(sizes), "mb": round(sum(sizes) / 1e6)}
        states = store.all_states()
        print(json.dumps({"step": "summary", "ok": True, "files": files,
                          "last_ok": {n: states.get(n, {}).get("last_ok") for n in SOURCES}}, ensure_ascii=False))
        return 0
    budget = int(argv[argv.index("--budget") + 1]) if "--budget" in argv else 3000
    names = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] != "--budget")]
    names = list(SOURCES) if names == ["all"] else names
    unknown = [n for n in names if n not in SOURCES]
    if unknown or not names:
        print(__doc__, "\nsources:", ", ".join(SOURCES), "\nunknown:", unknown, file=sys.stderr)
        return 2
    return run(store, names, "--full" in argv, budget)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
