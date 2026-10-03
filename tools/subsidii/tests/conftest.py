import os

import pytest

TEST_DSN = os.environ.get("SUBSIDII_TEST_DSN")


@pytest.fixture(scope="session", autouse=True)
def test_database():
    """Every connection of the tests goes to SUBSIDII_TEST_DSN. ingest.config reads SUBSIDII_DSN once, when the first test
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
        pytest.skip("SUBSIDII_TEST_DSN is not set: the database tests run on the server (ops/scripts/sb.sh)")
    from ingest import db, gold, load
    c = db.connect(autocommit=True)
    for s in ("ops", "silver", "stage", "gold"):
        c.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")
    c.execute("DROP TABLE IF EXISTS public.schema_migrations")
    db.migrate(c)
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
    from tests import helpers
    helpers.ARCH.clear()
    conn.execute("SELECT set_config('subsidii.today', '', false)")
    conn.execute("""TRUNCATE silver.block_diff, silver.payment, silver.holder, silver.beneficiary, silver.measure, silver.snapshot,
                    silver.fiscal_year, silver.population RESTART IDENTITY CASCADE""")
    conn.execute("TRUNCATE ops.raw_file, ops.change_log, ops.held, ops.job_run RESTART IDENTITY")
    conn.execute("""TRUNCATE gold.observation, gold.payment_agg, gold.municipality_fy, gold.summary, gold.unmatched, gold.place_match, gold.xwalk,
                    gold.build, gold.beneficiary, gold.org, gold.measure, gold.fiscal_year, gold.meta RESTART IDENTITY""")
