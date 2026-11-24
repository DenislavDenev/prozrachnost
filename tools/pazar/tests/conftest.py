"""The test database: the foundation of tools/danni migrated and its places loaded from a real EKATTE archive by
danni's own step, then the tables of Пазар. Built once for the session; every test starts with the tool's tables
empty. Destructive: only a database whose name contains `test`."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent
DANNI = ROOT.parent / "danni"   # the foundation is not copied into the tool: the tests need the sibling folder
FIX = ROOT / "tests" / "fixtures"
TEST_DSN = os.environ.get("PAZAR_TEST_DSN")
SCHEMAS = ("api_v1", "pub_pazar", "pub", "pazar", "pazar_src", "danni_src", "core", "ops", "old_silver", "old_gold",
           "silver", "gold", "stage")


def rebuild(dsn, tmp):
    from ingest import db, gold, pipeline
    with pipeline.connect(dsn) as c:
        pipeline.require_test_db(c)
        for schema in SCHEMAS:
            c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        c.execute("DROP TABLE IF EXISTS public.schema_migrations")
    (tmp / "archive" / "ekatte").mkdir(parents=True)
    shutil.copy(FIX / "ekatte.zip", tmp / "archive" / "ekatte" / "2026-09-29.002862bf0655.zip")
    env = {**os.environ, "DANNI_DSN": dsn, "DANNI_ARCHIVE": str(tmp / "archive"), "DANNI_DATA": str(tmp / "danni")}
    for step in ("migrate", "place"):
        subprocess.run([sys.executable, "-m", "ingest.run", "--step", step], cwd=DANNI, env=env, check=True, capture_output=True)
    with db.connect(dsn) as c:
        db.migrate(c)
        gold.load_reference(c)
    # danni again, now that the datasets of this tool are registered: their right to be shown comes from the licence
    # list of danni, and the places are published into the serving layer
    subprocess.run([sys.executable, "-m", "ingest.run", "--step", "check"], cwd=DANNI, env=env, capture_output=True)
    with pipeline.connect(dsn) as c:
        assert c.execute("SELECT is_displayable FROM core.dataset WHERE dataset = 'pazar.kolkostruva'").fetchone()[0]
        assert c.execute("SELECT count(*) FROM pub.municipality").fetchone()[0] == 265


@pytest.fixture(scope="session")
def schema(tmp_path_factory):
    if not TEST_DSN:
        pytest.skip("PAZAR_TEST_DSN is not set: the database tests run on the server (ops/scripts/st.sh) and in CI")
    assert "test" in psycopg.conninfo.conninfo_to_dict(TEST_DSN).get("dbname", ""), "the test database name must contain 'test'"
    from ingest import config
    config.DSN = TEST_DSN
    config.DATA = tmp_path_factory.mktemp("data")
    rebuild(TEST_DSN, tmp_path_factory.mktemp("foundation"))
    return TEST_DSN


@pytest.fixture
def c(schema):
    """A connection to the test database with the tool's tables emptied before the test (the tables stay)."""
    from ingest import pipeline
    conn = pipeline.connect(schema)
    reset(conn)
    yield conn
    conn.close()


def reset(conn):
    from tests import helpers
    from ingest import pipeline
    pipeline.require_test_db(conn)
    helpers.ARCH.clear()
    for schema in ("old_silver", "old_gold"):
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    for parent in ("pazar.price_span", "pazar.category_day", "pub_pazar.price_span", "pub_pazar.category_day"):
        for (p,) in conn.execute(f"SELECT inhrelid::regclass::text FROM pg_inherits WHERE inhparent = '{parent}'::regclass").fetchall():
            conn.execute(f"DROP TABLE {p}")
    conn.execute("DELETE FROM core.dataset_table WHERE schema_name = 'pub_pazar' AND (table_name LIKE 'price\\_span\\_%' OR table_name LIKE 'category\\_day\\_%')")
    landing = [f"pazar_src.{t}" for (t,) in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'pazar_src'")]
    clean = [f"pazar.{t}" for (t,) in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'pazar' AND tablename <> 'category'")]
    served = [f"pub_pazar.{t}" for (t,) in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'pub_pazar'")]
    conn.execute(f"TRUNCATE {', '.join(landing + clean + served)} RESTART IDENTITY CASCADE")
    conn.execute("DELETE FROM pub.publication WHERE tool = 'pazar'")
    conn.execute("UPDATE ops.check_rule SET is_active = true WHERE tool = 'pazar'")
    for table in ("check_result", "change_log", "hold", "fetch", "load", "run"):
        conn.execute(f"DELETE FROM ops.{table} WHERE tool = 'pazar'")
    conn.execute("DROP TABLE IF EXISTS pz_closed")
