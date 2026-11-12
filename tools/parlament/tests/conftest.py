"""The test database: the foundation of tools/danni migrated, with its places loaded from the real EKATTE archive by
danni's own step, then the tables of Парламент. Built once for the session; every database test starts with the
tables of Парламент empty and runs inside a step of its own (db.job), as the steps of n8n do.

Every connection goes to PARLAMENT_TEST_DSN (its name must contain "test"). ingest.config reads PARLAMENT_DSN once,
when the first test file imports it, so the DSN is set on the modules that connect, not in the environment. The heavy
lock of CT 118 is not taken by a test."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent
DANNI = ROOT.parent / "danni"   # the foundation is not copied into the tool: the tests need the sibling folder
SCHEMAS = ("api_v1", "pub_parlament", "pub", "parlament", "parlament_src", "danni_src", "core", "ops", "live", "old_ops")
CLEAN = ("assembly", "sitting", "item", "item_group", "mp", "vote", "roster", "line", "mp_stat", "body", "profile",
         "profile_past", "membership", "absence", "penalty", "speech", "bill", "bill_sponsor", "bill_committee",
         "bill_step", "bill_item")
SERVING = ("assembly", "sitting", "item", "item_group", "mp", "vote", "line", "mp_stat", "body", "profile", "membership",
           "absence", "penalty", "speech", "bill", "bill_sponsor", "bill_committee", "bill_step", "bill_item", "read_state")
DSN = os.environ.get("PARLAMENT_TEST_DSN")


def rebuild(dsn, tmp):
    from ingest import pipeline
    with pipeline.connect(dsn) as c:
        pipeline.require_test_db(c)
        for schema in SCHEMAS:
            c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        c.execute("DROP TABLE IF EXISTS public.schema_migrations")
    (tmp / "archive" / "ekatte").mkdir(parents=True)
    shutil.copy(DANNI / "tests" / "fixtures" / "ekatte.zip", tmp / "archive" / "ekatte" / "2026-09-29.002862bf0655.zip")
    env = {**os.environ, "DANNI_DSN": dsn, "DANNI_ARCHIVE": str(tmp / "archive"), "DANNI_DATA": str(tmp / "danni")}
    for step in ("migrate", "place"):
        subprocess.run([sys.executable, "-m", "ingest.run", "--step", step], cwd=DANNI, env=env, check=True, capture_output=True)
    with pipeline.connect(dsn) as c:
        pipeline.require_core(c, "0009")
        pipeline.migrate(c, "parlament", ROOT)
    # danni again, now that the datasets of this tool are registered: their right to be shown comes from the licence
    # list of danni, and the persons from its list of decisions
    subprocess.run([sys.executable, "-m", "ingest.run", "--step", "check"], cwd=DANNI, env=env, capture_output=True)
    with pipeline.connect(dsn) as c:
        assert c.execute("SELECT bool_and(is_displayable) FROM core.dataset WHERE tool = 'parlament'").fetchone()[0]


@pytest.fixture(scope="session")
def dsn(tmp_path_factory):
    if not DSN:
        pytest.skip("PARLAMENT_TEST_DSN required")
    assert "test" in psycopg.conninfo.conninfo_to_dict(DSN).get("dbname", ""), f"refusing to use {DSN!r}"
    from app import queries
    from ingest import db
    db.DSN, db.HEAVY_LOCK, queries.DSN = DSN, "", DSN
    rebuild(DSN, tmp_path_factory.mktemp("foundation"))
    return DSN


def wipe(c):
    """The tables of Парламент empty, its rows of the shared ledger gone, and no old database."""
    from ingest import pipeline
    pipeline.require_test_db(c)
    c.execute("DROP SCHEMA IF EXISTS live CASCADE; DROP SCHEMA IF EXISTS old_ops CASCADE")
    c.execute("TRUNCATE " + ", ".join([f"parlament.{t}" for t in CLEAN] + ["parlament_src.read_state"]
                                      + [f"pub_parlament.{t}" for t in SERVING]))
    c.execute("DELETE FROM pub.publication WHERE tool = 'parlament'")
    c.execute("UPDATE ops.check_rule SET is_active = true WHERE tool = 'parlament'")
    for table in ("check_result", "change_log", "hold", "fetch", "load", "run"):
        c.execute(f"DELETE FROM ops.{table} WHERE tool = 'parlament'")


@pytest.fixture
def conn(dsn, tmp_path, monkeypatch):
    """An empty Парламент, inside a step of its own."""
    from ingest import db, load
    monkeypatch.setattr(load, "RAW", tmp_path / "raw")
    with db.connect() as c:
        wipe(c)
        with db.job("test", conn=c):
            yield c
