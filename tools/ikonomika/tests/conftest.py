"""The test database: the foundation of tools/danni migrated and its places loaded from the real EKATTE archive by
danni's own step, then the tables of Икономика. Built once for the session; every test starts with them empty.

Every connection of the tests goes to IKONOMIKA_TEST_DSN. ingest.config reads IKONOMIKA_DSN once, when the first test
file imports ingest, so setting the environment in a test file is too late: the fixtures would wipe the production
database. The DSN is set on the modules that connect."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent
DANNI = ROOT.parent / "danni"   # the foundation is not copied into the tool: the tests need the sibling folder
SCHEMAS = ("api_v1", "pub_ikonomika", "pub", "ikonomika", "ikonomika_src", "danni_src", "core", "ops", "live", "old_ops")
DSN = os.environ.get("IKONOMIKA_TEST_DSN")


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
        pipeline.require_core(c, "0011")
        pipeline.migrate(c, "ikonomika", ROOT)
    # danni again, now that the datasets of this tool are registered: their right to be shown comes from its licence list
    subprocess.run([sys.executable, "-m", "ingest.run", "--step", "check"], cwd=DANNI, env=env, capture_output=True)
    with pipeline.connect(dsn) as c:
        assert c.execute("SELECT is_displayable FROM core.dataset WHERE dataset = 'ikonomika.series'").fetchone()[0]
        assert c.execute("SELECT count(*) FROM core.nuts").fetchone()[0] == 37


@pytest.fixture(scope="session", autouse=True)
def test_database():
    if DSN:
        assert "test" in psycopg.conninfo.conninfo_to_dict(DSN).get("dbname", ""), f"refusing to use {DSN!r}"
        from ingest import config
        config.DSN = DSN
        from ingest import run
        run.DSN = DSN
        from app import main
        main.DSN = DSN
    yield


@pytest.fixture(scope="session")
def dsn(tmp_path_factory):
    if not DSN:
        pytest.skip("IKONOMIKA_TEST_DSN required")
    rebuild(DSN, tmp_path_factory.mktemp("foundation"))
    return DSN


def reset(c):
    from ingest import pipeline
    pipeline.require_test_db(c)
    c.execute("DROP SCHEMA IF EXISTS live CASCADE; DROP SCHEMA IF EXISTS old_ops CASCADE")
    c.execute("""TRUNCATE ikonomika.observation, ikonomika.series, ikonomika.dim_label, ikonomika.source_state,
        ikonomika_src.observation, ikonomika_src.label, ikonomika_src.ref_label, pub_ikonomika.series, pub_ikonomika.dim_label,
        pub_ikonomika.source_state, pub_ikonomika.hold, pub_ikonomika.change_count RESTART IDENTITY""")
    c.execute("DELETE FROM pub.publication WHERE tool = 'ikonomika'")
    c.execute("UPDATE ops.check_rule SET is_active = true WHERE tool = 'ikonomika'")
    for table in ("check_result", "change_log", "hold", "fetch", "load", "run"):
        c.execute(f"DELETE FROM ops.{table} WHERE tool = 'ikonomika'")
    c.execute("DELETE FROM core.fx_rate")


@pytest.fixture
def conn(dsn, tmp_path, monkeypatch):
    """The test database with the tables of Икономика empty; raw files go to a temporary folder."""
    from ingest import pipeline, store
    monkeypatch.setattr(store, "RAW", tmp_path / "raw")
    with pipeline.connect(dsn) as c:
        reset(c)
        yield c


@pytest.fixture
def run(conn):
    """One run of a test step: what store.apply() and the source modules write under."""
    from ingest import pipeline
    with pipeline.run(conn, "ikonomika", "test") as r:
        yield r
