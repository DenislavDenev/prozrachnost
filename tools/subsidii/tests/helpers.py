import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path

from ingest import archive, build, config, gold, load, parse, pipeline

FIX = Path(__file__).parent / "fixtures" / "dfz"
ARCH = {}     # what the archive would hold: {(fy, day): (raw, sha, path)}
RUN = None    # the run the loads of a test belong to (conftest sets it)


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


def put(c, fy, day, raw, now=None, archive_rows=None):
    """The file as the archive would hold it, through the same policy the step uses. `now` is when the archive read it."""
    day = dt.date.fromisoformat(day) if isinstance(day, str) else day
    sha = hashlib.sha256(raw).hexdigest()
    snap = archive.Snap(fy, day, sha[:12], f"dfz/{fy}/{day}.{sha[:12]}.csv")
    ARCH[(fy, day)] = (raw, sha, f"/archive/{snap.rel}")
    return build.build_one(c, RUN, snap, raw, sha, ARCH[(fy, day)][2], now=now or pipeline.utcnow(), archive_rows=archive_rows,
                           snaps=archive_snaps(), idx={}, reader=lambda s: (*ARCH[(s.fy, s.day)], None))


def archive_snaps():
    out = {}
    for (fy, day), (raw, sha, path) in sorted(ARCH.items()):
        out.setdefault(fy, []).append(archive.Snap(fy, day, sha[:12], path.removeprefix("/archive/")))
    return out


def built(c, *files):
    """Files written, their aggregates built: what the step dfz does before the rules and the publication."""
    for fy, day, raw in files:
        assert put(c, fy, day, raw)[0] == "built"
    load.load_units(c)
    out = gold.build_missing(c)
    assert not out["problems"], out
    return out


def publish(c):
    """The rules and the publication, as the steps end."""
    problems = pipeline.run_checks(c, RUN)
    assert not problems, problems
    done = pipeline.publish(c, RUN, config.ROOT)
    assert not done.problems, done.problems
    return done


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


def population(c, day, persons=10000):
    """The registrations of every municipality on `day`, as Население publishes them (pub_naselenie)."""
    c.execute("""INSERT INTO pub_naselenie.snapshot (as_of, table_kind, url) VALUES (%s, 'monthly', 'https://www.grao.bg/tna/t41ob.txt')
                 ON CONFLICT DO NOTHING""", (day,))
    c.execute("""INSERT INTO pub_naselenie.population (as_of, table_kind, municipality_code, permanent, current, both_here)
                 SELECT %s, 'monthly', municipality_code, %s, %s, 0 FROM pub.municipality ON CONFLICT DO NOTHING""", (day, persons, persons))
