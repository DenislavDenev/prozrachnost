"""The test database: the foundation of tools/danni migrated with its places loaded from the real EKATTE archive by
danni's own step, then the tables of Закони и решения. Built once for the session; every database test starts with the
tables of Закони empty and with an archive of its own (the real fixtures of the data set)."""
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from ingest import pipeline

ROOT = Path(__file__).resolve().parent.parent
DANNI = ROOT.parent / "danni"            # the foundation is not copied into the tool: the tests need the sibling folder
FX = Path(__file__).parent / "fixtures" / "egov"
SET = "18da0fff-79b2-45c6-a9af-1509df96261b"
T0 = dt.datetime(2026, 10, 2, 5, 0, tzinfo=dt.timezone.utc)
SCHEMAS = ("api_v1", "pub_zakoni", "pub", "zakoni", "zakoni_src", "danni_src", "core", "ops", "silver", "gold", "ref", "old_ops")
TABLES = ("zakoni.act_institution", "zakoni.act_relation", "zakoni.act", "zakoni.consultation_file", "zakoni.consultation",
          "zakoni.strategy_doc", "zakoni.impact_contract", "zakoni.unmatched", "zakoni.source_state",
          "zakoni_src.pris_act", "zakoni_src.pris_institution", "zakoni_src.pris_tag", "zakoni_src.pris_related",
          "zakoni_src.consultation", "zakoni_src.consultation_file", "zakoni_src.strategy_doc", "zakoni_src.strategy_author",
          "zakoni_src.strategy_file", "zakoni_src.strategy_sub", "zakoni_src.impact_contract", "zakoni_src.institution_municipality",
          "pub_zakoni.act_institution", "pub_zakoni.act_relation", "pub_zakoni.act_change", "pub_zakoni.act",
          "pub_zakoni.consultation_file", "pub_zakoni.consultation", "pub_zakoni.strategy_doc", "pub_zakoni.source_state",
          "pub_zakoni.reconciliation")


def rebuild(dsn, tmp):
    with pipeline.connect(dsn) as c:
        pipeline.require_test_db(c)
        for schema in SCHEMAS:
            c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    (tmp / "archive" / "ekatte").mkdir(parents=True)
    shutil.copy(DANNI / "tests" / "fixtures" / "ekatte.zip", tmp / "archive" / "ekatte" / "2026-09-29.002862bf0655.zip")
    env = {**os.environ, "DANNI_DSN": dsn, "DANNI_ARCHIVE": str(tmp / "archive"), "DANNI_DATA": str(tmp / "danni")}
    for step in ("migrate", "place"):
        subprocess.run([sys.executable, "-m", "ingest.run", "--step", step], cwd=DANNI, env=env, check=True, capture_output=True)
    with pipeline.connect(dsn) as c:
        pipeline.require_core(c, "0009")
        pipeline.migrate(c, "zakoni", ROOT)
    # danni again, now that the dataset of this tool is registered: its right to be shown comes from the licence list of
    # danni; the check step also publishes the places in pub
    subprocess.run([sys.executable, "-m", "ingest.run", "--step", "check"], cwd=DANNI, env=env, capture_output=True)
    with pipeline.connect(dsn) as c:
        assert c.execute("SELECT bool_and(is_displayable) FROM core.dataset WHERE tool = 'zakoni'").fetchone()[0]
        assert c.execute("SELECT count(*) FROM pub.municipality").fetchone()[0] == 265


@pytest.fixture(scope="session")
def dsn(tmp_path_factory):
    dsn = os.environ.get("ZAKONI_TEST_DSN")
    if not dsn:
        pytest.skip("ZAKONI_TEST_DSN required")
    assert "test" in psycopg.conninfo.conninfo_to_dict(dsn).get("dbname", "")
    rebuild(dsn, tmp_path_factory.mktemp("foundation"))
    return dsn


@pytest.fixture
def conn(dsn, tmp_path, monkeypatch):
    from ingest import config, ref, run
    monkeypatch.setattr(ref, "RAW", tmp_path / "raw")
    monkeypatch.setattr(run, "DSN", dsn)
    monkeypatch.setattr(config, "DSN", dsn)
    from app import queries
    monkeypatch.setattr(queries, "DSN", dsn)
    with pipeline.connect(dsn) as c:
        pipeline.require_test_db(c)
        for schema in ("silver", "gold", "ref", "old_ops"):
            c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        c.execute(f"TRUNCATE {', '.join(TABLES)}")
        c.execute("DELETE FROM pub.publication WHERE tool = 'zakoni'")
        c.execute("UPDATE ops.check_rule SET is_active = true WHERE tool = 'zakoni'")
        for table in ("check_result", "change_log", "hold", "fetch", "load", "run"):
            c.execute(f"DELETE FROM ops.{table} WHERE tool = 'zakoni'")
        yield c


# ---------------------------------------------------------------- an archive of its own, from the real fixtures

def put(root, res, data: bytes, version="7"):
    d = root / "egov" / SET / res
    for old in d.glob("*.json") if d.is_dir() else []:
        old.unlink()
    d.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(data).hexdigest()
    (d / f"{version}.{sha[:12]}.json").write_bytes(data)


def set_state(root, when):
    (root / "state" / "egov.json").write_text(json.dumps({"last_ok": when.strftime("%Y-%m-%dT%H:%M:%SZ")}))


@pytest.fixture
def arch(tmp_path, monkeypatch):
    from ingest import archive
    root = tmp_path / "arhiv"
    for f in FX.glob("*.json"):
        if f.name == "_list.json":
            (root / "egov" / SET / "_list").mkdir(parents=True, exist_ok=True)
            (root / "egov" / SET / "_list" / f"2026-10-02.{hashlib.sha256(f.read_bytes()).hexdigest()[:12]}.json").write_bytes(f.read_bytes())
        else:
            put(root, f.stem, f.read_bytes())
    (root / "state").mkdir(parents=True)
    set_state(root, T0)
    monkeypatch.setattr(archive, "SET_DIR", root / "egov" / SET)
    monkeypatch.setattr(archive, "STATE_FILE", root / "state" / "egov.json")
    from ingest import move
    monkeypatch.setattr(move, "ARCHIVE", root)
    return root


def build(conn, now=T0, force=False, fresh=True, step="build"):
    """One `build` step at `now`: the archive read at that moment, then the clean tables, the rules and the publication.
    -> (problems, stats)."""
    from ingest import archive, run
    if fresh:     # the archive reads the source every day: its clock moves with ours
        archive.STATE_FILE.write_text(json.dumps({"last_ok": now.strftime("%Y-%m-%dT%H:%M:%SZ")}))
    with pipeline.run(conn, "zakoni", step) as r:
        problems = run.finish(conn, r, run.read_archive(conn, r, force, clock=now))
    return problems, r.stats


def count(conn, sql, args=()):
    return conn.execute(sql, args).fetchone()[0]


def edit(root, res, fn):
    """Rewrite one fixture answer with fn(records) -> records, keeping the legend."""
    p = next((root / "egov" / SET / res).glob("*.json"))
    d = json.loads(p.read_bytes())
    d["data"] = fn(d["data"])
    put(root, res, json.dumps(d, ensure_ascii=True, separators=(",", ":")).encode())
