"""The archive's command line (what the step script runs on the server).

    python -m archive.run kolkostruva erik ...   # these sources; `all` for every one
        --full            # ЕРИК: every election, not only the newest; Колко струва: ask again for old missing days
        --budget SECONDS  # stop a long source in time; the next run goes on from where this one stopped (default 3000)
    python -m archive.run freshness            # registers what is missing, then exit 1 with the problems
    python -m archive.run summary              # files and MB per source, the last good read of each
    python -m archive.run migrate              # the sources and rules of the archive in the shared database
    python -m archive.run replay               # the whole index and the state files into the register (the move)
    python -m archive.run parity               # every line of the index against the register

ARHIV_DIR is where the archive lives. NABLYUDATEL_DSN is the shared database (default dbname=prozrachnost); every run of a
source is a row in ops.run and every new line of the index a row in ops.fetch and ops.raw_file. When the database cannot
be reached the sources are still read and stored, the run exits 1, and the next run registers what is missing.
One JSON line per source on stdout; exit 1 when a source failed.
"""
import contextlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from .core import Http, Store, now
from .sources import SOURCES


class Failed(Exception):
    """A source that failed, with the message its state file keeps; ops.run gets the same text."""

    def __repr__(self):
        return str(self.args[0])


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


def run(store, names, full=False, budget=3000, conn=None):
    rc = 0
    for name in names:
        cfg, s, t = SOURCES[name], store.src(name), time.monotonic()
        s["run_at"] = now()
        ledger = contextlib.nullcontext()
        if conn is not None:
            from . import register
            ledger = register.pipeline.run(conn, register.TOOL, name, {"full": full, "budget": budget},
                                           register.pipeline.code_sha(register.ROOT))
        try:
            with ledger as r:
                try:
                    out = cfg["fn"](store, Http(pause=cfg["pause"], cookies=cfg.get("cookies", False), absent=cfg.get("absent", (404, 410))), full, t + budget)
                except Exception as e:
                    raise Failed(f"{type(e).__name__}: {e}"[:800]) from e
                finally:   # what was read is in the files and the index before the register is touched
                    store.save()
                s.update(last_ok=now(), last=out)
                if r is not None:
                    try:
                        out = {**out, "registered": register.sync(conn, r, store.root)}
                    except Exception as e:
                        raise Failed(f"register: {type(e).__name__}: {e}"[:800]) from e
            print(json.dumps({"source": name, "ok": True, "sec": round(time.monotonic() - t), **out}, ensure_ascii=False))
        except Exception as e:   # every failure is recorded in the state and in ops.run, where the freshness check finds it
            if conn is not None and type(e).__name__ == "Busy":
                print(json.dumps({"source": name, "skipped": str(e)}, ensure_ascii=False))
                continue
            s.update(last_error=str(e)[:800] if isinstance(e, Failed) else f"{type(e).__name__}: {e}"[:800], last_error_at=now())
            print(json.dumps({"source": name, "ok": False, "sec": round(time.monotonic() - t), "error": s["last_error"]},
                             ensure_ascii=False))
            rc = 1
        finally:   # written and dropped: a later source of this run never writes an old copy of this one's state
            store.save()
            store.state.clear()
    return rc


def check_freshness(conn, store, at=None):
    """The problems: a source that failed or is late (from ops.run), a line of the index that is not in the register
    once the missing ones were registered, a rule of the archive that failed."""
    from . import register
    at = at or datetime.now(timezone.utc)
    if not (store.root / "index.jsonl").exists():
        return [f"{store.root}: no index.jsonl, the archive is empty or elsewhere"]
    start = register.stamp(datetime.now(timezone.utc) - timedelta(minutes=1))
    with register.pipeline.run(conn, register.TOOL, "register", sha=register.pipeline.code_sha(register.ROOT)) as r:
        register.sync(conn, r, store.root)
    problems = freshness(register.run_states(conn, SOURCES), at)
    c = register.compare(conn, store.root, before=start)
    if c["missing"] or c["extra"] or c["size_differs"]:
        problems.append(f"register: {c['missing']} lines of the index not in ops.fetch, {c['extra']} fetches not in the index, "
                        f"{c['size_differs']} with another size")
    problems += [f"{title}: expected {e}, got {a}" for title, e, a in conn.execute(
        "SELECT title_bg, expected, actual FROM ops.check_latest WHERE tool = %s AND gating AND ok = false ORDER BY name",
        (register.TOOL,))]
    return problems


