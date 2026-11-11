"""The test database for the register: the foundation of tools/danni migrated, then the sources and rules of the
archive. Built once for the session; every test starts with the archive's rows gone. The tests of the archive itself
(test_archive.py) need no database."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DANNI = ROOT.parent / "danni"   # the foundation is not copied into the tool: the tests need the sibling folder


@pytest.fixture(autouse=True)
def no_shared_database(monkeypatch):
    """A test never reaches the real register: without the `conn` fixture the archive runs without a database."""
    monkeypatch.setenv("NABLYUDATEL_DSN", "")


@pytest.fixture(scope="session")
def dsn():
    dsn = os.environ.get("NABLYUDATEL_TEST_DSN")
    if not dsn:
        pytest.skip("NABLYUDATEL_TEST_DSN required")
    from ingest import pipeline
    with pipeline.connect(dsn) as c:
        pipeline.require_test_db(c)
        for (schema,) in c.execute("""SELECT nspname FROM pg_namespace WHERE nspname !~ '^pg_'
                AND nspname NOT IN ('information_schema', 'public')""").fetchall():
            c.execute(f'DROP SCHEMA "{schema}" CASCADE')
    subprocess.run([sys.executable, "-m", "ingest.run", "--step", "migrate"], cwd=DANNI, check=True, capture_output=True,
                   env={**os.environ, "DANNI_DSN": dsn})
    with pipeline.connect(dsn) as c:
        pipeline.require_core(c, "0008")
        pipeline.migrate(c, "nablyudatel", ROOT)
    return dsn


@pytest.fixture
def conn(dsn, monkeypatch):
    from ingest import pipeline
    monkeypatch.setenv("NABLYUDATEL_DSN", dsn)
    with pipeline.connect(dsn) as c:
        pipeline.require_test_db(c)
        for table in ("check_result", "fetch", "run"):
            c.execute(f"DELETE FROM ops.{table} WHERE tool = 'nablyudatel'")
        c.execute("DELETE FROM ops.raw_file r WHERE NOT EXISTS (SELECT FROM ops.fetch f WHERE f.raw_file_id = r.raw_file_id)")
        yield c
