"""The archive in the shared register of the database `prozrachnost`.

The archive itself does not change: files, `index.jsonl` and `state/<source>.json` are written as before, because other
tools read them by path. What this adds:

- every line of `index.jsonl` is a row in `ops.fetch` (source, key, time) and its file a row in `ops.raw_file` (sha256,
  bytes, path), so a tool finds an archived file by its sha256 with a query. The file is hashed again before it is
  registered and must be the one the line names;
- every run of a source is a row in `ops.run`; the freshness check reads the last good and the last failed run there.

`sync()` registers the lines that are not in the register yet. It runs after every source and catches up on its own
after a run that could not reach the database, because the index is the record of what was written.
"""
import hashlib
import json
import mimetypes
from collections import Counter
from datetime import timezone
from pathlib import Path

from psycopg.types.json import Jsonb

from ingest import pipeline

TOOL = "nablyudatel"
ROOT = Path(__file__).resolve().parent.parent
# one sync at a time: two runs of different sources may end together, and each registers every missing line
SYNC_LOCK = int.from_bytes(hashlib.sha256(b"nablyudatel-register").digest()[:8], "big", signed=True)
STAMP = "%Y-%m-%dT%H:%M:%SZ"
BATCH = 500   # lines registered in one transaction


class BadIndex(ValueError):
    """A line of index.jsonl that is not what the archive writes."""


def stamp(t):
    return t.astimezone(timezone.utc).strftime(STAMP)


def index_lines(root):
    """The lines of index.jsonl. A last line without its newline is being written right now: it waits for the next sync."""
    p = Path(root) / "index.jsonl"
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    elif lines:
        lines.pop()   # cut: written while we read
    out = []
    for n, raw in enumerate(lines, 1):
        try:
            e = json.loads(raw)
            if not (isinstance(e, dict) and all(isinstance(e.get(k), str) for k in ("at", "source", "key", "file", "sha256"))
                    and isinstance(e.get("bytes"), int)):
                raise ValueError("fields")
        except ValueError as err:
            raise BadIndex(f"index.jsonl line {n} is not a record of the archive: {raw[:200]}") from err
        out.append(e)
    return out


def key(e):
    return e["source"], e["key"], e["at"], e["sha256"]


def registered(conn):
    """{(source, key, at, sha256): [bytes, ...]} of every fetch of the archive in the register."""
    out = {}
    for source, ref, at, sha, size in conn.execute('''SELECT f.source, f.ref, f.fetched_at, r.sha256, r.bytes
            FROM ops.fetch f JOIN ops.raw_file r USING (raw_file_id) WHERE f.tool = %s''', (TOOL,)):
        out.setdefault((source, ref, stamp(at), sha), []).append(size)
    return out


def sync(conn, r, root, rehash=True):
    """Register every line of the index that is not in the register yet. Returns how many were registered.

    With `rehash` (every run) the file is read and must have the sha256 of its line. Without it (the move: 51 GB read
    from tank at about 8 MB/s would take two hours) the sha256 of the line is taken, as the archive computed it when it
    wrote the file, and only the size is checked; the contents are compared by a sync with `rehash` in a test database."""
    root = Path(root)
    conn.execute("SELECT pg_advisory_lock(%s)", (SYNC_LOCK,))
    try:
        have = Counter({k: len(v) for k, v in registered(conn).items()})
        todo = []
        for e in index_lines(root):
            k = key(e)
            if have[k] > 0:
                have[k] -= 1
            else:
                todo.append(e)
        n = 0
        # one transaction per batch: a commit waits for the WAL on disk, one per line took 0.4 s on CT 118
        for i in range(0, len(todo), BATCH):
            with conn.transaction():
                for e in todo[i:i + BATCH]:
                    path = root / e["file"]
                    if path.stat().st_size != e["bytes"]:
                        raise pipeline.CorruptRaw(f'{path}: {path.stat().st_size} bytes, the index says {e["bytes"]}')
                    media = mimetypes.guess_type(path.name)[0]
                    if rehash:
                        pipeline.register_raw(conn, r, e["source"], e["key"], path, media_type=media, fetched_at=e["at"],
                                              expect_sha=e["sha256"])
                    else:   # the two steps of register_raw without reading the file
                        rid = pipeline._register(conn, e["sha256"], e["bytes"], path, media)
                        pipeline._fetch(conn, r, e["source"], e["key"], rid, e["at"], None)
            n += len(todo[i:i + BATCH])
            r.count("registered", len(todo[i:i + BATCH]))
        return n
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (SYNC_LOCK,))


