"""The test database: the foundation of tools/danni migrated with its places loaded from the real EKATTE archive by
danni's own step, the serving layer of Население (the inhabitants for "per inhabitant"), then the tables of Субсидии.
Built once for the session; every database test starts with the tables of Субсидии empty. Only a database whose name
says it is a test one is used."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from ingest import config, pipeline

ROOT = Path(__file__).resolve().parent.parent
DANNI = ROOT.parent / "danni"           # the foundation is not copied into the tool: the tests need the sibling folder
NASELENIE = ROOT.parent / "naselenie"   # the registrations "per inhabitant" divides by are published by Население
TEST_DSN = os.environ.get("SUBSIDII_TEST_DSN")
SCHEMAS = ("api_v1", "pub_subsidii", "pub_naselenie", "pub", "subsidii", "subsidii_src", "naselenie", "naselenie_src",
           "danni_src", "core", "ops", "silver", "gold", "stage")
TABLES = ("subsidii_src.block_diff", "subsidii_src.payment", "subsidii_src.holder", "subsidii_src.line", "subsidii.observation",
          "subsidii.payment_agg", "subsidii.municipality_fy", "subsidii.summary", "subsidii.unmatched", "subsidii.place_match", "subsidii.build",
          "subsidii.beneficiary", "subsidii.org", "subsidii.measure", "subsidii.fiscal_year", "subsidii.meta", "subsidii.snapshot",
          "subsidii_src.beneficiary", "subsidii_src.measure", "subsidii.source_year",
          "pub_subsidii.fiscal_year", "pub_subsidii.snapshot", "pub_subsidii.build", "pub_subsidii.measure", "pub_subsidii.org",
          "pub_subsidii.recipient", "pub_subsidii.below_threshold", "pub_subsidii.municipality_fy", "pub_subsidii.payment_agg",
          "pub_subsidii.summary", "pub_subsidii.concentration", "pub_subsidii.org_measure", "pub_subsidii.block_diff",
          "pub_subsidii.unmatched", "pub_subsidii.change", "pub_subsidii.health", "pub_naselenie.population", "pub_naselenie.snapshot")


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
        pipeline.migrate(c, "naselenie", NASELENIE)
        pipeline.migrate(c, config.TOOL, ROOT)
    # danni again, now that the datasets of this tool are registered: their right to be shown comes from the licence
    # list of danni, the person columns from its list of decisions, and the places are published
    subprocess.run([sys.executable, "-m", "ingest.run", "--step", "check"], cwd=DANNI, env=env, capture_output=True)
    with pipeline.connect(dsn) as c:
        assert c.execute("SELECT is_displayable FROM core.dataset WHERE dataset = 'subsidii.paid'").fetchone()[0]
        assert not c.execute("SELECT is_displayable FROM core.dataset WHERE dataset = 'subsidii.dfz'").fetchone()[0]
        assert c.execute("SELECT count(*) FROM pub.municipality").fetchone()[0] == 265


@pytest.fixture(scope="session")
def dsn(tmp_path_factory):
    if not TEST_DSN:
        pytest.skip("SUBSIDII_TEST_DSN is not set: the database tests run on the server (ops/scripts/st.sh)")
    assert "test" in psycopg.conninfo.conninfo_to_dict(TEST_DSN).get("dbname", ""), "the test database name must contain 'test'"
    rebuild(TEST_DSN, tmp_path_factory.mktemp("foundation"))
    config.DSN = TEST_DSN
    return TEST_DSN


@pytest.fixture
def c(dsn):
    """A connection to a database whose tables of Субсидии are empty, and helpers.RUN, the run the loads belong to."""
    from tests import helpers
    with pipeline.connect(dsn) as conn:
        pipeline.require_test_db(conn)
        reset(conn)
        rid = conn.execute("INSERT INTO ops.run (tool, step) VALUES (%s, 'test') RETURNING run_id", (config.TOOL,)).fetchone()[0]
        helpers.RUN = pipeline.Run(conn, config.TOOL, "test", rid)
        yield conn
        conn.execute("SELECT set_config('subsidii.today', '', false)")


def reset(conn):
    from tests import helpers
    helpers.ARCH.clear()
    conn.execute("SELECT set_config('subsidii.today', '', false)")
    conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
    conn.execute("DELETE FROM pub.publication WHERE tool = %s", (config.TOOL,))
    conn.execute("UPDATE ops.check_rule SET is_active = true WHERE tool = %s", (config.TOOL,))
    for table in ("check_result", "change_log", "hold", "fetch", "load", "run"):
        conn.execute(f"DELETE FROM ops.{table} WHERE tool = %s", (config.TOOL,))
