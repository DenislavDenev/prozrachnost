import os

import pytest

TEST_DSN = os.environ.get("SIGURNOST_TEST_DSN")


@pytest.fixture(scope="session", autouse=True)
def test_database():
    """Every connection of the tests goes to SIGURNOST_TEST_DSN. ingest.config reads SIGURNOST_DSN once, when the first test
    file imports ingest, so the DSN is set on the modules that connect. These tests drop and fill tables: the name
    of the database must say it is a test one."""
    if TEST_DSN:
        assert "test" in TEST_DSN, f"refusing to use {TEST_DSN!r}: the test database name must contain 'test'"
        from ingest import config, db
        config.DSN = TEST_DSN
        db.config.DSN = TEST_DSN
        try:
            from app import main
            main.DSN = TEST_DSN
        except ImportError:
            pass
    yield


@pytest.fixture(scope="session")
def schema(test_database):
    """A fresh migrated database for the session."""
    if not TEST_DSN:
        pytest.skip("SIGURNOST_TEST_DSN is not set: the database tests run on the server (ops/scripts/sg.sh)")
    from ingest import db
    c = db.connect(autocommit=True)
    for s in ("ops", "silver", "gold"):
        c.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")
    c.execute("DROP TABLE IF EXISTS public.schema_migrations")
    db.migrate(c)
    from ingest import gold
    gold.load_reference(c)
    c.close()


@pytest.fixture
def c(schema):
    """A connection to a database emptied before the test (the tables stay)."""
    from ingest import db
    conn = db.connect(autocommit=True)
    reset(conn)
    yield conn
    conn.close()


def reset(conn):
    conn.execute("TRUNCATE silver.dataset, silver.link, silver.resource, silver.col, silver.row, silver.cell CASCADE")
    conn.execute("TRUNCATE ops.raw_file, ops.change_log, ops.held, ops.job_run RESTART IDENTITY")
    conn.execute("TRUNCATE gold.observation, gold.crime_row, gold.source_table, gold.unmatched, gold.check_result, gold.build RESTART IDENTITY")