def parity(conn, store):
    """The proof of the move: every line of every index, every key of every state file and the freshness against the
    register, recorded in ops.check_result as rules of kind parity."""
    from . import register
    with register.pipeline.run(conn, register.TOOL, "parity", sha=register.pipeline.code_sha(register.ROOT)) as r:
        c = register.compare(conn, store.root)
        st = register.compare_state(conn, store.root)
        old = {p.split(" ", 1)[0] for p in freshness(store.all_states())}
        new = {p.split(" ", 1)[0] for p in freshness(register.run_states(conn, SOURCES))}
        ok = all([register.pipeline.record_check(conn, r, "index_in_register", c["lines"], c["matched"], json.dumps(c)),
                  register.pipeline.record_check(conn, r, "register_in_index", 0, c["extra"] + c["size_differs"], json.dumps(c)),
                  register.pipeline.record_check(conn, r, "state_in_register", st["keys"], st["same"], json.dumps(st)),
                  register.pipeline.record_check(conn, r, "freshness_same", 0, len(old ^ new),
                                                 json.dumps({"state": sorted(old), "register": sorted(new)}))])
        r.stats.update(index=c, state=st, freshness={"state": sorted(old), "register": sorted(new)})
        return ok, {**c, "state": st, "freshness_state": sorted(old), "freshness_register": sorted(new)}


def main(argv):
    store = Store(os.environ.get("ARHIV_DIR", "/srv/prozrachnost/nablyudatel"))
    dsn = os.environ.get("NABLYUDATEL_DSN", "dbname=prozrachnost")
    if argv[:1] == ["summary"]:   # the weekly card: what the archive holds
        files = {}
        for d in sorted(p for p in store.root.iterdir() if p.is_dir() and p.name != "state"):
            sizes = [f.stat().st_size for f in d.rglob("*") if f.is_file()]
            files[d.name] = {"files": len(sizes), "mb": round(sum(sizes) / 1e6)}
        states = store.all_states()
        print(json.dumps({"step": "summary", "ok": True, "files": files,
                          "last_ok": {n: states.get(n, {}).get("last_ok") for n in SOURCES}}, ensure_ascii=False))
        return 0
    if argv[:1] in (["freshness"], ["migrate"], ["replay"], ["parity"]):
        from . import register
        step = argv[0]
        try:
            if not dsn:
                raise RuntimeError("NABLYUDATEL_DSN is empty: this step needs the shared database")
            with register.pipeline.connect(dsn) as conn:
                if step == "freshness":
                    problems = check_freshness(conn, store)
                    result = {"step": step, "ok": not problems, "problems": problems}
                elif step == "migrate":
                    register.pipeline.require_core(conn, "0008")
                    result = {"step": step, "ok": True, "migrations": register.pipeline.migrate(conn, register.TOOL, register.ROOT)}
                elif step == "replay":
                    with register.pipeline.run(conn, register.TOOL, "replay", sha=register.pipeline.code_sha(register.ROOT)) as r:
                        runs = register.import_states(conn, store.root, SOURCES)
                        result = {"step": step, "ok": True, "runs_imported": runs, "registered": register.sync(conn, r, store.root)}
                else:
                    ok, out = parity(conn, store)
                    result = {"step": step, "ok": ok, **out}
        except Exception as e:   # the database cannot be reached, a file does not match its line: a problem, exit 1
            result = {"step": step, "ok": False, "problems": [f"{type(e).__name__}: {e}"[:800]]}
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1
    budget = int(argv[argv.index("--budget") + 1]) if "--budget" in argv else 3000
    names = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] != "--budget")]
    names = list(SOURCES) if names == ["all"] else names
    unknown = [n for n in names if n not in SOURCES]
    if unknown or not names:
        print(__doc__, "\nsources:", ", ".join(SOURCES), "\nunknown:", unknown, file=sys.stderr)
        return 2
    conn, rc = None, 0
    if dsn:
        try:
            from . import register
            conn = register.pipeline.connect(dsn)
        except Exception as e:   # the archive's first job is to keep the answer: read the sources anyway
            print(json.dumps({"step": "register", "ok": False, "error": f"{type(e).__name__}: {e}"[:800]}, ensure_ascii=False))
            rc = 1
    try:
        return max(rc, run(store, names, "--full" in argv, budget, conn))
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
