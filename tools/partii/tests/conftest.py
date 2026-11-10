"""The test database: the foundation of tools/danni migrated by danni's own step, then the tables of Партии и пари.
Built once for the session; every test that takes `c` starts with them empty."""
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from ingest import config, pipeline

ROOT = Path(__file__).resolve().parent.parent
DANNI = ROOT.parent / "danni"   # the foundation is not copied into the tool: the tests need the sibling folder
SCHEMAS = ("api_v1", "pub_partii", "pub", "partii", "partii_src", "danni_src", "core", "ops")
TEST_DSN = os.environ.get("PARTII_TEST_DSN")


def rebuild(dsn, tmp):
    with pipeline.connect(dsn) as c:
        pipeline.require_test_db(c)
        for schema in SCHEMAS:
            c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        c.execute("DROP TABLE IF EXISTS public.schema_migrations")
    env = {**os.environ, "DANNI_DSN": dsn, "DANNI_ARCHIVE": str(tmp / "archive"), "DANNI_DATA": str(tmp / "danni")}
    subprocess.run([sys.executable, "-m", "ingest.run", "--step", "migrate"], cwd=DANNI, env=env, check=True, capture_output=True)
    with pipeline.connect(dsn) as c:
        pipeline.require_core(c, "0008")
        pipeline.migrate(c, "partii", ROOT)
    # danni's own step gives the datasets registered just now their right to be shown (its licence list) and publishes `pub`
    subprocess.run([sys.executable, "-m", "ingest.run", "--step", "check"], cwd=DANNI, env=env, capture_output=True)
    with pipeline.connect(dsn) as c:
        assert c.execute("SELECT bool_and(is_displayable) FROM core.dataset WHERE tool = 'partii'").fetchone()[0]


@pytest.fixture(scope="session")
def dsn(tmp_path_factory):
    if not TEST_DSN:
        pytest.skip("PARTII_TEST_DSN is not set: the database tests run on the server (ops/scripts/st.sh) and in CI")
    assert "test" in psycopg.conninfo.conninfo_to_dict(TEST_DSN).get("dbname", ""), "the test database name must contain 'test'"
    config.DSN = TEST_DSN      # the pages of the tests read the same database
    rebuild(TEST_DSN, tmp_path_factory.mktemp("foundation"))
    return TEST_DSN


def reset(conn):
    pipeline.require_test_db(conn)
    conn.execute("""TRUNCATE partii.observation, partii.participant_money, partii.unmatched, partii.agency, partii.report_label, partii.participant_label,
                    partii.report_column, partii.participant, partii.election, partii_src.report_cell, partii_src.report_row, partii_src.agency_use,
                    partii_src.candidate_fund, partii_src.donation, partii_src.participant_row, partii_src.answer, partii_src.page_fact,
                    partii_src.election_row RESTART IDENTITY CASCADE""")
    conn.execute("""TRUNCATE pub_partii.health, pub_partii.source_state, pub_partii.report_cell, pub_partii.report_row, pub_partii.agency_use, pub_partii.candidate_fund,
                    pub_partii.donation, pub_partii.reading, pub_partii.agency, pub_partii.unmatched, pub_partii.report_column, pub_partii.participant_money,
                    pub_partii.participant, pub_partii.election""")
    conn.execute("DELETE FROM pub.publication WHERE tool = 'partii'")
    conn.execute("UPDATE ops.check_rule SET is_active = true WHERE tool = 'partii'")
    for table in ("check_result", "change_log", "hold", "fetch", "load", "run"):
        conn.execute(f"DELETE FROM ops.{table} WHERE tool = 'partii'")
    # only this tool writes in the test database: the numbers of the loads start again, so two builds give the same tables
    conn.execute("ALTER TABLE ops.load ALTER COLUMN load_id RESTART")


def connect(dsn):
    conn = pipeline.connect(dsn)
    conn.execute("SET TIME ZONE 'UTC'")
    conn.execute("SET synchronous_commit = off")
    return conn


@pytest.fixture
def c(dsn):
    """A connection to a database emptied before the test (the tables stay)."""
    conn = connect(dsn)
    reset(conn)
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def built(dsn):
    """The whole fixture archive built once for a module of read-only tests."""
    from tests import helpers
    conn = connect(dsn)
    reset(conn)
    rep = helpers.build(conn, helpers.FIX)
    yield conn, rep
    conn.close()
