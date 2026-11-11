"""The test database: the foundation of tools/danni migrated and its places loaded from the real EKATTE archive
by danni's own step, then the tables of Образование. Built once for the session; every test starts with them empty."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from app import queries
from ingest import pipeline

ROOT = Path(__file__).resolve().parent.parent
DANNI = ROOT.parent / "danni"   # the foundation is not copied into the tool: the tests need the sibling folder
FIX = ROOT / "tests" / "fixtures"
SCHEMAS = ("api_v1", "pub_obrazovanie", "pub", "obrazovanie", "obrazovanie_src", "danni_src", "core", "ops", "live")
TABLES = ("obrazovanie.exam_result, obrazovanie.school, obrazovanie.nvo7_publication, obrazovanie.dzi_result, "
          "obrazovanie.dzi_publication, obrazovanie.nvo_result, obrazovanie.nvo_publication, obrazovanie.status_row, "
          "obrazovanie.status_publication, obrazovanie.context_grade, obrazovanie.context_kind, "
          "obrazovanie.context_publication, obrazovanie_src.nvo7_row, obrazovanie_src.nvo7, obrazovanie_src.dzi_row, "
          "obrazovanie_src.dzi, obrazovanie_src.nvo_row, obrazovanie_src.nvo, obrazovanie_src.status_row, "
          "obrazovanie_src.status, obrazovanie_src.context_grade, obrazovanie_src.context_row, obrazovanie_src.context, "
          "obrazovanie_src.register_row, obrazovanie_src.register, pub_obrazovanie.exam_result, pub_obrazovanie.school, "
          "pub_obrazovanie.nvo7_publication, pub_obrazovanie.dzi_result, pub_obrazovanie.dzi_publication, "
          "pub_obrazovanie.nvo_result, pub_obrazovanie.nvo_publication, pub_obrazovanie.status_row, "
          "pub_obrazovanie.status_publication, pub_obrazovanie.context_grade, pub_obrazovanie.context_kind, "
          "pub_obrazovanie.context_publication")


def rebuild(dsn, tmp):
    with pipeline.connect(dsn) as c:
        pipeline.require_test_db(c)
        for schema in SCHEMAS:
            c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        c.execute("DROP TABLE IF EXISTS public.schema_migrations")
    (tmp / "archive" / "ekatte").mkdir(parents=True)
    shutil.copy(FIX / "ekatte.zip", tmp / "archive" / "ekatte" / "2026-09-29.002862bf0655.zip")
    env = {**os.environ, "DANNI_DSN": dsn, "DANNI_ARCHIVE": str(tmp / "archive"), "DANNI_DATA": str(tmp / "danni")}
    for step in ("migrate", "place"):
        subprocess.run([sys.executable, "-m", "ingest.run", "--step", step], cwd=DANNI, env=env, check=True,
                       capture_output=True)
    with pipeline.connect(dsn) as c:
        pipeline.require_core(c, "0009")
        pipeline.migrate(c, "obrazovanie", ROOT)
    # danni again, now that the datasets of this tool are registered: their right to be shown comes from the licence
    # list of danni, and the places are published into the serving layer
    subprocess.run([sys.executable, "-m", "ingest.run", "--step", "check"], cwd=DANNI, env=env, capture_output=True)
    with pipeline.connect(dsn) as c:
        assert c.execute("SELECT bool_and(is_displayable) FROM core.dataset WHERE tool = 'obrazovanie'").fetchone()[0]
        assert c.execute("SELECT count(*) FROM pub.municipality").fetchone()[0] == 265


@pytest.fixture(scope="session")
def dsn(tmp_path_factory):
    dsn = os.environ.get("OBRAZOVANIE_TEST_DSN")
    if not dsn:
        pytest.skip("OBRAZOVANIE_TEST_DSN required")
    assert "test" in psycopg.conninfo.conninfo_to_dict(dsn).get("dbname", "")
    rebuild(dsn, tmp_path_factory.mktemp("foundation"))
    return dsn


@pytest.fixture
def serving(dsn, monkeypatch):
    """The pages read this test database."""
    monkeypatch.setattr(queries, "DSN", dsn)
    return dsn


@pytest.fixture
def conn(serving, monkeypatch, tmp_path):
    from ingest import run
    monkeypatch.setattr(run, "RAW", tmp_path / "raw")
    with pipeline.connect(serving) as c:
        pipeline.require_test_db(c)
        c.execute("DROP SCHEMA IF EXISTS live CASCADE; DROP SCHEMA IF EXISTS ops_old CASCADE")
        c.execute(f"TRUNCATE {TABLES}")
        c.execute("DELETE FROM pub.publication WHERE tool = 'obrazovanie'")
        c.execute("UPDATE ops.check_rule SET is_active = true WHERE tool = 'obrazovanie'")
        for table in ("check_result", "change_log", "hold", "fetch", "load", "run"):
            c.execute(f"DELETE FROM ops.{table} WHERE tool = 'obrazovanie'")
        yield c
