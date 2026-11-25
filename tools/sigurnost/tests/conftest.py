"""The test database: the foundation of tools/danni migrated and its places loaded from the real EKATTE archive by
danni's own step, then the tables of Сигурност. Built once for the session; every test that takes `c` starts with them empty.

Every connection of the tests goes to SIGURNOST_TEST_DSN. ingest.config reads SIGURNOST_DSN once, when the first test
file imports ingest, so the DSN is set on the module that every connection reads. These tests drop and fill schemas: the
name of the database must say it is a test one."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent
DANNI = ROOT.parent / "danni"   # the foundation is not copied into the tool: the tests need the sibling folder
SCHEMAS = ("api_v1", "pub_sigurnost", "pub", "sigurnost", "sigurnost_src", "danni_src", "core", "ops", "silver", "gold")
TEST_DSN = os.environ.get("SIGURNOST_TEST_DSN")


@pytest.fixture(scope="session", autouse=True)
def test_database():
    if TEST_DSN:
        assert "test" in psycopg.conninfo.conninfo_to_dict(TEST_DSN).get("dbname", ""), \
            f"refusing to use {TEST_DSN!r}: the test database name must contain 'test'"
        from ingest import config
        config.DSN = TEST_DSN
    yield


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
        pipeline.migrate(c, "sigurnost", ROOT)
    # danni again, now that the datasets of this tool are registered: their right to be shown comes from its licence list
    subprocess.run([sys.executable, "-m", "ingest.run", "--step", "check"], cwd=DANNI, env=env, capture_output=True)
    with pipeline.connect(dsn) as c:
        assert c.execute("SELECT bool_and(is_displayable) FROM core.dataset WHERE tool = 'sigurnost'").fetchone()[0]
        assert c.execute("SELECT count(*) FROM core.nuts WHERE level = 3").fetchone()[0] == 28


@pytest.fixture(scope="session")
def schema(test_database, tmp_path_factory):
    if not TEST_DSN:
        pytest.skip("SIGURNOST_TEST_DSN is not set: the database tests run on the server (ops/scripts/st.sh) and in CI")
    rebuild(TEST_DSN, tmp_path_factory.mktemp("foundation"))
    return TEST_DSN


def reset(conn):
    from ingest import gold, pipeline
    pipeline.require_test_db(conn)
    conn.execute("""TRUNCATE sigurnost.observation, sigurnost.crime_row, sigurnost.source_table, sigurnost.unmatched, sigurnost.reconciliation,
                    sigurnost.state_note, sigurnost.link, sigurnost.dataset, sigurnost_src.cell, sigurnost_src.row, sigurnost_src.col,
                    sigurnost_src.resource, sigurnost_src.list_entry CASCADE""")
    conn.execute("""TRUNCATE pub_sigurnost.observation, pub_sigurnost.crime_row, pub_sigurnost.source_table, pub_sigurnost.reconciliation,
                    pub_sigurnost.unmatched, pub_sigurnost.link, pub_sigurnost.dataset, pub_sigurnost.resource_note, pub_sigurnost.held,
                    pub_sigurnost.change, pub_sigurnost.state_note, pub_sigurnost.structure, pub_sigurnost.indicator""")
    conn.execute("DELETE FROM pub.publication WHERE tool = 'sigurnost'")
    conn.execute("UPDATE ops.check_rule SET is_active = true WHERE tool = 'sigurnost'")
    for table in ("check_result", "change_log", "hold", "fetch", "load", "run"):
        conn.execute(f"DELETE FROM ops.{table} WHERE tool = 'sigurnost'")
    # the numbers of the loads start again after those of the foundation (its places), so two builds give the same tables
    conn.execute("SELECT setval(pg_get_serial_sequence('ops.load', 'load_id'), (SELECT coalesce(max(load_id), 0) + 1 FROM ops.load), false)")
    gold.load_reference(conn)


def connect(dsn):
    from ingest import pipeline
    conn = pipeline.connect(dsn)
    conn.execute("SET TIME ZONE 'UTC'")
    conn.execute("SET synchronous_commit = off")
    return conn


@pytest.fixture
def c(schema):
    """A connection to a database emptied before the test (the tables stay)."""
    conn = connect(schema)
    reset(conn)
    yield conn
    conn.close()
