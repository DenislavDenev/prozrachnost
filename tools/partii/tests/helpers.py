"""The test archive: a copy of tests/fixtures (real answers of ЕРИК, see tools/cut_fixtures.py) that a test can add answers to."""
import datetime as dt
import hashlib
import json
import shutil
from pathlib import Path

from ingest import archive

FIX = Path(__file__).resolve().parent / "fixtures"
ERIK = "https://erik.bulnao.government.bg"
DONORS = "POST " + ERIK + "/Reports/GetDonorsByElectionId?draw=1&start=0&length=-1&electionId={}&isOldSystemElection=true"


def entries(table=None, erik_id=None, variant=None, root=FIX):
    return [e for e in archive.entries(root) if (table is None or e["table"] == table) and (erik_id is None or e["erik_id"] == erik_id)
            and (variant is None or e["variant"] == variant)]


def raw(table, erik_id=None, variant=None, which=-1):
    """The bytes of a fixture answer (the newest of its key by default)."""
    return archive.read(entries(table, erik_id, variant)[which], FIX)


def js(table, erik_id=None, variant=None, which=-1):
    return json.loads(raw(table, erik_id, variant, which))


def dump(d):
    return json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode()


def copy(tmp_path, keep=None):
    """A writable archive with the fixture answers `keep` chooses (all by default)."""
    root = tmp_path / "archive"
    lines = [json.loads(x) for x in (FIX / "index.jsonl").read_text(encoding="utf-8").splitlines()]
    st = json.loads((FIX / "state" / "erik.json").read_text(encoding="utf-8"))
    kept = []
    for line in lines:
        what = archive.classify(line["key"])
        if keep and not keep(dict(line, table=what[0], erik_id=what[1], variant=what[2])):
            continue
        (root / line["file"]).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIX / line["file"], root / line["file"])
        kept.append(line)
    (root / "state").mkdir(parents=True, exist_ok=True)
    (root / "index.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in kept), encoding="utf-8")
    keys = {x["key"]: x for x in kept}
    (root / "state" / "erik.json").write_text(json.dumps({"seen": {k: {"sha": v["sha256"], "file": v["file"], "at": v["at"]} for k, v in keys.items()},
                                                          "last_ok": st["last_ok"], "last": st.get("last", {"elections": 21})}, ensure_ascii=False), encoding="utf-8")
    return root


def stamp(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def add(root, key, data, at, ext="json", last_ok=None):
    """Adds an answer to the archive the way Наблюдател does: the file, the line of the index, the state."""
    sha = archive.page_sha(data) if ext == "html" else hashlib.sha256(data).hexdigest()
    what = archive.classify(key)
    rel = f"erik/{what[1] or ''}/t_{sha[:12]}.{ext}".replace("//", "/")
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(data)
    with (root / "index.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": stamp(at), "source": "erik", "key": key, "file": rel, "sha256": sha, "bytes": len(data)}, ensure_ascii=False) + "\n")
    p = root / "state" / "erik.json"
    st = json.loads(p.read_text(encoding="utf-8"))
    st["seen"][key] = {"sha": sha, "file": rel, "at": stamp(at)}
    st["last_ok"] = stamp(last_ok or at)
    p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    return sha


def read_again(root, at, full=False):
    """The archive read the source again at `at` and nothing changed: the daily elections, or all of them (`full`, on Sunday)."""
    p = root / "state" / "erik.json"
    st = json.loads(p.read_text(encoding="utf-8"))
    st["last_ok"] = stamp(at)
    st["last"] = {"elections": 21 if full else 6}
    p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def donors_without(d, *rows):
    """The same answer without the given rows (1-based), with the counter and the totals the source would then give."""
    out = dict(d, data=[r for i, r in enumerate(d["data"], start=1) if i not in rows])
    out["recordsTotal"] = out["recordsFiltered"] = len(out["data"])
    out["additionalParameters"] = {"TotalDonationPrice": sum(r["donationPrice"] for r in out["data"]), "TotalDonationValue": sum(r["donationValue"] for r in out["data"])}
    return out


def utc(y, m, d, h=1):
    return dt.datetime(y, m, d, h, tzinfo=dt.timezone.utc)


def build(c, root, any_age=True):
    """One run of the step `erik` over the archive `root`."""
    from ingest import pipeline, run
    with pipeline.run(c, "partii", "erik") as r:
        return run.erik(c, r, any_age=any_age, root=root)


def public_hash(c):
    """One hash of every row of the landing zone and the clean tables (not of ops, which holds the times of the runs)."""
    h = hashlib.sha256()
    tables = [r[0] for r in c.execute("""SELECT table_schema || '.' || table_name FROM information_schema.tables
                                         WHERE table_schema IN ('partii', 'partii_src') AND table_type = 'BASE TABLE' ORDER BY 1""")]
    for t in tables:
        cols = [r[0] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema || '.' || table_name = %s ORDER BY ordinal_position", (t,))]
        for row in c.execute(f"SELECT {', '.join(cols)} FROM {t} ORDER BY {', '.join(str(i + 1) for i in range(len(cols)))}"):
            h.update(repr(row).encode())
    return h.hexdigest()


def all_text(c):
    """Every text value this tool wrote, for the search for a person's name."""
    out = []
    for schema, table, col in c.execute("""SELECT table_schema, table_name, column_name FROM information_schema.columns
                                           WHERE table_schema IN ('partii', 'partii_src') AND data_type IN ('text', 'jsonb', 'character varying')""").fetchall():
        out += [str(r[0]) for r in c.execute(f"SELECT DISTINCT {col}::text FROM {schema}.{table} WHERE {col} IS NOT NULL")]
    out += [" ".join(str(x) for x in r) for r in c.execute("SELECT field, old, new FROM ops.change_log WHERE tool = 'partii'")]
    out += [str(r[0]) for r in c.execute("SELECT reason FROM ops.load WHERE tool = 'partii' AND reason IS NOT NULL")]
    return "\n".join(out)
