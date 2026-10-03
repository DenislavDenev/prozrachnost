import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path

from ingest import archive, build, parse

FIX = Path(__file__).parent / "fixtures" / "dfz"
ARCH = {}     # what the archive would hold: {(fy, day): (raw, sha, path)}


def fixture(fy):
    return (FIX / f"fy{fy}.csv").read_bytes()


def rows_of(raw):
    return list(csv.reader(io.StringIO(raw.decode("cp1251"), newline=""), delimiter=";"))


def csv_of(rows):
    """Bytes of the file as the source writes it: cp1251, every cell quoted, a line feed ends each line."""
    buf = io.StringIO()
    csv.writer(buf, delimiter=";", quoting=csv.QUOTE_ALL, lineterminator="\n").writerows(rows)
    return buf.getvalue().encode("cp1251")


def derive(fy, drop=(), edit=None, keep=None):
    """A new file from a real fixture: without some blocks (by the number of the ОБЩО row), or with rows edited by a function."""
    rows = rows_of(fixture(fy))
    head, body = rows[0], rows[1:]
    blocks = []
    for r in body:
        if r[parse.MEASURE] == parse.TOTAL_WORD:
            blocks.append([r])
        else:
            blocks[-1].append(r)
    out = [head]
    for i, b in enumerate(blocks):
        if i in drop or (keep is not None and i not in keep):
            continue
        for r in b:
            r = list(r)
            if edit:
                edit(i, r)
            out.append(r)
    return csv_of(out)


def put(c, fy, day, raw, now=None, st=None, archive_rows=None):
    """The file as the archive would hold it, through the same policy the step uses."""
    day = dt.date.fromisoformat(day) if isinstance(day, str) else day
    sha = hashlib.sha256(raw).hexdigest()
    snap = archive.Snap(fy, day, sha[:12], f"dfz/{fy}/{day}.{sha[:12]}.csv")
    ARCH[(fy, day)] = (raw, sha, f"/archive/{snap.rel}")
    return build.build_one(c, snap, raw, sha, ARCH[(fy, day)][2], st=st, now=now, archive_rows=archive_rows, snaps=archive_snaps(), reader=lambda s: ARCH[(s.fy, s.day)])


def archive_snaps():
    out = {}
    for (fy, day), (raw, sha, path) in sorted(ARCH.items()):
        out.setdefault(fy, []).append(archive.Snap(fy, day, sha[:12], path.removeprefix("/archive/")))
    return out


def archive_dir(tmp_path, files, last_ok="2026-10-02T00:40:00Z", form=(2024, 2025)):
    """A tiny archive as Наблюдател keeps it: {(fy, day): raw bytes} -> dfz/<fy>/<day>.<sha12>.csv, index.jsonl, state/dfz.json."""
    idx, seen = [], {}
    for (fy, day), raw in sorted(files.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        sha = hashlib.sha256(raw).hexdigest()
        rel = f"dfz/{fy}/{day}.{sha[:12]}.csv"
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(raw)
        idx.append({"at": f"{day}T00:31:00Z", "source": "dfz", "key": f"fy{fy}", "file": rel, "sha256": sha, "bytes": len(raw)})
        seen[f"fy{fy}"] = {"sha": sha, "file": rel, "at": f"{day}T00:31:00Z"}
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "index.jsonl").write_text("".join(json.dumps(e) + "\n" for e in idx), encoding="utf-8")
    last = {str(fy): {"rows": rows_count(files, fy), "new": True} for fy in form}
    (tmp_path / "state" / "dfz.json").write_text(json.dumps({"seen": seen, "run_at": last_ok, "last_ok": last_ok, "last": last}), encoding="utf-8")
    return tmp_path


def rows_count(files, fy):
    newest = [raw for (f, d), raw in sorted(files.items(), key=lambda kv: str(kv[0][1])) if f == fy]
    return newest[-1].count(b"\n") - 1 if newest else 0


def count(c, sql, *a):
    return c.execute(sql, a).fetchone()[0]