# ---------------------------------------------------------------- runs and the state files

def run_states(conn, names):
    """The last good and the last failed run of each source, in the shape of state/<source>.json, so the freshness rule
    is the same one that read the state files."""
    out = {}
    for step, ok, err_at, err in conn.execute('''SELECT step,
              max(finished_at) FILTER (WHERE status = 'ok'),
              max(finished_at) FILTER (WHERE status = 'failed'),
              (array_agg(error ORDER BY finished_at DESC, run_id DESC) FILTER (WHERE status = 'failed'))[1]
            FROM ops.run WHERE tool = %s AND step = ANY(%s) GROUP BY step''', (TOOL, list(names))):
        s = {}
        if ok:
            s["last_ok"] = stamp(ok)
        if err_at:
            s.update(last_error=err, last_error_at=stamp(err_at))
        out[step] = s
    return out


def import_states(conn, root, names):
    """The last good and the last failed run that state/<source>.json remembers, as rows of ops.run (once per source).
    The state files have no older history."""
    n = 0
    for name in names:
        p = Path(root) / "state" / f"{name}.json"
        if not p.exists():
            continue
        if conn.execute("SELECT 1 FROM ops.run WHERE tool = %s AND step = %s AND params ? 'imported_from'", (TOOL, name)).fetchone():
            continue
        s = json.loads(p.read_text(encoding="utf-8"))
        rows = []
        if s.get("last_ok"):
            rows.append((s["last_ok"], "ok", None, s.get("last") or {}))
        if s.get("last_error_at"):
            rows.append((s["last_error_at"], "failed", s.get("last_error"), {}))
        for at, status, error, stats in sorted(rows, key=lambda x: x[0]):   # run_id follows time
            conn.execute('''INSERT INTO ops.run (tool, step, started_at, finished_at, status, params, stats, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''',
                         (TOOL, name, at, at, status, Jsonb({"imported_from": f"state/{name}.json"}), Jsonb(stats), error))
            n += 1
    return n


# ---------------------------------------------------------------- parity

def compare(conn, root, before=None):
    """Every line of the index against the register: (lines, matched, missing, extra, size_differs).
    `before` (a stamp) leaves out the lines written from then on: a run may still be registering them."""
    lines = [e for e in index_lines(root) if before is None or e["at"] < before]
    reg = registered(conn)
    if before is not None:
        reg = {k: v for k, v in reg.items() if k[2] < before}
    left = {k: list(v) for k, v in reg.items()}
    matched = missing = size_differs = 0
    for e in lines:
        sizes = left.get(key(e))
        if not sizes:
            missing += 1
        elif e["bytes"] in sizes:
            sizes.remove(e["bytes"])
            matched += 1
        else:
            sizes.pop()
            size_differs += 1
    extra = sum(len(v) for v in left.values())
    return {"lines": len(lines), "matched": matched, "missing": missing, "extra": extra, "size_differs": size_differs}


def compare_state(conn, root):
    """Every key that state/<source>.json remembers: its newest answer is the newest fetch of that key in the register."""
    newest = {(s, ref): sha for s, ref, sha in conn.execute('''SELECT DISTINCT ON (f.source, f.ref) f.source, f.ref, r.sha256
            FROM ops.fetch f JOIN ops.raw_file r USING (raw_file_id) WHERE f.tool = %s
            ORDER BY f.source, f.ref, f.fetched_at DESC, f.fetch_id DESC''', (TOOL,))}
    keys = same = 0
    for p in sorted((Path(root) / "state").glob("*.json")):
        for ref, v in json.loads(p.read_text(encoding="utf-8")).get("seen", {}).items():
            keys += 1
            same += newest.get((p.stem, ref)) == v.get("sha")
    return {"keys": keys, "same": same}
